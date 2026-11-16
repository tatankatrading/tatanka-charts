"""1hr 21 EMA port: settings, scaled length, the server payload and the browser code (node). Parity with the
original C# is checked by tests/htfema_parity.py (needs mono; its live mode as NinjaTrader runs a live chart)."""
import bisect
import json
import shutil
import subprocess
from datetime import datetime

import pytest

from amt_parity import synth
from chartsrv import htfema
from chartsrv.amt_runner import epoch_ms
from chartsrv.tpo_service import chart_bars
from htfema_parity import WEB, js_values
from test_hunt_web_chart import run_app

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def test_settings_and_scaled_length():
    s = htfema.settings({})
    assert (s["HtfPeriod"], s["EmaLength"], s["Mode"]) == (60, 21, "SmoothScaled")
    assert htfema.GetScaledLength(s, "Minute", 5) == 252 and htfema.GetScaledLength(s, "Minute", 1) == 1260
    assert htfema.GetScaledLength(s, "Tick", 2000) == 21                        # untimed charts: the plain length
    assert htfema.GetScaledLength(htfema.settings({"EmaLength": 3}), "Minute", 8) == 22   # 22.5: to even, as C#
    assert htfema.GetScaledLength(s, "Day", 1) == 1
    for bad in [{"Mode": "Fast"}, {"HtfPeriodType": "Tick"}, {"HtfPeriod": 0}, {"EmaLength": 2**31}, {"Nope": 1}]:
        with pytest.raises(ValueError):
            htfema.settings(bad)


def test_nt_ema_seeds_with_the_first_value():
    e = htfema.NtEma(3)
    assert [e.add(x) for x in (10, 14, 14)] == [10, 12, 13]


def _payload(m1, s, chart_min, start):
    scaled = htfema.GetScaledLength(s, "Minute", chart_min)
    return htfema.compute(s, m1, chart_min, scaled, start, m1[-1].t + 60_000)


@needs_node
@pytest.mark.parametrize("mode", htfema.MODES)
def test_chart_starting_mid_history_matches_a_full_run(mode):
    """The chart holds only recent bars: the server's seed and HTF timeline make it draw what a chart with all
    the history would."""
    s = htfema.settings({"Mode": mode})
    m1 = synth(30, 6, datetime(2026, 10, 16, 12, 7))
    bars = [{"t": b[0], "c": b[6]} for b in chart_bars(m1, 5)]
    full = js_values(bars, _payload(m1, s, 5, bars[0]["t"]), 300_000)
    cut = 3000
    part = js_values(bars[cut:], _payload(m1, s, 5, bars[cut]["t"]), 300_000)
    assert part == full[cut:] and all(v is not None for v in part)


def test_payload_shape_and_window():
    s = htfema.settings({})
    m1 = synth(10, 2, datetime(2026, 10, 16, 12, 7))
    d = _payload(m1, s, 5, m1[-600].t)
    assert d["scaled"] == 252 and d["smooth"]["count"] > 252 and d["htf"][0][0] < m1[-600].t <= d["htf"][1][0]
    assert all(v is not None for _, v in d["htf"])
    assert htfema.history_ms(s, 5, 252, 10**13, 10**13) == 10**13 - 14 * 86_400_000   # 8 lengths of 1h and of 252x5m


def _thu(h, m=0, sec=0):
    return epoch_ms(datetime(2026, 10, 15, h, m, sec))


