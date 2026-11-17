"""Feeds the volume-profile ports (vp.py) this server's data the way NinjaTrader feeds the indicators theirs, and keeps
what they built between requests (GET /api/vp/{prof|aavp|pvp}).

- AAVP and PVP: 1-minute bars (NinjaTrader's AddDataSeries(Minute, 1)), stamped at their end as NinjaTrader stamps
  them, each with its session's open and trading day (the CME 17:00-16:00 CT session). A series is built once, in a
  worker, from the stored minutes it needs; after that each request feeds it the minutes stored since (in the worker
  too, when that is over a day) and the forming one. PVP's "until touched" ends: the chart finds them on its own bars,
  as NinjaTrader does (web/studies.js pvpTouches); the first minute after a closed profile whose range holds its POC,
  VAH or VAL (vp.holds) is sent too, for a period that ended before the chart's first loaded bar.
- SVP (the "prof" study): every trade of each session window, up or down by the tick rule. A finished session is
  worked out once, in a worker, and kept in the vp_cache table; the current one grows from the trades stored since,
  and so does one that ended less than SVP_SETTLE_MS ago (a trade stamped before its end can be stored a moment after
  it: a feed's delay, a clock behind this one; NinjaTrader adds every trade of the window by its own time).
  Trades are stored only while a trade feed runs (the NinjaTrader add-on, or a Databento import); a minute of the
  session with no stored trades takes its 1-minute bar instead (gap-filled history is bars only): the bar's volume
  spread evenly over its ticks, up when it closed at or above its open, as the AAVP/PVP read bars. The answer says
  how many minutes came from bars ("approx"). Each session also has its POC, VAH and VAL at each minute's end ("dev",
  SvpDev): what its plots hold on each chart bar (NinjaTrader's Data Box); a finished one's are worked out in the
  background, from its trades read again in a worker, and kept (its minutes are not: a busy session's are a megabyte).
  Kept sessions older than any chart can ask for go at the daily upkeep (prune). On an RTH chart only the regular
  hours' trades and minutes count; Session time zone Chart is the chart's own zone.

The settings are the indicators' (nt_schema.json keys prof, aavp, pvp), saved for every device. A client that
already holds the finished profiles (PVP) or sessions (SVP) of the same build names them in `have` and gets only
what changed."""
import asyncio
import bisect
import hashlib
import json
import logging
import math
import secrets
import sqlite3
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import sessions, vp, workers
from .sessions import CT, day_open, next_session_start, session_start, to_ct
from .store import connect, iter_bars_1m
from .studies import RthClock

log = logging.getLogger(__name__)
MINUTE = 60_000
KINDS = ("prof", "aavp", "pvp")
MAX_BUILDERS = 24           # built series kept however long nobody asks for them; past that, the idle ones go ...
BUILDER_IDLE_S = 60         # ... once nobody has asked for them this long (a chart asks for each profile every 3 s) ...
BUILDERS_CAP = 4 * MAX_BUILDERS   # ... and the least recently asked past this many, however recently (memory)
SVP_SETTLE_MS = 120_000     # a session is kept this long after its end: a trade stamped before it, stored late, still counts
SVP_LOOKBACK_DAYS = 45       # how far back sessions are looked for (SessionsToKeep of them, weekends skipped), at least
SVP_MAX_SESSIONS = 100       # Sessions to retain at most (nt_schema.json)
DAY_MS = 86_400_000
SVP_CATCH_UP = 20_000        # at most this many trades (every symbol's) since the last request are read on the event loop
MINUTES_CATCH_UP = 1_440     # AAVP/PVP: at most this many minutes since the last request are read on the event loop
ZONES = {"ExchangeTemplate": CT, "Central": CT, "Chart": CT,   # Chart: the chart's time zone when it names one (svp_zone)
         "Eastern": ZoneInfo("America/New_York"), "UTC": ZoneInfo("UTC")}
SCHEMA_SQL = "CREATE TABLE IF NOT EXISTS vp_cache (key TEXT PRIMARY KEY, data TEXT NOT NULL) WITHOUT ROWID"


