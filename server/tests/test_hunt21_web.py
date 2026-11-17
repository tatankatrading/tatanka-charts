"""Round 21's bug hunt of round 20's chart page changes (web/app.js, studies.js, layout.js) and the free-data bars'
answer for a source that didn't answer (ext.py, app.py): each fixed. The app.js parts run in node with what they call
stubbed."""
import asyncio
import json
import subprocess

import httpx
import pytest

from chartsrv import ext
from chartsrv.feeds.fred import SourceDown
from test_amt import client  # noqa: F401  (the app on the simulator, as a fixture)
from test_hunt_web_chart import APP, WEB, app_lines, app_part, needs_node, run_app


def _lines(first: str, last: str) -> str:
    """The lines of web/app.js from the one starting with `first` to the next one that is exactly `last`."""
    start = next(i for i, line in enumerate(APP) if line.startswith(first))
    return "\n".join(APP[start:next(i for i in range(start, len(APP)) if APP[i] == last) + 1])


def _node(js: str):
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


# ---- c06: a zoom keeps the view it made; the AMT margin's cap no longer scrolls the bar under the cursor away ---------

@needs_node
def test_a_zoom_with_the_amt_margin_keeps_its_view_unless_the_empty_space_is_over_the_cap():
    """The visible-range subscription runs amtLayout after each wheel step. Under about 183 bars in view each step lowers
    the cap (30% of the view) and applying the new margin scrolled the last bar to it, undoing the library's zoom on the
    cursor: the bar zoomed on slid off the left. Now a zoom keeps its view, unless the space right of the last bar is
    over the cap (zoomed in that space: the last bar comes in to it); turning a study on still scrolls to its margin."""
    out = run_app(["AMT_ROOM", "rightMargin", "amtLayout"], """
const n = 1000;
let range, opts;
const ts = { options: () => ({ ...opts, barSpacing: 6 }), getVisibleLogicalRange: () => ({ ...range }), setVisibleLogicalRange(r) { range = { ...r }; },
  applyOptions(o) {   // as the library: the right offset is the scroll position, the last bar that many bars from the edge
    if (!('rightOffset' in o)) return;
    opts.rightOffset = o.rightOffset;
    const w = range.to - range.from;
    range = { from: n - 1 + o.rightOffset - w, to: n - 1 + o.rightOffset };
  } };
const chart = { timeScale: () => ts }, window = { innerWidth: 1500 };
const S = { cs: { rightBars: 6 }, st: { amt: true, ifthen: false, dsma: false }, ext: null, ifSet: {}, ifRows: [], dsmaSet: {}, bars: Array(n) };
/*APP*/
const run = (r, ro, zoomed) => { range = { ...r }; opts = { rightOffset: ro }; amtLayout(zoomed); return [range.from, range.to, opts.rightOffset]; };
console.log(JSON.stringify([
  run({ from: 870, to: 1020 }, 55, true),   // zoomed in on a bar mid-chart (21 bars of space, cap 45)
  run({ from: 920, to: 1070 }, 55, true),   // zoomed in the space right of the last bar (71 bars of it)
  run({ from: 700, to: 1005 }, 6, false),   // the AMT just turned on: its margin
  run({ from: 500, to: 650 }, 55, false),   // scrolled back: stays
]));""")
    assert out == [[870, 1020, 45], [894, 1044, 45], [749, 1054, 55], [500, 650, 45]]


