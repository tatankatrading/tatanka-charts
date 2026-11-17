"""Round 22 of the bug hunt in the volume profiles: an SVP session that just ended takes the trades stored a moment late
before it is kept, many profiles on screen at once are not built again at every ask, and the data window has AAVP's and
PVP's levels on the bar before the forming one at load, where NinjaTrader's history pass puts them."""
import asyncio
import json
import shutil
import subprocess
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from chartsrv import vp_service
from chartsrv.indicators import defaults
from chartsrv.models import Bar, Tick
from chartsrv.sessions import CT
from chartsrv.store import Store

MIN = 60_000
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def ct(d, h, mi=0, s=0, ms=0):   # November 2026 (Thursday the 12th)
    return int(datetime(2026, 11, d, h, mi, s, ms * 1000, tzinfo=CT).timestamp() * 1000)


def _svc(tmp_path, monkeypatch, clock):
    """A VpService on an empty store, ES, the clock clock[0] (ms), jobs in threads."""
    monkeypatch.setenv("CHART_WORKERS", "0")
    monkeypatch.setattr(time, "time", lambda: clock[0] / 1000)
    store = Store(tmp_path / "t.db")
    inst = SimpleNamespace(tick_size=0.25, rth=("08:30", "15:00"), calendar="equity")
    m = SimpleNamespace(store=store, instruments={"ES": inst}, ind=SimpleNamespace(overrides={}),
                        flush_for_read=lambda symbol="": [], recent_minutes=lambda symbol, until=None: (clock[0] // MIN * MIN, []))
    return vp_service.VpService(m)


def _vol(p):
    return sum(r[1] + r[2] for r in p["rows"])


def _from_storage(store, w):
    """The session worked out from what is stored now, in time order."""
    st, = vp_service.svp_job(store.path, "ES", [w], store.max_tick_rowid(), 0.25)
    return vp_service.svp_json(st.ticks, *w, st.last, 0.25, 1, 68.0, False)


def _kept(store, w):
    row = store.db.execute("SELECT data FROM vp_cache WHERE key=?", (f"svp2|ES|{w[0]}|{w[1]}|0.25",)).fetchone()
    return None if row is None else sum(u + d for _, (u, d) in json.loads(row[0])["ticks"])


# ---- SVP: a session that just ended -----------------------------------------------------------------------------------
def test_svp_session_that_just_ended_takes_the_trades_stored_a_moment_late(tmp_path, monkeypatch):
    """A chart's poll lands 20 ms after the 15:00 close. Trades stamped 14:59:59.96-.99 are stored a moment later (a feed
    that stamps the exchange's time, the add-on's buffer after a reconnect). NinjaTrader adds them to that session by
    their own time. Here the session takes them until SVP_SETTLE_MS after its end (answered as forming, not kept); then
    it is worked out once from storage and kept, with them, and a restart reads them too. Before, the first poll after
    the end kept it without them for good."""
    clock = [ct(12, 14, 59, 30)]
    svc = _svc(tmp_path, monkeypatch, clock)
    store = svc.m.store
    w = (ct(12, 8, 30), ct(12, 15))
    store.write([Tick("ES", t, 6800.0 + (t // 10_000 % 5) * 0.25, 5, 1) for t in range(w[0], ct(12, 14, 59, 30), 10_000)], [])
    s = {**defaults("prof"), "SessionsToKeep": 1}

    async def go():
        out = {}
        await svc.get("prof", "ES", given=s)                                     # forming
        clock[0] = ct(12, 15, 0, 0, 20)
        out["first"] = (await svc.get("prof", "ES", given=s))["sessions"][-1]    # the first poll after the end
        store.write([Tick("ES", ct(12, 14, 59, 59, 960 + i * 10), 6810.0, 300, 1) for i in range(4)], [])   # stored late
        clock[0] = ct(12, 15, 0, 3)
        out["next"] = (await svc.get("prof", "ES", given=s))["sessions"][-1]
        out["kept_then"] = _kept(store, w)
        clock[0] = w[1] + vp_service.SVP_SETTLE_MS                               # settled: worked out once and kept
        out["settled"] = (await svc.get("prof", "ES", given=s))["sessions"][-1]
        await svc.settled()
        fresh = vp_service.VpService(svc.m)                                      # a restart
        await fresh.get("prof", "ES", given=s)
        await fresh.settled()
        out["restart"] = (await fresh.get("prof", "ES", given=s))["sessions"][-1]
        return out
    out = asyncio.run(go())
    want = _from_storage(store, w)
    assert _vol(out["first"]) == _vol(want) - 1200 and out["first"]["done"] is False
    assert out["next"]["done"] is False and out["next"]["rows"] == want["rows"] and out["next"]["high_edge"] == 6810.125
    assert out["kept_then"] is None                                              # not kept while it settles
    assert out["settled"]["done"] is True and out["settled"]["rows"] == want["rows"]
    assert _kept(store, w) == _vol(want)
    r = out["restart"]
    assert r["rows"] == want["rows"] and r["dev"][-1][1:] == [r["poc"], r["vah"], r["val"]]


def test_svp_back_to_back_windows_the_one_that_ended_takes_late_trades_too(tmp_path, monkeypatch):
    """A 24-hour window (start = end) opens the next session as one ends: while the new one forms, the one that just
    ended takes the trades stored late too, and is kept only once settled."""
    clock = [ct(12, 10, 0, 0, 500)]
    svc = _svc(tmp_path, monkeypatch, clock)
    store = svc.m.store
    w1, w2 = (ct(11, 10), ct(12, 10)), (ct(12, 10), ct(13, 10))
    store.write([Tick("ES", t + 1000, 7000.0 + (t // MIN % 3) * 0.25, 1, 1) for t in range(*w1, MIN)]
                + [Tick("ES", ct(12, 10, 0, 0, 100), 7001.0, 2, 1)], [])
    s = {**defaults("prof"), "SessionStart": "10:00", "SessionEnd": "10:00", "UseInstrumentHours": False, "SessionsToKeep": 2}

    async def go():
        first = await svc.get("prof", "ES", given=s)
        store.write([Tick("ES", ct(12, 9, 59, 59, 900), 7005.0, 600, 1)], [])    # stamped before 10:00, stored late
        clock[0] = ct(12, 10, 0, 3)
        nxt = await svc.get("prof", "ES", given=s)
        kept_then = _kept(store, w1)
        clock[0] = w1[1] + vp_service.SVP_SETTLE_MS
        settled = await svc.get("prof", "ES", given=s)
        return first, nxt, kept_then, settled
    first, nxt, kept_then, settled = asyncio.run(go())
    assert [p["start"] for p in first["sessions"]] == [w1[0], w2[0]]
    want = _from_storage(store, w1)
    a, b = nxt["sessions"]
    assert a["done"] is False and a["rows"] == want["rows"] and a["poc"] == 7005.0 and b["done"] is False
    assert kept_then is None
    a, b = settled["sessions"]
    assert a["done"] is True and a["rows"] == want["rows"] and b["done"] is False and _vol(b) == 2
    assert _kept(store, w1) == _vol(want)


def test_svp_replay_finishes_a_session_at_its_end(tmp_path, monkeypatch):
    """Bar replay reads only what was stored before its clock: a session that ended before the clock is finished there,
    as before."""
    clock = [ct(13, 10)]
    svc = _svc(tmp_path, monkeypatch, clock)
    store = svc.m.store
    w = (ct(12, 8, 30), ct(12, 15))
    store.write([Tick("ES", t + 1000, 7000.0 + (t // MIN % 3) * 0.25, 1, 1) for t in range(w[0], ct(12, 16), MIN)], [])
    s = {**defaults("prof"), "SessionsToKeep": 1}
    r = asyncio.run(svc.get("prof", "ES", given=s, until=ct(12, 15, 0, 30), rid="x"))
    p = r["sessions"][-1]
    assert p["start"] == w[0] and p["done"] is True and _vol(p) == 390


# ---- many profiles at once --------------------------------------------------------------------------------------------
def test_more_profiles_than_max_builders_asked_in_turn_are_each_built_once(tmp_path, monkeypatch):
    """Each chart asks for each of its profiles every 3 seconds. With more distinct ones on screen than MAX_BUILDERS (two
    charts with 4 AAVP, 4 PVP and 4 SVP, and one more), the oldest went at every ask: each was built again, from storage,
    at every poll. A built series goes only once nobody has asked for it for a while (or past a hard cap)."""
    clock = [ct(12, 10, 30)]
    svc = _svc(tmp_path, monkeypatch, clock)
    svc.m.store.insert_bars_if_missing("ES", [Bar(t=t, o=7000, h=7000.25, l=6999.75, c=7000.25, v=10)
                                              for t in range(ct(10, 17), ct(12, 10, 30), MIN)])
    builds, real = [], vp_service.build_minutes

    def job(*a):
        builds.append(a[3])
        return real(*a)
    monkeypatch.setattr(vp_service, "build_minutes", job)
    n = vp_service.MAX_BUILDERS + 2
    sets = [{**defaults("pvp"), "Period": "Daily", "ProfilesToKeep": k} for k in range(1, n + 1)]

    async def go():
        for _ in range(3):   # three poll rounds
            for g in sets:
                await svc.get("pvp", "ES", given=g)
    asyncio.run(go())
    assert len(builds) == n and len(svc.entries) == n

    made = []
    for _ in range(3):
        for i in range(n):
            svc._entry(("aavp", "ES", i), lambda: made.append(1))
    assert len(made) == n
    # nobody asks for the PVPs any more: past MAX_BUILDERS they go once idle; the ones asked for stay
    for k in [k for k in svc.entries if k[0] == "pvp"]:
        svc.entries[k].used -= vp_service.BUILDER_IDLE_S + 1
    svc._entry(("aavp", "ES", "one more"), lambda: made.append(1))
    assert len(svc.entries) == n + 1 and all(k[0] == "aavp" for k in svc.entries)
    for x in svc.entries.values():   # all idle: the least recently asked go, down to MAX_BUILDERS
        x.used -= vp_service.BUILDER_IDLE_S + 1
    svc._entry(("aavp", "ES", "one more"), lambda: made.append(1))
    assert len(svc.entries) == vp_service.MAX_BUILDERS and list(svc.entries)[-1] == ("aavp", "ES", "one more")
    assert list(svc.entries)[0] == ("aavp", "ES", n + 1 - vp_service.MAX_BUILDERS) and len(made) == n + 1
    # a hard cap for memory, however recently asked
    for i in range(vp_service.BUILDERS_CAP + 5):
        svc._entry(("prof", "ES", i), lambda: None)
    assert len(svc.entries) == vp_service.BUILDERS_CAP and ("prof", "ES", vp_service.BUILDERS_CAP + 4) in svc.entries


# ---- the data window at load ------------------------------------------------------------------------------------------
@needs_node
def test_at_load_the_bar_before_the_forming_one_has_aavp_and_pvp_levels():
    """NinjaTrader's history pass runs the series in time-stamp order (bars stamped at their end, the primary first on a
    tie): the forming 1-minute bar, the last, publishes the levels on the chart bar then current. When it ends before the
    forming chart bar does, that is the bar before the forming one; then the forming bar is reset and filled at the
    transition. So at load (and after a changed setting, a load again) both have the levels; only the last one in the
    forming bar's last minute (every minute of a 1-minute chart), after the last bar closed, and on tick or range bars.
    Replay goes by its clock. Later bars: each its own, as before."""
    from test_hunt_web_chart import app_part
    js = "\n".join(app_part(n) for n in ("vpViewKey", "vpBarKey", "vpLiveNote")) + """
const M = 60e3;
let P = 5 * M, NOW = 0;
Date.now = () => NOW;
const S = { bars: [], st: { aavp: true }, vpLive: {}, rth: false, replay: null, liveWait: false, kind: 'time', spec: '5m',
  vpSet: { aavp: {} }, vp: { aavp: { view: 'false false {}' } } };
const baseOf = (k) => k.replace(/_\\d+$/, ''), vpBase = (k) => ({ key: k });
const isTime = () => S.kind === 'time', calSpec = () => false, barCloseMs = (b) => b.t + P;
let latest = null;
const vpLatest = () => latest;
const noted = () => [...(S.vpLive.aavp || new Map()).entries()].map(([k, v]) => [Number(k.split('|')[1]), v[0]]);
const bars = (spec, step, n) => {   // a new load (vpAll): n bars of `step` from 1000M
  S.spec = spec; P = step; S.vpLive = {};
  S.bars = Array.from({ length: n }, (_, i) => ({ t: 1000 * M + i * (step || M), seq: i }));
};
const load = (spec, step, n, now) => {   // ... and the answer, at the clock `now`
  bars(spec, step, n);
  NOW = now;
  latest = { poc: 7000, vah: 7001, val: 6999 };
  vpLiveNote('aavp');
  return noted().map((x) => x[0]);
};
const out = {};
out.inside = load('5m', 5 * M, 4, 1000 * M + 3 * P + 2.5 * M);    // 5 minutes, loaded in the forming bar's 3rd minute
S.bars.push({ t: 1000 * M + 4 * P, seq: 4 }); NOW = 1000 * M + 4 * P + 0.5 * M; latest = { ...latest, poc: 7002 };
vpLiveNote('aavp');                                                  // the next bar: its own only
out.next = noted();
delete S.vpLive.aavp; NOW += 2 * M; latest = { ...latest, poc: 7003 };
vpLiveNote('aavp');                                                  // a changed setting: loaded again
out.changed = noted();
out.lastMinute = load('5m', 5 * M, 4, 1000 * M + 3 * P + 4.5 * M);  // in the forming bar's last minute
out.closed = load('5m', 5 * M, 4, 1000 * M + 4 * P + 10 * M);       // the last bar closed a while ago (a halt, a weekend)
out.oneMinute = load('1m', M, 4, 1000 * M + 3 * M + 20e3);
out.hour = load('1h', 60 * M, 4, 1000 * M + 3 * 60 * M + 7.5 * M);
S.kind = 'tick';
out.tick = load('500t', 0, 4, 1000 * M + 3 * M + 20e3);
S.kind = 'time';
S.replay = { jumps: 1, at: 1000 * M + 3 * P + M }; S.vp.aavp.view = '1 false {}';
out.replay = load('5m', 5 * M, 4, 5000 * M);                        // the replay clock, not the wall clock
S.replay.at = 1000 * M + 3 * P + 4 * M;
out.replayLast = load('5m', 5 * M, 4, 1000 * M + 3 * P + M);
S.replay = null; S.vp.aavp.view = 'false false {}';
bars('5m', 5 * M, 4); NOW = 1000 * M + 3 * P + M; latest = null;
vpLiveNote('aavp');                                                  // no profile yet: the next answer is the load's
latest = { poc: 7000, vah: 7001, val: 6999 };
vpLiveNote('aavp');
out.waited = noted().map((x) => x[0]);
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["inside"] == [2, 3]
    assert out["next"] == [[2, 7000], [3, 7000], [4, 7002]]
    assert out["changed"] == [[3, 7003], [4, 7003]]
    assert out["lastMinute"] == [3] and out["closed"] == [3] and out["oneMinute"] == [3]
    assert out["hour"] == [2, 3] and out["tick"] == [3]
    assert out["replay"] == [2, 3] and out["replayLast"] == [3]
    assert out["waited"] == [2, 3]
