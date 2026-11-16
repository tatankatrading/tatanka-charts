"""Gap Analysis on a seconds chart (1s-59s), as NinjaTrader allows Minute and Second charts: the engine over the chart's
own bars, built from the stored trades from its first bar on, each stamped at its end (the last of a session at the
session's end), the forming one as it is now.

With a tatanka-indicators checkout (TATANKA_INDICATORS) and mono, the C# GapEngine runs over seconds bars built here
from the same trades by NinjaTrader's rule (as gap_parity.py builds minute ones), and every gap must equal this port's.
Without them, the seconds chart's gaps are checked against the 1-minute chart's: the same open, RTH close and size."""
import asyncio
import os
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from amt_parity import synth
from gap_parity import D, G, RTHS, T, same
from test_amt import client  # noqa: F401  (the app on the simulator, as a fixture)
from test_hunt_web_chart import needs_node, run_app

import chartsrv.gaps_service as gs
from chartsrv import gaps
from chartsrv.amt_runner import ct
from chartsrv.bars import parse_spec
from chartsrv.config import Instrument
from chartsrv.models import Tick
from chartsrv.sessions import SessionClock, to_ct
from chartsrv.store import Store
from chartsrv.studies import RthClock
from chartsrv.tpo_service import session_end

HERE = Path(__file__).resolve().parent
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_mono = pytest.mark.skipif(not INDICATORS or not shutil.which("mono") or not shutil.which("mcs"),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS) and mono")
ROOT = {"ES": (0.25, 6800.0, "equity"), "GC": (0.10, 3500.0, "metals")}


def inst(root):
    tick, _, cal = ROOT[root]
    return Instrument(root, root, tick, 50, root, rth=RTHS[root], calendar=cal)


def trades(root="ES", days=6, seed=3, edges=False):
    """Synthetic 1-minute bars' trades: 4 to 9 a minute at random milliseconds, the first at its open, the last at its
    close, its high and low among them (the stored 1-minute bars are these trades'). edges: each day a print 2 s before
    the RTH open and 0.1 s before the RTH close, 8 ticks away (the stored 1-minute bars then leave them out), so the bar
    that straddles the open, and the RTH chart's last bar (clamped to the close), carry a price of their own."""
    tick, price, _ = ROOT[root]
    rng = random.Random(seed)
    m1 = synth(days, seed, datetime(2026, 10, 16, 12, 7), tick, price)
    o, c = ((int(x[:2]) * 60 + int(x[3:5]) - 1) % 1440 for x in RTHS[root])   # the minutes before the open and close
    out = []
    for m in m1:
        n = rng.randint(4, 9)
        at = sorted(rng.sample(range(60_000), n))
        mid = [m.h, m.l] + [round(rng.uniform(m.l, m.h) / tick) * tick for _ in range(n - 4)]
        rng.shuffle(mid)
        for t, p in zip(at, [m.o, *mid, m.c]):
            out.append(Tick(root, m.t + t, p, rng.randint(1, 5), rng.choice((1, -1))))
        mod = ct(m.t).hour * 60 + ct(m.t).minute
        if edges and mod in (o, c):
            out.append(Tick(root, m.t + (58_000 if mod == o else 59_900), round(m.c / tick + (-8 if mod == o else 8)) * tick, 3, 1))
    return m1, sorted(out, key=lambda x: x.ts)


def nt_bars(ticks, size, rth=None):
    """NinjaTrader's seconds bars of the trades: from each session's open (an RTH template's: the RTH open), a bar only
    where a trade printed, stamped at its end, the last of a session at the session's end. [end ms, o, h, l, c]."""
    period, clock, hours = size * 1000, SessionClock(), RthClock(rth) if rth else None
    out, key = [], None
    for tk in ticks:
        if hours and hours.day(tk.ts) is None:
            continue
        o, e = hours.window(to_ct(tk.ts)) if hours else (clock.locate(tk.ts), session_end(clock.locate(tk.ts)))
        k = o + (tk.ts - o) // period * period
        if k != key:
            key = k
            out.append([min(k + period, e), tk.price, tk.price, tk.price, tk.price])
        else:
            b = out[-1]
            b[2], b[3], b[4] = max(b[2], tk.price), min(b[3], tk.price), tk.price
    return out


def port(root, ticks, size, rth, s):
    r = gs.SecondsRun(gs.own_hours(s, RTHS[root]), parse_spec(f"{size}s"), inst(root), rth, ticks[0].ts)
    r.feed(ticks)
    return r, r.payload(gs.own_hours(s, RTHS[root]))


@needs_mono
@pytest.mark.parametrize("root,size,rth,edges", [("ES", 15, False, False), ("ES", 7, False, True), ("ES", 30, True, False),
                                                 ("ES", 7, True, True), ("GC", 10, False, False), ("GC", 7, True, True)])
