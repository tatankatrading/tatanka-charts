"""HTF EMA on a live chart: a bar that ran live keeps what its last call showed, as NinjaTrader's realtime bars do
(Calculate.OnPriceChange), where a reload shows each bar as of its close. A source bar that closes inside a chart bar
counts from the chart bar's next call (NinjaTrader runs the chart series first on the trade that opens the next
source bar); one that closed by the chart bar's open counts at once where both are Second or Minute bars
(barsTimeStamped). htfEmaLive in web/studies.js, and the page's use of it in web/app.js (each HTF EMA on a chart its
own, anew on a reload, settings change or toggle, none in bar replay or Smooth). tests/htfema_parity.py checks it
against the C# on a live chart, call by call and bar by bar."""
import json
import shutil
import subprocess

import pytest

from htfema_parity import WEB
from test_hunt_web_chart import app_part

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")

STUDIES = f"""
const vm = require('vm'), fs = require('fs'), ctx = {{}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(WEB / 'studies.js'))}, 'utf8')
  + ';this.f=htfEmaValues;this.live=htfEmaLive;this.keep=htfEmaKeep;', ctx);
const htfEmaValues = ctx.f, htfEmaLive = ctx.live, htfEmaKeep = ctx.keep;
"""


def node(js: str):
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


# 1-minute chart bars from T; the source's closed bars [close, EMA]; Exact unless said (Live: alpha 2/(3+1) = 0.5)
BARS_JS = STUDIES + """
const T = 1.8e12, M = 60e3, S15 = 15e3;
const D = (htf, extra = {}) => ({ scaled: 1, len: 3, mode: 'ExactHTF', htf, ends: [], smooth: null, due: 1, ...extra });
const bar = (i, c) => ({ t: T + i * M, c });
"""


@needs_node
@pytest.mark.parametrize("stamped", [True, False])
def test_a_live_bar_keeps_its_last_calls_value(stamped):
    """A source bar closing inside the forming bar after its last price change: the bar shows what that call showed,
    also once the bar has closed and later answers came; the next price change or bar counts it. A reload shows it in
    the bar it closed in. One closing at the new bar's open: counted once the answer has it where both are time-stamped
    (no new price needed), else from the bar's next call."""
    out = node(BARS_JS + f"""
const k = htfEmaKeep(T + M, {json.dumps(stamped)}), out = {{}};
const bars = [bar(0, 1), bar(1, 100)];
let d = D([[T + S15, 10], [T + 4 * S15, 12]]);                        // closed by bar 1's open
const show = (update) => htfEmaLive(k, bars, d, M, false, undefined, update);
out.load = show(false)[1];
out.call = show(true)[1];                                                // a trade at 100: a call
d = D([...d.htf, [T + M + S15, 13]]);                                   // the answer: a source bar closed inside bar 1
out.answer = show(false)[1];
out.samePrice = show(true)[1];                                          // a trade at 100 again: no call
bars[1] = bar(1, 101);
out.newPrice = show(true)[1];                                           // a new price: a call, counts it
d = D([...d.htf, [T + M + 3 * S15, 14]]);                               // another inside bar 1, no price after it
out.after = show(false)[1];
bars.push(bar(2, 102));
let v = show(true);                                                     // bar 2 opens: bar 1 has closed live
out.closed = [v[1], v[2]];
d = D([...d.htf, [T + 2 * M, 15]]);                                     // the answer: one closing at bar 2's open
v = show(false);
out.atOpen = [v[1], v[2]];
bars[2] = bar(2, 103);
out.next = show(true)[2];
d = D([...d.htf, [T + 2 * M + S15, 16]]);
out.later = show(false)[1];                                             // later answers leave the closed bar alone
out.reload = htfEmaValues(bars, d, M, false);                           // a reload: each bar as of its close
out.kept = [...k.live.entries()];
console.log(JSON.stringify(out));""")
    assert out["load"] == out["call"] == out["answer"] == out["samePrice"] == 12
    assert out["newPrice"] == out["after"] == 13
    assert out["closed"] == [13, 14]                                     # NinjaTrader's bar 1: 13; a reload's: 14
    assert out["atOpen"] == ([13, 15] if stamped else [13, 14])
    assert out["next"] == 15 and out["later"] == 13
    assert out["reload"] == [10, 14, 16]
    assert out["kept"] == [[1.8e12 + 60e3, 13]]


