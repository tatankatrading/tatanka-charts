# Copyright (c) 2026 Tatanka Trading LLC.
# This Source Code Form is subject to the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, obtain one at https://mozilla.org/MPL/2.0/.
"""Tatanka 1hr 21 EMA (HTF EMA) v1.0.1, ported from the NinjaTrader 8 source
(tatankatrading/tatanka-indicators, ninjatrader/TatankaHtfEma.cs).

The indicator's OnBarUpdate runs in the browser (web/studies.js, htfEmaValues), on the bars the chart holds,
live on every price change. The server supplies what the chart cannot compute from its own bars:
NinjaTrader's EMA over the higher-timeframe bars (closed ones only: as 1.0.1, Exact and Live never use the forming
one; it has closed once a trade after it came, as NinjaTrader sees it), and the chart-timeframe EMA up to the
chart's first bar, so the SmoothScaled line starts settled instead of from a cold seed. A seconds chart's EMA is
thousands of bars long (75600 on a 1s chart), far more than the chart holds: its seed comes from stored trades and,
further back, from the 1-minute bars (seconds_seed). On an RTH chart all of it comes from the regular hours, as
NinjaTrader's added series follows an RTH trading-hours template.

The source series is any of NinjaTrader's time bars: Second (built from the stored trades, as the seconds charts are),
Minute (from the 1-minute bars), Day, Week, Month and Year (from the daily bars, grouped as this app's 2D, week and
month charts group them; a Second source in whole minutes is that Minute source, the same bars)."""
import asyncio
import math
from datetime import date, timedelta
from itertools import chain

from . import sessions
from .amt_runner import ct, epoch_ms
from .replay import next_trading_session, trading_session
from .sessions import SessionClock
from .store import iter_bars_1m, iter_closes_1s
from .studies import RthClock
from .tpo_service import chart_bars, rth_day, session_end

VERSION = "1.0.1"
MODES = ("LiveHTF", "ExactHTF", "SmoothScaled")
DEFAULTS = {"HtfPeriodType": "Minute", "HtfPeriod": 60, "EmaLength": 21, "Mode": "SmoothScaled"}
TYPES = ("Second", "Minute", "Day", "Week", "Month", "Year")   # NinjaTrader's time-based BarsPeriodTypes
CALENDAR = {"Day": ("D", 1), "Week": ("W", 1), "Month": ("M", 1), "Year": ("M", 12)}   # (unit, how many a period)
INT_MAX = 2**31 - 1     # NinjaTrader's [Range(1, int.MaxValue)]
SETTLE = 8              # EMA lengths of history loaded before what is shown: the seed's weight is ~e^-16
MAX_DAYS = 400
CAL_DAYS = 20 * 366     # Day, Week, Month and Year sources: daily bars from at most this far back (the 1M chart's)
TRADE_DAYS = {"D": 1, "W": 5, "M": 21}   # trading days in one of each unit
SECONDS_EXACT = 4       # seconds charts: lengths of their own bars, from stored trades, before the first bar
SECONDS_TRADES = 4_000_000   # at most this many stored trades read a request; further back, the 1-minute bars
SHARED = 16             # seconds charts' answers kept for identical requests (shared)


def settings(overrides: dict | None) -> dict:
    s = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown HTF EMA setting {k}")
        s[k] = v
    if s["HtfPeriodType"] not in TYPES:
        raise ValueError(f"HtfPeriodType must be one of {', '.join(TYPES)}")
    if s["Mode"] not in MODES:
        raise ValueError(f"Mode must be one of {', '.join(MODES)}")
    for k in ("HtfPeriod", "EmaLength"):
        if isinstance(s[k], bool) or not isinstance(s[k], int) or not 1 <= s[k] <= INT_MAX:
            raise ValueError(f"{k} must be 1-{INT_MAX}")
    return s