@needs_node
def test_a_resize_sets_the_amt_margin_and_only_a_zoom_keeps_its_view():
    """Every range change ran amtLayout as a zoom, a resize too: a phone turned to landscape (the AMT margin 25 bars to
    55) kept its 26 bars of space, and the margin option, now 55, stopped every later amtLayout from setting it. A zoom
    is now a range change with a new bar spacing on the same width; a new width sets the margin as a load does."""
    out = run_app(["AMT_ROOM", "amtRoomFrame", "amtRoomCheck", "rightMargin", "amtLayout"], """
const n = 1000;
let width = 390, spacing = 2, range, opts = { rightOffset: 25 };
const ts = { width: () => width, options: () => ({ ...opts, barSpacing: spacing }), getVisibleLogicalRange: () => ({ ...range }),
  setVisibleLogicalRange(r) { range = { ...r }; spacing = width / (r.to - r.from); },
  applyOptions(o) {   // as the library: the right offset is the scroll position, the last bar that many bars from the edge
    if (!('rightOffset' in o)) return;
    opts.rightOffset = o.rightOffset;
    range = { from: n - 1 + o.rightOffset - width / spacing, to: n - 1 + o.rightOffset };
  } };
const chart = { timeScale: () => ts }, window = { innerWidth: 390 };
const S = { cs: { rightBars: 6 }, st: { amt: true, ifthen: false, dsma: false }, ext: null, ifSet: {}, ifRows: [], dsmaSet: {}, bars: Array(n) };
/*APP*/
const at = () => [+(range.to - (n - 1)).toFixed(1), opts.rightOffset];   // the space right of the last bar, the option
range = { from: n - 1 + 26 - 195, to: n - 1 + 26 };   // a phone upright: 26 bars of space
amtRoomCheck();
const out = [at()];
width = 844; window.innerWidth = 844;   // turned: the library keeps the bar spacing and the space, the view widens
range = { from: range.to - width / spacing, to: range.to };
amtRoomCheck();
out.push(at());
spacing = 8; range = { from: n - 1 + 20 - width / 8, to: n - 1 + 20 };   // zoomed in on a bar (cap 31): kept
amtRoomCheck();
out.push(at());
range = { from: range.from - 5, to: range.to - 5 };   // scrolled: the same cap, nothing set
amtRoomCheck();
out.push(at());
console.log(JSON.stringify(out));""")
    assert out == [[26, 25], [55, 55], [20, 31], [15, 31]]


# ---- m05: Reset view and a timeframe's first load set the margin as a load does, after a zoom too -------------------

@needs_node
def test_reset_view_and_a_first_load_after_a_zoom_are_not_a_zoom():
    """Reset view (Alt+R) and a timeframe's first load set 150 bars and the margin option as it is: after a zoom that
    is the zoom's lower cap, so the bar spacing changes on the same width and amtRoomCheck took it for a wheel zoom,
    keeping the short view (31 bars of room where the margin is 55). A range the page itself sets for a load is not a
    zoom now: the margin is set as on a fresh load. A wheel zoom right after still keeps the view it made, also after
    a Reset that set the range it already had (the library then sends no range change)."""
    load = app_lines("    const ro = chart.timeScale().options().rightOffset;", "    chart.timeScale().setVisibleLogicalRange(")
    out = run_app(["AMT_ROOM", "amtRoomFrame", "amtRoomCheck", "rightMargin", "latestRange", "amtLayout", "resetView"], """
let n = 1000, width = 1200, spacing = 1, range = { from: 0, to: 1 }, opts = { rightOffset: 55 };
const ts = { width: () => width, options: () => ({ ...opts, barSpacing: spacing }), getVisibleLogicalRange: () => ({ ...range }),
  setVisibleLogicalRange(r) { range = { ...r }; spacing = width / (r.to - r.from); },
  applyOptions(o) {   // as the library: the right offset is the scroll position, the last bar that many bars from the edge
    if (!('rightOffset' in o)) return;
    opts.rightOffset = o.rightOffset;
    range = { from: n - 1 + o.rightOffset - width / spacing, to: n - 1 + o.rightOffset };
  } };
const chart = { timeScale: () => ts }, window = { innerWidth: 1500 }, setAuto = () => {};
const S = { cs: { rightBars: 6 }, st: { amt: true, ifthen: false, dsma: false }, ext: null, ifSet: {}, ifRows: [], dsmaSet: {}, bars: Array(n) };
/*APP*/
const frame = () => {   // the library's range change, then amtRoomCheck on the next frame, until the range holds
  for (let i = 0; i < 3; i++) { const r = JSON.stringify(range); amtRoomCheck(); if (JSON.stringify(range) === r) break; }
};
const at = () => [+(range.to - (n - 1)).toFixed(1), opts.rightOffset];   // the space right of the last bar, the option
const wheel = () => { spacing = 12; range = { from: n - 1 + 20 - width / 12, to: n - 1 + 20 }; frame(); };   // on a bar (cap 30)
const firstLoad = (bars) => {   // applyHistory with no view to restore
  n = bars; S.bars = Array(n);
  const c = S.bars;
  {
/*LOAD*/
  }
  frame();
};
const out = {};
firstLoad(1000);
out.load = at();   // 150 bars and the margin (the last bar's own space too)
wheel();
out.zoomed = at();
resetView(); frame();
out.reset = at();
resetView(); frame();
out.resetAgain = at();
const was = JSON.stringify(range);
resetView();   // the range it has already: the library sends no range change
out.sameRange = JSON.stringify(range) === was;
wheel();
out.zoomedAfterSameRange = at();
firstLoad(1200);   // another timeframe, opened for the first time
out.firstLoad = at();
console.log(JSON.stringify(out));""".replace("/*LOAD*/", load))
    assert out == {"load": [56, 55], "zoomed": [20, 30], "reset": [55, 55], "resetAgain": [56, 55], "sameRange": True,
                   "zoomedAfterSameRange": [20, 30], "firstLoad": [55, 55]}