@needs_node
def test_a_live_mode_bar_keeps_its_last_calls_price_too():
    """Live (one EMA step with the bar's price): the kept value is the last call's, at that call's price."""
    out = node(BARS_JS + """
const k = htfEmaKeep(T + M, true), bars = [bar(0, 1), bar(1, 100)];
let d = D([[T + 4 * S15, 12]], { mode: 'LiveHTF' });
const show = (update) => htfEmaLive(k, bars, d, M, false, undefined, update);
show(false);
bars[1] = bar(1, 104);
const call = show(true)[1];
d = D([...d.htf, [T + M + S15, 20]], { mode: 'LiveHTF' });
const answer = show(false)[1];
bars.push(bar(2, 104));
const closed = show(true)[1];
console.log(JSON.stringify({ call, answer, closed, reload: htfEmaValues(bars, d, M, false)[1] }));""")
    assert out == {"call": 58, "answer": 58, "closed": 58, "reload": 62}   # 0.5 * 104 + 0.5 * 12 (20 for a reload)


@needs_node
@pytest.mark.parametrize("stamped", [True, False])
def test_a_close_the_server_had_with_the_market_shut_waits_for_the_trade_after_it(stamped):
    """Loaded in the daily halt, the server has the 16:00 source bar closed (no source bar forming: due none) though
    no trade came after it. The session's first trade is a call before the source series takes that close: not
    counted then, but where both series are time-stamped and the bar opened after it (NinjaTrader's catch-up)."""
    out = node(BARS_JS + f"""
const k = htfEmaKeep(T - 61 * M, {json.dumps(stamped)}), bars = [bar(-61, 1)];   // the last bar before the close
let d = D([[T - 120 * M, 10], [T - 60 * M, 11]], {{ due: null }});        // closed at the close; none forming
const show = (update) => htfEmaLive(k, bars, d, M, false, undefined, update);
const out = {{ load: show(false)[0] }};
bars.push(bar(0, 1));
out.first = show(true)[1];                                              // the session's first trade
d = D(d.htf, {{ due: T + M }});                                         // the answer: that trade's
out.answer = show(false)[1];
bars[1] = bar(0, 2);
out.next = show(true)[1];
console.log(JSON.stringify(out));""")
    assert out == {"load": 10, "first": 11 if stamped else 10, "answer": 11 if stamped else 10, "next": 11}


@needs_node
def test_kept_values_stay_with_their_bars_when_bars_come_or_go_at_the_left():
    """Older bars loaded at the left (and a redraw with the answer for the new first bar) leave the kept values with
    their bars; bars trimmed from the left take theirs with them."""
    out = node(BARS_JS + """
const k = htfEmaKeep(T + M, true);
let bars = [bar(0, 1), bar(1, 100)], d = D([[T + 4 * S15, 12]]);
const show = (update) => htfEmaLive(k, bars, d, M, false, undefined, update);
show(true);
d = D([...d.htf, [T + M + S15, 13]]);
show(false);
bars.push(bar(2, 102));
show(true);                                                             // bar 1 keeps 12 (a reload: 13)
bars = [bar(-2, 1), bar(-1, 1), ...bars];                                // older bars
d = D([[T - 2 * M + S15, 5], ...d.htf]);
const older = show(false);
bars = bars.slice(4);                                                   // trimmed past bar 1
bars.push(bar(3, 104));
show(true);
console.log(JSON.stringify({ older, kept: [...k.live.keys()].map((t) => (t - T) / M) }));""")
    assert out["older"] == [5, 5, 5, 12, 13]
    assert out["kept"] == [2]


