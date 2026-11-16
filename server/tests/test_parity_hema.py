"""HTF EMA: NinjaTrader's source timeframes and lengths (parity audit). NinjaTrader adds its source series as
AddDataSeries(HtfPeriodType, HtfPeriod), any BarsPeriodType, with lengths up to Int32's. This app built Minute 1-1440
and Day 1 sources and lengths to 500; now it builds NinjaTrader's time bars, Second (from the stored trades), Minute,
Day, Week, Month and Year, multi-day ones too, and takes any Int32 length.

With TATANKA_INDICATORS set to a tatanka-indicators checkout and mono, tests/htfema_parity.py checks them against the
C# (historical and live, as NinjaTrader runs them)."""
import asyncio
import json
import os
import pickle
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from amt_parity import synth
from chartsrv import dsma, htfema, indicators, sessions, store, workers
from chartsrv.amt_runner import ct, epoch_ms
from chartsrv.bars import calendar_bucket, parse_spec
from chartsrv.models import Bar
from htfema_parity import js_values
from test_hunt_web_chart import run_app
from test_redesign_htf_seconds import build, trades

HERE = Path(__file__).resolve().parent
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
needs_mono = pytest.mark.skipif(not (INDICATORS and shutil.which("mcs") and shutil.which("mono") and shutil.which("node")),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS), mono and node")
RTH = ("08:30", "15:00")


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """Three days of ES trades to Friday 2026-10-16 12:07 CT and their 1-minute bars, stored as the live feed stores them."""
    sessions.set_calendar({"days": []})
    ticks = trades(3, 5)
    mins = build("1m", ticks)
    path = tmp_path_factory.mktemp("hema_src") / "chart.db"
    st = store.Store(path)
    st.write(ticks, [("ES", b) for b in mins])
    st.close()
    return path, ticks, mins


# ---- the settings ----------------------------------------------------------------------------------------------------

def test_settings_take_ninjatraders_time_bars_and_int32_lengths():
    for t in ("Second", "Minute", "Day", "Week", "Month", "Year"):
        assert htfema.settings({"HtfPeriodType": t, "HtfPeriod": 3})["HtfPeriodType"] == t
    for bad in ({"HtfPeriodType": "Tick"}, {"HtfPeriodType": "Range"}, {"HtfPeriod": 0}, {"EmaLength": 2**31},
                {"EmaLength": 0}, {"HtfPeriod": 2.0}):
        with pytest.raises(ValueError):
            htfema.settings(bad)
    assert htfema.settings({"EmaLength": 2**31 - 1, "HtfPeriod": 2**31 - 1})["EmaLength"] == 2**31 - 1
    src = lambda t, n: htfema.source(htfema.settings({"HtfPeriodType": t, "HtfPeriod": n}))   # noqa: E731
    assert [src("Second", 120), src("Second", 90), src("Minute", 2000), src("Day", 3), src("Week", 2), src("Month", 3),
            src("Year", 2)] == [("minute", 2), ("second", 90), ("minute", 2000), ("D", 3), ("W", 2), ("M", 3), ("M", 24)]
    spec = indicators.by_name("hema")
    assert spec["HtfPeriodType"]["choices"] == list(htfema.TYPES)
    assert (spec["HtfPeriod"]["max"], spec["EmaLength"]["max"]) == (2**31 - 1, 2**31 - 1)
    assert indicators.clean("hema", {"HtfPeriodType": "Week", "EmaLength": 1000}) == {"HtfPeriodType": "Week", "EmaLength": 1000}
    with pytest.raises(ValueError):
        indicators.clean("hema", {"EmaLength": 2**31})


def test_scaled_length_past_int32_is_ninjatraders_one():
    """(int)Math.Round(...) of a double past Int32 is int.MinValue on .NET Framework (and mono): Math.Max(1, it) is 1."""
    s = lambda n: htfema.settings({"HtfPeriodType": "Year", "HtfPeriod": 1, "EmaLength": n})   # noqa: E731
    assert htfema.GetScaledLength(s(1000), "Second", 15) == 2_102_400_000
    assert htfema.GetScaledLength(s(2000), "Second", 15) == 1
    week = htfema.settings({"HtfPeriodType": "Week", "HtfPeriod": 1})
    assert htfema.GetScaledLength(week, "Minute", 5) == 21 * 7 * 24 * 12   # 21 weeks of 5-minute bars