def test_an_htf_bar_closes_with_the_first_trade_after_it():
    """1.0.1: NinjaTrader's source series closes an hour with the first trade after it (its next bar, or a chart bar
    opening past it). The server counts it closed then, whatever its own clock says; at the session's close, where no
    trade can come, by its clock. held: the clock is past an hour no trade has closed yet."""
    s = htfema.settings({"Mode": "ExactHTF"})
    m1 = synth(5, 4, datetime(2026, 10, 15, 20, 7))   # to Thursday 20:06 CT
    upto = lambda t: [m for m in m1 if m.t <= t]     # noqa: E731  (the minutes traded in by t)
    last = lambda d: d["htf"][-1][0]                 # noqa: E731
    d = htfema.compute(s, upto(_thu(13, 59)), 5, 252, _thu(9), _thu(14, 0, 1))   # 14:00:01, no trade since 13:59
    assert last(d) == _thu(13) and d["due"] == _thu(14) and d["held"]
    d = htfema.compute(s, upto(_thu(14)), 5, 252, _thu(9), _thu(13, 59, 59))     # a 14:00 trade, the clock behind it
    assert last(d) == _thu(14) and d["due"] == _thu(15) and not d["held"]
    d = htfema.compute(s, upto(_thu(14, 30)), 5, 252, _thu(9), _thu(14, 30, 1))   # the 14:00 hour forming
    assert last(d) == _thu(14) and d["due"] == _thu(15) and not d["held"]
    d = htfema.compute(s, upto(_thu(15, 59)), 5, 252, _thu(9), _thu(16, 0, 30))   # the session's close
    assert last(d) == _thu(16) and d["due"] is None and not d["held"]
    rth = ("08:30", "15:00")                          # an RTH chart: its hours count from 08:30, end at 15:00
    d = htfema.compute(s, upto(_thu(13, 29)), 5, 252, _thu(9), _thu(13, 30, 1), rth=rth)
    assert last(d) == _thu(12, 30) and d["due"] == _thu(13, 30) and d["held"]
    d = htfema.compute(s, upto(_thu(14, 59)), 5, 252, _thu(9), _thu(15, 0, 1), rth=rth)
    assert last(d) == _thu(15) and d["due"] is None and not d["held"]


@needs_node
@pytest.mark.parametrize("mode", ["ExactHTF", "LiveHTF"])
@pytest.mark.parametrize("chart_min", [5, 240])
def test_a_live_chart_shows_the_last_closed_hour(mode, chart_min):
    """A live chart's forming bar, minute by minute through six hour closes and the daily halt, with the server's
    answer then: Exact is the EMA through the last closed hour, never the forming one's (1.0.0 took that one in
    realtime, moving with every price and unlike a reload), held still while an hour forms and stepping with the
    first trade after it, inside a 4h bar too; Live is one EMA step from it at the chart's price."""
    s = htfema.settings({"Mode": mode})
    m1 = synth(10, 4, datetime(2026, 10, 15, 20, 7))   # to Thursday 20:06 CT
    hours = chart_bars(m1, 60)
    ema = htfema.NtEma(s["EmaLength"])
    through = [None] + [ema.add(h[6]) for h in hours]   # through[n]: the EMA through the first n hours
    alpha, scaled = 2.0 / (s["EmaLength"] + 1.0), htfema.GetScaledLength(s, "Minute", chart_min)
    cb = chart_bars(m1, chart_min)
    bars, opens = [{"t": b[0], "c": b[6]} for b in cb], [b[0] for b in cb]
    answers, which, queries, want = [], {}, [], []
    for i in range(bisect.bisect_left([m.t for m in m1], _thu(13)), len(m1)):
        m = m1[i]                                    # its trades so far: the last at its close
        n = sum(h[1] <= m.t for h in hours)          # the hours a trade has come after: closed
        if n not in which:
            which[n] = len(answers)
            answers.append(htfema.compute(s, m1[:i + 1], chart_min, scaled, bars[0]["t"], m.t + 30_000))
        assert answers[which[n]]["due"] == hours[n][1] and not answers[which[n]]["held"]
        queries.append([bisect.bisect_right(opens, m.t) - 1, m.c, which[n]])
        want.append(through[n] if mode == "ExactHTF" else alpha * m.c + (1.0 - alpha) * through[n])
    js = f"""
const vm = require('vm'), fs = require('fs'), ctx = {{}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(WEB / 'studies.js'))}, 'utf8') + ';this.f=htfEmaValues;', ctx);
const a = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(a.q.map(([j, p, n]) => {{
  const bs = a.bars.slice(0, j + 1);
  bs[j] = {{ t: bs[j].t, c: p }};
  return ctx.f(bs, a.d[n], a.p)[j];
}})));"""
    data = json.dumps({"bars": bars, "d": answers, "p": chart_min * 60_000, "q": queries})
    got = json.loads(subprocess.run(["node", "-e", js], input=data, capture_output=True, text=True, check=True).stdout)
    assert len(answers) == 7 and got == want   # 13:00 to 20:06: the hours closing at 14, 15, 16, 18, 19 and 20