# ---- the page --------------------------------------------------------------------------------------------------------

PAGE_PARTS = ["hemaSetOf", "hemaOf", "hemaReset", "hemaClear", "hemaPeriodMs", "hemaCal", "hemaStamped", "hemaAfter",
              "hemaPoint", "hemaVals", "hemaDraw", "hemaLive", "hemaRequest", "hemaTick"]
PAGE_JS = STUDIES + """
let clock = 1.8e12 + 600e3;
Date.now = () => clock;
const T = 1.8e12, M = 60e3, asked = [];
const S = { st: { hema: true, hema_2: true }, symbol: 'ES', spec: '1m', rth: false, replay: null, instruments: { ES: {} },
  bars: [{ t: T, c: 1 }, { t: T + M, c: 100 }], priceFormat: null,
  hemaSets: { hema: { HtfPeriodType: 'Second', HtfPeriod: 15, EmaLength: 3, Mode: 'ExactHTF' },
    hema_2: { HtfPeriodType: 'Day', HtfPeriod: 1, EmaLength: 3, Mode: 'ExactHTF' } }, hx: {} };
const chart = { addSeries: () => ({ applyOptions() {}, setData(p) { this.pts = p; }, update(p) { this.last = p; } }) };
const L = { LineSeries: 0 }, lineOpts = {}, hemaStyle = () => ({}), renderHemaInfo = () => {}, indReady = () => true;
const timeOf = (b) => b.t, isTime = () => true, specSeconds = () => 60, instKeys = () => Object.keys(S.hemaSets);
const hemaEach = (fn) => instKeys('hema').map((k) => fn(k));
let answers = {}, slow = null;   // the server: each one's answer, by its key; slow: one that answers later
const api = async (method, path) => {
  const q = new URLSearchParams(path.split('?')[1]), k = q.get('HtfPeriodType') === 'Day' ? 'hema_2' : 'hema';
  asked.push([k, clock, q.get('HtfPeriod')]);
  if (slow) { let release; const p = new Promise((ok, no) => { release = { ok, no }; }); slow.push(release); return p; }
  return answers[k];
};
const D = (htf, extra = {}) => ({ scaled: 1, len: 3, mode: 'ExactHTF', htf, ends: [], smooth: null, due: T + 3 * M, ...extra });
/*APP*/
const settle = () => new Promise((r) => setTimeout(r, 5));
"""


def page(js: str):
    return node(PAGE_JS.replace("/*APP*/", "\n".join(app_part(p) for p in PAGE_PARTS)) + js)


@needs_node
def test_each_htf_ema_on_a_chart_keeps_its_own_and_starts_anew():
    """Two on a chart (a Second and a Day source): each keeps what its own live bars showed. A new load (hemaClear),
    one's settings changed or it toggled (hemaReset): its bars as a reload shows them, the other's kept."""
    out = page("""
(async () => {
  const out = {};
  answers = { hema: D([[T - 15e3, 11], [T + M, 12]]), hema_2: D([[T - 600 * M, 50]]) };
  hemaClear();
  await hemaRequest();
  S.bars[1] = { t: T + M, c: 101 };
  hemaTick(false);                                                     // a call on bar 1
  answers = { hema: D([[T - 15e3, 11], [T + M, 12], [T + M + 15e3, 13]]), hema_2: D([[T - 600 * M, 50], [T + M + 15e3, 51]]) };
  await hemaRequest();                                                 // an answer: closed inside bar 1
  S.bars.push({ t: T + 2 * M, c: 102 });
  hemaTick(true);                                                      // bar 2: bar 1 closed live
  await settle();
  out.closed = ['hema', 'hema_2'].map((k) => hemaOf(k).vals);
  hemaReset('hema');                                                   // its settings changed
  await hemaRequest('hema');
  out.reset = ['hema', 'hema_2'].map((k) => hemaOf(k).vals);
  hemaClear();                                                         // a new load (another symbol, timeframe, reload)
  await hemaRequest();
  out.clear = ['hema', 'hema_2'].map((k) => [hemaOf(k).vals, hemaOf(k).keep.live.size, hemaOf(k).keep.last === T + 2 * M]);
  console.log(JSON.stringify(out));
})();""")
    assert out["closed"] == [[11, 12, 13], [50, 50, 51]]   # each bar 1 kept its call's (a reload: 13 and 51)
    assert out["reset"] == [[11, 13, 13], [50, 50, 51]]
    assert out["clear"] == [[[11, 13, 13], 0, True], [[50, 51, 51], 0, True]]