# ---- Day, Week, Month and Year bars ----------------------------------------------------------------------------------

def test_calendar_groups_are_the_charts_own():
    """N trading days, weeks and months from the fixed starts the 2D, week and month charts use (calendar_bucket)."""
    day = date(2024, 1, 1)
    days = [day + timedelta(days=i) for i in range(800) if (day + timedelta(days=i)).weekday() < 5]
    for unit, mult in (("D", 1), ("D", 2), ("D", 3), ("D", 7), ("W", 1), ("W", 2), ("M", 1), ("M", 3), ("M", 12), ("M", 24)):
        ours = [htfema._group(d, unit, mult) for d in days]
        charts = [calendar_bucket(sessions.day_open(d), unit, mult) for d in days]
        assert [a == b for a, b in zip(ours, ours[1:])] == [a == b for a, b in zip(charts, charts[1:])], (unit, mult)


def test_calendar_bars_close_with_their_last_day_the_forming_one_with_its_last_weekday():
    oct_days = [(date(2026, 10, d), 100.0 + d) for d in (5, 6, 7, 8, 9, 12, 13, 14)]   # Mon 5 - Wed 14
    at = lambda d, h=16, m=0: epoch_ms(datetime(2026, 10, d, h, m))   # noqa: E731
    assert htfema.calendar_bars(oct_days, "W", 1, None) == [[at(9), 109.0], [at(16), 114.0]]   # the forming week: Fri 16
    assert htfema.calendar_bars(oct_days, "W", 1, RTH) == [[at(9, 15), 109.0], [at(16, 15), 114.0]]
    assert htfema.calendar_bars(oct_days, "M", 1, None) == [[at(30), 114.0]]                  # Sat 31: Friday 30
    two = htfema.calendar_bars(oct_days, "D", 2, None)
    assert [ct(c).day for c, _ in two] == [5, 7, 9, 13, 15] and two[-1][1] == 114.0          # Wed 14 forms to Thu 15
    year = htfema.calendar_bars([(date(2026, 12, 30), 1.0)], "M", 12, None)
    assert year == [[epoch_ms(datetime(2026, 12, 31, 16)), 1.0]]


def test_the_daily_bars_kept_for_daily_sma_are_the_sources_days():
    """The endpoint takes Day, Week, Month and Year sources from the daily bars it keeps per symbol (Daily SMA x4's,
    years of them for a Month source): the same days, a holiday halt's in the next day's, as the minutes give."""
    sessions.set_calendar({"days": [{"date": "2026-10-13", "name": "Halt", "halt": "12:00"}]})
    try:
        m1 = synth(40, 3, datetime(2026, 10, 16, 12, 7))
        for rth in (None, RTH):
            d = dsma.DailyBars("equity", rth)
            opens, ends, closes = [], [], []
            d.add([(m.t, m.c) for m in m1], opens, ends, closes)
            cached = [(htfema.trade_date(e), c) for e, c in zip(ends, closes)]
            for unit, mult in (("D", 1), ("D", 2), ("W", 1), ("M", 1)):
                assert htfema.calendar_bars(cached, unit, mult, rth) == \
                    htfema.calendar_bars(htfema.days_of(m1, "equity", rth), unit, mult, rth), (rth, unit, mult)
            s = htfema.settings({"HtfPeriodType": "Week", "EmaLength": 2, "Mode": "ExactHTF"})
            now = m1[-1].t + 60_000
            args = (s, m1, 5, 252, m1[-3000].t, now, "equity")
            assert htfema.compute(*args, rth=rth, days=cached) == htfema.compute(*args, rth=rth)
    finally:
        sessions.set_calendar({"days": []})


# ---- Second bars -----------------------------------------------------------------------------------------------------

def _m(t, c, n=0):
    return Bar(t=t, o=c, h=c, l=c, c=c, v=max(n, 1), n=n)