# ---- c07: a source that didn't answer is 503 (asked again), not 400 (a refusal, not asked again) ---------------------

def _ext_market(tmp_path, monkeypatch, answer):
    from test_review_fixes import build_market
    return build_market(tmp_path, monkeypatch, http=httpx.AsyncClient(transport=httpx.MockTransport(answer)))[0]


def test_yahoo_or_fred_not_answering_is_a_passing_failure_and_a_refusal_is_not(tmp_path, monkeypatch):
    """Every ValueError of /api/ext/bars was a 400, and Compare stops asking after a 400 (round 20, for AAPL on 30 s):
    one Yahoo timeout, connect error or 429 left the line empty (or frozen) for good. A source that didn't answer is
    now SourceDown (503 from the API, asked again), through the IBKR/Alpaca message and a formula's part too."""
    mode = {"yahoo": "down"}

    def answer(req):
        if "finance.yahoo.com" in req.url.host:
            if mode["yahoo"] == "down":
                raise httpx.ConnectError("no route", request=req)
            return httpx.Response(429 if mode["yahoo"] == "429" else 404)
        raise httpx.ConnectError("no route", request=req)   # FRED
    m = _ext_market(tmp_path, monkeypatch, answer)

    async def go():
        got = {}
        for sym, spec, how in (("AAPL", "1D", "down"), ("AAPL", "1h", "429"), ("AAPL", "1m", "404"), ("AAPL", "30s", "down"),
                               ("FRED:DGS10", "1D", "down"), ("FRED:DGS10", "5m", "down")):
            mode["yahoo"] = how
            try:
                await m.ext.bars(sym, spec)
            except ValueError as e:
                got[f"{sym} {spec} {how}"] = (isinstance(e, SourceDown), str(e))
        return got
    got = asyncio.run(go())
    assert got["AAPL 1D down"] == (True, "Yahoo did not answer (ConnectError)")
    assert got["AAPL 1h 429"] == (True, "Yahoo did not answer (HTTPStatusError)")
    assert got["AAPL 1m 404"] == (False, "Yahoo has no AAPL")   # no such ticker: a refusal
    assert got["AAPL 30s down"][0] is False and "time bars" in got["AAPL 30s down"][1]
    assert got["FRED:DGS10 1D down"] == (True, "FRED did not answer (ConnectError)")
    assert got["FRED:DGS10 5m down"][0] is False and "daily or longer" in got["FRED:DGS10 5m down"][1]


def test_a_source_down_after_ibkr_or_alpaca_fell_through_stays_a_passing_failure(tmp_path, monkeypatch):
    m = _ext_market(tmp_path, monkeypatch, lambda req: (_ for _ in ()).throw(httpx.ConnectError("no route", request=req)))

    async def ibkr(sym, spec, rth=True, why=None):
        why.append(("IBKR", "no answer in 10 s"))
    monkeypatch.setattr(m.ext, "_ibkr", ibkr)
    with pytest.raises(SourceDown, match=r"^No AAPL bars from IBKR \(no answer in 10 s\) or Yahoo: Yahoo did not answer"):
        asyncio.run(m.ext.bars("AAPL", "1D"))


def test_the_api_answers_503_for_a_source_down_and_400_for_a_refusal(client):  # noqa: F811
    def answer(req):
        raise httpx.ConnectError("no route", request=req)
    client.app.state.market.http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    for sym, spec, code, says in (("SPX", "1m", 503, "Yahoo did not answer (ConnectError)"),
                                  ("SPX/VIX", "1m", 503, "Yahoo did not answer (ConnectError)"),   # a formula's part
                                  ("SPX", "30s", 400, "time bars"), ("ES1!/", "1m", 400, "ends too soon")):
        r = client.get("/api/ext/bars", params={"symbol": sym, "spec": spec})
        assert r.status_code == code and says in r.json()["detail"], (sym, spec, r.status_code, r.text)
    assert issubclass(ext.SourceDown, ValueError)   # what catches a ValueError still catches it


# ---- c08: an anchored VWAP's anchor out of reach is not paged back to on every load ----------------------------------