def test_seconds_gaps_equal_the_csharp_engine_over_ninjatraders_seconds_bars(tmp_path, root, size, rth, edges):
    """7s bars straddle 08:30 and 15:00 (their open price is from before the RTH open); 30s on an RTH chart; GC's moved
    hours (Use Instrument Hours on). On an RTH chart a 7s bar ends at the RTH close (the last one, 6 s, stamped there):
    with a print just before the close (edges) it holds the RTH close. Every gap (open, RTH close, size, fill bar, edge,
    bars to fill) equal."""
    r = subprocess.run(["sh", str(HERE / "gap_csharp/build.sh"), INDICATORS, str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    _, ticks = trades(root, edges=edges)
    s = gaps.settings({})
    lines = [f"S|{s['RthStart']}|{s['RthEnd']}|{D(s['MinimumGapPoints'])}|{root}|{int(s['UseInstrumentHours'])}"]
    bars = nt_bars(ticks, size, RTHS[root] if rth else None)
    lines += [f"U|{i}|{T(ct(e))}|{D(o)}|{D(h)}|{D(lo)}|{D(c)}" for i, (e, o, h, lo, c) in enumerate(bars)] + ["E"]
    (tmp_path / "rec.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["mono", str(tmp_path / "harness.exe"), str(tmp_path / "rec.txt"), str(tmp_path / "out.txt")], check=True)
    cs = [x for x in (tmp_path / "out.txt").read_text().splitlines() if x.startswith("H|") and x.count("|") == 12]
    run, d = port(root, ticks, size, rth, s)
    py = []
    for g in run.engine.History:
        G(py, "H", g)
    assert len(run.opens) == len(bars) and len(cs) == len(py) >= 4, (len(run.opens), len(bars), len(cs), len(py))
    assert all(same(a, b) for a, b in zip(cs, py)), [(a, b) for a, b in zip(cs, py) if not same(a, b)][:3]
    assert d["seconds"] == size and d["bar"] == len(bars) - 1 and d["t"] == run.opens[-1]


@pytest.mark.parametrize("size,rth", [(15, False), (30, True), (1, False)])
def test_seconds_gaps_are_the_minute_charts_at_one_bar_size_that_fits_the_minute(size, rth):
    """A bar size that divides the minute: the same gaps as the 1-minute chart's (open, RTH close, size, direction,
    filled or not), opening in the same minute, filled in the same minute; only the bars to fill are the chart's."""
    m1, ticks = trades("ES", days=5)
    s = gaps.settings({})
    run, d = port("ES", ticks, size, rth, s)
    eng, opens = gs.run(gs.own_hours(s, RTHS["ES"]), 1, m1, rth=RTHS["ES"] if rth else None)
    mins = gs.payload(eng, opens, 1)["gaps"]
    secs = d["gaps"]
    assert len(secs) == len(mins) >= 3
    for a, b in zip(secs, mins):
        assert (a["dir"], a["open"], a["settle"], a["edge"]) == (b["dir"], b["open"], b["settle"], b["edge"])
        assert a["st"] // 60_000 == b["st"] // 60_000 and (a["ft"] is None) == (b["ft"] is None)
        if a["ft"] is not None:
            assert a["ft"] // 60_000 == b["ft"] // 60_000 and a["fb"] - a["sb"] >= b["fb"] - b["sb"]


class FakeMarket:
    def __init__(self, path):
        self.store = Store(path)
        self.instruments = {"ES": inst("ES")}

    def flush_for_read(self):
        pass


def payload_of(ticks, size, s, start):
    r = gs.SecondsRun(s, parse_spec(f"{size}s"), inst("ES"), False, start)
    r.feed(ticks)
    return r.payload(s)


def test_a_seconds_chart_reads_only_new_trades_and_a_replay_follows_its_clock(tmp_path, monkeypatch):
    """Live: a second ask reads the trades stored since the first and comes out as one run over them all; a late print
    of the forming bar goes in it. Replay: a later clock reads on, an earlier one works it out again. At most
    SECONDS_TRADES trades back are read."""
    _, ticks = trades("ES", days=4)
    m = FakeMarket(tmp_path / "chart.db")
    svc = gs.GapService.__new__(gs.GapService)
    svc.m, svc.seconds = m, gs.OrderedDict()
    s = gs.own_hours(gaps.settings({}), RTHS["ES"])
    sp, start = parse_spec("15s"), ticks[0].ts
    cut = len(ticks) * 2 // 3

    async def go():
        m.store.write(ticks[:cut], [])
        first = await svc.get_seconds("ES", sp, s, start)
        assert first == payload_of(ticks[:cut], 15, s, start)
        late = Tick("ES", ticks[cut - 1].ts, ticks[cut - 1].price, 1, 1)   # stored after, in the forming bar
        m.store.write([late, *ticks[cut:]], [])
        again = await svc.get_seconds("ES", sp, s, start)
        assert again == payload_of([*ticks[:cut], late, *ticks[cut:]], 15, s, start) != first
        assert len(svc.seconds) == 1
        # replay: later, then earlier clocks
        stored = sorted([*ticks, late], key=lambda t: t.ts)
        for until in (ticks[cut // 2].ts, ticks[cut].ts + 1000, ticks[cut // 3].ts):
            until = until // 1000 * 1000
            got = await svc.get_seconds("ES", sp, s, start, until=until)
            assert got == payload_of([t for t in stored if t.ts < until], 15, s, start), until
        assert len(svc.seconds) == 2   # the live run and the replay's
        monkeypatch.setattr(gs, "SECONDS_TRADES", 5000)
        svc.seconds.clear()
        capped = await svc.get_seconds("ES", sp, s, start)
        floor = stored[-5000].ts
        assert capped["from"] == floor and capped == payload_of([t for t in stored if t.ts >= floor], 15, s, floor)
    asyncio.run(go())


def test_the_api_takes_second_charts_and_refuses_other_bar_types(client):  # noqa: F811
    """As NinjaTrader: Minute and Second charts; tick, volume, range and day charts are refused."""
    ok = client.get("/api/gaps?symbol=ES&spec=15s")
    assert ok.status_code == 200 and ok.json()["seconds"] == 15 and "gaps" in ok.json()
    assert client.get("/api/gaps?symbol=ES&spec=5m").json().get("seconds") is None
    for spec in ("2000t", "16r", "1D"):
        r = client.get(f"/api/gaps?symbol=ES&spec={spec}")
        assert r.status_code == 400 and "second chart" in r.json()["detail"], spec


# ---- the chart page: a second chart asks once its own bars are in --------------------------------------------------------

ASK_JS = """
const S = { st: { gap: true }, symbol: 'ES', spec: '1m', rth: false, instruments: { ES: {} }, replay: null, liveWait: false,
  bars: [{ t: 1000 }, { t: 61000 }], gapSet: { RthStart: 83000, RthEnd: 150000, MinimumGapPoints: 0, days: 90, EnabledAnalysis: true,
  UseInstrumentHours: true }, gaps: 'drawn', gapError: '' };
const indReady = () => true, gapPrim = { update() {} }, renderGapInfo = () => {}, gapCatchUp = () => {};
const asked = [];
const api = async (method, path) => { asked.push(Object.fromEntries(new URLSearchParams(path.split('?')[1]))); return { gaps: [], bar: 0, t: 0 }; };
/*APP*/
const ask = async (reset) => {
  await gapRequest(reset);
  return [asked.splice(0).map((q) => [q.spec, q.start ?? null, q.until ?? null]), S.gaps && 'answer'];
};
(async () => {
  const out = {};
  out.minute = await ask(true);
  S.spec = '30s'; S.liveWait = true;           // subscribe(): the 1-minute bars still shown
  out.switch = await ask(true);
  S.bars = [{ t: 90000 }, { t: 120000 }]; S.liveWait = false;   // applyHistory: the 30-second bars
  out.loaded = await ask(true);
  S.replay = { at: 100000, jumps: 1, loading: true };            // a replay start: the live bars still shown
  out.replay = await ask(true);
  S.bars = [{ t: 30000 }, { t: 60000 }]; S.replay.loading = false;   // the replay's bars
  out.replayed = await ask(true);
  S.replay = null; S.liveWait = true;           // back to live: the replay's bars until the live ones come
  out.exit = await ask(true);
  console.log(JSON.stringify(out));
})();"""


@needs_node
def test_a_second_chart_asks_for_gaps_from_its_own_first_bar():
    """subscribe() asked while the bars of the timeframe before (or the live ones under a replay start) were still on
    the chart, so the first request named their first bar as start (on a replay start, after its until), and that
    answer was drawn until the right one came. Now a second chart asks only once its own bars are in."""
    out = run_app(["replayUntil", "studyView", "GAP_SERVER", "gapSpecOk", "gapSeconds", "gapRthOk", "GAP_RTH_BAD", "gapBusy",
                   "gapRequest"], ASK_JS)
    assert out["minute"] == [[["1m", None, None]], "answer"]   # a minute chart: as before, the History days
    assert out["switch"] == [[], None]
    assert out["loaded"] == [[["30s", "90000", None]], "answer"]
    assert out["replay"] == [[], None]
    assert out["replayed"] == [[["30s", "30000", "100000"]], "answer"]
    assert out["exit"] == [[], None]
