"""Volume profiles against the NinjaTrader originals (parity round 2).

- PVP: touches on the chart's own bars (Renko bricks and Heikin Ashi bars too), against the C# Touches.Observe in
  tests/vp_csharp's harness. Its C lines are the chart's bars at their NinjaTrader time stamps (a time bar's close, a
  brick's last trade), taken before a minute of the same time stamp, as NinjaTrader orders its series; ObserveTouches
  runs on each and after each minute. web/studies.js pvpTouches gets the same bars as this app has them (at their open).
- SVP: its POC, VAH and VAL plots on each chart bar (the harness's B lines: PublishLevels) against the server's
  per-minute values (vp_service.SvpDev) and the chart's pick for a bar (svpAt); an RTH chart's trades only; the Chart
  time zone; a holiday halt's sessions.
- AAVP (and all three): NinjaTrader's GetXByTime placement (vpView), the data window, the texts on the chart.
- Round 3: a late trade in the forming SVP session's per-bar values, RTH in every SVP read (caught up, bars, replay), GC on
  Chart, what is kept (no minutes, values in the background, nodev, pruned), PVP touches kept between draws on Heikin
  Ashi, the live values after a setting change and a replay jump.
- Round 4: no old setting's levels on the bars after a change (the answer held, one on its way); SVP's per-bar values
  worked out in the background reach a paused replay."""
import asyncio
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from chartsrv import vp
from chartsrv.vp_service import MinuteState

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1] / "web"
STUDIES = (WEB / "studies.js").read_text()
sys.path.insert(0, str(HERE))
import vp_parity  # noqa: E402

INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
needs_mono = pytest.mark.skipif(not (shutil.which("mcs") and shutil.which("mono") and shutil.which("node")),
                                reason="needs mono and node")
FIVE = 5 * 60_000