HELD_JS = """
let clock = 1.8e12;
Date.now = () => clock;
const asked = [], t0 = clock;
const S = { st: { hema: true }, symbol: 'ES', spec: '4h', bars: [{ t: clock - 7200e3, c: 1 }], instruments: { ES: {} }, replay: null,
  hemaSet: { HtfPeriodType: 'Minute', HtfPeriod: 60, EmaLength: 21, Mode: 'ExactHTF' }, hema: null };
const indReady = () => true, hemaDraw = () => {}, hemaLive = () => {};
let answer = { due: t0 + 3600e3, held: false };
const api = async (method, path) => { asked.push(path); return { mode: S.hemaSet.Mode, htf: [], ...answer }; };
/*APP*/
const at = async (t) => { clock = t; hemaTick(false); await new Promise((r) => setTimeout(r, 5)); return asked.length; };
(async () => {
  await hemaRequest();   // the history
  const out = {};
  answer = { due: t0 + 3600e3, held: true };      // the server: past the hour, but no trade after it yet
  out.closed = await at(t0 + 3600.2e3);           // a trade just after the hour (the 4h bar goes on)
  out.trade = await at(t0 + 3600.4e3);            // held: every trade asks, not once a minute
  answer = { due: t0 + 7200e3, held: false };     // the server has the trade that closed the hour
  out.trade2 = await at(t0 + 3600.6e3);
  out.after = await at(t0 + 3601e3);              // the next hour forming: no more
  answer = { due: t0 + 7200e3, held: true };
  out.next = await at(t0 + 7200.1e3);             // the next hour's close
  out.late = await at(t0 + 7270e3);               // held over a minute (no trade the server has): once a minute
  out.stuck = await at(t0 + 7280e3);
  console.log(JSON.stringify(out));
})();"""


@needs_node
def test_a_held_hour_asks_again_with_each_trade():
    """The server holds an hour past its close until a trade after it comes (held); a 4h chart's forming bar then asks
    again on each trade, so it steps with the trade that closes the hour, as NinjaTrader 1.0.1 does, not a minute on."""
    out = run_app(["hemaEach", "hemaRequest", "hemaTick"], HELD_JS)
    assert out == {"closed": 2, "trade": 3, "trade2": 4, "after": 4, "next": 5, "late": 6, "stuck": 6}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_FEED", "sim")
    monkeypatch.setenv("CHART_SIM_WARMUP_MIN", "300")
    from fastapi.testclient import TestClient
    from chartsrv.app import app
    with TestClient(app) as c:
        yield c


def test_htfema_endpoint(client):
    import time
    start = int(time.time() * 1000) - 3 * 3600_000
    r = client.get("/api/htfema", params={"symbol": "es", "spec": "5m", "start": start})
    assert r.status_code == 200 and r.json()["scaled"] == 252 and r.json()["smooth"] is not None
    r = client.get("/api/htfema", params={"symbol": "ES", "spec": "2000t", "start": start, "Mode": "LiveHTF"})
    assert r.status_code == 200 and r.json()["scaled"] == 21 and r.json()["smooth"] is None and r.json()["ends"] == []
    for spec in ("8k", "1W", "1M"):   # Renko was a KeyError (500)
        r = client.get("/api/htfema", params={"symbol": "ES", "spec": spec, "start": start})
        assert r.status_code == 200, spec
    for params, code in [({"Mode": "Fast"}, 400), ({"HtfPeriodType": "Range"}, 400), ({"EmaLength": 0}, 400),
                         ({"spec": "xx"}, 400), ({"symbol": "ZZ"}, 404)]:
        q = {"symbol": "ES", "spec": "5m", "start": start, **params}
        assert client.get("/api/htfema", params=q).status_code == code, params