AVWAP_JS = """
const OLDER_MAX = 60000, now = Date.UTC(2026, 10, 12, 15), calls = [];
let S = {}, anchor = null, want = null, scaleKey = 'k';
const isTime = () => S.kind === 'time', inst = () => ({ rth: ['08:30', '15:00'] }), anchorMs = () => anchor;
const chart = { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 10 }) }) }, viewApply = () => {}, drawAvwap = () => {};
const olderUntil = (reached, done, fail) => calls.push({ reached, done, fail });
/*APP*/
function load() {
/*BLOCK*/
}
const chartOf = (kind, spec, n = 3000, step = 60e3) => ({ kind, spec, symbol: 'ES', rth: false,
  bars: Array.from({ length: n }, (_, i) => ({ t: now - (n - i) * step })) });
const out = {};
S = chartOf('time', '1m'); anchor = now - 90 * 86400e3; load();
out.oneMinute90d = calls.length;   // 60,000 1-minute bars reach about 60 days back: not paged at all
S = chartOf('time', '5m', 3000, 300e3); load();
out.fiveMinute90d = calls.length;   // within reach: loaded back
calls.length = 0;
S = chartOf('time', '1m'); want = { t: S.bars[0].t - 86400e3, key: 'k' }; load();   // a view to restore a day back too
S.bars[0].t = want.t;
out.viewOnly = [calls.length, calls[0].reached()];   // loaded back to the view, not the anchor
want = null; calls.length = 0;
S = chartOf('tick', '2000t'); load();
out.tickFirst = calls.length;   // no clock to judge by: tried
S.bars.length = OLDER_MAX; calls[0].fail();   // and it ran out of room
calls.length = 0;
S = chartOf('tick', '2000t'); load();
out.tickAgain = calls.length;   // not again
S = chartOf('time', '1m'); anchor = now - 10 * 86400e3; load();
out.oneMinute10d = calls.length;   // a closer anchor: loaded back
console.log(JSON.stringify(out));"""


@needs_node
def test_an_avwap_anchor_beyond_the_most_a_chart_holds_is_not_paged_back_to_on_every_load():
    """Round 20 loads the bars back to an anchor older than the first bar. The anchor is per symbol, so a daily chart's
    anchor 90 days back sent ES 1m paging /api/older up to 60,000 bars on each load (timeframe, tab, reconnect),
    20-30 s each, to start at the first bar anyway. Now a time chart works out whether the bars reach it, and a tick
    chart that ran out of room once isn't sent back again."""
    block = _lines("  const backView = ", "  }")
    js = AVWAP_JS.replace("/*APP*/", "\n".join(app_part(p) for p in ("specSeconds", "avwapFar", "avwapReach")))
    out = _node(js.replace("/*BLOCK*/", block))
    assert out == {"oneMinute90d": 0, "fiveMinute90d": 1, "viewOnly": [1, True], "tickFirst": 1, "tickAgain": 0, "oneMinute10d": 1}


AVWAP_MORE = """const out = {};
S = chartOf('time', '5s', 765, 15e3); anchor = now - 6 * 86400e3; load();
out.sparseSeconds = calls.length;   // a 5 s bar every 15 s: 35,000 bars back, not the 63,000 the clock says
calls.length = 0;
S = chartOf('time', '1m', 3000, 120e3); anchor = now - 80 * 86400e3; load();
out.quietMinutes = calls.length;   // a 1 m bar every 2 minutes: 55,000 bars back, not the 75,000 the clock says
calls.length = 0;
anchor = now - 90 * 86400e3;
const tried = (spec, older, at = null) => {   // a tick chart loaded back that stopped short: is it tried again?
  calls.length = 0;
  S = chartOf('tick', spec); load();
  const first = calls.length;
  S.older = older;
  if (at != null) { S.bars[0].t = at; S.bars.length = OLDER_MAX; }   // past the anchor, and full
  calls[0].fail();
  calls.length = 0;
  want = null;   // (the next load has no view to restore: only the anchor to load back to)
  S = chartOf('tick', spec); load();
  return [first, calls.length];
};
out.historyEnded = tried('500t', { more: false, waiting: false });   // the stored trades end before it: not again
out.ntNoAnswer = tried('700t', { more: false, waiting: true });   // NinjaTrader didn't answer: again
out.error = tried('900t', { more: true, waitUntil: now + 15e3 });   // a failed request: again
want = { t: anchor - 86400e3, key: 'k' };   // a view to restore further back than the anchor: the anchor was reached
out.viewShort = tried('1100t', { more: true }, anchor - 1000);
console.log(JSON.stringify(out));"""