def test_second_bars_count_from_the_session_open_and_stop_at_its_close():
    """A grid of N seconds from the 17:00 CT open (an RTH chart: the RTH open), the last bar cut at the 16:00 close (the
    RTH close), stamped with its close; one only where a trade printed, its close the last trade's."""
    o = epoch_ms(datetime(2026, 10, 14, 17))
    end = epoch_ms(datetime(2026, 10, 15, 16))
    prints = [(o + 1000, 1.0), (o + 6000, 2.0), (o + 8000, 3.0), (end - 2000, 4.0), (end - 1000, 5.0)]
    mins = [_m(o, 2.0), _m(end - 60_000, 5.0)]
    bars, last = htfema.seconds_bars(mins, 7, lambda lo, hi: [p for p in prints if lo <= p[0] < hi], o, htfema.frame_of(None), False)
    assert bars == [[o + 7000, 2.0, 1], [o + 14_000, 3.0, 1], [end, 5.0, 1]] and last == end - 1000   # 82800 s: cut at 16:00
    rth_open = epoch_ms(datetime(2026, 10, 15, 8, 30))
    prints = [(rth_open + 1000, 1.0), (rth_open + 31_000, 2.0)]
    bars, _ = htfema.seconds_bars([_m(rth_open, 2.0)], 30, lambda lo, hi: prints, rth_open, htfema.frame_of(RTH), False)
    assert bars == [[rth_open + 30_000, 1.0, 1], [rth_open + 60_000, 2.0, 1]]


def test_a_minute_without_stored_trades_stands_for_the_bars_closing_in_it():
    """Where no trade is stored (a gap filled from 1-minute history), the bars closing in a minute all close at its close,
    no more of them than its trades; on a minute-aligned chart the ones inside it come as one, k at once."""
    o = epoch_ms(datetime(2026, 10, 14, 17))
    t = o + 600_000
    none = lambda lo, hi: []   # noqa: E731
    bars, _ = htfema.seconds_bars([_m(t, 7.0)], 15, none, t, htfema.frame_of(None), False)
    assert bars == [[t + 15_000, 7.0, 1], [t + 30_000, 7.0, 1], [t + 45_000, 7.0, 1], [t + 60_000, 7.0, 1]]
    bars, _ = htfema.seconds_bars([_m(t, 7.0, n=2)], 15, none, t, htfema.frame_of(None), False)
    assert bars == [[t + 45_000, 7.0, 1], [t + 60_000, 7.0, 1]]
    bars, _ = htfema.seconds_bars([_m(t, 7.0)], 5, none, t, htfema.frame_of(None), True)
    assert bars == [[t + 5000, 7.0, 1], [t + 55_000, 7.0, 10], [t + 60_000, 7.0, 1]]
    bars, _ = htfema.seconds_bars([_m(t, 7.0), _m(t + 60_000, 8.0)], 90, none, t, htfema.frame_of(None), False)
    assert bars == [[t + 30_000, 7.0, 1], [t + 120_000, 8.0, 1]]   # 17:09:00-17:10:30 and 17:10:30-17:12:00
    e = htfema.NtEma(3)
    e.add(1.0)
    assert e.add_many(7.0, 10) == pytest.approx(7 + (1 - 7) * 0.5**10) and e.count == 11


def test_a_second_source_reads_at_most_the_trade_limit(db, monkeypatch):
    """Further back than SECONDS_TRADES trades (counted on the minutes), the 1-minute bars stand in, as for a seconds
    chart's seed."""
    path, _, mins = db
    asked = []

    def closes(lo, hi):
        asked.append(lo)
        return store.iter_closes_1s(path, "ES", lo, hi)
    monkeypatch.setattr(htfema, "SECONDS_TRADES", 20_000)
    s = htfema.settings({"HtfPeriodType": "Second", "HtfPeriod": 30, "Mode": "ExactHTF"})
    d = htfema.compute(s, mins, 5, 2, mins[-600].t, mins[-1].t + 60_000, "equity", second_closes=closes)
    counted, j = 0, len(mins)
    while counted + mins[j - 1].n <= 20_000:
        counted, j = counted + mins[j - 1].n, j - 1
    assert asked == [mins[j].t] and all(v is not None for _, v in d["htf"])


@needs_node
@pytest.mark.parametrize("spec, rth", [("1m", None), ("5m", None), ("1h", None), ("15s", None), ("1D", None), ("5m", RTH),
                                       ("15s", RTH)])