@needs_node
def test_replay_and_smooth_bars_show_what_a_reload_shows():
    """Bar replay steps whole bars, each at its close (no trades inside it to have a last call): as a reload, nothing
    kept. Smooth (this chart's own EMA) does not depend on source bars closing: nothing kept either."""
    out = page("""
(async () => {
  const out = {};
  delete S.hemaSets.hema_2;
  answers = { hema: D([[T - 15e3, 11], [T + M, 12]]) };
  hemaClear();
  await hemaRequest();
  S.replay = { data: [], idx: 0, at: T + 2 * M };
  S.bars[1] = { t: T + M, c: 101 };
  hemaTick(false);
  answers = { hema: D([[T - 15e3, 11], [T + M, 12], [T + M + 15e3, 13]]) };
  await hemaRequest();
  S.bars.push({ t: T + 2 * M, c: 102 });
  hemaTick(true);
  out.replay = [hemaOf().vals, hemaOf().keep.live.size, hemaOf().keep.call];
  S.replay = null;
  hemaClear();
  answers = { hema: D([[T + M, 12]], { mode: 'SmoothScaled' }) };
  await hemaRequest();
  S.bars.push({ t: T + 3 * M, c: 103 });
  hemaTick(true);
  out.smooth = [hemaOf().keep.live.size, hemaOf().keep.call];
  console.log(JSON.stringify(out));
})();""")
    assert out["replay"] == [[11, 13, 13], 0, None]
    assert out["smooth"] == [0, None]


@needs_node
def test_a_second_source_asks_once_each_source_bar_has_closed():
    """Inside a forming bar a Second source asks again once each source bar has closed (the answer's due, not asked
    for since), at most every 10 seconds, so the next price change counts it as NinjaTrader's does: 14.5 s after the
    last ask on a 15 s source (once every 15 s would wait for the next trade after 15 s)."""
    out = page("""
(async () => {
  delete S.hemaSets.hema_2;
  const at = async (t, due) => { clock = t; if (due) answers.hema = D([], { due }); hemaTick(false); await settle(); return asked.length; };
  answers = { hema: D([], { due: T + 615e3 }) };
  await hemaRequest();                                                // asked at 600 s: the source bar closes at 615 s
  const out = [await at(T + 616e3, T + 630e3)];                        // past it: asks; the next closes at 630 s
  out.push(await at(T + 625e3));                                       // not due
  out.push(await at(T + 630.5e3, T + 630e3));                          // due, 14.5 s since: asks (no trade after 630 s
  out.push(await at(T + 631e3));                                       // at the server yet): that due asked for
  out.push(await at(T + 645.6e3, T + 660e3));                          // 15 s since: asks (the clocks can differ)
  out.push(await at(T + 660.5e3, T + 665e3));                          // due, 14.9 s since: asks
  out.push(await at(T + 665.5e3, T + 680e3));                          // a new due, 5 s since: waits for 10 s
  out.push(await at(T + 670.6e3));
  console.log(JSON.stringify(out));
})();""")
    assert out == [2, 2, 3, 3, 4, 5, 5, 6]