@needs_node
def test_an_avwap_anchor_on_sparse_bars_is_tried_and_one_past_the_stored_history_is_not_tried_again():
    """The clock's count of bars back assumed a bar in every interval: seconds bars (and a quiet minute) exist only
    where a trade was, so an anchor 60,000 bars reach was never loaded back to. Seconds bars are now tried once, and
    minute bars count at the loaded bars' own pace too. And an anchor past the stored history (the trades of a tick
    chart, NinjaTrader's limit) was paged back to again on every load: it's remembered too, unless NinjaTrader didn't
    answer, the request failed or the anchor was reached (only the view to restore wasn't)."""
    block = _lines("  const backView = ", "  }")
    head = AVWAP_JS[:AVWAP_JS.index("const out = {};")]
    js = head.replace("/*APP*/", "\n".join(app_part(p) for p in ("specSeconds", "avwapFar", "avwapReach"))) + AVWAP_MORE
    out = _node(js.replace("/*BLOCK*/", block))
    assert out == {"sparseSeconds": 1, "quietMinutes": 1, "historyEnded": [1, 0], "ntNoAnswer": [1, 1], "error": [1, 1],
                   "viewShort": [1, 1]}


# ---- c09: Apply to all charts on the first HTF EMA leaves every chart's HTF EMA 2 as it was ---------------------------

def _layout_part(name: str) -> str:
    """One top-level function of web/layout.js as written there."""
    src = (WEB / "layout.js").read_text()
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start) + 3]


FREEZE = {"nt": {"EmaLength": 21, "HtfPeriod": 60}, "was": {"EmaLength": 21, "HtfPeriod": 60}, "now": {"EmaLength": 50, "HtfPeriod": 60}}


@needs_node
def test_apply_to_all_keeps_every_tabs_hema_2_as_it_shows():
    """instFreeze froze the open tab's HTF EMA 2 only: another tab's (and the linked tabs') stored no EmaLength, so
    once the starting settings took 50, theirs showed 50 too. Now every tab and the shared set keep theirs."""
    out = run_app(["instFreeze"], f"""
const TK_MULTI = false, saved = [], store = {{ studies: JSON.stringify({{ hema: true, hema_2: true }}) }};
const local = {{ get: (k, d) => store[k] ?? d, set: (k, v) => {{ store[k] = v; }} }};
const rawLocal = (k, d) => {{ try {{ return JSON.parse(local.get(k, '')) ?? d; }} catch {{ return d; }} }};
const S = {{ st: {{ hema: true, hema_2: true }}, tab: 0, sharedSet: {{}}, tabs: [
  {{ own: {{ st: {{ hema: true }}, set: {{}} }} }},                                      // open: hema_2 added (S.st)
  {{ own: {{ st: {{ hema: true, hema_2: false }}, set: {{ hema_2: {{ HtfPeriod: 240 }} }} }} }},   // off, its own period
  {{ own: {{ st: {{ hema: true, hema_2: true }}, set: {{ hema_2: {{ EmaLength: 50 }} }} }} }},     // already 50: nothing of its own now
  {{ link: true }}] }};
const ownTab = () => S.tabs[S.tab], saveTabs = () => saved.push(1), indApply = () => {{}}, cellPost = () => {{}};
{_layout_part("tkInstFreeze")}
/*APP*/
instFreeze('hema', {json.dumps(FREEZE)});
console.log(JSON.stringify({{ tabs: S.tabs.map((t) => t.own?.set ?? null), shared: S.sharedSet, stored: JSON.parse(store.indSet), saved: saved.length }}));""")
    assert out["tabs"] == [{"hema_2": {"EmaLength": 21}}, {"hema_2": {"EmaLength": 21, "HtfPeriod": 240}}, {}, None]
    assert out["shared"] == out["stored"] == {"hema_2": {"EmaLength": 21}} and out["saved"] == 1