def js_part(name: str) -> str:
    start = STUDIES.index(f"function {name}(")
    return STUDIES[start:STUDIES.index("\n}\n", start) + 3]


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """The vp harness, from TATANKA_INDICATORS' sources when set, else the copies kept here."""
    out = tmp_path_factory.mktemp("vp")
    args = ["sh", str(HERE / "vp_csharp" / "build.sh"), str(out)] + ([INDICATORS] if INDICATORS else [])
    r = subprocess.run(args, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return str(out / "harness.exe")


def final_minutes(lines: list[str]) -> list[dict]:
    """Each minute's last update (M lines), with an open and a close made up from it: up from low to high, down from high
    to low."""
    out = {}
    for line in lines:
        f = line.split("|")
        if f[0] == "M":
            lo, hi, up = float(f[4]), float(f[5]), f[7] == "1"
            out[int(f[3])] = {"begin": int(f[2]), "end": int(f[8]), "l": lo, "h": hi, "o": lo if up else hi, "c": hi if up else lo}
    return [out[k] for k in sorted(out)]


def five_minute(mins: list[dict]) -> list[dict]:
    """5-minute bars from each session's open, as this app's time bars align; NinjaTrader stamps them at their close."""
    bars = {}
    for m in mins:
        k = (m["begin"], (m["end"] - m["begin"] - 1) // FIVE)
        b = bars.get(k)
        if b is None:
            bars[k] = {"t": m["begin"] + k[1] * FIVE, "nt": m["begin"] + (k[1] + 1) * FIVE, "o": m["o"], "h": m["h"], "l": m["l"], "c": m["c"]}
        else:
            b.update(h=max(b["h"], m["h"]), l=min(b["l"], m["l"]), c=m["c"])
    return [bars[k] for k in sorted(bars)]


def heikin_ashi(bars: list[dict], tick: float) -> list[dict]:
    """web/app.js haAt (NinjaTrader's HeikenAshi on the tick; Math.round halves up)."""
    r = lambda v: math.floor(v / tick + 0.5) * tick   # noqa: E731
    out = []
    for b in bars:
        p = out[-1] if out else None
        o = r((p["o"] + p["c"]) / 2 if p else (b["o"] + b["c"]) / 2)
        c = r((b["o"] + b["h"] + b["l"] + b["c"]) / 4)
        out.append({**b, "o": o, "c": c, "h": max(b["h"], o, c), "l": min(b["l"], o, c)})
    return out


def renko(mins: list[dict], tick: float, size: int) -> list[dict]:
    """chartsrv.bars.RenkoSeries over each minute's close (at its end): bricks `size` ticks tall, restarting at each
    session's open, the last one forming. NinjaTrader stamps a brick with its last trade's time."""
    out, cur, lo, hi, sess = [], None, None, None, None
    for m in mins:
        p, t = round(m["c"] / tick), m["end"]
        if m["begin"] != sess:
            sess, lo, hi = m["begin"], p, p
            cur = {"t": t, "nt": t, "o": p, "h": p, "l": p, "c": p}
            out.append(cur)
            continue
        o, c = lo, hi
        up = c >= o
        while True:
            if (up and p >= c + size) or (not up and o != c and p >= o + size) or (o == c and p >= c + size):
                no = c if up or o == c else o
                nc = no + size
            elif (not up and p <= c - size) or (up and o != c and p <= o - size) or (o == c and p <= c - size):
                no = c if (not up) or o == c else o
                nc = no - size
            else:
                break
            cur.update(o=no, c=nc, h=max(no, nc), l=min(no, nc))
            o, c, up = no, nc, nc > no
            cur = {"t": t, "nt": t, "o": c, "h": c, "l": c, "c": c}
            out.append(cur)
        lo, hi = o, c
        cur.update(h=max(cur["h"], p), l=min(cur["l"], p), c=p, nt=t)
    return [{**b, **{k: round(b[k] * tick, 9) for k in "ohlc"}} for b in out]


def interleave(lines: list[str], bars: list[dict]) -> list[str]:
    """The M lines and the chart's bars (C lines) in NinjaTrader's order: by time stamp, the chart's bar first."""
    out, chart = [], sorted(bars, key=lambda b: b["nt"])
    k = 0
    for line in lines:
        f = line.split("|")
        if f[0] == "M":
            while k < len(chart) and chart[k]["nt"] <= int(f[8]):
                out.append(f"C|{chart[k]['l']!r}|{chart[k]['h']!r}|{chart[k]['nt']}")
                k += 1
        if f[0] != "S":
            out.append(line)
    out += [f"C|{b['l']!r}|{b['h']!r}|{b['nt']}" for b in chart[k:]]
    return out


def js_touches(profiles: list[dict], bars: list[dict]) -> dict:
    js = js_part("barIndexFrom") + js_part("pvpTouches") + """
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const m = pvpTouches(d.profiles, d.bars, (i) => [d.bars[i].l, d.bars[i].h]);
console.log(JSON.stringify([...m].map(([k, v]) => [k, v])));
"""
    r = subprocess.run(["node", "-e", js], input=json.dumps({"profiles": profiles, "bars": bars}), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {k: v for k, v in json.loads(r.stdout)}


@needs_mono
@pytest.mark.parametrize("chart", ["5 minute", "Heikin Ashi", "Renko"])
@pytest.mark.parametrize("market", ["ES", "GC"])
def test_pvp_touches_on_the_charts_own_bars_are_ninjatraders(harness, chart, market):
    """NinjaTrader's PVP ends an "until touched" line at the first chart bar after the period whose high and low hold the
    level (Lows[0][0], Highs[0][0]: a Renko brick's, a Heikin Ashi bar's). This app's took the 1-minute bars' range. Now
    every touch is the C#'s: the same bar, on 5-minute, Heikin Ashi and Renko bars, the GC prices as stored (2600.1)
    against levels of 26001 * 0.1 (NinjaTrader 1.0.1's margin)."""
    tick, price, head = (0.25, 6800.0, "P|Daily|1|20|0.25|1|60|68|0") if market == "ES" else (0.1, 2600.0, "P|Daily|1|20|0.1|1|50|68|0")
    lines = vp_parity.minutes(random.Random(11), date(2026, 6, 1), 9, price, tick)
    mins = final_minutes(lines)
    bars = five_minute(mins)
    if chart == "Heikin Ashi":
        bars = heikin_ashi(bars, tick)
    elif chart == "Renko":
        bars = renko(mins, tick, 10)
    bars = [{**b, "l": round(b["l"], 9), "h": round(b["h"], 9)} for b in bars]
    run = [head, *interleave(lines, bars), "E"]
    want = subprocess.run(["mono", harness], input="\n".join(run) + "\n", capture_output=True, text=True, check=True).stdout
    nt, start = {}, None
    for line in want.splitlines():
        f = line.split("|")
        if f[0] == "PROFILE":
            start = int(f[2])
        elif f[0] == "TOUCH":
            nt[start] = f[1:]

    st = MinuteState(vp.PeriodicSeries("Daily", 1, 20, tick, True, int(head.split("|")[6]), 68.0, False))
    for line in lines:   # the profiles as the server has them (vp_parity checks them against the C#)
        f = line.split("|")
        if f[0] == "M":
            st.step((date.fromisoformat(f[1]), int(f[2]), int(f[3]), float(f[4]), float(f[5]), float(f[6]), f[7] == "1", int(f[8])))
    profiles = []
    for p in st.series.profiles:
        s = p.snapshot()
        profiles.append({"start": p.start_chart, "last": p.last_chart, "closed": p.closed, "rows": s["rows"][:1],
                         "poc": s["poc"], "vah": s["vah"], "val": s["val"], "touch": getattr(p, "touch", {})})
    got = js_touches(profiles, [{"t": b["t"], "l": b["l"], "h": b["h"]} for b in bars])
    mine = {p["start"]: [str(bars[t[k]["i"]]["nt"]) if k in t else "-" for k in ("poc", "vah", "val")]
            for p in profiles if p["closed"] for t in [got[p["start"]]]}
    assert {k: v for k, v in nt.items() if k in mine} == mine
    assert all(v == ["-"] * 3 for k, v in nt.items() if k not in mine)   # the forming profile: none
    assert sum(v.count("-") < 3 for v in mine.values()) >= 4 and any("-" in v for v in mine.values())   # touched and naked
    # the minutes' touches (the server's) would end some lines on other bars
    if chart != "5 minute":
        by_min = {p["start"]: [p["touch"].get(k) for k in ("poc", "vah", "val")] for p in profiles if p["closed"]}
        assert any(str(m) != n for k in mine for m, n in zip(by_min[k], mine[k]) if m is not None and n != "-")


@needs_node
def test_pvp_touches_before_the_first_bar_come_from_the_minutes():
    """A period that ended before the chart's first loaded bar: a touch its minutes found before that bar stands (the
    bars between are not here, so its line ends off the chart's left as before); one they found later, or none, is looked
    for on the chart's bars from the first."""
    bars = [{"t": 1000 + 300 * i, "l": 100 + i, "h": 101 + i} for i in range(10)]
    p = {"start": 0, "last": 500, "closed": True, "rows": [[1]], "poc": 103.0, "vah": 108.5, "val": 90.0,
         "touch": {"poc": 900, "vah": 2500}}
    got = js_touches([p, {**p, "start": 1, "closed": False}, {**p, "start": 2, "last": 1600, "touch": {}}], bars)
    assert got == {0: {"poc": {"t": 900}, "vah": {"i": 8}}, 2: {"poc": {"i": 2}, "vah": {"i": 8}}}


def consumed(lines: list[str]) -> list[tuple]:
    """T lines as the indicator takes them (TickCounter.Consume: a live tick bar's volume grows in place): (price, volume,
    time), as vp_parity.port does."""
    out, last, counted = [], -1, 0.0
    for line in lines:
        f = line.split("|")
        if f[0] != "T":
            continue
        index, vol = int(f[1]), float(f[3])
        if index < last or vol <= 0:
            continue
        if index != last:
            last, counted = index, 0.0
        volume = max(0.0, vol - counted)
        counted = max(counted, vol)
        if volume > 0:
            out.append((float(f[2]), volume, int(f[4])))
    return out


def js_svp_at(sessions: list[dict], times: list[int]) -> list:
    js = js_part("svpAt") + """
const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(d.times.map((t) => svpAt(d.sessions, t))));
"""
    r = subprocess.run(["node", "-e", js], input=json.dumps({"sessions": sessions, "times": times}), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_mono
@pytest.mark.parametrize("head", ["V|08:30|15:00|0.25|1|20|68|0", "V|08:30|15:00|0.25|4|20|70|1", "V|17:00|16:00|0.25|2|20|68|0"])
def test_svp_per_bar_values_are_ninjatraders(harness, head):
    """NinjaTrader's SVP sets its POC, VAH and VAL plots on every chart bar (its Data Box): the current session's snapshot
    as of the bar's close, the last session's once it ended, none before the first. The server's per-minute values
    (SvpDev, from each session's minutes) and the chart's pick for a bar (svpAt) give the C#'s on 5-minute bars, every
    bar of the day: in each session, between them and before the first."""
    from chartsrv.vp_service import SvpState, svp_dev, svp_json
    _, start, end, tick, tpr, keep, pct, atl = head.split("|")
    tick, tpr, keep, pct, atl = float(tick), int(tpr), int(keep), float(pct), atl == "1"
    body = vp_parity.trades(random.Random(5), date(2026, 2, 2), 5, 6800.0, tick)
    body = [x for x in body if x != "S"]
    t0 = vp_parity.MS(vp_parity.DT(int(body[0].split("|")[4])).replace(hour=0, minute=0, second=0, microsecond=0))
    closes = list(range(t0 + FIVE, int(body[-1].split("|")[4]) + 2 * FIVE, FIVE))
    lines, k = [head], 0
    for line in body:   # each bar before the trades of its close's millisecond and later, as NinjaTrader orders them
        while k < len(closes) and closes[k] <= int(line.split("|")[4]):
            lines.append(f"B|{closes[k]}")
            k += 1
        lines.append(line)
    lines += [f"B|{t}" for t in closes[k:]] + ["E"]
    out = subprocess.run(["mono", harness], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True).stdout
    unbits = lambda x: vp_parity.struct.unpack("<d", vp_parity.struct.pack("<q", int(x)))[0]   # noqa: E731
    want = [None if f[2] == "-" else [unbits(x) for x in f[2:5]] for f in (l.split("|") for l in out.splitlines() if l.startswith("PLOT|"))]

    a, b = vp.parse_time(start), vp.parse_time(end)
    states: list[SvpState] = []
    for price, volume, ts in consumed(body):
        w = vp.try_window(vp_parity.DT(ts), a, b)
        if not w:
            continue
        if not states or states[-1].begin != vp_parity.MS(w[0]):
            states.append(SvpState(vp_parity.MS(w[0]), vp_parity.MS(w[1])))
            del states[:-keep]
        states[-1].add(price, volume, ts, tick)
    sessions = [{**svp_json(st.ticks, st.begin, st.end, st.last, tick, tpr, pct, atl), "done": True,
                 "dev": svp_dev(st.mins, tick, tpr, pct, atl)} for st in states]
    got = js_svp_at(sessions, closes)
    assert len(got) == len(want) == len(closes) and got == want
    assert want.count(None) >= 50 and len({tuple(x) for x in want if x}) > 100   # before the first session; it develops


def test_svp_dev_is_the_snapshot_at_each_minute():
    """SvpDev's value at a minute's end is the session's snapshot of what traded before it (svp_json's), with 1-minute
    bars where no trade is stored, at any setting."""
    from chartsrv.vp_service import SvpState, svp_dev, svp_json
    rng, st, cut = random.Random(3), SvpState(0, 10 ** 9), {}
    p = 27000
    for i in range(3000):
        ts = i * 7_000 + rng.randint(0, 5_000)
        if 120 <= ts // 60_000 < 125:   # five minutes from their bars
            continue
        if ts // 60_000 >= 125 and 121 * 60_000 not in cut:
            for m in range(120, 125):
                st.add_bar(m * 60_000, 6750.0, 6752.5, 6748.0, 6751.0, 400, 0.25)
                cut[m * 60_000 + 60_000] = {k: list(v) for k, v in st.ticks.items()}
        p += rng.choice((-1, 0, 1))
        st.add(p * 0.25, rng.randint(1, 30), ts, 0.25)
        cut[ts // 60_000 * 60_000 + 60_000] = {k: list(v) for k, v in st.ticks.items()}
    for tpr, pct, atl in ((1, 68.0, False), (3, 70.0, True)):
        dev = svp_dev(st.mins, 0.25, tpr, pct, atl)
        by = {t: v for t, *v in dev}
        last = None
        for m in sorted(cut):
            j = svp_json(cut[m], 0, 1, 1, 0.25, tpr, pct, atl)
            want = [j["poc"], j["vah"], j["val"]]
            assert (by[m] if m in by else last) == want
            last = want


# ---- the Session VP service: RTH charts, the Chart time zone, the per-bar values in its answers -----------------------
MIN = 60_000


def _ct(d, h, mi=0):
    from chartsrv.sessions import CT
    return int(datetime(2026, 10, d, h, mi, tzinfo=CT).timestamp() * 1000)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    """A VpService on ES trades every minute of Tuesday Oct 13 2026's Globex day (17:00 Monday to 16:00) at 7000, but at
    7100 (20 lots) from 05:00 to 06:00 CT, overnight; the clock Wednesday 10:30 CT. Jobs in threads."""
    from types import SimpleNamespace

    from chartsrv import vp_service
    from chartsrv.models import Tick
    from chartsrv.store import Store
    monkeypatch.setenv("CHART_WORKERS", "0")
    monkeypatch.setattr(time, "time", lambda: _ct(14, 10, 30) / 1000)
    store = Store(tmp_path / "t.db")
    store.write([Tick("ES", t + 1000, 7100.0 if _ct(13, 5) <= t < _ct(13, 6) else 7000.0 + (t // MIN % 3) * 0.25,
                      20 if _ct(13, 5) <= t < _ct(13, 6) else 1, 1)
                 for t in range(_ct(12, 17), _ct(13, 16), MIN)], [])
    inst = SimpleNamespace(tick_size=0.25, rth=("08:30", "15:00"), calendar="equity")
    m = SimpleNamespace(store=store, instruments={"ES": inst}, ind=SimpleNamespace(overrides={}),
                        flush_for_read=lambda symbol="": [], recent_minutes=lambda symbol: (_ct(14, 10, 30), []))
    return vp_service.VpService(m)


def test_svp_on_an_rth_chart_takes_its_regular_hours_only(svc):
    """NinjaTrader's tick series (AddDataSeries(Tick, 1)) follows the chart's Trading Hours: on an RTH template a 17:00-16:00
    window holds the RTH trades only. Here the overnight 7100s made the POC on an ETH chart; on an RTH chart (rth) they
    are left out, as the 1-minute bars of minutes without trades would be."""
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00"}

    async def go():
        eth = await svc.get("prof", "ES", given=s)
        rth = await svc.get("prof", "ES", given=s, rth=True)
        return eth, rth
    eth, rth = asyncio.run(go())
    e, r = eth["sessions"][-1], rth["sessions"][-1]
    assert e["start"] == r["start"] == _ct(12, 17)
    assert e["poc"] == 7100.0 and r["poc"] != 7100.0 and r["high_edge"] < 7100
    assert sum(x[1] + x[2] for x in r["rows"]) == (_ct(13, 15) - _ct(13, 8, 30)) // MIN   # 08:30-15:00, a lot a minute
    assert eth["key"] != rth["key"]


def test_svp_chart_time_zone_is_the_charts(svc):
    """Session time zone Chart is NinjaTrader's chart zone (its General options'): here the chart's own, the clock's,
    sent as tz. 09:30-16:00 on a New York chart is 08:30-15:00 CT, as Eastern; a Chicago chart's (or none sent) runs it in
    CT, as before. GC: test_svp_chart_time_zone_on_gc_moves_to_its_own_hours."""
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "09:30", "SessionEnd": "16:00", "SessionTimeZone": "Chart"}

    async def go():
        return [await svc.get("prof", "ES", given=s, chart_tz=tz) for tz in ("America/New_York", "America/Chicago", "", "Not/AZone")]
    ny, chi, none, bad = asyncio.run(go())
    assert [p["start"] for p in ny["sessions"]] == [_ct(13, 8, 30)] and ny["sessions"][0]["end"] == _ct(13, 15)
    assert [p["start"] for p in chi["sessions"]] == [_ct(13, 9, 30)]
    nodev = lambda d: [{k: v for k, v in p.items() if k != "dev"} for p in d["sessions"]]   # noqa: E731 (in the background)
    assert nodev(none) == nodev(chi) == nodev(bad) and ny["key"] != chi["key"] == none["key"]
    east = asyncio.run(svc.get("prof", "ES", given={**s, "SessionTimeZone": "Eastern"}))
    assert nodev(east) == nodev(ny) and east["hours"] == ny["hours"] == "09:30-16:00"


def test_svp_answers_carry_the_per_bar_values(svc):
    """Each session in the answer has its POC, VAH and VAL at each minute's end (dev): the forming one's at once, the
    finished ones' worked out in the background and kept (the next answers have them, a new service reads them back).
    A chart holds a finished session only once it has them."""
    from chartsrv import vp_service
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00"}

    async def go():
        first = await svc.get("prof", "ES", given=s)
        await svc.settled()
        again = await svc.get("prof", "ES", given=s)
        fresh = vp_service.VpService(svc.m)
        return first, again, await fresh.get("prof", "ES", given=s)
    first, again, fresh = asyncio.run(go())
    done = first["sessions"][-1]
    assert done["done"] and "dev" not in done and again["sessions"][-1]["dev"] == fresh["sessions"][-1]["dev"]
    dev = again["sessions"][-1]["dev"]
    assert dev[0][0] == _ct(12, 17, 1) and dev[-1][1:] == [done["poc"], done["vah"], done["val"]] == [7100.0, done["vah"], done["val"]]
    assert any(v[1] == 7100.0 for v in dev) and dev[0][1] != 7100.0   # the POC moves to 7100 at 05:00


# ---- where a time is drawn: NinjaTrader's GetXByTime ----------------------------------------------------------------
XOF = """
const ct = (s) => Date.parse(s + '-05:00'), sp = 10;   // CDT
const ts = { getVisibleLogicalRange: () => ({ from: 0, to: 1e6 }), logicalToCoordinate: (i) => i * sp, options: () => ({ barSpacing: sp }) };
const chart = { timeScale: () => ts }, series = { coordinateToPrice: (y) => 8000 - y, priceToCoordinate: (p) => 8000 - p };
const view = (P, open, close, step, end) => {   // Friday's bars to its 16:00 close, then Sunday's from 17:00
  const bars = [];
  for (let t = ct(open); t < ct('2026-10-09T16:00:00'); t += step) bars.push({ t });
  for (let t = ct('2026-10-11T17:00:00'); t < ct(close); t += step) bars.push({ t });
  return [bars, vpView(chart, series, bars, P, 1000, end)];
};
const M = 60e3, out = {};
let [bars, v] = view(5 * M, '2026-10-09T15:00:00', '2026-10-11T18:00:00', 5 * M);
const fri = bars.findIndex((b) => b.t === ct('2026-10-09T15:55:00'));
out.five = { fri, sunOpen: v.xOf(ct('2026-10-11T17:00:00')) / sp, friClose: v.xOf(ct('2026-10-09T16:00:00')) / sp,
  inside: v.xOf(ct('2026-10-11T17:02:00')) / sp, weekend: v.xOf(ct('2026-10-10T12:00:00')) / sp,
  first: v.xOf(bars[0].t) / sp, before: v.xOf(bars[0].t - 10 * M) / sp, last: v.xOf(bars.at(-1).t + 5 * M) / sp, n: bars.length };
const H4 = 240 * M, close = (b) => Math.min(b.t + H4, ct('2026-10-09T16:00:00') + (b.t >= ct('2026-10-11T17:00:00') ? 3 * 86400e3 : 0));
[bars, v] = view(H4, '2026-10-08T17:00:00', '2026-10-12T05:00:00', H4, close);
out.h4 = { n: bars.length, friLast: bars.findIndex((b) => b.t === ct('2026-10-09T13:00:00')), friClose: v.xOf(ct('2026-10-09T16:00:00')) / sp,
  sunOpen: v.xOf(ct('2026-10-11T17:00:00')) / sp, mid: v.xOf(ct('2026-10-09T15:00:00')) / sp };
console.log(JSON.stringify(out));
"""


@needs_node
def test_times_are_placed_as_ninjatraders_get_x_by_time():
    """NinjaTrader's GetXByTime (measured on its charts): a time inside a bar lies between the previous bar's center and
    that bar's by the time into it, a time in a gap is the previous bar's center. So a period opening Sunday 17:00 starts
    on Friday's last bar (one bar left of its first: this app drew it on the first), a finished period ends on its last
    bar, and a forming profile's last minute (17:02 on a 5-minute bar) is 2/5 of the way to the forming bar (this app
    drew it on the bar). A session's last bar closes with the session (4-hour bars: Friday's 13:00 bar at 16:00)."""
    js = "".join(js_part(f) for f in ("barIndexFrom", "barIndexTo", "logicalX", "vpView")) + XOF
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    f = out["five"]
    assert f["sunOpen"] == f["friClose"] == f["weekend"] == f["fri"] and f["inside"] == f["fri"] + 0.4
    assert f["first"] == -1 and f["before"] == -3 and f["last"] == f["n"] - 1
    h = out["h4"]
    assert h["friClose"] == h["sunOpen"] == h["friLast"] and h["mid"] == h["friLast"] - 0.25


# ---- the data window and the on-chart texts ---------------------------------------------------------------------------
@needs_node
def test_the_data_window_has_each_profiles_plots_at_the_bar():
    """POC, VAH and VAL in the data window for the bar under the crosshair, as NinjaTrader's Data Box has the plots:
    SVP's the session's as of the bar's close (the last bar: now), to a tick; AAVP's and PVP's only on the bars that formed
    while it ran and the last one at load (NinjaTrader resets them on history; the one before it too when loaded while
    it forms: test_hunt22_vp), each at its latest. A hidden one, none."""
    from test_hunt_web_chart import app_part
    js = js_part("svpAt") + "\n".join(app_part(n) for n in ("vpViewKey", "vpBarKey", "vpLiveNote", "vpBarValues", "vpDataCells")) + """
const M = 60e3, P = 5 * M, tick = 0.25;
const S = { bars: [0, 1, 2, 3].map((i) => ({ t: 1000 * M + i * P, seq: i })), st: { prof: true, aavp: true, pvp_2: true, pvp: true },
  stHidden: { pvp: true }, vpLive: {}, rth: false, replay: null, vpSet: { aavp: {}, pvp_2: {} },
  vp: { aavp: { view: 'false false {}' }, pvp_2: { view: 'false false {}' } } };
const sessions = [{ start: 1000 * M, done: false, poc: 7001, vah: 7002, val: 6999, dev_to: 1012 * M,
  dev: [[1001 * M, 7000, 7000.5, 6999.5], [1006 * M, 7000.5, 7001, 6999.75]] }];
const latest = { aavp: { poc: 7010, vah: 7020, val: 7005 }, pvp_2: { poc: 7100.125, vah: 7101, val: 7099 } };
const vpKeys = () => ['prof', 'aavp', 'pvp_2', 'pvp'], baseOf = (k) => k.replace(/_\\d+$/, ''), instNo = (k) => Number(/_(\\d+)$/.exec(k)?.[1] || 1);
const vpBase = (k) => ({ kind: baseOf(k), periodMs: P, d: baseOf(k) === 'prof' ? { sessions } : {}, set: {}, key: k });
const vpLatest = (st) => latest[st.key] ? { ...latest[st.key] } : null;
const barCloseMs = (b) => b.t + P, pxTick = (p) => (Math.round(p / tick) * tick).toFixed(2);
const isTime = () => true, calSpec = () => false;   // loaded long after these bars: the last bar only
const out = {};
vpLiveNote('aavp'); vpLiveNote('pvp_2');            // the answers at load: the last bar
out.load = [0, 1, 2, 3].map((i) => vpDataCells(i));
S.bars.push({ t: 1000 * M + 4 * P, seq: 4 }); latest.aavp.poc = 7011;
vpLiveNote('aavp'); vpLiveNote('pvp_2');            // the next bar formed while it ran
out.live = [3, 4].map((i) => vpDataCells(i));
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    none = [["AAVP POC", "–"], ["VAH", "–"], ["VAL", "–"], ["PVP 2 POC", "–"], ["VAH", "–"], ["VAL", "–"]]
    svp = lambda poc, vah, val: [["SVP POC", poc], ["VAH", vah], ["VAL", val]]   # noqa: E731
    assert out["load"][0] == svp("7000.00", "7000.50", "6999.50") + none        # closes at 1005: the 1001 minute's
    assert out["load"][1] == svp("7000.50", "7001.00", "6999.75") + none        # closes at 1010: 1006's
    assert out["load"][2] == svp("7001.00", "7002.00", "6999.00") + none        # closes at 1015, past its minutes: now
    assert out["load"][3] == svp("7001.00", "7002.00", "6999.00") + [["AAVP POC", "7010.00"], ["VAH", "7020.00"], ["VAL", "7005.00"],
                                                                      ["PVP 2 POC", "7100.25"], ["VAH", "7101.00"], ["VAL", "7099.00"]]
    assert out["live"][0][3][1] == "7010.00" and out["live"][1][3][1] == "7011.00"   # each bar its own


@needs_node
def test_on_chart_texts_name_this_apps_settings():
    """The waiting texts name what this app has (the stored history, Backfill; ETH/RTH), not NinjaTrader's Days to load
    and Trading Hours."""
    js = js_part("vpMessage") + """
console.log(JSON.stringify(['aavp', 'pvp', 'prof'].map((kind) => vpMessage({ kind, d: null, set: {} }))));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    texts = json.loads(r.stdout)
    assert all("Days to load" not in t and "Trading Hours" not in t for t in texts)
    assert texts[0].startswith("Tatanka AAVP: waiting for 1-minute history.") and "Backfill" in texts[0] and "ETH/RTH" in texts[2]


def test_svp_draws_a_holiday_halts_partial_session_and_the_evening_after(tmp_path, monkeypatch):
    """Done before this round (the parity audit's items 5 and 9): the answer has MLK Day 2026's own 08:30-15:00 session,
    partial (to the 12:00 halt), as NinjaTrader draws it, and with 17:00-16:00 the session that opens on its evening
    (Tuesday's Globex day)."""
    from types import SimpleNamespace

    from chartsrv import sessions, vp_service
    from chartsrv.config import load_holidays
    from chartsrv.indicators import defaults
    from chartsrv.models import Bar
    from chartsrv.sessions import CT
    from chartsrv.store import Store
    ms = lambda *a: int(datetime(*a, tzinfo=CT).timestamp() * 1000)   # noqa: E731
    monkeypatch.setenv("CHART_WORKERS", "0")
    monkeypatch.setattr(time, "time", lambda: ms(2026, 1, 21, 11) / 1000)
    sessions.set_calendar(load_holidays(HERE.parents[1] / "config"))
    store = Store(tmp_path / "t.db")
    store.insert_bars_if_missing("ES", [Bar(t=t, o=6800, h=6801, l=6799, c=6800, v=10)
                                        for t in range(ms(2026, 1, 12), ms(2026, 1, 21, 11), 10 * MIN) if sessions.is_open(t, "equity")])
    svc = vp_service.VpService(SimpleNamespace(store=store, instruments={"ES": SimpleNamespace(tick_size=0.25, rth=("08:30", "15:00"), calendar="equity")},
                                               ind=SimpleNamespace(overrides={}), flush_for_read=lambda symbol="": []))
    rth = asyncio.run(svc.get("prof", "ES", given={**defaults("prof"), "SessionsToKeep": 4}))
    mlk = next(p for p in rth["sessions"] if p["start"] == ms(2026, 1, 19, 8, 30))
    assert mlk["rows"] and mlk["last"] <= ms(2026, 1, 19, 12) and mlk["end"] == ms(2026, 1, 19, 15)
    night = asyncio.run(svc.get("prof", "ES", given={**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00", "SessionsToKeep": 3}))
    assert ms(2026, 1, 19, 17) in [p["start"] for p in night["sessions"] if p["rows"]]


@needs_node
def test_svp_at_a_session_in_its_first_minute_and_one_on_its_way():
    """A forming session with no whole minute yet: the forming bar has its values now, the bars before the last
    session's; a session whose values are still being worked out gives none from its start."""
    js = js_part("svpAt") + """
const a = { start: 0, done: true, poc: 1, vah: 2, val: 0, dev: [[60, 1, 2, 0]] };
const b = { start: 1000, done: false, poc: 5, vah: 6, val: 4, dev: [], dev_to: 0 };
const c = { start: 1000, done: true, poc: 5, vah: 6, val: 4 };
console.log(JSON.stringify([svpAt([a, b], Infinity), svpAt([a, b], 1030), svpAt([a, c], 1030), svpAt([a, c], 900), svpAt([a, b], 30)]));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [[5, 6, 4], [1, 2, 0], None, [1, 2, 0], None]


# ---- SVP round 3: the forming session's late trades, RTH everywhere, GC on Chart, what is kept ------------------------
def _svc(tmp_path, monkeypatch, clock, inst=None, symbol="ES"):
    """A VpService on an empty store, the clock clock[0] (ms), jobs in threads."""
    from types import SimpleNamespace

    from chartsrv import vp_service
    from chartsrv.store import Store
    monkeypatch.setenv("CHART_WORKERS", "0")
    monkeypatch.setattr(time, "time", lambda: clock[0] / 1000)
    store = Store(tmp_path / "t.db")
    inst = inst or SimpleNamespace(tick_size=0.25, rth=("08:30", "15:00"), calendar="equity")
    m = SimpleNamespace(store=store, instruments={symbol: inst}, ind=SimpleNamespace(overrides={}), flush_for_read=lambda symbol="": [])
    return vp_service.VpService(m)


def test_svp_forming_sessions_per_bar_values_take_a_late_trade(tmp_path, monkeypatch):
    """A trade stamped in a minute a request has fed already (stored a moment after that minute's end: the add-on's
    latency, NinjaTrader's clock behind this one) is fed again from its minute: every later value has it, as worked out
    from scratch. Here 50 lots at 7002 stamped 08:59:59.8, stored after a request at 09:00:30, move the 09:00 POC to 7002."""
    from chartsrv import vp_service
    from chartsrv.indicators import defaults
    from chartsrv.models import Tick
    clock = [_ct(14, 9, 0) + 30_000]
    svc = _svc(tmp_path, monkeypatch, clock)
    store = svc.m.store
    store.write([Tick("ES", t + 1000, 7000.0 + (t // MIN % 2) * 0.25, 1, 1) for t in range(_ct(14, 8, 30), _ct(14, 9), MIN)]
                + [Tick("ES", _ct(14, 8, 40) + 30_000, 7001.0, 2, 1)], [])
    s = defaults("prof")
    sk = (s["TicksPerRow"], float(s["ValueAreaPercent"]), s["ValueAreaMethod"] == "AtLeastTarget")

    async def go():
        a = await svc.get("prof", "ES", given=s)
        store.write([Tick("ES", _ct(14, 9) - 200, 7002.0, 50, 1)], [])   # 08:59:59.8, stored at 09:00:31
        clock[0] = _ct(14, 9, 1) + 30_000
        store.write([Tick("ES", _ct(14, 9) + 40_000, 7002.0, 1, 1)], [])
        return a, await svc.get("prof", "ES", given=s)
    a, b = asyncio.run(go())
    assert a["sessions"][-1]["dev"][-1][0] == _ct(14, 9)   # the 09:00 minute was fed by the first request
    cur = b["sessions"][-1]
    st, = next(iter(svc.entries.values())).state.values()   # the forming session's state
    fresh = vp_service.svp_dev(st.mins, 0.25, *sk)
    assert cur["dev"] == fresh and cur["dev_to"] == _ct(14, 9, 1)
    at9 = [v for v in cur["dev"] if v[0] <= _ct(14, 9)][-1]
    assert at9[1] == 7002.0 == cur["poc"]


def test_svp_dev_feeds_the_minute_ending_at_its_upto():
    """SvpDev.feed takes the minutes that end by upto, that one too: bar replay's clock is a minute's start, and the
    minute ending there is whole."""
    from chartsrv.vp_service import SvpDev, SvpState
    st = SvpState(0, 10 * MIN)
    for i in range(3):
        st.add(100.0 + i, 1 + i, i * MIN + 5_000, 0.25)
    d = SvpDev(0.25, 1, 68.0, False).feed(st.mins, 2 * MIN)
    assert d.done == 2 * MIN and [v[0] for v in d.dev] == [MIN, 2 * MIN]
    d.feed(st.mins, 3 * MIN - 1)
    assert d.done == 2 * MIN
    d.feed(st.mins, 3 * MIN)
    assert d.done == 3 * MIN and d.dev[-1][1] == 102.0


def test_svp_rth_chart_leaves_out_live_trades_after_the_close(tmp_path, monkeypatch):
    """An RTH chart's forming 17:00-16:00 session: trades stored between requests (caught up on the event loop) count only
    in the regular hours, as the ones read in the worker. 400 lots at 7100 at 15:01 and 15:02 make the ETH chart's POC,
    not the RTH chart's."""
    from chartsrv.indicators import defaults
    from chartsrv.models import Tick
    clock = [_ct(14, 15) + 30_000]
    svc = _svc(tmp_path, monkeypatch, clock)
    store = svc.m.store
    store.write([Tick("ES", t + 1000, 7000.0 + (t // MIN % 3) * 0.25, 1, 1) for t in range(_ct(13, 17), _ct(14, 15), MIN)], [])
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00", "SessionsToKeep": 1}

    async def go():
        await svc.get("prof", "ES", given=s, rth=True)
        await svc.get("prof", "ES", given=s)
        store.write([Tick("ES", _ct(14, 15, m) + 1000, 7100.0, 400, 1) for m in (1, 2)], [])
        clock[0] = _ct(14, 15, 3) + 30_000
        return await svc.get("prof", "ES", given=s, rth=True), await svc.get("prof", "ES", given=s)
    rth, eth = asyncio.run(go())
    r, e = rth["sessions"][-1], eth["sessions"][-1]
    assert not r["done"] and r["start"] == e["start"] == _ct(13, 17)
    assert e["poc"] == 7100.0 and r["poc"] != 7100.0 and r["high_edge"] < 7100 and r["last"] < _ct(14, 15)
    assert sum(x[1] + x[2] for x in r["rows"]) == (_ct(14, 15) - _ct(14, 8, 30)) // MIN
    assert all(v[1] != 7100.0 for v in r["dev"])


def test_svp_rth_chart_takes_regular_hours_bars_and_replays_them_too(tmp_path):
    """An RTH chart: a minute with no stored trades takes its 1-minute bar only in the regular hours (svp_job), and bar
    replay's forming session reads only those trades and bars (svp_upto). Overnight here: bars at 7100 (05:00-06:00 CT)
    and trades at 7200 (06:30); in the regular hours bars at 7000 (09:00-10:00) and a trade at 7001 (10:30)."""
    from chartsrv.models import Bar, Tick
    from chartsrv.store import Store
    from chartsrv.vp_service import SvpState, svp_job, svp_upto
    store = Store(tmp_path / "t.db")
    store.insert_bars_if_missing("ES", [Bar(t=t, o=7100, h=7100, l=7100, c=7100, v=40) for t in range(_ct(14, 5), _ct(14, 6), MIN)]
                                 + [Bar(t=t, o=7000, h=7000, l=7000, c=7000, v=4) for t in range(_ct(14, 9), _ct(14, 10), MIN)])
    store.write([Tick("ES", _ct(14, 6, 30) + i, 7200.0, 30, 1) for i in range(3)] + [Tick("ES", _ct(14, 10, 30), 7001.0, 2, 1)], [])
    w, rth = (_ct(13, 17), _ct(14, 16)), ("08:30", "15:00")
    eth_st, = svp_job(store.path, "ES", [w], store.max_tick_rowid(), 0.25)
    rth_st, = svp_job(store.path, "ES", [w], store.max_tick_rowid(), 0.25, rth=rth)
    assert eth_st.approx == 120 and rth_st.approx == 60 and rth_st.trades == 1
    assert sorted(k * 0.25 for k in rth_st.ticks) == [7000.0, 7001.0]
    rep = svp_upto(store.path, "ES", SvpState(*w), _ct(14, 11), store.max_tick_rowid(), 0.25, rth)
    assert rep.approx == 60 and rep.trades == 1 and sorted(k * 0.25 for k in rep.ticks) == [7000.0, 7001.0]
    assert sorted(rep.mins) == [t + MIN for t in range(_ct(14, 9), _ct(14, 10), MIN)] + [_ct(14, 10, 31)]
    whole = svp_upto(store.path, "ES", SvpState(*w), _ct(14, 11), store.max_tick_rowid(), 0.25)
    assert whole.approx == 120 and whole.trades == 4


def test_svp_chart_time_zone_on_gc_moves_to_its_own_hours(tmp_path, monkeypatch):
    """GC with Session time zone Chart: ES's regular hours typed in the chart's zone (New York, Chicago, London, Tokyo)
    run GC's own, 07:20-12:30 CT, every way (Use Instrument Hours: in CT as a whole, in another zone each date's)."""
    from types import SimpleNamespace

    from chartsrv.indicators import defaults
    from chartsrv.models import Tick
    clock = [_ct(14, 18)]
    gc = SimpleNamespace(tick_size=0.1, rth=("07:20", "12:30"), calendar="metals")
    svc = _svc(tmp_path, monkeypatch, clock, gc, "GC")
    svc.m.store.write([Tick("GC", t + 1000, 2600.0 + (t // MIN % 5) * 0.1, 1, 1) for t in range(_ct(12, 17), _ct(14, 16), MIN)
                       if not _ct(13, 16) <= t < _ct(13, 17)], [])

    async def go():
        out = {}
        for tz, a, b in (("America/New_York", "09:30", "16:00"), ("America/Chicago", "08:30", "15:00"),
                         ("Europe/London", "14:30", "21:00"), ("Asia/Tokyo", "22:30", "05:00")):
            s = {**defaults("prof"), "SessionStart": a, "SessionEnd": b, "SessionTimeZone": "Chart", "SessionsToKeep": 2}
            out[tz] = await svc.get("prof", "GC", given=s, chart_tz=tz)
        return out
    got = asyncio.run(go())
    for tz, d in got.items():
        assert [(p["start"], p["end"]) for p in d["sessions"]] == [(_ct(13, 7, 20), _ct(13, 12, 30)), (_ct(14, 7, 20), _ct(14, 12, 30))], tz
    assert got["America/Chicago"]["hours"] == "07:20-12:30" and got["America/New_York"]["hours"] == "08:20-13:30"


def test_svp_kept_sessions_hold_no_minutes_and_their_values_come_in_the_background(svc, monkeypatch):
    """A finished session is kept as its profile alone (as before the per-bar values: a row of the previous version
    serves at once), so a full answer parses no per-minute data. Its per-bar values are worked out in a worker from its
    trades read again (svp_dev_job), newest first, kept, and equal to the session's worked out from scratch."""
    from chartsrv import vp_service, workers
    from chartsrv.indicators import defaults
    from chartsrv.vp_service import SvpState, svp_dev
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00"}
    ran = []
    real = workers.run

    async def run(kind, fn, *args):
        ran.append(fn.__name__)
        return await real(kind, fn, *args)
    monkeypatch.setattr(workers, "run", run)

    async def go():
        first = await svc.get("prof", "ES", given=s)
        rows = dict(svc.m.store.db.execute("SELECT key, data FROM vp_cache"))
        await svc.settled()
        again = vp_service.VpService(svc.m)
        ran.clear()
        return first, rows, await again.get("prof", "ES", given=s)
    first, rows, back = asyncio.run(go())
    assert ran == [] and len(rows) == 1 and set(json.loads(next(iter(rows.values())))) == {"ticks", "last", "approx"}
    done = back["sessions"][-1]
    assert "dev" not in first["sessions"][-1] and done["rows"] == first["sessions"][-1]["rows"]
    st = SvpState(_ct(12, 17), _ct(13, 16))
    for price, size, ts in svc.m.store.db.execute("SELECT price, size, ts FROM ticks ORDER BY ts, rowid"):
        st.add(price, size, ts, 0.25)
    assert done["dev"] == svp_dev(st.mins, 0.25, 1, 68.0, False)


def test_svp_poll_sends_held_sessions_per_bar_values_once_ready(svc):
    """A chart holds a finished session from its first answer; while its per-bar values are worked out it names it in
    nodev, and the answer's "same" brings them once they are kept (not before; and not to a chart that has them)."""
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00", "SessionsToKeep": 2}

    async def go():
        first = await svc.get("prof", "ES", given=s)
        starts = {p["start"] for p in first["sessions"] if p["done"]}
        early = await svc.get("prof", "ES", key=first["key"], have=starts, given=s, nodev=starts)
        await svc.settled()
        ready = await svc.get("prof", "ES", key=first["key"], have=starts, given=s, nodev=starts)
        held = await svc.get("prof", "ES", key=first["key"], have=starts, given=s)
        full = await svc.get("prof", "ES", given=s)
        return starts, early, ready, held, full
    starts, early, ready, held, full = asyncio.run(go())
    assert starts == {_ct(12, 17)}
    assert early["sessions"] == [{"start": _ct(12, 17), "same": True}]
    assert ready["sessions"] == [{"start": _ct(12, 17), "same": True, "dev": full["sessions"][-1]["dev"]}]
    assert held["sessions"] == [{"start": _ct(12, 17), "same": True}]


def test_svp_per_bar_values_follow_the_setting_asked_last(svc):
    """The background work is for one setting at a time: asked for another meanwhile (another chart, a changed value area),
    it stops after the session in hand and the next request starts the new one's."""
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00"}
    db = svc.m.store.db
    kept = lambda pct: db.execute("SELECT COUNT(*) FROM vp_cache WHERE key LIKE ?", (f"%|{pct!r}|0",)).fetchone()[0]   # noqa: E731

    async def go():
        await svc.get("prof", "ES", given=s)
        await svc.settled()
        await svc.get("prof", "ES", given={**s, "ValueAreaPercent": 70.0})   # its work starts ...
        await svc.get("prof", "ES", given={**s, "ValueAreaPercent": 75.0})   # ... and is no longer wanted
        await svc.settled()
        n70, n75 = kept(70.0), kept(75.0)
        await svc.get("prof", "ES", given={**s, "ValueAreaPercent": 75.0})
        await svc.settled()
        return kept(68.0), n70, n75, kept(75.0)
    assert asyncio.run(go()) == (1, 0, 0, 1)


def test_svp_sessions_no_chart_can_reach_go_at_the_upkeep(tmp_path, monkeypatch):
    """vp_cache's SVP rows (profiles and per-bar values) opening before the longest lookback (Sessions to retain at its
    most, 100) are dropped by the daily upkeep; newer ones and other rows stay."""
    from test_review_fixes import build_market

    from chartsrv import vp_service
    m, _ = build_market(tmp_path, monkeypatch)
    now = _ct(14, 16, 30)
    day = 86_400_000
    old, new = now - 200 * day, now - 150 * day
    with m.store.db:
        m.store.db.executemany("INSERT OR REPLACE INTO vp_cache VALUES (?, ?)", [
            (f"svp2|ES|{old}|{old + day}|0.25", "{}"), (f"svp2|ES|{old}|{old + day}|0.25|1|68.0|0", "{}"),
            (f"svp2|NQ|{new}|{new + day}|0.25|rth08:30-15:00", "{}"), ("svp2|bad", "{}"), (f"amtpd2|ES|{old}|{old + day}", "{}")])
    assert vp_service.svp_lookback(100) + 1 < 200
    asyncio.run(m.upkeep.once(now))
    assert sorted(k for (k,) in m.store.db.execute("SELECT key FROM vp_cache")) == [
        f"amtpd2|ES|{old}|{old + day}", f"svp2|NQ|{new}|{new + day}|0.25|rth08:30-15:00"]


# ---- the chart's side: PVP's touches on Heikin Ashi bars, kept between draws; the data window's live values -----------
TOUCHES = """
const M = 60e3, tick = 0.25;
let seed = 7;
const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const mk = (n, t0, p0) => { const out = []; let p = p0; for (let i = 0; i < n; i++) {   // a gap every 10 bars: Heikin Ashi's open lags in it
  const o = Math.round((p + (i % 10 === 5 ? (rnd() < 0.5 ? -5 : 5) : 0)) / tick) * tick; p = o + (rnd() - 0.5) * 3; const c = Math.round(p / tick) * tick;
  const h = Math.max(o, c) + Math.round(rnd() * 4) * tick, l = Math.min(o, c) - Math.round(rnd() * 4) * tick; out.push({ t: t0 + i * M, o, h, l, c }); } return out; };
const haOf = (bars) => { const out = []; for (let i = 0; i < bars.length; i++) { const b = bars[i], p = out[i - 1], r = (v) => Math.round(v / tick) * tick;
  const o = r(p ? (p.o + p.c) / 2 : (b.o + b.c) / 2), c = r((b.o + b.h + b.l + b.c) / 4); out.push({ o, c, h: Math.max(b.h, o, c), l: Math.min(b.l, o, c) }); } return out; };
const profiles = []; for (let k = 0; k < 12; k++) { const lv = 6990 + k * 1.75; profiles.push({ start: k, closed: true, rows: [1], last: 1000 * M + k * 5 * M, poc: lv, vah: lv + 2.5, val: lv - 3.25 }); }
const all = mk(400, 1000 * M, 7000);
const plain = (bars) => (i) => [bars[i].l, bars[i].h], haHl = (bars) => { const h = haOf(bars); return (i) => [h[i].l, h[i].h]; };
const fresh = (bars, hl) => JSON.stringify([...pvpTouches(profiles, bars, hl)]);
const inst = () => ({ tick_size: tick });
let altSeries = { _style: 'ha' };
const S = { bars: all.slice(0, 200), ha: [] };
const out = {};
const d = { profiles };
out.ha = JSON.stringify([...vpTouches('pvp', d)]) === fresh(S.bars, haHl(S.bars));
out.differs = fresh(S.bars, haHl(S.bars)) !== fresh(S.bars, plain(S.bars));
// plain bars, counted: the next draws check only the bars since (the last one again: it was forming)
altSeries = null;
let reads = 0;
const counted = (b) => ({ t: b.t, o: b.o, c: b.c, get h() { reads += 1; return b.h; }, get l() { reads += 1; return b.l; } });
S.bars = all.slice(0, 200).map(counted);
vpTouches('pvp', d);
const full = reads;
reads = 0;
vpTouches('pvp', d);
out.again = reads;
all[199].h += 30; all[199].l -= 30;   // the bar that was forming reached every level before it closed
for (const b of all.slice(200, 260)) S.bars.push(counted(b));
reads = 0;
out.more = JSON.stringify([...vpTouches('pvp', d)]) === fresh(all.slice(0, 260), plain(all));
out.moreReads = reads; out.full = full;
S.bars.unshift(...mk(30, 1000 * M - 30 * M, 7000).map(counted));   // older bars in front: all again, indexes moved
out.older = JSON.stringify([...vpTouches('pvp', d)]) === fresh(S.bars.map((b) => ({ t: b.t, h: b.h, l: b.l })), plain(S.bars));
console.log(JSON.stringify(out));
"""


@needs_node
def test_pvp_touches_on_heikin_ashi_and_kept_between_draws():
    """The chart's PVP touches (app.js vpTouches) on Heikin Ashi take the Heikin Ashi bars' highs and lows (S.ha), as
    NinjaTrader's Highs[0] and Lows[0] there. Asked at every draw, they are kept: the next draws check only the bars
    since (and the last one again), and give what a check of every bar gives; older bars in front start again."""
    from test_hunt_web_chart import app_part
    js = js_part("barIndexFrom") + js_part("pvpTouches") + "\n".join(app_part(n) for n in ("haAt", "vpTouchMemo", "vpTouches")) + TOUCHES
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ha"] and out["differs"]
    assert out["more"] and out["older"]
    assert 0 < out["again"] <= 2 * 36 and out["moreReads"] <= 61 * out["again"] and out["full"] > 5 * out["again"]


@needs_node
def test_a_changed_vp_setting_empties_the_live_values():
    """NinjaTrader loads an indicator again when a setting changes: AAVP's and PVP's plots are empty on the bars of
    history again. indApply drops the values noted on the bars (S.vpLive) of the profile whose setting changed."""
    from test_hunt_web_chart import app_part
    js = app_part("indApply") + """
const noop = () => {}, asked = [];
const S = { vpSet: { aavp: { AnchorPeriod: 'Month' }, pvp: { Period: 'Weekly' } }, st: { dsma: false }, tpoSet: {}, gapSet: {}, hemaSets: {}, ifSet: {},
  vpLive: { aavp: new Map([['1|1', [1, 2, 0]]]), pvp: new Map([['1|1', [5, 6, 4]]]) } };
const indSnap = () => ({ vp: { aavp: { AnchorPeriod: 'Session' }, pvp: { Period: 'Weekly' } }, tpo: {}, gap: {}, hema: {}, ifthen: {} });
const TPO_DRAWING = [], instKeys = () => [], vpKeys = () => ['aavp', 'pvp'], vpRequest = (k) => asked.push(k);
const prim = { update: noop };
const [indSets, tpoRequest, renderTpoInfo, gapRequest, renderGapInfo, dsmaDraw, dsmaRequest, ifRequest, renderVpInfo, amtLayout,
  renderIfInfo, renderLlInfo, legend] = Array(13).fill(noop);
const tpoPrim = prim, gapPrim = prim, ifPrim = prim, llPrim = prim, vpPrim = () => prim;
indApply();
console.log(JSON.stringify({ asked, live: Object.keys(S.vpLive) }));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"asked": ["aavp"], "live": ["pvp"]}


@needs_node
def test_live_values_are_noted_on_the_new_bars_after_a_jump():
    """After a replay jump (or another timeframe, ETH/RTH) the old bars stay until the new ones come: an answer arriving
    meanwhile is not noted on the old last bar, nor one asked for another view; applyHistory notes the levels on the new
    last bar (replayLoad clears its loading first)."""
    from test_hunt_web_chart import app_part
    js = "\n".join(app_part(n) for n in ("vpViewKey", "vpBarKey", "vpLiveNote", "vpLiveTick")) + """
const S = { bars: [{ t: 1, seq: 1 }], st: { aavp: true }, vpLive: {}, rth: false, replay: { jumps: 3, loading: true }, liveWait: false,
  vpSet: { aavp: {} }, vp: { aavp: { view: 'false false {}' } } };
const vpKeys = () => ['aavp'], baseOf = (k) => k, vpBase = (k) => ({ key: k }), vpLatest = () => ({ poc: 7, vah: 8, val: 6 });
const out = [];
S.vp.aavp.view = '3 false {}';
vpLiveTick(); out.push([...(S.vpLive.aavp || new Map()).keys()]);      // loading: the old bars
S.replay.loading = false; S.bars = [{ t: 9, seq: 9 }];
S.vp.aavp.view = '2 false {}';
vpLiveTick(); out.push([...(S.vpLive.aavp || new Map()).keys()]);      // an answer of the clock before
S.vp.aavp.view = '3 false {}';
vpLiveTick(); out.push([...(S.vpLive.aavp || new Map()).keys()]);      // the new clock's on the new last bar
S.liveWait = true; S.bars = [{ t: 20, seq: 20 }];
vpLiveTick(); out.push([...S.vpLive.aavp.keys()]);                     // another timeframe on its way
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [[], [], ["9|9"], ["9|9"]]
    hist, load = app_part("applyHistory"), app_part("replayLoad")
    assert hist.index("S.liveWait = false") < hist.index("vpLiveTick()")
    assert load.index("r.loading = false;") < load.index("applyHistory(")


@needs_node
def test_svp_sessions_are_held_without_their_values_and_get_them_with_same():
    """The chart holds a finished session from its first answer, its per-bar values or not; those without are named in
    nodev and come with the server's "same"; then nothing more is asked of it."""
    from test_hunt_web_chart import app_part
    js = "\n".join(app_part(n) for n in ("vpList", "vpMerge", "vpViewKey", "vpRequest")) + """
const S = { st: { prof: true }, symbol: 'ES', instruments: { ES: {} }, vpSet: { prof: {} }, vp: {}, vpErr: {}, vpLive: {}, rth: false, replay: null };
const vpHeld = {}, vpBusy = {}, vpPrims = { prof: {} }, ax = { zone: 'America/Chicago' }, REPLAY_RID = 'r';
const baseOf = (k) => k, indReady = () => true, replayUntil = () => {}, vpPrim = () => ({ update() {} });
const [renderVpInfo, legend, vpLiveNote] = Array(3).fill(() => {});
const asked = [], answers = [
  { key: 'k1', sessions: [{ start: 1, done: true, poc: 1 }, { start: 2, done: false, poc: 2, dev: [] }] },
  { key: 'k1', sessions: [{ start: 1, same: true, dev: [[60, 1, 2, 0]] }, { start: 2, done: false, poc: 3, dev: [] }] },
  { key: 'k1', sessions: [{ start: 1, same: true }, { start: 2, done: false, poc: 4, dev: [] }] }];
const api = async (method, url) => { asked.push(new URLSearchParams(url.split('?')[1])); return answers[asked.length - 1]; };
(async () => {
  const out = [];
  for (let i = 0; i < 3; i++) {
    await vpRequest('prof');
    const q = asked.at(-1);
    out.push([q.get('have'), q.get('nodev'), S.vp.prof.sessions[0].dev || null]);
  }
  console.log(JSON.stringify(out));
})();
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [[None, None, None], ["1", "1", [[60, 1, 2, 0]]], ["1", None, [[60, 1, 2, 0]]]]


@needs_node
def test_no_old_setting_levels_on_the_bars_after_a_change():
    """NinjaTrader loads the indicator again at once on a changed setting: the last bar and every bar after get the new
    setting's levels. Here the new answer takes a moment: the answer held and one asked before the change (arriving
    after it) are the old setting's, noted on no bar (a tick, a bar closing meanwhile); the new one on the bar then
    forming. The old answer is drawn until the new one comes."""
    from test_hunt_web_chart import app_part
    js = "\n".join(app_part(n) for n in ("indApply", "vpList", "vpMerge", "vpViewKey", "vpRequest", "vpBarKey", "vpLiveNote",
                                         "vpLiveTick")) + """
const noop = () => {}, prim = { update: noop };
const S = { vpSet: { aavp: { AnchorPeriod: 'Session' } }, st: { dsma: false, aavp: true }, tpoSet: {}, gapSet: {}, hemaSets: {}, ifSet: {},
  symbol: 'ES', instruments: { ES: {} }, vp: {}, vpErr: {}, vpLive: {}, bars: [{ t: 1, seq: 1 }], rth: false, replay: null, liveWait: false };
const vpHeld = {}, vpBusy = {}, vpPrims = { aavp: prim }, ax = { zone: 'America/Chicago' }, REPLAY_RID = 'r';
const baseOf = (k) => k, indReady = () => true, replayUntil = noop, vpPrim = () => prim, vpKeys = () => ['aavp'];
const vpBase = (k) => ({ kind: 'aavp', d: S.vp[k] }), vpLatest = (st) => st.d.profile;
const isTime = () => true, calSpec = () => false, barCloseMs = (b) => b.t + 300e3;   // bars long closed (test_hunt22_vp)
const TPO_DRAWING = [], instKeys = () => [];
const [indSets, tpoRequest, renderTpoInfo, gapRequest, renderGapInfo, dsmaDraw, dsmaRequest, ifRequest, renderVpInfo, amtLayout,
  renderIfInfo, renderLlInfo, legend] = Array(13).fill(noop);
const tpoPrim = prim, gapPrim = prim, ifPrim = prim, llPrim = prim;
const asked = [];
const api = (method, url) => new Promise((resolve) => asked.push({ s: JSON.parse(new URLSearchParams(url.split('?')[1]).get('s')).AnchorPeriod, resolve }));
const prof = (poc) => ({ profile: { poc, vah: poc + 1, val: poc - 1 } });
const settle = () => new Promise((r) => setTimeout(r, 0));
const live = () => [...(S.vpLive.aavp || new Map()).entries()];
(async () => {
  const out = {};
  vpRequest('aavp'); asked[0].resolve(prof(100)); await settle();        // at load: the last bar
  out.load = live();
  vpRequest('aavp');                                                      // the poll: the Session anchor's, on its way
  S.vpSet = { aavp: { AnchorPeriod: 'Month' } };                          // the anchor changed
  indApply({ vp: { aavp: { AnchorPeriod: 'Session' } }, tpo: {}, gap: {}, hema: {}, ifthen: {} });
  vpLiveTick();                                                           // a trade on bar 1
  S.bars.push({ t: 2, seq: 2 }); vpLiveTick();                            // bar 1 closes, bar 2 opens
  asked[1].resolve(prof(105)); await settle();                            // the Session answer arrives
  vpLiveTick();
  out.old = [live(), S.vp.aavp.profile.poc];
  asked[2].resolve(prof(200)); await settle();                            // the Month one
  vpLiveTick();
  out.asked = asked.map((a) => a.s);
  out.live = live();
  console.log(JSON.stringify(out));
})();
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["load"] == [["1|1", [100, 101, 99]]]
    assert out["asked"] == ["Session", "Session", "Month"]
    assert out["old"] == [[], 100]                    # nothing noted; the held answer still drawn
    assert out["live"] == [["2|2", [200, 201, 199]]]  # bar 1 closed before it came: empty, not the Session anchor's


@needs_node
def test_svp_in_a_paused_replay_gets_per_bar_values_worked_out_in_the_background():
    """In replay the poll asks nothing (the clock moves with the steps), except SVP while a finished session it holds
    has no per-bar values yet: asked every 3 seconds, paused too, until they come. Live: every profile, as before."""
    from test_hunt_web_chart import app_part
    js = "\n".join(app_part(n) for n in ("replayUntil", "vpList", "vpMerge", "vpViewKey", "vpRequest", "vpNoDev",
                                         "vpPoll")) + """
const S = { st: { prof: true, aavp: true }, symbol: 'ES', instruments: { ES: {} }, vpSet: { prof: {}, aavp: {} }, vp: {}, vpErr: {}, vpLive: {},
  rth: false, ext: false, replay: { jumps: 1, at: 5e6, playing: false } };
const document = { visibilityState: 'visible' };
const vpHeld = {}, vpBusy = {}, vpPrims = { prof: {}, aavp: {} }, ax = { zone: 'America/Chicago' }, REPLAY_RID = 'r';
const baseOf = (k) => k, indReady = () => true, vpPrim = () => ({ update() {} }), vpKeys = () => ['prof', 'aavp'];
const [renderVpInfo, legend, vpLiveNote] = Array(3).fill(() => {});
const asked = [], answers = { prof: [
  { key: 'k1', sessions: [{ start: 1, done: true, poc: 1 }, { start: 2, done: false, poc: 2, dev: [] }] },   // the jump's
  { key: 'k1', sessions: [{ start: 1, same: true }, { start: 2, done: false, poc: 2, dev: [] }] },          // not ready yet
  { key: 'k1', sessions: [{ start: 1, same: true, dev: [[60, 1, 2, 0]] }, { start: 2, done: false, poc: 2, dev: [] }] }],
  aavp: [{ profile: { poc: 1, vah: 2, val: 0 } }] };
const api = async (method, url) => {
  const k = url.split('/')[3].split('?')[0], q = new URLSearchParams(url.split('?')[1]);
  asked.push([k, q.get('nodev'), q.get('until'), q.get('rid')]);
  return answers[k].shift() || answers[k].at(-1) || { profile: null };
};
const settle = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  const out = [];
  vpRequest('prof'); vpRequest('aavp'); await settle();   // the jump (subscribe's vpAll)
  for (let i = 0; i < 3; i++) {                           // paused: the poll
    const n = asked.length;
    vpPoll(); await settle();
    out.push([asked.slice(n), S.vp.prof.sessions[0].dev || null]);
  }
  document.visibilityState = 'hidden';
  delete vpHeld.prof.byStart.get(1).dev;
  const n = asked.length;
  vpPoll(); await settle();
  out.push(asked.slice(n).length);
  document.visibilityState = 'visible';
  S.replay = null; answers.prof.push({ key: 'k1', sessions: [] });
  const m = asked.length;
  vpPoll(); await settle();
  out.push(asked.slice(m).map((a) => a[0]));
  console.log(JSON.stringify(out));
})();
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out[0] == [[["prof", "1", "5000000", "r"]], None]                 # held without: asked again, paused
    assert out[1] == [[["prof", "1", "5000000", "r"]], [[60, 1, 2, 0]]]      # they came
    assert out[2] == [[], [[60, 1, 2, 0]]]                                   # nothing more to ask
    assert out[3] == 0                                                       # a hidden page asks nothing
    assert out[4] == ["prof", "aavp"]                                        # live: every profile


def test_svp_replay_sends_held_sessions_per_bar_values_once_ready(svc):
    """The server side of a paused replay's follow-up: a finished session's per-bar values asked for in replay (nodev,
    the replay clock) come with "same" once worked out, as live."""
    from chartsrv.indicators import defaults
    s = {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00", "SessionsToKeep": 2}
    until = _ct(14, 9)

    async def go():
        first = await svc.get("prof", "ES", given=s, until=until, rid="r")
        starts = {p["start"] for p in first["sessions"] if p["done"]}
        await svc.settled()
        ready = await svc.get("prof", "ES", key=first["key"], have=starts, given=s, until=until, rid="r", nodev=starts)
        full = await svc.get("prof", "ES", given=s)
        return first, starts, ready, full
    first, starts, ready, full = asyncio.run(go())
    assert starts == {_ct(12, 17)} and "dev" not in first["sessions"][0]
    assert ready["sessions"][0] == {"start": _ct(12, 17), "same": True, "dev": full["sessions"][0]["dev"]}