def source(s: dict) -> tuple[str, int]:
    """How the source series is built: ("second", N) from the trades, ("minute", N) from the 1-minute bars (a Second
    source in whole minutes too: the same bars), or (unit, N) of the daily bars, unit D (trading days), W or M."""
    t, n = s["HtfPeriodType"], s["HtfPeriod"]
    if t == "Second":
        return ("minute", n // 60) if n % 60 == 0 else ("second", n)
    if t == "Minute":
        return "minute", n
    unit, per = CALENDAR[t]
    return unit, n * per


class NtEma:
    """NinjaTrader 8's EMA: the first input, then Input * 2/(1+Period) + (1 - 2/(1+Period)) * previous."""

    def __init__(self, period: int):
        self.constant1 = 2.0 / (1 + period)
        self.constant2 = 1 - (2.0 / (1 + period))
        self.values: list[float] = []
        self.count = 0   # the inputs taken (add_many's count as many)

    def add(self, x: float) -> float:
        v = x if not self.values else x * self.constant1 + self.constant2 * self.values[-1]
        self.values.append(v)
        self.count += 1
        return v

    def add_many(self, x: float, k: int) -> float:
        """k inputs, all x, at once (a minute standing for the seconds bars in it: within rounding of k add()s)."""
        if k == 1:
            return self.add(x)
        v = x if not self.values else x + (self.values[-1] - x) * self.constant2 ** k
        self.values.append(v)
        self.count += k
        return v


def GetPeriodSeconds(period_type: str, value: int) -> float:
    if period_type == "Second":
        return value
    if period_type == "Minute":
        return value * 60.0
    if period_type == "Day":
        return value * 86400.0
    if period_type == "Week":
        return value * 604800.0
    if period_type == "Month":
        return value * 2592000.0
    if period_type == "Year":
        return value * 31536000.0
    return 0


def GetScaledLength(s: dict, chart_type: str, chart_value: int) -> int:
    htfSec = GetPeriodSeconds(s["HtfPeriodType"], s["HtfPeriod"])
    chartSec = GetPeriodSeconds(chart_type, chart_value)
    if htfSec <= 0 or chartSec <= 0:
        return max(1, s["EmaLength"])
    scaled = int(round(s["EmaLength"] * (htfSec / chartSec)))    # Math.Round: midpoints to even, as round()
    if not -2**31 <= scaled <= INT_MAX:   # (int) of a double past Int32 is int.MinValue (.NET Framework, mono)
        scaled = -2**31
    return max(1, scaled)


def chart_period(spec_kind: str, spec_seconds: int) -> tuple[str, int]:
    """Our bar spec as NinjaTrader's BarsPeriodType and value: hour charts are 60n-minute charts."""
    if spec_kind != "time":
        return {"tick": "Tick", "volume": "Volume", "range": "Range", "renko": "Renko"}[spec_kind], spec_seconds
    if spec_seconds in (86400, 604800, 2678400):   # 1D, 1W, 1M
        return {86400: "Day", 604800: "Week", 2678400: "Month"}[spec_seconds], 1
    return "Minute", spec_seconds // 60


def chart_period_spec(sp) -> tuple[str, int]:
    """A BarSpec as NinjaTrader's BarsPeriodType and value: 15s is Second 15, 2D is Day 2, 3M is Month 3."""
    if sp.cal:
        return {"D": "Day", "W": "Week", "M": "Month"}[sp.cal], sp.mult
    if sp.kind == "time" and sp.size % 60:
        return "Second", sp.size
    return chart_period(sp.kind, sp.size)


def htf_minutes(s: dict) -> int:
    kind, n = source(s)
    return n if kind == "minute" else 1440   # a 1440-minute bucket is the session


def htf_bars(s: dict, mins, calendar: str | None, rth: tuple[str, str] | None = None) -> list[list]:
    """The higher-timeframe bars, as chart_bars gives them (rth: an RTH chart's). Day: one per trade date, as the
    chart's 1D bars: a holiday halt's short session (MLK Day, ...) is in the next day's bar, which closes at that day's
    16:00 close (on an RTH chart its RTH close)."""
    bars = chart_bars(mins, htf_minutes(s), rth)
    if s["HtfPeriodType"] != "Day" or not calendar:
        return bars
    days: list[list] = []
    for b in bars:
        k = sessions.counts_toward(sessions.session_start(b[4]) if rth else b[4], calendar)
        if days and days[-1][4] == k:
            d = days[-1]
            d[2], d[3], d[6] = max(d[2], b[2]), min(d[3], b[3]), b[6]
        else:
            days.append([b[0], rth_day(k, rth)[1] if rth else session_end(k), b[2], b[3], k, b[5], b[6]])
    return days


def history_ms(s: dict, chart_minutes: int | None, scaled: int, from_ms: int, now_ms: int,
               chart_seconds: int | None = None, rth: tuple[str, str] | None = None, source_bars: bool = True) -> int:
    """How far back to load the 1-minute bars: SETTLE lengths of both EMAs before the chart's first bar, in trading
    days (a seconds chart's or source's as if it had a bar every period; rth: an RTH chart's, whose days are as long as
    its regular hours). source_bars False: the source's bars come from elsewhere (the daily bars, calendar_history_ms)."""
    hours = RthClock(rth) if rth else None
    per_day = hours.end - hours.start if hours else 1380   # Globex minutes per day, or the regular hours'

    def span(minutes):   # the trading days one bar spans: a bar longer than the regular hours is one session's
        return min(minutes, per_day) / per_day if minutes < 1380 else minutes / 1380
    chart = chart_minutes or (chart_seconds or 0) / 60
    kind, n = source(s)
    if not source_bars:
        htf = 0
    elif kind == "second":
        htf = span(n / 60) * s["EmaLength"] * SETTLE
    else:   # a day of minutes per trading day of a Day, Week, Month or Year bar
        htf = span(htf_minutes(s)) * TRADE_DAYS.get(kind, 1) * (1 if kind == "minute" else n) * s["EmaLength"] * SETTLE
    smooth = scaled * span(chart) * SETTLE if chart and s["Mode"] == "SmoothScaled" else 0
    days = math.ceil(max(htf, smooth) * 7 / 5) + 3
    return min(from_ms, now_ms) - min(days, MAX_DAYS) * 86_400_000


def calendar_history_ms(s: dict, from_ms: int, now_ms: int) -> int:
    """Day, Week, Month and Year sources: from when the daily bars are read, SETTLE lengths of the source before the
    chart's first bar (at most CAL_DAYS back)."""
    unit, n = source(s)
    days = math.ceil(TRADE_DAYS[unit] * n * s["EmaLength"] * SETTLE * 7 / 5) + 3
    return min(from_ms, now_ms) - min(days, CAL_DAYS) * 86_400_000


def trade_date(close_ms: int) -> date:
    """The trade date of a daily bar closing at close_ms (its 16:00 close, an early close, or the RTH close)."""
    return (ct(close_ms) + timedelta(hours=7)).date()


def day_close(day: date, rth: tuple[str, str] | None) -> int:
    """When a trade date's daily bar closes, as the Day source has it: 16:00 CT, or the RTH close on an RTH chart."""
    k = sessions.day_open(day)
    return rth_day(k, rth)[1] if rth else session_end(k)


def days_of(mins, calendar: str | None, rth: tuple[str, str] | None) -> list[tuple[date, float]]:
    """(trade date, close) of each daily bar of the minutes, as the Day source builds them (htf_bars)."""
    return [(trade_date(d[1]), d[6]) for d in htf_bars(DEFAULTS | {"HtfPeriodType": "Day", "HtfPeriod": 1}, mins, calendar, rth)]


def _group(day: date, unit: str, mult: int) -> int:
    """Which bar of `mult` trading days (D), weeks or months a trade date is in, counted from a fixed start: the 2D, 3D,
    week and month charts' groups (bars.calendar_bucket)."""
    if unit == "D":
        bd = (day.toordinal() - 1) // 7 * 5 + min(day.weekday(), 4)   # date(1, 1, 1) was a Monday
        return bd // mult
    if unit == "W":
        return (day.toordinal() - 1) // 7 // mult
    return (day.year * 12 + day.month - 1) // mult


def _group_last_day(g: int, unit: str, mult: int) -> date:
    """The last weekday of group g (_group): when a forming one closes, at the latest."""
    top = date(9999, 12, 31).toordinal()
    if unit == "D":
        bd = g * mult + mult - 1
        return date.fromordinal(min(bd // 5 * 7 + 1 + bd % 5, top))
    if unit == "W":
        return date.fromordinal(min((g * mult + mult - 1) * 7 + 5, top))   # the Friday
    mi = min(g * mult + mult, 9999 * 12 + 11)                  # the month after the group's last
    d = date(mi // 12, mi % 12 + 1, 1) - timedelta(days=1) if mi < 9999 * 12 + 11 else date(9999, 12, 31)
    while d.weekday() > 4:
        d -= timedelta(days=1)
    return d


def calendar_bars(days, unit: str, mult: int, rth: tuple[str, str] | None) -> list[list]:
    """[close ms, close] of each Day, Week, Month or Year bar of the daily bars `days` ((trade date, close), in order):
    `mult` trading days counted Monday-Friday, weeks or months, from a fixed start (the 2D, week and month charts'
    groups). A bar closes with its last day (16:00 CT; an RTH chart's at the RTH close); the last one, maybe still
    forming, with its last weekday."""
    out, g = [], None
    for day, c in days:
        k = _group(day, unit, mult)
        if out and k == g:
            out[-1] = [day_close(day, rth), c]
        else:
            out.append([day_close(day, rth), c])
            g = k
    if out:
        out[-1][0] = max(out[-1][0], day_close(_group_last_day(g, unit, mult), rth))
    return out


def frame_of(rth: tuple[str, str] | None):
    """frame(t): where a seconds bar's grid counts from at t and where its session ends: the 17:00 CT session open and
    its 16:00 close, or on an RTH chart that day's regular hours (TimeSeries.bucket's origin)."""
    if rth:
        hours, windows = RthClock(rth), {}

        def frame(t):
            day = hours.day(t)
            if day not in windows:
                windows[day] = hours.window(sessions.to_ct(t))
            return windows[day]
        return frame
    clock, ends = SessionClock(), {}

    def frame(t):
        o = clock.locate(t)
        if o not in ends:
            ends[o] = session_end(o)
        return o, ends[o]
    return frame


def seconds_bars(mins: list, seconds: int, second_closes, trades_from: int, frame, batch: bool) -> tuple[list, int | None]:
    """The Second source's bars: [close ms, close, k] each, as NinjaTrader's seconds bars (a grid of `seconds` from
    the session open, the last one cut at the close, stamped with its close; one only where a trade printed). From
    trades_from on they are built from the stored trades (second_closes(lo, hi): (second ms, close)); before that, and
    in a minute with none stored, each 1-minute bar stands for the source bars that close in it, all at its close and no
    more than its trades. batch: those closing inside the minute come as two, the first and [close, price, k] for the
    others (minute-aligned charts tell no more apart). Also the last print's time."""
    period = seconds * 1000
    out: list[list] = []
    cur = None   # the bar being built: [open, close ms, price]
    last = None

    def bar_at(t):
        o, e = frame(t)
        b = o + (t - o) // period * period
        return b, min(b + period, e)

    def flush():
        nonlocal cur
        if cur is not None:
            out.append([cur[1], cur[2], 1])
            cur = None

    def put(t, p):   # a print at t
        nonlocal cur, last
        last = t
        b, close = bar_at(t)
        if cur is not None and cur[0] == b:
            cur[2] = p
            return
        flush()
        cur = [b, close, p]

    def minute(m):   # no stored trades in it: prints at its close in each bar closing in it, and in the one going on
        nonlocal last
        o, e = frame(m.t)
        end = m.t + 60_000
        closes = [o + j * period for j in range((m.t - o) // period + 1, (min(end, e - 1) - o) // period + 1)]
        if m.t < e <= end:
            closes.append(e)   # the session's last, cut at its close
        if m.n:
            closes = closes[len(closes) - m.n:] if len(closes) > m.n else closes
        inside = [c for c in closes if c < end]
        if batch and len(inside) > 2:   # the first (maybe the bar trades began), then the others at once
            put(inside[0] - 1, m.c)
            flush()
            out.append([inside[-1], m.c, len(inside) - 1])
            last = inside[-1] - 1
            closes = closes[len(inside):]
        for c in closes:
            put(c - 1, m.c)
        if bar_at(end - 1)[1] > end:
            put(end - 1, m.c)   # the bar it ends in closes later: its price so far
    prints = iter(second_closes(trades_from, mins[-1].t + 60_000) if second_closes is not None and mins else ())
    nxt = next(prints, None)
    for m in mins:
        end = m.t + 60_000
        while nxt is not None and nxt[0] < m.t:   # a stored trade in a minute without a bar
            put(*nxt)
            nxt = next(prints, None)
        if m.t < trades_from or nxt is None or nxt[0] >= end:
            minute(m)
            continue
        while nxt is not None and nxt[0] < end:
            put(*nxt)
            nxt = next(prints, None)
    while nxt is not None:
        put(*nxt)
        nxt = next(prints, None)
    if cur is not None:
        out.append([cur[1], cur[2], 1])
    return out, last


def chart_key(period_ms: int, rth: tuple[str, str] | None):
    """thin for a seconds, minute, hour or 1D chart (bars of period_ms from the session open, or on an RTH chart the RTH
    open, the last cut at the close): the chart bar that first sees a source bar closing at c, as htfEmaValues counts
    (the one holding c, or the next where c is its session's close)."""
    frame, hours = frame_of(rth), RthClock(rth) if rth else None

    def key(c):
        if hours and hours.day(c) is None:
            return (c,)   # an RTH close: past every bar of that day
        o, e = frame(c)
        return (o, (c - o) // period_ms, c >= e)
    return key


def second_records(s: dict, mins: list, from_ms: int, second_closes, rth, batch: bool,
                   after: int | None = None) -> tuple[list, int | None]:
    """A Second source's bars (seconds_bars) from SETTLE lengths of them before from_ms (counted on the minutes, as many
    as close in each), from the stored trades for at most the last SECONDS_TRADES of them (counted on the minutes, their
    volume where they have no count), and the last print's time. after: only those closing after it, from every stored
    trade of mins (which start at the minute it is in: a resumed answer's, compute)."""
    n = source(s)[1]
    if after is not None:
        if not mins:
            return [], None
        out, at = seconds_bars(mins, n, second_closes, mins[0].t, frame_of(rth), batch)
        return [r for r in out if r[0] > after], at
    i = next((j for j, m in enumerate(mins) if m.t >= from_ms), len(mins))
    need = SETTLE * s["EmaLength"]
    while i > 0 and need > 0:
        i -= 1
        need -= 60 / n
    j, total = len(mins), 0
    while j > i:
        total += mins[j - 1].n or mins[j - 1].v
        if total > SECONDS_TRADES:
            break
        j -= 1
    src = mins[i:]
    trades_from = mins[j].t if j < len(mins) else (mins[-1].t + 60_000 if mins else 0)
    return seconds_bars(src, n, second_closes, trades_from, frame_of(rth), batch)


def compute(s: dict, minute_bars, chart_minutes: int | None, scaled: int, from_ms: int, now_ms: int,
            calendar: str | None = None, bucket=None, chart_seconds: int | None = None, second_closes=None,
            rth: tuple[str, str] | None = None, days=None, thin=None, keep_before: int | None = None,
            resume: dict | None = None) -> dict:
    """htf: [close ms, EMA or None until EmaLength bars] for each closed HTF bar from the last one before
    from_ms; due: when the forming HTF bar closes (None: none trading), for the browser to ask again; held: due has
    passed but no trade after it has come yet (the browser asks again with the next one).
    An HTF bar has closed once a trade after its close is in minute_bars (NinjaTrader 1.0.1 steps with that trade:
    the source series' next bar, or a chart bar past it), or, with none yet, once now_ms is past its close and the
    market shut then (the session's close, a halt: none can come); minute_bars must end by now_ms.
    smooth: the chart EMA and bar count before from_ms (time charts in SmoothScaled mode);
    ends: session closes (16:00 CT) from from_ms, which cut the last chart bar of each session.
    calendar: the instrument's holiday group, for Day bars as the chart's 1D bars; bucket: D, 2D and 3D charts'
    TimeSeries.bucket, for their own bars in smooth. chart_seconds: a seconds chart's bar (1-59 s; chart_minutes is
    None), second_closes(since, before) its stored trades as (second ms, close), for seconds_seed and a Second source.
    rth: the instrument's regular hours, for an RTH chart: only their minutes (and trades), the HTF and chart bars
    counted from the RTH open, the ends at the RTH close.
    days: the daily bars ((trade date, close)) a Day, Week, Month or Year source groups (else built from the minutes);
    thin: a Second source's entries, only the last of those the same chart bar sees first (chart_key).
    keep_before (a Second source): the answer also has "resume", where it stood after the last closed source bar
    closing by then (the stored history before it is complete); given back as resume, with minute_bars from its
    "from" on, only the source bars after it are built: the same answer for a fraction of the trades."""
    mins = list(minute_bars)
    if rth:
        hours, opens = RthClock(rth), {}
        mins = [m for m in mins if hours.day(m.t) is not None]
        if second_closes is not None:   # a seconds RTH chart's trades too: the regular hours', a day's at a time
            all_hours = second_closes
            second_closes = lambda lo, hi: chain.from_iterable(all_hours(*w) for w in hours.windows(lo, hi))  # noqa: E731

        def rth_open(ts):   # where an RTH chart's bars count from, worked out once a day (as Series.rth_open)
            day = hours.day(ts)
            if day not in opens:
                opens[day] = hours.window(sessions.to_ct(ts))[0]
            return opens[day]
    ema = NtEma(s["EmaLength"])
    htf, due, held = [], None, False
    shown = [None]   # the thin key of htf's last entry
    if resume is not None:   # where an earlier answer stood (keep_before)
        ema.values, ema.count = [resume["ema"][0]], resume["ema"][1]
        htf, shown[0] = list(resume["htf"]), resume["key"]   # (its entries are never changed, only replaced)

    def push(x):   # the entries the chart reads: with thin, only the last of each run one chart bar sees first
        if thin is not None:
            k = thin(x[0])
            if htf and k == shown[0]:
                htf[-1] = x
                return
            shown[0] = k
        htf.append(x)
    last = mins[-1].t if mins else -math.inf   # the minute of the last trade (a Second source: its second)
    kind, n = source(s)
    snap = None   # (keep_before) after the last closed source bar by then: its close, the EMA, htf's length and last entry
    if kind == "second":
        records, at = second_records(s, mins, from_ms, second_closes, rth, batch=bool(chart_minutes) and not chart_seconds,
                                     after=resume["at"] if resume is not None else None)
        last = max(last, at if at is not None else -math.inf)
    elif kind == "minute":
        records = ([b[1], b[6], 1] for b in htf_bars(s, mins, calendar, rth))
    else:
        records = ([c, p, 1] for c, p in calendar_bars(days_of(mins, calendar, rth) if days is None else days, kind, n, rth))

    def trading(t):   # the market (an RTH chart's: its regular hours) open at t: a trade can still come
        return sessions.is_open(t, calendar) and (not rth or hours.day(t) is not None)
    for close, c, k in records:
        if last < close and (close > now_ms or trading(close)):
            if k > 1:   # a minute standing for k seconds bars: the ones before its last have closed
                v = ema.add_many(c, k - 1)
                push([close - n * 1000, v if ema.count >= s["EmaLength"] else None])
            due, held = close, close <= now_ms
            break                                         # the forming HTF bar: only closed ones count
        v = ema.add_many(c, k)
        push([close, v if ema.count >= s["EmaLength"] else None])
        if keep_before is not None and close <= keep_before:
            snap = (close, v, ema.count, len(htf), htf[-1], shown[0])
    first = next((i for i, x in enumerate(htf) if x[0] >= from_ms), len(htf))
    smooth = None
    if resume is not None:
        smooth = resume["smooth"]   # from the bars before from_ms: as it was
    elif chart_minutes and s["Mode"] == "SmoothScaled":
        e = NtEma(scaled)
        if bucket:   # bars of several sessions, a holiday halt's in the next day's: not one bar per session
            k = c = None
            for m in mins:
                t = bucket(m.t)
                if t >= from_ms:
                    break
                if k is not None and t != k:
                    e.add(c)
                k, c = t, m.c
            if c is not None:
                e.add(c)
        else:
            for b in chart_bars(mins, chart_minutes, rth):
                if b[0] >= from_ms:
                    break
                e.add(b[6])
        smooth = {"seed": e.values[-1] if e.values else None, "count": len(e.values)}
    elif chart_seconds and s["Mode"] == "SmoothScaled":
        smooth = seconds_seed(mins, chart_seconds, scaled, from_ms, second_closes, rth_open if rth else None)
    ends = []
    if chart_minutes or chart_seconds:
        hh, mm = divmod(RthClock(rth).end, 60) if rth else (16, 0)
        d = ct(from_ms).replace(hour=hh, minute=mm, second=0, microsecond=0)
        while epoch_ms(d) <= now_ms + 86_400_000:
            ends.append(epoch_ms(d))
            d += timedelta(days=1)
    out = {"version": VERSION, "ready": True, "len": s["EmaLength"], "scaled": scaled, "mode": s["Mode"],
           "htf": htf[max(0, first - 1):], "due": due, "held": held, "smooth": smooth, "ends": ends}
    if snap is not None:   # htf from the entry before from_ms on, as sent: what any later answer sends of it
        out["resume"] = {"at": snap[0], "from": snap[0] // 60_000 * 60_000, "ema": [snap[1], snap[2]],
                         "htf": htf[max(0, first - 1):snap[3] - 1] + [snap[4]], "key": snap[5], "smooth": smooth}
    elif resume is not None and keep_before is not None:
        out["resume"] = resume   # no source bar closed since: where it stood
    return out


def seconds_seed(mins: list, seconds: int, scaled: int, from_ms: int, second_closes=None, origin=None) -> dict:
    """A seconds chart's EMA and bar count before from_ms. Its bars are built from trades, and SETTLE lengths of them
    are millions of ES trades (8 x 21 hours of trading at the defaults), so: the last SECONDS_EXACT lengths are the
    chart's own bars, rebuilt from the stored trades (second_closes; at most SECONDS_TRADES of them, as the 1-minute
    bars count them, or their volume where they don't), each closing at its last second's trade. Before that, and in a
    minute with no stored trades (bars imported or filled from 1-minute history), each 1-minute bar stands for the chart
    bars that open in it, all at its close: no more than its trade count, as a seconds bar only exists where a trade
    printed (NinjaTrader's too). origin: where a timestamp's bars count from (an RTH chart's: its RTH open; else the
    session open)."""
    period = seconds * 1000
    constant1, constant2 = 2.0 / (1 + scaled), 1 - 2.0 / (1 + scaled)
    locate = origin or SessionClock().locate
    v, count = None, 0

    def add(c, k=1):   # k bars closing at c: NinjaTrader's EMA step, or k of them on the same close at once
        nonlocal v, count
        if v is None:
            v = c
        elif k == 1:
            v = c * constant1 + constant2 * v
        else:
            v = c + (v - c) * constant2 ** k
        count += k

    def held(m):   # the chart bars that open in a minute, counted from the session open (0 trades: not known)
        o = locate(m.t)
        first = o - (o - m.t) // period * period          # the first one opening at or after m.t
        k = len(range(first, m.t + 60_000, period))
        return min(k, m.n) if m.n else k

    before = [m for m in mins if m.t < from_ms]
    exact, need, trades = len(before), SECONDS_EXACT * scaled, 0   # before[exact:]: from the stored trades
    if second_closes is not None:
        while exact and need > 0:
            trades += before[exact - 1].n or before[exact - 1].v   # no count (imported, filled): its volume, no fewer
            if trades > SECONDS_TRADES:
                break
            exact -= 1
            need -= held(before[exact])
    for m in before[:exact]:
        if m.t + 60_000 <= from_ms:   # not the minute the chart starts in
            add(m.c, held(m))
    if exact == len(before):
        return {"seed": v, "count": count}
    secs = iter(second_closes(before[exact].t, from_ms))
    nxt = next(secs, None)
    k = c = None   # the chart bar being built: its open and close
    for m in before[exact:]:
        end = m.t + 60_000
        if nxt is None or nxt[0] >= end:   # no stored trade in this minute: its 1-minute bar
            if end <= from_ms:
                if k is not None:
                    add(c)
                    k = None
                add(m.c, held(m))
            continue
        while nxt is not None and nxt[0] < end:
            o = locate(nxt[0])
            t = o + (nxt[0] - o) // period * period   # as TimeSeries.bucket
            if k is not None and t != k:
                add(c)
            k, c = t, nxt[1]
            nxt = next(secs, None)
    if k is not None:
        add(c)
    return {"seed": v, "count": count}


def seconds_job(db_path, symbol: str, s: dict, since: int, split: int, live: list, scaled: int, from_ms: int,
                now_ms: int, calendar: str | None, chart_seconds: int, rth: tuple[str, str] | None = None) -> dict:
    """compute for a seconds chart in Smooth mode, a worker job (workers.run): its seed reads up to SECONDS_TRADES
    stored trades, seconds of CPU that in a thread hold the server's GIL. The 1-minute bars are the stored ones before
    split, then `live`."""
    return compute(s, chain(iter_bars_1m(db_path, symbol, since, split), live), None, scaled, from_ms, now_ms, calendar,
                   chart_seconds=chart_seconds, second_closes=lambda lo, hi: iter_closes_1s(db_path, symbol, lo, hi),
                   rth=rth)


def source_job(db_path, symbol: str, s: dict, since: int, split: int, live: list, scaled: int, from_ms: int,
               now_ms: int, calendar: str | None, chart_minutes: int | None, chart_seconds: int | None,
               rth: tuple[str, str] | None = None, resume: dict | None = None, keep: bool = True) -> dict:
    """compute for a Second source, a worker job: its bars come from up to SECONDS_TRADES stored trades. Thinned to
    what the chart reads (compute's thin): the last that each of its bars sees. With where the last answer for this
    chart stood (resume; the minutes from its "from" instead of since), only the source bars since; the answer's
    "resume" is for the next (resumed()), unless keep is False (the database can't be written: what is stored before
    split may not be complete)."""
    thin = chart_key((chart_seconds or chart_minutes * 60) * 1000, rth)
    return compute(s, chain(iter_bars_1m(db_path, symbol, resume["from"] if resume else since, split), live),
                   chart_minutes, scaled, from_ms, now_ms, calendar, chart_seconds=chart_seconds, rth=rth, thin=thin,
                   second_closes=lambda lo, hi: iter_closes_1s(db_path, symbol, lo, hi), keep_before=split if keep else None,
                   resume=resume)


# A Second source's answers rebuild every source bar from the chart's first bar, from up to SECONDS_TRADES stored trades
# (seconds of CPU on a long chart). Live and Exact ask again every 10 seconds or so inside a forming chart bar: each
# answer goes on from where the last for the same chart stood (compute's resume), the SHARED last kept.
_resumes: dict[tuple, dict] = {}
_history: dict[str, int] = {}   # per symbol: its stored history changed (forget) this many times


async def resumed(key: tuple, job) -> dict:
    """job(resume) for the chart's answer (key as shared()'s): from where the last one stood, or (None) the whole
    span. Its "resume" is kept for the next, not sent; not if the stored history changed meanwhile (forget)."""
    sym, was = key[0], _history.get(key[0], 0)
    d = await job(_resumes.get(key))
    r = d.pop("resume", None)
    if _history.get(sym, 0) == was:
        _resumes.pop(key, None)
        if r is not None:
            _resumes[key] = r
            while len(_resumes) > SHARED:
                del _resumes[next(iter(_resumes))]
    return d


_answers: dict[tuple, asyncio.Future] = {}


async def shared(key: tuple, now_ms: int, work) -> dict:
    """One computation for identical requests (key: symbol first, then the chart, its first bar and the settings),
    for a seconds chart's costly ones: the frames and devices asking for the same chart at once, a reload. One being
    worked out is waited for, one worked out is given while its HTF bar still forms (now_ms before its due), and to
    those that waited for it when none forms (the market shut: nothing changes meanwhile); a failure is every
    waiter's. The SHARED last are kept; forget() drops a symbol's. work(): a coroutine that works it out."""
    while (f := _answers.get(key)) is not None:
        waited = not f.done()
        d = await asyncio.shield(f)
        if d["due"] is not None and now_ms < d["due"] or waited and d["due"] is None:   # none forming: the market shut
            return d
        if _answers.get(key) is f:   # out of date (its HTF bar has closed since): this request works it out again
            break
    _answers.pop(key, None)
    f = _answers[key] = asyncio.ensure_future(work())

    def failed(f):   # not kept
        if (f.cancelled() or f.exception() is not None) and _answers.get(key) is f:
            del _answers[key]
    f.add_done_callback(failed)
    while len(_answers) > SHARED:
        del _answers[next(iter(_answers))]
    return await asyncio.shield(f)


def forget(symbol: str):
    """Stored history changed (a gap fill): worked out again, from the start."""
    _history[symbol] = _history.get(symbol, 0) + 1
    for k in [k for k in _answers if k[0] == symbol]:
        del _answers[k]
    for k in [k for k in _resumes if k[0] == symbol]:
        del _resumes[k]


def next_open(bucket, now_ms: int, rth: tuple[str, str] | None = None) -> int | None:
    """2D, 3D, week and month charts (bucket: their series' TimeSeries.bucket): when the next bar opens once the one
    holding the last session is over, from its close (16:00 CT; rth: an RTH chart's, at the RTH close) to then,
    where the HTF bar closing with it runs after it, as on a tie; None while it trades."""
    last = trading_session(now_ms)
    if now_ms < (rth_day(last, rth)[1] if rth else session_end(last)):
        return None
    t = bucket(next_trading_session(last))
    return t if t != bucket(last) else None