@needs_node
def test_apply_to_all_keeps_hema_2_on_the_charts_no_page_has_open_and_in_other_workspaces():
    """layout.js's studyAll only took the study's own settings out of every chart position and workspace. Now it
    also keeps each HTF EMA 2 there as it shows, except on a chart open now (that page does its own, once)."""
    from test_hunt_web_ui import node
    tab = lambda st, s: {"id": "a", "sym": "ES", "spec": "5m", "own": {"st": st, "set": s}}   # noqa: E731
    keys = {"chartTabs": json.dumps([tab({"hema": True, "hema_2": True}, {"hema": {"EmaLength": 30}})]),
            "c1.chartTabs": json.dumps([tab({"hema": True, "hema_2": True}, {"hema": {"EmaLength": 30}}), {"id": "b", "sym": "NQ", "spec": "1m", "link": True}]),
            "c1.indSet": json.dumps({"hema": {"EmaLength": 30}}), "c1.studies": json.dumps({"hema": True, "hema_2": True})}
    other = {"id": "Y", "name": "Swing", "updated": 1001, "by": "pc", "data": {"layout": "1", "keys": {
        "chartTabs": json.dumps([tab({"hema": True, "hema_2": True}, {"hema_2": {"HtfPeriod": 240}})])}}}
    main = {"id": "X", "name": "Day", "updated": 1000, "by": "pc", "data": {"layout": "1", "keys": keys}}
    out = node(f"""
const srv = makeServer([{json.dumps(main)}, {json.dumps(other)}]), t = makeTimers();
const B = makeBrowser(WEB, {{ keys: {{ ...{json.dumps(keys)}, wsId: 'X', wsStamp: '1000' }} }});
const w = srv.connect(B.open({{ api: srv.api, timers: t }}));
await w.ws.start();
const n = await w.ws.studyAll('hema', -1, {json.dumps(FREEZE)});
const sets = (json) => JSON.parse(json).map((x) => x.own?.set ?? null);
out({{ n, here: sets(B.shared.get('chartTabs')), c1: sets(B.shared.get('c1.chartTabs')), c1shared: JSON.parse(B.shared.get('c1.indSet')),
  other: sets(srv.get('Y').data.keys.chartTabs) }});""")
    assert out["n"] == 1
    assert out["here"] == [{}]   # this page's own chart: its tabs froze in applyAll, before the settings changed
    assert out["c1"] == [{"hema_2": {"EmaLength": 21}}, None] and out["c1shared"] == {"hema_2": {"EmaLength": 21}}
    assert out["other"] == [{"hema_2": {"EmaLength": 21, "HtfPeriod": 240}}]


# ---- m04: and on a chart whose linked tabs' set was never saved -----------------------------------------------------

@needs_node
def test_apply_to_all_keeps_hema_2_on_a_linked_chart_with_no_saved_set():
    """Linked tabs with HTF EMA 2 added at the starting settings save no set: indSet (c1.indSet) is not stored. studyAll
    froze only the sets stored, so on a chart position no page has open (a phone shows chart 1 only) and in another
    workspace, that HTF EMA 2 took the new EmaLength. Now a set not stored is frozen as {} and written when it moves."""
    from test_hunt_web_ui import node
    linked = lambda i: json.dumps([{"id": i, "sym": "NQ", "spec": "1m", "link": True}])   # noqa: E731
    both = json.dumps({"hema": True, "hema_2": True})
    keys = {"layout": "2", "chartTabs": json.dumps([{"id": "a", "sym": "ES", "spec": "5m", "own": {"st": {"hema": True}, "set": {}}}]),
            "c1.chartTabs": linked("b"), "c1.studies": both,                                  # no c1.indSet
            "c2.chartTabs": linked("c"), "c2.studies": json.dumps({"hema": True}),            # no HTF EMA 2: nothing to keep
            "c3.chartTabs": linked("d")}                                                       # no studies stored at all
    other = {"id": "Y", "name": "Swing", "updated": 1001, "by": "pc", "data": {"layout": "1", "keys": {
        "chartTabs": linked("e"), "studies": both, "c1.chartTabs": linked("f"), "c1.studies": json.dumps({"hema": True})}}}
    main = {"id": "X", "name": "Day", "updated": 1000, "by": "pc", "data": {"layout": "2", "keys": keys}}
    js = f"""
const srv = makeServer([{json.dumps(main)}, {json.dumps(other)}]), t = makeTimers();
const B = makeBrowser(WEB, {{ keys: {{ ...{json.dumps(keys)}, wsId: 'X', wsStamp: '1000' }} }});
const w = srv.connect(B.open({{ api: srv.api, timers: t, width: WIDTH }}));
await w.ws.start();
const n = await w.ws.studyAll('hema', -1, {json.dumps(FREEZE)});
const ok = srv.get('Y').data.keys, set = (k, v = B.shared.get(k)) => (v == null ? null : JSON.parse(v));
out({{ n, c1: set('c1.indSet'), c2: set('c2.indSet'), c3: set('c3.indSet'), here: set('indSet'), Y: set(0, ok.indSet),
  Yc1: set(0, ok['c1.indSet']), frames: w.frames().map((f) => f.posted.filter((m) => m.type === 'indall').length) }});"""
    kept = {"hema_2": {"EmaLength": 21}}
    phone = node(js.replace("WIDTH", "400"))   # a phone: chart 1 only, chart 2's position has no page open
    assert phone == {"n": 1, "c1": kept, "c2": None, "c3": None, "here": None, "Y": kept, "Yc1": None, "frames": []}
    wide = node(js.replace("WIDTH", "1400"))   # both charts open: chart 2's page keeps its own ('indall')
    assert wide == {"n": 1, "c1": None, "c2": None, "c3": None, "here": None, "Y": kept, "Yc1": None, "frames": [1, 1]}