def svp_lookback(keep: int) -> int:
    """How many days back SVP looks for its sessions: 7 days for 5 sessions, and holidays."""
    return max(SVP_LOOKBACK_DAYS, keep * 7 // 5 + 20)


def svp_zone(s: dict, chart_tz: str = "") -> ZoneInfo | None:
    """The zone SVP's window is typed in: NinjaTrader's sessionZone. Chart is the chart's own time zone (its clock's,
    chart_tz, an IANA name), as NinjaTrader's Chart is its General options' zone; Central time when the chart names none
    or one this server doesn't know."""
    name = s.get("SessionTimeZone")
    if name == "Chart" and chart_tz:
        try:
            return ZoneInfo(chart_tz)
        except (ValueError, KeyError, OSError):   # not a zone name: as before, CT
            pass
    return ZONES.get(name)


def svp_hours(s: dict, rth, tz: ZoneInfo | None = None) -> dict:
    """SVP's settings with the session window, written for ES's 08:30-15:00 CT, at the instrument's own regular hours rth
    (CT "HH:MM" open and close) as a whole (sessions.move_clocks: GC's 08:30-15:00 is 07:20-12:30), while Use Instrument
    Hours is on (the default) and the window is in Central time (ExchangeTemplate, Central, and Chart on a chart in
    Chicago time); else as typed. The NinjaTrader Session Volume Profile's InstrumentHours.MoveTimes (v1.1.0). A window
    in another zone (Eastern, UTC, a chart's) moves per date: svp_zone_windows. tz: the zone (svp_zone), else the
    setting's."""
    if not s.get("UseInstrumentHours", True) or (tz or ZONES.get(s.get("SessionTimeZone"))) is not CT:
        return s
    start, end = sessions.move_clocks(s["SessionStart"], s["SessionEnd"], sessions.rth_mover(rth))
    return {**s, "SessionStart": start, "SessionEnd": end}


def _clock(t: datetime) -> str:
    """InstrumentHours.Clock: "HH:mm", or "HH:mm:ss" with seconds."""
    return f"{t.hour:02d}:{t.minute:02d}" + (f":{t.second:02d}" if t.second else "")


class ZoneWindows:
    """Use Instrument Hours outside CT (Eastern and UTC here; any zone in tests/vp_parity.py): the NinjaTrader Session
    Volume Profile's TatankaVpCore.ZoneWindows (v1.1.0). The window is typed in its zone (ES's hours are 09:30-16:00
    Eastern). Each date's window is put in CT at that date's offset from it (at noon), moved there as a whole
    (sessions.move_window) and put back, the move kept per offset: Eastern's 09:30-16:00 runs 08:20-13:30 on GC. A zone
    whose daylight saving differs from Chicago's moves each date at its own offset: UTC's 14:30-21:00 is 08:30-15:00 CST
    and runs 13:20-18:30 on GC in winter, 09:30-16:00 CDT and 13:20-21:00 in summer. start, end: as typed; move:
    sessions.rth_mover's function for the instrument; tz: the zone."""

    def __init__(self, start: timedelta, end: timedelta, move, tz):
        self.start, self.move, self.tz = start, move, tz
        self.span = end - start if end > start else end + timedelta(days=1) - start   # vp.try_window's
        self.a, self.b = start.seconds // 60, end.seconds // 60
        self.shifts: dict[int, tuple[int, int]] = {}                  # offset: start and end moved by (minutes)
        self.windows: dict[date, tuple[datetime, datetime]] = {}      # date: [begin, finish)

    def window(self, d: date) -> tuple[datetime, datetime]:
        """The window typed for date d, moved at d's offset from CT: [begin, finish) as naive times in the zone, seconds
        kept. A start moved back past midnight begins the day before."""
        w = self.windows.get(d)
        if w is None:
            noon = datetime(d.year, d.month, d.day, 12, tzinfo=self.tz)
            off = round((noon.utcoffset() - noon.astimezone(CT).utcoffset()).total_seconds() / 60)
            by = self.shifts.get(off)
            if by is None:
                a, b = (self.a - off) % 1440, (self.b - off) % 1440
                x, y = sessions.move_window(a, b, self.move)
                by = self.shifts[off] = ((x - a + 720) % 1440 - 720, (y - b + 720) % 1440 - 720)
            day = datetime(d.year, d.month, d.day) + self.start
            w = self.windows[d] = (day + timedelta(minutes=by[0]), day + self.span + timedelta(minutes=by[1]))
        return w

    def try_window(self, local: datetime) -> tuple[datetime, datetime] | None:
        """vp.try_window over the moved windows: the latest one holding local, of those typed for the day after (its
        start moved back past midnight), the same day and the day before (overnight)."""
        d = local.date()
        for k in (1, 0, -1):
            b, e = self.window(d + timedelta(days=k))
            if b <= local < e:
                return b, e
        return None

    @staticmethod
    def label(begin: datetime, end: datetime) -> str:
        """A window as the label shows it (ZoneWindows.Label): "HH:mm-HH:mm"."""
        return f"{_clock(begin)}-{_clock(end)}"


def svp_zone_windows(s: dict, rth, tz: ZoneInfo | None = None) -> ZoneWindows | None:
    """SVP's windows outside CT (Eastern, UTC, a chart's zone) at the instrument's own regular hours rth, each date's
    (ZoneWindows), while Use Instrument Hours is on; None in CT (svp_hours), with it off or on an instrument with ES's
    hours (as typed). The session times must be ones vp.parse_time takes. tz: the zone (svp_zone), else the setting's."""
    tz = tz or ZONES.get(s.get("SessionTimeZone"))
    move = sessions.rth_mover(rth) if s.get("UseInstrumentHours", True) and tz is not None and tz is not CT else None
    return ZoneWindows(vp.parse_time(s["SessionStart"]), vp.parse_time(s["SessionEnd"]), move, tz) if move else None


def trading_day(begin_ms: int) -> date:
    """The trade date of the session opening at begin_ms (a 17:00 CT open trades the next day)."""
    return (to_ct(begin_ms) + timedelta(hours=7)).date()


class MinuteSource:
    """A stored or live 1-minute bar as the indicators' BarsInProgress 1 sees it: (trading day, session open, index,
    low, high, volume, up, end time). The session is looked up only when a minute leaves the last one."""

    def __init__(self):
        self.begin = self.end = None
        self.td = None

    def args(self, b) -> tuple:
        if self.end is None or b.t >= self.end or b.t < self.begin:
            self.begin = session_start(b.t)
            self.end = next_session_start(self.begin)
            self.td = trading_day(self.begin)
        return (self.td, self.begin, b.t // MINUTE, float(b.l), float(b.h), float(b.v or 0), b.c >= b.o, b.t + MINUTE)


class MinuteState:
    """An AAVP or PVP series and how far it has been fed. Pickled to and from a worker for the first build."""

    def __init__(self, series, hours=None):
        self.series = series
        self.hours = hours      # an RTH chart's RthClock: only its regular hours' minutes, as NinjaTrader's series on its template
        self.src = MinuteSource()
        self.pend_t = None      # open time of the last minute fed: read again next time (it may have been forming)
        self.last = None        # PVP: the profile being fed
        self.watch: list[tuple] = []        # PVP: (price, profile, level) of closed profiles' untouched levels ...
        self.watch_p: list[float] = []      # ... and their prices, both sorted by price

    def feed(self, bars):
        if getattr(self, "hours", None) is not None:
            bars = [b for b in bars if self.hours.day(b.t) is not None]
        ser = self.series
        if isinstance(ser, vp.AnchorSeries):
            for b in bars:
                ser.upsert(*self.src.args(b))
                self.pend_t = b.t
            return
        for b in bars:
            self.step(self.src.args(b))
            self.pend_t = b.t

    def step(self, a: tuple):
        """A PVP's minute (MinuteSource.args) as its OnBarUpdate takes it, then ObserveTouches with that minute."""
        ser = self.series
        ser.add(*a)
        if ser.current is not self.last:
            if self.last is not None and self.last.closed:
                self._watch(self.last)
            self.last = ser.current
            kept = {id(p) for p in ser.profiles}
            self.watch = [w for w in self.watch if id(w[1]) in kept]
            self.watch_p = [w[0] for w in self.watch]
        self._touch(a[3], a[4], a[7])

    def _watch(self, p: vp.MinuteProfile):
        s = p.snapshot()
        p.touch = {}
        if not s["rows"]:
            return
        for name in ("poc", "vah", "val"):
            i = bisect.bisect_right(self.watch_p, s[name])
            self.watch_p.insert(i, s[name])
            self.watch.insert(i, (s[name], p, name))

    def _touch(self, low: float, high: float, time: int):
        """Touches.Observe for every closed profile: the minute holds the level (vp.holds, NinjaTrader 1.0.1's margin)
        and ends after the profile's last minute. The levels near the range are looked up, then each checked."""
        m = 2e-9 * max(1.0, abs(low), abs(high))   # wider than any level's own margin
        i = bisect.bisect_left(self.watch_p, low - m)
        j = bisect.bisect_right(self.watch_p, high + m)
        if i == j:
            return
        keep = []
        for w in self.watch[i:j]:
            if time > w[1].last_chart and vp.holds(low, high, w[0]):
                w[1].touch[w[2]] = time
            else:
                keep.append(w)
        self.watch[i:j] = keep
        self.watch_p[i:j] = [w[0] for w in keep]


def build_minutes(db_path, symbol: str, state: MinuteState, since: int, before: int) -> MinuteState:
    """Worker job: the series fed every stored minute in [since, before)."""
    state.feed(iter_bars_1m(db_path, symbol, since, before))
    return state


class SvpState:
    """The forming session of an SVP: its window, the trades seen (by rowid) and the tick rule's state. mins: what each
    minute added (its end -> tick index -> [up, down]), for the POC, VAH and VAL the session had at each minute's end
    (svp_dev); None where they are not wanted (a finished session read for its profile). devs: the forming session's,
    worked out so far for each setting (SvpDev); a trade stored late, in a minute one of them was fed already, has it
    fed again from that minute (SvpDev.late)."""

    def __init__(self, begin: int, end: int, mins: bool = True):
        self.begin, self.end = begin, end
        self.rowid = 0
        self.ticks: dict[int, list] = {}     # tick index -> [up, down]
        self.mins: dict[int, dict] | None = {} if mins else None   # minute end -> tick index -> [up, down]
        self.devs: dict[tuple, SvpDev] = {}
        self.last_price = math.nan
        self.last_up = True
        self.last = None
        self.trades = 0      # trades taken
        self.approx = 0      # minutes taken from 1-minute bars
        self.upto = begin    # bar replay's: read through here (trades and minutes before it)

    def _minute(self, end: int) -> dict | None:
        """The minute ending at `end` (its tick index -> [up, down]); the devs fed past it take it again."""
        if self.mins is None:
            return None
        for d in self.devs.values():
            if end <= d.done and end < d.late:
                d.late = end
        return self.mins.setdefault(end, {})

    def add_bar(self, t: int, o: float, h: float, low: float, c: float, v: float, tick: float):
        """A minute with no stored trades: its volume spread evenly over its ticks, up or down by its close."""
        if not v or v <= 0 or not all(math.isfinite(x) for x in (o, h, low, c)) or h < low:
            return
        lo_t, hi_t = vp.round_away(low / tick), vp.round_away(h / tick)
        each, side = v / (hi_t - lo_t + 1), 0 if c >= o else 1
        m = self._minute(t // MINUTE * MINUTE + MINUTE)
        for k in range(lo_t, hi_t + 1):
            r = self.ticks.get(k)
            if r is None:
                r = self.ticks[k] = [0.0, 0.0]
            r[side] += each
            if m is not None:
                r = m.get(k)
                if r is None:
                    r = m[k] = [0.0, 0.0]
                r[side] += each
        self.approx += 1
        if self.last is None or t + MINUTE > self.last:
            self.last = min(t + MINUTE, self.end)

    def add(self, price: float, size: float, ts: int, tick: float):
        """TatankaVpCore.Session.Add at one-tick rows (rows of more ticks are summed from these)."""
        if size <= 0 or not math.isfinite(price) or not math.isfinite(size):
            return
        if not math.isnan(self.last_price):
            if price > self.last_price:
                self.last_up = True
            elif price < self.last_price:
                self.last_up = False
        k = vp.round_away(price / tick)
        side = 0 if self.last_up else 1
        r = self.ticks.get(k)
        if r is None:
            r = self.ticks[k] = [0.0, 0.0]
        r[side] += size
        if self.mins is not None:
            m = self._minute(ts // MINUTE * MINUTE + MINUTE)
            r = m.get(k)
            if r is None:
                r = m[k] = [0.0, 0.0]
            r[side] += size
        self.last_price = price
        self.last = ts if self.last is None else max(self.last, ts)
        self.trades += 1


def svp_job(db_path, symbol: str, windows: list, max_rowid: int, tick: float, now: int | None = None, rth=None,
            mins: tuple = ()) -> list:
    """Worker job: each window's trades in time order through the tick rule, and the 1-minute bar of each minute
    with none. In the forming window (it ends after `now`), with trades coming in, bars only for minutes before this
    one (the trades of this minute and on come later). rth: an RTH chart's regular hours (CT "HH:MM" open and close):
    only what traded in them, as NinjaTrader's tick series on the chart's RTH template. mins: the windows whose minutes
    are kept (SvpState.mins: the forming one's), not the others' (a busy session's are a megabyte). [SvpState]"""
    db = connect(db_path, readonly=True)
    hours = RthClock(rth) if rth else None
    out = []
    try:
        for b, e in windows:
            st = SvpState(b, e, (b, e) in mins)
            # the minutes with trades first, then the trades streamed (a busy session has a million: not held at once)
            traded = {m for (m,) in db.execute("SELECT DISTINCT ts / ? FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                               "AND rowid<=? AND size>0", (MINUTE, symbol, b, e, max_rowid))}
            until = now // MINUTE * MINUTE if (now is not None and e > now and traded) else e
            for t, o, h, low, c, v in db.execute("SELECT t, o, h, l, c, v FROM bars_1m WHERE symbol=? AND t>=? AND t<? ORDER BY t",
                                                 (symbol, b, until)):
                if t // MINUTE not in traded and (hours is None or hours.day(t) is not None):
                    st.add_bar(t, o, h, low, c, v, tick)
            for price, size, ts in db.execute("SELECT price, size, ts FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                              "AND rowid<=? ORDER BY ts, rowid", (symbol, b, e, max_rowid)):
                if hours is None or hours.day(ts) is not None:
                    st.add(price, size, ts, tick)
            st.rowid = max_rowid
            out.append(st)
    finally:
        db.close()
    return out


def svp_upto(db_path, symbol: str, st: SvpState, upto: int, max_rowid: int, tick: float, rth=None) -> SvpState:
    """Worker job, bar replay's forming session: its trades from where it was read to `upto` (a minute's start), and
    the 1-minute bar of each minute in between with none. rth: as svp_job's."""
    db = connect(db_path, readonly=True)
    hours = RthClock(rth) if rth else None
    a, b = st.upto, min(upto, st.end)
    try:
        traded = {m for (m,) in db.execute("SELECT DISTINCT ts / ? FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                           "AND rowid<=? AND size>0", (MINUTE, symbol, a, b, max_rowid))}
        for t, o, h, low, c, v in db.execute("SELECT t, o, h, l, c, v FROM bars_1m WHERE symbol=? AND t>=? AND t<? ORDER BY t",
                                             (symbol, a, b)):
            if t // MINUTE not in traded and (hours is None or hours.day(t) is not None):
                st.add_bar(t, o, h, low, c, v, tick)
        for price, size, ts in db.execute("SELECT price, size, ts FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                          "AND rowid<=? ORDER BY ts, rowid", (symbol, a, b, max_rowid)):
            if hours is None or hours.day(ts) is not None:
                st.add(price, size, ts, tick)
    finally:
        db.close()
    st.upto = max(a, b)
    return st


class SvpDev:
    """SVP's POC, VAH and VAL at each minute's end, as its plots hold them on each chart bar (PublishLevels: the
    session's snapshot as of the bar, NinjaTrader's Data Box): one setting's, from a session's minutes (SvpState.mins),
    fed in time order. dev: [minute end, POC, VAH, VAL] where they changed. late: the earliest minute fed already that
    took a trade since (one stored after a request at that minute's end): fed again from there."""

    def __init__(self, tick: float, tpr: int, percent: float, at_least: bool):
        self.args = (tick, tpr, percent, at_least)
        self.rows: dict[int, list] = {}   # row key -> [up, down]
        self.done = 0                      # the minute end fed last
        self.late = math.inf
        self.dev: list[list] = []

    def feed(self, mins: dict, upto: float = math.inf):
        """The minutes after the last fed that end by upto (and again from a minute that took a late trade)."""
        tick, tpr, pct, at_least = self.args
        if self.late <= self.done:   # its values from that minute on were worked out without the trade
            m0 = self.late
            self.rows = {}
            for m in sorted(k for k in mins if k < m0):   # in the order a fresh feed adds them
                for k, (up, down) in mins[m].items():
                    r = self.rows.setdefault(math.floor(k / tpr), [0.0, 0.0])
                    r[0] += up
                    r[1] += down
            while self.dev and self.dev[-1][0] >= m0:
                self.dev.pop()
            self.done = m0 - 1
        self.late = math.inf
        for m in sorted(k for k in mins if self.done < k <= upto):
            for k, (up, down) in mins[m].items():
                r = self.rows.setdefault(math.floor(k / tpr), [0.0, 0.0])
                r[0] += up
                r[1] += down
            if self.rows:
                p = vp.svp_snapshot(self.rows, tick, tpr, pct, at_least)
                v = [m, p["poc"], p["vah"], p["val"]]
                if not self.dev or self.dev[-1][1:] != v[1:]:
                    self.dev.append(v)
            self.done = m
        return self


def svp_dev(mins: dict, tick: float, tpr: int, percent: float, at_least: bool) -> list:
    """A finished session's per-minute POC, VAH and VAL (SvpDev)."""
    return SvpDev(tick, tpr, percent, at_least).feed(mins).dev


def svp_dev_job(db_path, symbol: str, window: tuple, max_rowid: int, tick: float, rth, tpr: int, percent: float,
                at_least: bool) -> tuple[list, int]:
    """Worker job: a finished session's per-minute POC, VAH and VAL for one setting, read again from storage with its
    minutes (svp_job's, kept only here). (dev, minutes from 1-minute bars)"""
    st, = svp_job(db_path, symbol, [window], max_rowid, tick, None, rth, (tuple(window),))
    return svp_dev(st.mins, tick, tpr, percent, at_least), st.approx


def _dev_upto(mins: dict, tick: float, tpr: int, percent: float, at_least: bool, upto: int) -> SvpDev:
    """Worker job: a forming session's SvpDev through its minutes ended by upto."""
    return SvpDev(tick, tpr, percent, at_least).feed(mins, upto)


def _r(x: float) -> float:
    return round(x, 4)


def minute_json(p: vp.MinuteProfile, rows: bool = True) -> dict | None:
    """rows=False: a finished profile the client already has, its touches only."""
    if not rows:
        return {"start": p.start_chart, "same": True, "touch": getattr(p, "touch", {})}
    s = p.snapshot()
    if not s["rows"]:
        return None
    return {"start": p.start_chart, "last": p.last_chart, "first": p.first_loaded, "closed": p.closed,
            "touch": getattr(p, "touch", {}),
            "window": [p.window[0].isoformat(), p.window[1].isoformat()],
            "rows": [[r[0], r[1], _r(r[2]), _r(r[3])] for r in s["rows"]],
            "poc": s["poc"], "vah": s["vah"], "val": s["val"], "poc_i": s["poc_index"], "vah_i": s["vah_index"],
            "val_i": s["val_index"], "low_edge": s["low_edge"], "high_edge": s["high_edge"], "max": _r(s["max_volume"]),
            "max_delta": _r(s["max_abs_delta"]), "tpr": s["ticks_per_row"], "capped": s["capped"]}


def svp_json(ticks: dict, begin: int, end: int, last, tick: float, tpr: int, percent: float, at_least: bool, approx: int = 0) -> dict | None:
    rows: dict[int, list] = {}
    for k, (up, down) in ticks.items():
        r = rows.setdefault(math.floor(k / tpr), [0.0, 0.0])
        r[0] += up
        r[1] += down
    if not rows:
        return None
    s = vp.svp_snapshot(rows, tick, tpr, percent, at_least)
    return {"start": begin, "end": end, "last": last, "rows": [[r[0], _r(r[1]), _r(r[2])] for r in s["rows"]],
            "row_size": s["row_size"], "poc": s["poc"], "vah": s["vah"], "val": s["val"], "poc_key": s["poc_key"],
            "vah_key": s["vah_key"], "val_key": s["val_key"], "low_edge": s["low_edge"], "high_edge": s["high_edge"],
            "max": _r(s["max_volume"]), "approx": approx}


class _Entry:
    def __init__(self, state, gen: int):
        self.state = state      # AAVP, PVP: MinuteState; SVP: {window: SvpState} of the windows still taking trades
        self.gen = gen          # the symbol's history it is built from (VpService.gen)
        self.lock = asyncio.Lock()
        self.used = 0.0         # when it was last asked for (time.monotonic)
        self.ready = False
        self.builds = 0         # bar replay's: built again from the start (it went back), a new build for `have`
        self.dev_task = None    # SVP: finished sessions' per-bar values being worked out (VpService._svp_devs) ...
        self.dev_want = None    # ... and the setting the latest request wanted them for


class VpService:
    def __init__(self, market):
        self.m = market
        self.entries: OrderedDict = OrderedDict()   # (kind, symbol, settings) -> _Entry
        self.gen: dict[str, int] = {}                # symbol -> times its history changed
        self.boot = secrets.token_hex(4)   # in every build key: a page's key from before a restart (an import) never matches
        self.capped = False                # said once in the log: more profiles asked for at once than BUILDERS_CAP
        market.store.db.execute(SCHEMA_SQL)
        with market.store.db:   # sessions worked out before minutes without trades took their bars; and ones partly
            market.store.db.execute("DELETE FROM vp_cache WHERE key LIKE 'svp|%'")   # from bars, as an import of
            market.store.db.execute(   # their trades (then a restart) brings the real ones
                "DELETE FROM vp_cache WHERE key LIKE 'svp2|%' AND json_extract(data, '$.approx') > 0")

    def settings(self, kind: str, given: dict | None = None) -> dict:
        """The saved settings, or `given` (checked, over the defaults) when a chart sends its own."""
        from .indicators import defaults
        return {**defaults(kind), **(self.m.ind.overrides.get(kind, {}) if given is None else given)}

    def forget(self, symbol: str):
        """The symbol's history changed (a gap fill): everything built from it goes. A request still building from
        the old history answers under its old build key and keeps nothing."""
        for k in [k for k in self.entries if k[1] == symbol]:
            del self.entries[k]
        self.gen[symbol] = self.gen.get(symbol, 0) + 1
        with self.m.store.db:
            self.m.store.db.execute("DELETE FROM vp_cache WHERE key LIKE ?", (f"svp2|{symbol}|%",))

    def prune(self, now: int | None = None) -> int:
        """Upkeep: the kept SVP sessions (profiles and per-bar values) no chart can ask for any more, opening before the
        lookback of the most Sessions to retain (and a day: a window in another zone opens up to a day earlier).
        Returns how many went."""
        cut = (now if now is not None else int(time.time() * 1000)) - (svp_lookback(SVP_MAX_SESSIONS) + 1) * DAY_MS
        old = []
        for (k,) in self.m.store.db.execute("SELECT key FROM vp_cache WHERE key LIKE 'svp2|%'"):
            try:
                if int(k.split("|")[2]) < cut:
                    old.append((k,))
            except (IndexError, ValueError):
                old.append((k,))
        if old:
            with self.m.store.db:
                self.m.store.db.executemany("DELETE FROM vp_cache WHERE key=?", old)
        return len(old)

    def _entry(self, key, make):
        """The built series of key (made when missing), now the most recently asked. Every chart asks for each of its
        profiles every 3 seconds, so past MAX_BUILDERS the least recently asked goes only once nobody has asked for it
        for BUILDER_IDLE_S (by count alone, more profiles on screen than that were each built again at every ask), and
        past BUILDERS_CAP regardless."""
        now = time.monotonic()
        e = self.entries.get(key)
        if e is None:
            e = self.entries[key] = _Entry(make(), self.gen.get(key[1], 0))
        e.used = now
        self.entries.move_to_end(key)
        while len(self.entries) > MAX_BUILDERS:
            k, old = next(iter(self.entries.items()))
            if now - old.used < BUILDER_IDLE_S:
                if len(self.entries) <= BUILDERS_CAP:
                    break
                if not self.capped:
                    self.capped = True
                    log.warning("Volume profiles: more than %d asked for at once; the least recently asked are built "
                                "again when next asked", BUILDERS_CAP)
            del self.entries[k]
        return e

    def build_key(self, key: tuple, gen: int) -> str:
        """Names a build (its settings, the symbol's history and this server run): what a client's `have` refers to."""
        return hashlib.sha1(repr((key, gen, self.boot)).encode()).hexdigest()[:12]

    async def get(self, kind: str, symbol: str, key: str = "", have: set[int] = frozenset(), given: dict | None = None,
                  until: int | None = None, rid: str = "", rth: bool = False, chart_tz: str = "",
                  nodev: set[int] = frozenset()) -> dict:
        """key and have: the build the client holds profiles of, and the starts of its finished ones. given: the
        settings the chart uses, if not the saved ones. until: bar replay's clock, the profiles as they stood then (to
        its minute): built apart from the live ones (and from another replaying chart's: rid names the chart),
        carried forward as the replay plays and built again if it goes back. rth: an RTH chart's (its regular hours'
        minutes or trades). chart_tz: the chart's time zone, SVP's on Session time zone Chart. nodev: SVP's held
        sessions without their per-bar values yet (sent with them when they are ready)."""
        s = self.settings(kind, given)
        tick = self.m.instruments[symbol].tick_size
        base = {"kind": kind, "symbol": symbol, "tick": tick}
        if kind == "prof":
            return {**base, **await self._svp(symbol, s, tick, key, have, until, rid, rth, chart_tz, nodev)}
        return {**base, **await self._minutes(kind, symbol, s, tick, key, have, until, rid, rth)}

    # --- AAVP / PVP ------------------------------------------------------------------------------------------------
    async def _minutes(self, kind: str, symbol: str, s: dict, tick: float, held: str, have, until: int | None = None,
                       rid: str = "", rth: bool = False) -> dict:
        rows = (s["RowsLayout"] == "NumberOfRows", s["RowSize"], float(s["ValueAreaPercent"]), s["ValueAreaMethod"] == "AtLeastTarget")
        inst_rth = getattr(self.m.instruments.get(symbol), "rth", None) if rth else None
        hours = RthClock(inst_rth) if inst_rth else None   # an RTH chart: its regular hours' minutes only
        if kind == "aavp":
            key = (kind, symbol, s["AnchorPeriod"], *rows, bool(hours))
            make = lambda: MinuteState(vp.AnchorSeries(s["AnchorPeriod"], tick, *rows), hours)   # noqa: E731
        else:
            key = (kind, symbol, s["Period"], s["PeriodMultiplier"], s["ProfilesToKeep"], *rows, bool(hours))
            make = lambda: MinuteState(vp.PeriodicSeries(s["Period"], s["PeriodMultiplier"], s["ProfilesToKeep"], tick, *rows), hours)   # noqa: E731
        if until is not None:
            key = (*key, "replay", rid)
        e = self._entry(key, make)
        async with e.lock:
            m = self.m
            split, live = m.recent_minutes(symbol) if until is None else m.recent_minutes(symbol, until)
            st = e.state
            if until is not None and e.ready and (st.pend_t is None or until // MINUTE * MINUTE <= st.pend_t):   # went back
                st = e.state = make()
                e.ready = False
                e.builds += 1
            if not e.ready:
                t0 = time.perf_counter()
                since = self._first_minute(kind, symbol, s, split)
                st = e.state = await workers.run("chart", build_minutes, m.store.path, symbol, st, since, split)
                first = m.store.db.execute("SELECT 1 FROM bars_1m WHERE symbol=? AND t<? LIMIT 1", (symbol, since)).fetchone() is None
                ser = st.series
                profiles = [ser.current] if kind == "aavp" else ser.profiles
                if profiles and profiles[0] is not None:
                    profiles[0].first_loaded = first   # history before what was read: the oldest one is whole
                e.ready = True
                log.info("%s %s built in %.1fs", kind.upper(), symbol, time.perf_counter() - t0)
            elif st.pend_t is not None and split - st.pend_t > MINUTES_CATCH_UP * MINUTE:
                # nobody asked for a while (the page was hidden, or on other symbols): the minutes since, in a worker
                st = e.state = await workers.run("chart", build_minutes, m.store.path, symbol, st, st.pend_t, split)
            else:
                st.feed(iter_bars_1m(m.store.path, symbol, st.pend_t if st.pend_t is not None else split, split))
            st.feed(live)
            ser = st.series
            if kind == "aavp":
                return {"profile": minute_json(ser.current) if ser.current else None,
                        "first_td": ser.first_td.isoformat() if ser.first_td else None,
                        "last_td": ser.last_td.isoformat() if ser.last_td else None}
            bk = self.build_key(key, (e.gen, e.builds))
            have = have if held == bk else ()
            return {"key": bk, "profiles": [j for p in ser.profiles if (j := minute_json(p, not (p.closed and p.start_chart in have)))]}

    def _first_minute(self, kind: str, symbol: str, s: dict, split: int) -> int:
        """Where a new series starts reading: the open of its anchor period (AAVP), or of the oldest of the
        ProfilesToKeep periods with stored minutes (PVP), from the trading day of the latest stored minute."""
        db = self.m.store.db
        row = db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=? AND t<?", (symbol, split)).fetchone()
        td = trading_day(session_start(row[0] if row and row[0] is not None else split))
        if kind == "aavp":
            return day_open(vp.anchor_window(td, s["AnchorPeriod"])[0])
        w = vp.period_window(td, s["Period"], s["PeriodMultiplier"])
        for _ in range(s["ProfilesToKeep"] - 1):
            # the period of the latest minute stored before this one: a weekend, a holiday or a stretch with nothing
            # stored makes no profile, so it is not one of those kept
            row = db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=? AND t<?", (symbol, day_open(w[0]))).fetchone()
            if row[0] is None:
                break
            w = vp.period_window(trading_day(session_start(row[0])), s["Period"], s["PeriodMultiplier"])
        return day_open(w[0])

    # --- SVP --------------------------------------------------------------------------------------------------------
    def _windows(self, symbol: str, s: dict, now: int, replay: bool = False, zw: ZoneWindows | None = None,
                 tz: ZoneInfo | None = None, hours: RthClock | None = None) -> list[tuple[int, int]]:
        """The newest SessionsToKeep session windows with trades or 1-minute bars, oldest first, as epoch ms [begin, end).
        replay: `now` is bar replay's clock: only what was stored before it counts (a window opening then has none yet).
        zw: Use Instrument Hours outside CT, each date's window. The loop starts at tomorrow's, as ZoneWindows.try_window
        does, for a start moved back past the zone's midnight. With config/futures.json's hours no Eastern or UTC start
        moves before 05:00 CT (06:00 ET), so tomorrow's opens after now and is skipped before any query; hours set in
        the instrument editor to open near midnight CT can move an Eastern one back into today. tz: the zone the window is
        typed in (svp_zone), else the setting's. hours: an RTH chart's, only what traded in them counts."""
        tz = tz or ZONES[s["SessionTimeZone"]]
        start, end = vp.parse_time(s["SessionStart"]), vp.parse_time(s["SessionEnd"])
        today = datetime.fromtimestamp(now / 1000, tz).date()
        out, db = [], self.m.store.db
        # every window with trades or bars in it, as the indicator makes one from the first tick in it: a holiday halt's
        # own (MLK Day's 08:30-15:00 to the 12:00 halt, partial) and the one that opens on its evening (17:00-16:00 from
        # MLK Day's 17:00: Tuesday's Globex day) too
        for back in range(-1 if zw else 0, svp_lookback(s["SessionsToKeep"])):
            d = today - timedelta(days=back)
            day = datetime(d.year, d.month, d.day)
            if zw:
                b_local, e_local = zw.window(d)
            else:
                b_local = day + start
                e_local = day + end if end > start else day + timedelta(days=1) + end
            b = int(b_local.replace(tzinfo=tz).timestamp() * 1000)
            e = int(e_local.replace(tzinfo=tz).timestamp() * 1000)
            upto = min(e, now) if replay else e
            if b > now or not any(db.execute("SELECT 1 FROM ticks WHERE symbol=? AND ts>=? AND ts<? LIMIT 1", (symbol, x, y)).fetchone()
                                  or db.execute("SELECT 1 FROM bars_1m WHERE symbol=? AND t>=? AND t<? AND v>0 LIMIT 1", (symbol, x, y)).fetchone()
                                  for x, y in (hours.windows(b, upto) if hours else [(b, upto)])):
                continue
            out.append((b, e))
            if len(out) >= s["SessionsToKeep"]:
                break
        return out[::-1]

    async def _svp(self, symbol: str, s: dict, tick: float, held: str, have, until: int | None = None, rid: str = "",
                   rth: bool = False, chart_tz: str = "", nodev=frozenset()) -> dict:
        """rth: an RTH chart's, from what traded in its regular hours (NinjaTrader's tick series on its RTH template).
        chart_tz: the chart's time zone (IANA), the window's with Session time zone on Chart. nodev: of the held finished
        sessions, those the client has no per-bar values of."""
        m = self.m
        inst = m.instruments.get(symbol)
        own = inst.rth if inst is not None else None
        tz = svp_zone(s, chart_tz)
        if inst is not None:   # its own hours (Use Instrument Hours)
            s = svp_hours(s, own, tz)
        try:
            vp.parse_time(s["SessionStart"])
            vp.parse_time(s["SessionEnd"])
        except ValueError as ex:
            return {"error": str(ex), "sessions": []}
        zw = svp_zone_windows(s, own, tz) if inst is not None else None   # outside CT: each date's window
        rth_hours = tuple(own) if rth and own else None
        hours = RthClock(rth_hours) if rth_hours else None
        key = (("prof", symbol, s["SessionStart"], s["SessionEnd"], s["SessionTimeZone"], getattr(tz, "key", str(tz)))
               + (("own hours", *own) if zw else ()) + (("rth", *rth_hours) if rth_hours else ()))
        replay = until is not None
        e = self._entry((*key, "replay", rid) if replay else key, lambda: None)
        async with e.lock:
            m.flush_for_read()
            dv = m.store.data_version()   # another program's commits (an import) from here on: see below
            now, max_rowid = int(time.time() * 1000), m.store.max_tick_rowid()
            if replay:
                now = min(now, until // MINUTE * MINUTE)
            windows = self._windows(symbol, s, now, replay, zw, tz, hours)
            tpr, pct, at_least = s["TicksPerRow"], float(s["ValueAreaPercent"]), s["ValueAreaMethod"] == "AtLeastTarget"
            sk = (tpr, pct, at_least)
            bk = self.build_key((*key, tpr, pct, at_least, s["SessionsToKeep"]), e.gen)
            have, nodev = (have, nodev) if held == bk else ((), ())
            tail = f"|rth{rth_hours[0]}-{rth_hours[1]}" if rth_hours else ""
            ckey = lambda b, en: f"svp2|{symbol}|{b}|{en}|{tick!r}{tail}"   # noqa: E731
            dkey = lambda b, en: f"{ckey(b, en)}|{tpr}|{pct!r}|{int(at_least)}"   # noqa: E731
            # live: the windows still taking trades, grown from those stored since and not kept yet: the forming one and,
            # outside replay, one that ended less than SVP_SETTLE_MS ago (a trade stamped before its end can be stored a
            # moment after it); after that, worked out once from storage, in time order, and kept. Replay reads what was
            # stored before its clock: a session that ended then is finished.
            live_w = [w for w in windows if w[1] + (0 if replay else SVP_SETTLE_MS) > now]
            done: dict[tuple, dict] = {}
            missing = []
            for w in windows:
                if w in live_w or w[0] in have:   # taking trades, or a finished one the client holds: not read again
                    continue
                row = m.store.db.execute("SELECT data FROM vp_cache WHERE key=?", (ckey(*w),)).fetchone()
                if row:
                    d = json.loads(row[0])
                    done[w] = {"ticks": {int(k): v for k, v in d["ticks"]}, "last": d["last"], "approx": d.get("approx", 0)}
                else:
                    missing.append(w)
            live = e.state or {}   # window -> SvpState
            if replay:   # its forming session, read to the replay clock: carried forward, or again from its open
                cur_w = live_w[-1] if live_w else None
                cur = live.get(cur_w)
                live = {}
                if cur_w is not None:
                    live[cur_w] = await workers.run("chart", svp_upto, m.store.path, symbol,
                                                    cur if cur is not None and cur.upto <= now else SvpState(*cur_w),
                                                    now, max_rowid, tick, rth_hours)
            else:
                # one past its settling (it is worked out again from storage, in time order), or with no trades yet: its
                # newest minutes are bars, read again each time; or nobody asked for a while: worked out again in a
                # worker, not caught up here through every symbol's trades since
                live = {w: st for w, st in live.items() if w in live_w and st.trades and max_rowid - st.rowid <= SVP_CATCH_UP}
            e.state = live
            jobs = missing + ([] if replay else [w for w in live_w if w not in live])
            if jobs:
                t0 = time.perf_counter()
                built = await workers.run("chart", svp_job, m.store.path, symbol, jobs, max_rowid, tick, now, rth_hours,
                                          tuple(live_w))   # the live ones' minutes, for their per-bar values
                for st in built:
                    if (st.begin, st.end) in live_w:
                        live[(st.begin, st.end)] = st
                    else:
                        done[(st.begin, st.end)] = {"ticks": st.ticks, "last": st.last, "approx": st.approx}
                # kept only if the history they were read from is still the stored one: a gap fill (forget()) or an
                # import (another program) that landed meanwhile has them worked out again by the next request
                if e.gen == self.gen.get(symbol, 0) and m.store.data_version() == dv:
                    rows = [(ckey(st.begin, st.end), json.dumps({"ticks": [[k, v] for k, v in st.ticks.items()], "last": st.last,
                                                                 "approx": st.approx})) for st in built if (st.begin, st.end) not in live_w]
                    try:
                        with m.store.db:
                            m.store.db.executemany("INSERT OR REPLACE INTO vp_cache VALUES (?, ?)", rows)
                    except sqlite3.Error as ex:   # a full disk: answered all the same, worked out again next time
                        log.warning("SVP %s: sessions not kept (%s)", symbol, ex)
                log.info("SVP %s: %d sessions worked out in %.1fs", symbol, len(jobs), time.perf_counter() - t0)
            low = min((st.rowid for st in live.values()), default=max_rowid)
            if not replay and low < max_rowid:   # the trades stored since, in the order they came
                for price, size, ts, sym, rid in m.store.db.execute(
                        "SELECT price, size, ts, symbol, rowid FROM ticks WHERE rowid>? AND rowid<=? ORDER BY rowid", (low, max_rowid)):
                    if sym == symbol and (hours is None or hours.day(ts) is not None):
                        for st in live.values():
                            if st.begin <= ts < st.end and rid > st.rowid:
                                st.add(price, size, ts, tick)
                for st in live.values():
                    st.rowid = max_rowid
            # the POC, VAH and VAL at each minute's end (the per-bar values): the finished sessions' kept (sent with the
            # ones the client lacks, and for the ones it holds without them: nodev), or worked out in the background, each
            # read again from storage in a worker (its minutes are kept nowhere: a busy session's are a megabyte), newest
            # first, the next answers having them; the live ones' carried forward here
            devs, dev_to = {}, {}
            want = [w for w in windows if w not in live_w and (w in done or w[0] in nodev)]
            for w in want:
                row = m.store.db.execute("SELECT data FROM vp_cache WHERE key=?", (dkey(*w),)).fetchone()
                if row:
                    devs[w] = json.loads(row[0])["dev"]
            need = [w for w in want if w not in devs]
            e.dev_want = sk   # a task on another setting stops after its session in hand
            if need and (e.dev_task is None or e.dev_task.done()):
                e.dev_task = asyncio.create_task(self._svp_devs(e, symbol, need, max_rowid, tick, rth_hours, sk, dkey, dv))
            for w, st in live.items():
                cd = st.devs.get(sk)
                if cd is None:
                    cd = st.devs[sk] = await workers.run("chart", _dev_upto, st.mins, tick, *sk, now)
                devs[w] = list(cd.feed(st.mins, now).dev)   # (a copy: the state goes on)
                dev_to[w] = cd.done
            out = []
            for w in windows:
                b, en = w
                if w not in live_w and b in have:
                    out.append({"start": b, "same": True, **({"dev": devs[w]} if b in nodev and w in devs else {})})
                    continue
                st = live.get(w)
                d = {"ticks": st.ticks, "last": st.last, "approx": st.approx} if st is not None else done.get(w)
                if d and (j := svp_json(d["ticks"], b, en, d["last"], tick, tpr, pct, at_least, d["approx"])):
                    j["done"] = w not in live_w
                    if w in devs:
                        j["dev"] = devs[w]
                        if w in dev_to:
                            j["dev_to"] = dev_to[w]   # its minutes so far: the values after them are its latest
                    out.append(j)
        if zw:   # the window it ran: outside CT the newest session's (each date has its own), today's before any
            hours_text = zw.label(*(datetime.fromtimestamp(x / 1000, zw.tz).replace(tzinfo=None) for x in windows[-1])) if windows \
                else zw.label(*zw.window(datetime.fromtimestamp(now / 1000, zw.tz).date()))
        else:
            hours_text = f"{s['SessionStart']}-{s['SessionEnd']}"
        return {"key": bk, "sessions": out, "hours": hours_text}   # the window it ran

    async def settled(self):
        """Waits for the per-bar values being worked out in the background (SVP's finished sessions)."""
        for e in list(self.entries.values()):
            if e.dev_task is not None:
                await e.dev_task

    async def _svp_devs(self, e: _Entry, symbol: str, windows: list, max_rowid: int, tick: float, rth, sk: tuple, dkey,
                        dv: int):
        """Background: finished sessions' per-minute POC, VAH and VAL for one setting (sk), newest first, each read again
        in a worker (svp_dev_job) and kept in vp_cache as it comes (not when the history they came from changed
        meanwhile). Stops when a chart asks for another setting (e.dev_want): the next request starts that one's."""
        try:
            for w in sorted(windows, reverse=True):
                if e.dev_want != sk:
                    return
                dev, approx = await workers.run("chart", svp_dev_job, self.m.store.path, symbol, w, max_rowid, tick, rth, *sk)
                if e.gen != self.gen.get(symbol, 0) or self.m.store.data_version() != dv:
                    return
                with self.m.store.db:
                    self.m.store.db.execute("INSERT OR REPLACE INTO vp_cache VALUES (?, ?)",
                                            (dkey(*w), json.dumps({"dev": dev, "approx": approx})))
        except Exception as ex:   # noqa: BLE001 (sqlite, a worker gone at a stop): the next request tries again
            log.warning("SVP %s: per-bar values not kept (%s)", symbol, ex)