def test_what_the_chart_reads_of_a_second_source_is_sent_and_nothing_more(db, spec, rth):
    """The endpoint sends a Second source's closed bars thinned to the last each chart bar sees (chart_key): the line
    drawn is the one all of them draw, from at most one entry a chart bar."""
    path, ticks, mins = db
    sp = parse_spec(spec)
    bars = [{"t": b.t, "c": b.c} for b in build(spec, [tk for tk in ticks if not rth or htfema.RthClock(rth).day(tk.ts) is not None])]
    bars = bars[len(bars) // 3:]
    now = mins[-1].t + 60_000
    s = htfema.settings({"HtfPeriodType": "Second", "HtfPeriod": 20, "Mode": "LiveHTF"})
    chart_min, chart_sec = (None, sp.size) if sp.size < 60 else (sp.size // 60, None)
    args = (s, mins, chart_min, 5, bars[0]["t"], now, "equity")
    kw = {"chart_seconds": chart_sec, "rth": rth, "second_closes": lambda lo, hi: store.iter_closes_1s(path, "ES", lo, hi)}
    full = htfema.compute(*args, **kw)
    thin = htfema.compute(*args, **kw, thin=htfema.chart_key(sp.size * 1000, rth))
    period = sp.size * 1000
    assert js_values(bars, thin, period) == js_values(bars, full, period)
    sessions_ = len({htfema.trade_date(b["t"] + 7 * 3_600_000) for b in bars})   # a bar closing at 16:00 keys apart
    assert len(thin["htf"]) <= len(bars) + sessions_ + 1 and (len(thin["htf"]) < len(full["htf"]) or sp.size <= 20)


@pytest.mark.parametrize("spec, rth, setting", [
    ("1m", None, {"HtfPeriod": 15, "Mode": "LiveHTF"}), ("15s", None, {"HtfPeriod": 7, "Mode": "ExactHTF"}),
    ("1D", None, {"HtfPeriod": 20, "Mode": "ExactHTF"}), ("5m", RTH, {"HtfPeriod": 45, "Mode": "LiveHTF"}),
    ("1m", None, {"HtfPeriod": 90, "Mode": "SmoothScaled"})])
def test_a_second_source_goes_on_from_where_its_last_answer_stood(db, monkeypatch, spec, rth, setting):
    """Live and Exact ask again every 10 seconds or so inside a forming chart bar. Each answer goes on from where the
    last for the same chart stood (after its last source bar closing before the minutes still in memory), reading only
    the trades since: the same answer as one built from the chart's first bar."""
    path, _, mins = db
    s = htfema.settings({"HtfPeriodType": "Second", "EmaLength": 21, **setting})
    sp = parse_spec(spec)
    chart_min, chart_sec = (None, sp.size) if sp.size < 60 else (sp.size // 60, None)
    start = mins[-2000 if spec == "1D" else -600].t
    since = htfema.history_ms(s, chart_min, 5, start, mins[-1].t, chart_sec, rth)
    read = []
    real = htfema.iter_closes_1s
    monkeypatch.setattr(htfema, "iter_closes_1s", lambda db_, sym, lo, hi: read.append(lo) or real(db_, sym, lo, hi))
    end = mins[-1].t + 60_000
    resume, steps = None, [end - 200 * 60_000 + 7_000, 9_000, 4_000, 47_000, 61_000, 15_000, 1_000, 30 * 60_000 + 500, 12_000,
                           59_000, 21_000, 3 * 60_000]
    now, resumed = steps[0], 0
    for step in steps[1:] + [0]:
        split = now // 60_000 * 60_000
        live = [m for m in mins if split <= m.t < now]
        args = (path, "ES", s, since, split, live, 5, start, now, "equity", chart_min, chart_sec, rth)
        read.clear()
        full = htfema.source_job(*args)
        whole = list(read)
        read.clear()
        part = htfema.source_job(*args, resume)
        if resume is not None:
            resumed += 1
            assert read == [resume["from"]] and read[0] > whole[0] and read[0] >= split - 30 * 60_000 - 180_000
        assert {k: v for k, v in part.items() if k != "resume"} == {k: v for k, v in full.items() if k != "resume"}, (now, step)
        assert part["resume"]["at"] <= split
        resume, now = part["resume"], now + step
    assert resumed == len(steps) - 1


def test_a_resumed_answer_is_dropped_when_the_stored_history_changes():
    """resumed(): each answer's resume is kept for the next of the same chart (the last SHARED), not sent; a gap fill
    (forget) drops them, and one worked out while it came is not kept."""
    key, asked = ("ES", "1m", False, 1, 15, "Second", 21, "ExactHTF"), []

    async def job(r):
        asked.append(r)
        return {"htf": [], "resume": {"at": len(asked)}}
    try:
        assert asyncio.run(htfema.resumed(key, job)) == {"htf": []}
        asyncio.run(htfema.resumed(key, job))
        assert asked == [None, {"at": 1}]

        async def gap_fill_meanwhile(r):
            htfema.forget("ES")
            return await job(r)
        asyncio.run(htfema.resumed(key, gap_fill_meanwhile))
        asyncio.run(htfema.resumed(key, job))
        assert asked[2:] == [{"at": 2}, None]
        for i in range(htfema.SHARED + 3):
            asyncio.run(htfema.resumed(("NQ", i), job))
        assert len(htfema._resumes) == htfema.SHARED and key not in htfema._resumes
    finally:
        htfema.forget("ES")
        htfema.forget("NQ")


# ---- the endpoint ----------------------------------------------------------------------------------------------------

class _Pool:
    def __init__(self):
        self.jobs = []

    async def run(self, kind, fn, *args):
        self.jobs.append((kind, fn.__name__))
        fn, args = pickle.loads(pickle.dumps((fn, args)))   # as a worker gets them
        return pickle.loads(pickle.dumps(fn(*args)))


def test_the_endpoint_builds_each_source(db, monkeypatch):
    import chartsrv.app as A
    path, _, mins = db
    now = mins[-1].t + 60_000   # Friday 12:07 CT
    market = SimpleNamespace(store=SimpleNamespace(path=path), recent_minutes=lambda sym: (now, []),
                             instruments={"ES": SimpleNamespace(rth=RTH, calendar="equity", tick_size=0.25, decimals=2)})
    monkeypatch.setattr(A.app.state, "market", market, raising=False)
    monkeypatch.setattr(A, "now_ms", lambda: now)
    pool = _Pool()
    monkeypatch.setattr(workers, "run", pool.run)
    read = []
    real = dsma.daily_bars
    monkeypatch.setattr(dsma, "daily_bars", lambda *a: read.append(a[4]) or real(*a))
    htfema.forget("ES")
    dsma.forget("ES")
    start = mins[-1000].t

    def ask(spec="5m", **kw):
        return asyncio.run(A.htf_ema("ES", spec, start, **{"Mode": "ExactHTF", **kw}))
    week = ask(HtfPeriodType="Week", HtfPeriod=1, EmaLength=1)
    assert week["htf"] == [] and week["due"] == epoch_ms(datetime(2026, 10, 16, 16))   # this week forms to Friday 16:00
    day = ask(HtfPeriodType="Day", HtfPeriod=1, EmaLength=1)
    assert [(ct(c).day, v is not None) for c, v in day["htf"]] == [(15, True)]   # from the last day before the chart's
    assert day["due"] == epoch_ms(datetime(2026, 10, 16, 16))
    one_day = htfema.settings({"HtfPeriodType": "Day", "HtfPeriod": 1, "EmaLength": 1})
    assert len(read) == 2 and read[1] == htfema.calendar_history_ms(one_day, start, now)
    # an RTH chart: the regular session's days, each closing at the RTH close (15:00 CT) with its last trade then
    thu, fri = epoch_ms(datetime(2026, 10, 15, 15)), epoch_ms(datetime(2026, 10, 16, 15))
    rth_day = ask(HtfPeriodType="Day", HtfPeriod=1, EmaLength=1, rth=True)
    assert rth_day["htf"] == [[thu, [m for m in mins if m.t < thu][-1].c]] and rth_day["due"] == fri
    assert day["htf"][0][1] == [m for m in mins if m.t < thu + 3_600_000][-1].c != rth_day["htf"][0][1]   # ETH: to 16:00
    rth_week = ask(HtfPeriodType="Week", HtfPeriod=1, EmaLength=1, rth=True)
    assert rth_week["htf"] == [] and rth_week["due"] == fri
    long = ask(HtfPeriodType="Minute", HtfPeriod=1, EmaLength=1000)
    assert long["len"] == 1000 and long["htf"][-1][1] is not None
    sec = ask(HtfPeriodType="Second", HtfPeriod=15)
    assert pool.jobs == [("chart", "source_job")] and len(sec["htf"]) <= 1001
    want = htfema.source_job(path, "ES", htfema.settings({"HtfPeriodType": "Second", "HtfPeriod": 15, "Mode": "ExactHTF"}),
                             htfema.history_ms(htfema.settings({"HtfPeriodType": "Second", "HtfPeriod": 15}), 5, 1, start, now),
                             now, [], 1, start, now, "equity", 5, None)
    assert want.pop("resume")["at"] <= now and {**want, "next": None} == sec
    assert len(htfema._resumes) == 1   # the next answer for this chart goes on from it
    market.write_failed_at = now   # the database can't be written: what is stored may be short, none kept
    ask(HtfPeriodType="Second", HtfPeriod=20)
    assert len(htfema._resumes) == 1
    del market.write_failed_at
    assert ask(HtfPeriodType="Second", HtfPeriod=120)["htf"] == ask(HtfPeriodType="Minute", HtfPeriod=2)["htf"]
    assert len(pool.jobs) == 2   # Second 120 is Minute 2: from the minutes
    for spec in ("2000t", "16r", "2D", "1W"):
        with pytest.raises(A.HTTPException) as e:
            ask(spec, HtfPeriodType="Second", HtfPeriod=15)
        assert e.value.status_code == 400 and "Minute or longer source" in e.value.detail
    htfema.forget("ES")
    dsma.forget("ES")


# ---- the page --------------------------------------------------------------------------------------------------------

PAGE_JS = """
let clock = 1.8e12;
Date.now = () => clock;
const asked = [], t0 = clock;
const S = { st: { hema: true }, symbol: 'ES', spec: '5m', bars: [{ t: clock - 120e3, c: 1 }], instruments: { ES: {} }, replay: null,
  hemaSet: { HtfPeriodType: 'Second', HtfPeriod: 15, EmaLength: 21, Mode: 'ExactHTF' }, hema: null };
const indReady = () => true, hemaDraw = () => {}, hemaLive = () => {};
const api = async (method, path) => {   // the forming source bar's close: 15 s on, a Minute 1 source's at the next minute
  asked.push(path);
  const due = S.hemaSet.HtfPeriodType === 'Minute' ? Math.floor(clock / 60e3) * 60e3 + 60e3 : clock + 15e3;
  return { mode: S.hemaSet.Mode, htf: [], due, held: false };
};
const els = {}, infoEl = (k) => (els[k] ??= { textContent: '' });
/*APP*/
const at = async (t) => { clock = t; hemaTick(false); await new Promise((r) => setTimeout(r, 5)); return asked.length; };
(async () => {
  const out = {};
  await hemaRequest();
  // asked at t0, due 15 s on; past it 16 s on: asks, due 31 s on; 26 s on: not due; 37 s on: due, 21 s since: asks
  out.second = [await at(t0 + 16e3), await at(t0 + 26e3), await at(t0 + 37e3)];
  S.hemaSet = { ...S.hemaSet, HtfPeriodType: 'Minute', HtfPeriod: 1 };
  // 61 s on, the answer in hand due 52 s on: asks (24 s since), due 120 s on; 90 and 98 s on: not due; 121 s on: asks
  out.minute = [await at(t0 + 61e3), await at(t0 + 90e3), await at(t0 + 98e3), await at(t0 + 121e3)];
  out.text = [];
  for (const [t, n, mode] of [['Second', 15, 'ExactHTF'], ['Week', 1, 'ExactHTF'], ['Month', 2, 'LiveHTF'], ['Year', 1, 'ExactHTF'],
    ['Day', 3, 'ExactHTF'], ['Minute', 240, 'ExactHTF']]) {
    S.hemaSet = { HtfPeriodType: t, HtfPeriod: n, EmaLength: 21, Mode: mode };
    S.hema = { scaled: 5 };
    renderHemaInfo();
    out.text.push(els.hema.textContent);
  }
  console.log(JSON.stringify(out));
})();"""


@needs_node
def test_the_page_asks_each_source_once_its_bar_closed_and_names_each_source():
    """Inside a forming chart bar, Exact and Live ask again on the first update after a source bar has closed, every
    10 seconds at most, a Second source and a Minute one alike (the Minute one asked only a minute after its last ask
    before: its close 24 s after it waited). The legend names each source."""
    out = run_app(["hemaEach", "hemaRequest", "hemaTick", "renderHemaInfo"], PAGE_JS)
    assert out["second"] == [2, 2, 3] and out["minute"] == [4, 4, 4, 5]
    assert out["text"] == ["Last closed 15s EMA 21.", "Last closed 1W EMA 21.", "2M EMA 21 with the live price.",
                           "Last closed 1Y EMA 21.", "Last closed 3D EMA 21.", "Last closed 4h EMA 21."]


# ---- against the C# ----------------------------------------------------------------------------------------------------

CASES = [   # [days, chart, seed, root, settings, flags...]
    ["150", "5m", "7", "ES", {"Mode": "ExactHTF", "HtfPeriodType": "Week", "HtfPeriod": 1, "EmaLength": 5}],
    ["150", "1D", "4", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Week", "HtfPeriod": 2, "EmaLength": 3}],
    ["120", "1D", "5", "NQ", {"Mode": "ExactHTF", "HtfPeriodType": "Day", "HtfPeriod": 3, "EmaLength": 8}],
    ["200", "1D", "8", "CL", {"Mode": "ExactHTF", "HtfPeriodType": "Month", "HtfPeriod": 1, "EmaLength": 3}],
    ["420", "1D", "2", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Year", "HtfPeriod": 1, "EmaLength": 1}],
    ["150", "5m", "3", "CL", {"Mode": "ExactHTF", "HtfPeriodType": "Week", "HtfPeriod": 1, "EmaLength": 5}, "rth"],
    ["60", "5m", "4", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Minute", "HtfPeriod": 60, "EmaLength": 600}],
    ["3", "1m", "8", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Second", "HtfPeriod": 15}],
    ["2", "15s", "3", "ES", {"Mode": "ExactHTF", "HtfPeriodType": "Second", "HtfPeriod": 7}],
    ["2", "t3", "4", "GC", {"Mode": "ExactHTF", "HtfPeriodType": "Second", "HtfPeriod": 20}],
    ["3", "5m", "5", "CL", {"Mode": "LiveHTF", "HtfPeriodType": "Second", "HtfPeriod": 90}, "rth"],
    ["3", "15s", "2", "ES", {"Mode": "SmoothScaled", "HtfPeriodType": "Second", "HtfPeriod": 30}],
    ["20", "5m", "7", "ES", {"Mode": "LiveHTF"}],
    ["15", "5m", "7", "ES", {"Mode": "ExactHTF", "HtfPeriodType": "Day", "HtfPeriod": 2, "EmaLength": 3}, "live"],
    ["40", "5m", "5", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Minute", "HtfPeriod": 60, "EmaLength": 600}, "live"],
    ["10", "1m", "4", "CL", {"Mode": "ExactHTF", "HtfPeriodType": "Second", "HtfPeriod": 300}, "live"],
    ["3", "1m", "4", "ES", {"Mode": "ExactHTF", "HtfPeriodType": "Second", "HtfPeriod": 15}, "live"],
    ["3", "1m", "4", "ES", {"Mode": "LiveHTF", "HtfPeriodType": "Second", "HtfPeriod": 15}, "live"],
    ["3", "5m", "6", "NQ", {"Mode": "ExactHTF", "HtfPeriodType": "Second", "HtfPeriod": 45}, "live", "gaps"],
    ["3", "4h", "5", "NQ", {"Mode": "LiveHTF", "HtfPeriodType": "Second", "HtfPeriod": 90}, "live", "gaps", "bip1first"],
    ["40", "4h", "2", "GC", {"Mode": "ExactHTF", "HtfPeriodType": "Day", "HtfPeriod": 1, "EmaLength": 5}, "live", "break"],
]


@needs_mono
def test_every_source_against_the_csharp(tmp_path):
    """tests/htfema_parity.py on the C# built from the checkout: every value equal, historical and live (each call, and
    every bar the live chart ends on: a bar that ran live keeps its last call's value on both)."""
    r = subprocess.run(["sh", str(HERE / "htfema_csharp" / "build.sh"), INDICATORS, str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    for days, chart, seed, root, s, *flags in CASES:
        r = subprocess.run([sys.executable, str(HERE / "htfema_parity.py"), str(tmp_path), days, chart, seed, root,
                            json.dumps(s), *flags], capture_output=True, text=True, cwd=HERE.parent)
        assert r.returncode == 0 and "PARITY OK" in r.stdout, (chart, s, flags, r.stdout[-2000:] + r.stderr[-2000:])
        assert "every value equal" in r.stdout or "live" in flags