# ---- m07: a page that apply() reloaded into ignores the page before's storage events ----------------------------------

STALE_JS = """
const srv = makeServer([
  { id: 'main', name: 'Main', updated: 1000, by: 'pc', data: { layout: '1', keys: { chartTabs: '[ES 5m]', sym: 'ES' } } },
  { id: 'w2', name: 'Second', updated: 1001, by: 'pc', data: { layout: '1', keys: { chartTabs: '[NQ 1m]', sym: 'NQ' } } }]);
const t = makeTimers(), B = makeBrowser(WEB, { keys: KEYS });
// what the page before writes, as Chrome can deliver it to the page after it in the same tab (here: a window that
// only listens, and never reloads, hears it)
const spy = B.open({ api: async () => ({ workspaces: [] }), timers: makeTimers(), reload: () => {} }), stale = [];
spy.listeners.storage.push((e) => stale.push(e));
const one = srv.connect(B.open({ api: srv.api, timers: t }));
await one.ws.start();
ACT
await settle();
one.connected = false;
const two = srv.connect(B.open({ api: srv.api, timers: t }));   // the page after the reload
let heard = 0;   // its wsOpened events
const hear = () => { for (const e of stale.splice(0)) { heard += e.key === 'wsOpened'; for (const f of two.listeners.storage) f(e); } };
if (EARLY) hear();   // while it starts
await two.ws.start();
await settle();
hear();
await settle();
await t.runAll();
const after = { one: one.reloads, two: two.reloads, toasts: two.toasts, wsId: B.shared.get('wsId'),
  updated: srv.items.map((w) => w.updated), heard };
const frame = B.open({ api: async () => ({ workspaces: [] }), timers: makeTimers(), reload: () => {} });
frame.local.set('chartTabs', '[a chart of the layout changed it]');   // a real change from a chart frame: still saved
await t.runAll();
after.saved = srv.get(B.shared.get('wsId')).data.keys.chartTabs;
out(after);"""


@needs_node
@pytest.mark.parametrize("early", [True, False])
def test_signing_in_on_a_new_device_opens_the_first_workspace_with_one_reload(early):
    """A new device (no workspace open) opens the first one: apply() writes its settings, wsId and wsOpened and
    reloads. Chrome can deliver those storage events to the page after the reload in the same tab. Its wsOpened
    event read as another window opening a workspace: W.id went null, start() opened the first one again and the page
    reloaded again, 2 to 5 times after a sign-in. Now a wsOpened still as this page started is not a newer opening,
    and settings as this page started are not a change to save (a save would tell every other device the workspace
    changed). A real change from a chart frame after them is still saved."""
    from test_hunt_web_ui import node
    js = STALE_JS.replace("KEYS", json.dumps({"chartTabs": "[a new device]", "sym": "CL"})).replace("ACT", "")
    out = node(js.replace("EARLY", "true" if early else "false"))
    assert out == {"one": 1, "two": 0, "toasts": [], "wsId": "main", "updated": [1000, 1001], "heard": 1,
                   "saved": "[a chart of the layout changed it]"}


@needs_node
@pytest.mark.parametrize("early", [True, False])
def test_a_workspace_picked_in_the_menu_stays_open_after_its_reload(early):
    """The same events after a pick in the menu: the page after the reload went back to the first workspace (Main)
    when Second was picked."""
    from test_hunt_web_ui import node
    keys = {"chartTabs": "[ES 5m]", "sym": "ES", "wsId": "main", "wsStamp": "1000"}
    js = STALE_JS.replace("KEYS", json.dumps(keys)).replace("ACT", "one.button('Second').onclick();")
    out = node(js.replace("EARLY", "true" if early else "false"))
    assert out == {"one": 1, "two": 0, "toasts": [], "wsId": "w2", "updated": [1000, 1001], "heard": 1,
                   "saved": "[a chart of the layout changed it]"}