@needs_node
@pytest.mark.parametrize("source", [("Minute", 1), ("Minute", 5), ("Day", 1)])
def test_any_source_asks_on_the_trade_after_its_close(source):
    """Not only a Second source: inside a forming bar a Minute or Day source asks again on the first trade after its
    close too (once for that close), though the last ask was less than a minute before (the source closed 55 s after
    it; it waited for a minute since, a trade or more late, and the chart bar's price change on that later trade kept
    the value without it where NinjaTrader's counts it)."""
    kind, n = source
    out = page(f"""
(async () => {{
  delete S.hemaSets.hema_2;
  S.hemaSets.hema = {{ HtfPeriodType: '{kind}', HtfPeriod: {n}, EmaLength: 3, Mode: 'ExactHTF' }};
  const at = async (t, due) => {{ clock = t; if (due !== undefined) answers.hema = answers.hema_2 = D([], {{ due }}); hemaTick(false); await settle(); return asked.length; }};
  clock = T + 605e3;
  answers = {{ hema: D([], {{ due: T + 660e3 }}) }};
  answers.hema_2 = answers.hema;                                       // (the scaffold's server: a Day source's key)
  await hemaRequest();                                                 // asked 5 s after the last close
  const out = [await at(T + 661e3, T + 720e3)];                        // the trade after the next close: asks
  out.push(await at(T + 668e3));                                       // not due
  out.push(await at(T + 721e3, T + 720e3));                            // due, the server without the trade yet: asks
  out.push(await at(T + 725e3));                                       // that due asked for: waits
  out.push(await at(T + 782e3));                                       // a minute since: asks (the clocks differ)
  console.log(JSON.stringify(out));
}})();""")
    assert out == [2, 2, 3, 3, 4]


@needs_node
@pytest.mark.parametrize("how", ["settings", "error", "reload"])
def test_an_answer_asked_for_before_a_reset_is_dropped(how):
    """A source, length or mode change (also a toggle, a tab switch to other settings, settings saved on another
    device) while the old settings' answer is on its way: that answer is dropped, neither drawn nor kept on the bars
    that close before the new settings' answer, which is asked for once it is in (a failed one shows no error). A
    reload of the same chart meanwhile drops it the same way (it was asked for the bars before)."""
    out = page("""
(async () => {
  delete S.hemaSets.hema_2;
  const out = {};
  answers = { hema: D([[T - 15e3, 10], [T + M, 12]]) };
  hemaClear();
  await hemaRequest();
  slow = [];
  const inflight = hemaRequest('hema');                                // the old settings' ask, slow to answer
  if ('""" + how + """' === 'reload') hemaClear();                     // a reload of this chart: hemaClear, hemaRequest
  else { S.hemaSets.hema = { ...S.hemaSets.hema, HtfPeriod: 30 }; hemaReset('hema'); }   // indApply: moved
  hemaRequest('hema');                                                 // busy: asked again once it answers
  if ('""" + how + """' === 'error') slow[0].no(new Error('Gateway Timeout'));
  else slow[0].ok(D([[T - 15e3, 10], [T + M, 12], [T + M + 15e3, 1000]]));
  await inflight; await settle();
  out.between = [hemaOf().data, hemaOf().vals, hemaOf().error, slow.length];
  S.bars[1] = { t: T + M, c: 101 };
  hemaTick(false);                                                     // a call on bar 1
  S.bars.push({ t: T + 2 * M, c: 102 });
  hemaTick(true);                                                      // bar 2: bar 1 closed live
  await settle();
  slow[1].ok('""" + how + """' === 'reload' ? D([[T - 15e3, 10], [T + M, 12], [T + M + 15e3, 13]])
    : D([[T - 30e3, 20], [T + M, 22], [T + M + 30e3, 23]]));
  await settle(); await settle();
  out.asked = asked.map((a) => a[2]);
  out.final = hemaOf().vals;
  out.reload = htfEmaValues(S.bars, hemaOf().data, M, false);
  out.kept = hemaOf().keep.live.size;
  console.log(JSON.stringify(out));
})();""")
    assert out["between"] == [None, [], "", 2]
    assert out["asked"] == ["15", "15", "15" if how == "reload" else "30"]
    assert out["final"] == out["reload"] == ([10, 13, 13] if how == "reload" else [20, 23, 23])
    assert out["kept"] == 0