# ---- c10: a tag priced just under the live price goes below its label (and the countdown), not above it --------------

@needs_node
def test_a_tag_just_under_the_live_price_stacks_below_its_label_and_countdown():
    """The anchor block (the live price's label and the countdown under it) was sorted at its middle, half a tag
    under the price: an alert a tick under the market was drawn above the live price. Now it sorts at the price."""
    js = """
const src = require('fs').readFileSync(%s, 'utf8');
const i = src.indexOf('const axisStack = {'), j = src.indexOf('\\n};', i) + 3;
eval(src.slice(i, j).replace('const axisStack', 'globalThis.axisStack'));
const run = (ys, anchor) => {
  axisStack.owners.clear(); axisStack.anchor = anchor;
  const v = axisStack.views({}, ys.map((y) => ({ coordinate: () => y })));
  axisStack.laid = false; axisStack.layout();
  return v.map((t) => Math.round(axisStack.pos.get(t)));
};
console.log(JSON.stringify([run([305.7], () => [300, 2]), run([305, 310], () => [300, 2]), run([290, 295, 305, 310], () => [300, 2]),
  run([300], () => [300, 2]), run([325], () => [300, 2]), run([295, 305], () => [300, 1]), run([305.7], () => [300, 1])]));""" % json.dumps(str(WEB / "studies.js"))
    out = _node(js)
    assert out == [[338], [338, 357], [262, 281, 338, 357], [281], [338], [281, 319], [319]]


# ---- c11: on the Bars and Line styles the tags go around the live price label too -----------------------------------

@needs_node
def test_the_axis_anchor_counts_the_bars_or_line_styles_own_price_label():
    """Bars and Line (and a FRED/EIA chart's line) give the last price label to their own series and hide the
    candles': the anchor counted only the candles', so tags covered the live price on those styles."""
    anchor = _lines("  axisStack.anchor = () => {", "  };")
    out = _node(f"""
const axisStack = {{}}, S = {{ lastBar: {{ c: 500 }}, cs: {{ countdown: true }}, replay: null, ext: null }}, isTime = () => true;
let altSeries = null, cOpts = {{}};
const candles = {{ options: () => cOpts, priceToCoordinate: (p) => 1000 - p }};
{anchor}
const run = (c, alt) => {{ cOpts = c; altSeries = alt ? {{ options: () => alt }} : null; return axisStack.anchor(); }};
console.log(JSON.stringify([
  run({{ lastValueVisible: true }}, null),                                              // candles
  run({{ lastValueVisible: false }}, {{ visible: true, lastValueVisible: true }}),          // Bars, Line
  run({{ lastValueVisible: true }}, {{ visible: true, lastValueVisible: false }}),          // Heikin Ashi
  run({{ lastValueVisible: false }}, {{ visible: false, lastValueVisible: true }}),         // no label shown
]));""")
    assert out == [[500, 2], [500, 2], [500, 2], None]


# ---- c12/c24: a channel alert on a stock or formula from the watchlist starts with two different prices --------------

@needs_node
def test_a_channel_on_a_symbol_with_no_tick_starts_under_the_value():
    """symTick() is 0 for a stock, series or formula that is not the chart's own, so the channel's lower bound started
    equal to the upper one and Create said "A channel needs two different prices". Now 20 steps of the value's 4th
    significant digit under it; a future keeps its 20 ticks."""
    out = run_app(["CHANNEL", "freeOwn", "symTick", "symDec", "freeFmt", "fmtSym", "syncDlg"], """
const els = {};
const $ = (q) => (els[q] ||= { value: '', hidden: false, textContent: '', options: [] });
const S = { symbol: 'ES', ext: null, instruments: { ES: { tick_size: 0.25, decimals: 2 } } }, dlg = { sym: 'ES', repeat: false };
const bondTick = () => 0, bond32 = () => '', num = (v) => parseFloat(v), indDlgSync = () => {}, setTrig = () => {};
/*APP*/
const run = (sym, v1) => {
  dlg.sym = sym; $('#al-src').value = 'price'; $('#al-op').value = 'enter'; $('#al-v1').value = v1; $('#al-v2').value = '';
  syncDlg();
  return $('#al-v2').value;
};
console.log(JSON.stringify([run('SPX', '29.01'), run('AAPL+MSFT', '1169.92'), run('VIXY', '0.0123'), run('ES', '6900.00'), run('X', '0')]));""")
    assert out == ["28.81", "1149.92", "0.0121", "6895.00", "-0.02"]
