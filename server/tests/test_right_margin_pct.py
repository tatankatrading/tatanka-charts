"""The chart's right margin in % of the width (Chart settings, Right margin [n] [bars | % of width]): while the chart
follows the latest bars, the latest bar stays at (100 - p)% of the price pane's width on a load, Reset view and go to
live, as new bars come, after every zoom and a resize; a chart scrolled back keeps its view. In bars (the default)
nothing changed. The app.js parts run in node on a time scale that does what the library (Lightweight Charts 5.1) does:
the last bar's x, a wheel or pinch zoom on the bar under the cursor (at once), a new bar, a resize, and what the page
asks of it (a range, the margin option's scroll, a scroll position) on its next frame."""
import re

from test_hunt_web_chart import APP, WEB, app_lines, needs_node, run_app

PARTS = ["AMT_ROOM", "amtRoomFrame", "amtRoomCheck", "pctBars", "rightMargin", "latestRange", "amtLayout", "resetView", "zoom",
         "goLive"]
LOAD = app_lines("    const ro = chart.timeScale().options().rightOffset;", "    chart.timeScale().setVisibleLogicalRange(")

# The library's time scale: yu is its right offset (the scroll position, in bars past the last bar), opt the rightOffset
# option (where go to live and the option itself put it). The last bar's x is width - (yu + 0.5) * spacing - 1. What the
# page asks waits in T.q for the next frame (render); its animated scroll to the latest bars never applies its last step.
LIB_JS = """
const T = { W: 1200, bs: 6, yu: 6, opt: 6, n: 1000, q: [], realTime: 0 };
const clampBs = (b) => Math.max(0.5, Math.min(30, b));
const ts = {
  width: () => T.W,
  options: () => ({ rightOffset: T.opt, barSpacing: T.bs }),
  getVisibleLogicalRange: () => ({ from: T.n - 1 + T.yu - T.W / T.bs + 1, to: T.n - 1 + T.yu }),
  setVisibleLogicalRange(r) { T.q.push(() => { T.bs = clampBs(T.W / (r.to - r.from + 1)); T.yu = r.to - (T.n - 1); }); },
  applyOptions(o) { if ('rightOffset' in o) { T.opt = o.rightOffset; T.q.push(() => { T.yu = o.rightOffset; }); } },
  scrollToPosition(p, animated) { T.q.push(() => { T.yu = p; }); },
  scrollToRealTime() { T.realTime += 1; const from = T.yu, to = T.opt; T.q.push(() => { T.yu = from + (to - from) * 0.97; }); },
};
const render = () => { for (const f of T.q.splice(0)) f(); };
const chart = { timeScale: () => ts }, window = { innerWidth: 1500 }, setAuto = () => {};
const S = { cs: { rightBars: 6, rightUnit: 'pct', rightPct: 50 }, st: { amt: false, ifthen: false, dsma: false }, ext: null,
  ifSet: {}, ifRows: [], dsmaSet: {}, bars: Array(T.n).fill({}) };
/*APP*/
const key = () => `${T.W}|${T.bs}|${T.yu}|${T.opt}|${T.n}`;
let frames = 0;
// the library's next frame and amtRoomCheck after it, again while the page moves the chart
const frame = () => {
  for (let i = 0; i < 10; i++) { render(); const k = key(); amtRoomCheck(); frames = Math.max(frames, i + 1); if (!T.q.length && key() === k) return; }
  frames = 99;
};
const floatIndexAt = (x) => T.n - 1 + T.yu - (T.W - 1 - x) / T.bs;
const zoomAt = (x, scale) => { const i = floatIndexAt(x); T.bs = clampBs(T.bs + scale * T.bs / 10); T.yu += i - floatIndexAt(x); };
const wheel = (x, scale) => { zoomAt(x, scale); frame(); };
// a pinch (or a trackpad): a zoom step every frame, before what the page asked on the last one is drawn (checkFirst: the
// page's check runs before the library draws, else after)
const pinch = (x, scale, steps, checkFirst) => {
  for (let i = 0; i < steps; i++) { zoomAt(x, scale); if (checkFirst) { amtRoomCheck(); render(); } else { render(); amtRoomCheck(); } }
  frame();
};
const newBar = () => {   // shiftVisibleRangeOnNewBar: the scroll position stays while the last bar is in view
  const r = ts.getVisibleLogicalRange(), seen = r.from <= T.n - 1 && T.n - 1 <= r.to;
  T.n += 1; S.bars.push({});
  if (!seen) T.yu -= 1;
  frame();
};
const resize = (w) => { T.W = w; frame(); };
const firstLoad = () => { const c = S.bars; {
/*LOAD*/
} frame(); };
const lastX = () => T.W - (T.yu + 0.5) * T.bs - 1;
const at = () => +(lastX() / T.W).toFixed(4);   // the last bar's x as a share of the pane's width
const across = () => +(T.W / T.bs).toFixed(1);   // bars across the pane
"""


def run(js: str, parts=PARTS):
    return run_app(parts, (LIB_JS + js).replace("/*LOAD*/", LOAD))


@needs_node
def test_bars_from_width_spacing_and_percent_put_the_last_bar_there():
    """pctBars: the right offset in bars that puts the latest bar's middle at (100 - p)% of the pane, where the library
    draws it (offset + 0.5 bars and 1 px in from the right edge); never below 0 (0% is the last bar at the edge, as 0
    bars), 0 while the chart has no width. In bars, the setting is the margin."""
    out = run("""
const rows = [];
for (const W of [330, 800, 1650]) for (const bs of [0.5, 1.7, 6, 30]) for (const p of [0, 1, 40, 50, 60, 90]) {
  const ro = pctBars(W, bs, p), x = W - (ro + 0.5) * bs - 1;
  rows.push({ W, bs, p, ro, x });
}
const none = [pctBars(0, 6, 50), pctBars(800, 0, 50)];
const bars = (S.cs.rightUnit = 'bars', rightMargin());
S.cs.rightUnit = 'pct'; T.W = 1000; T.bs = 10;
console.log(JSON.stringify({ rows, none, bars, pct: rightMargin() }));""")
    for r in out["rows"]:
        assert r["ro"] >= 0
        if r["ro"] > 0:   # unclamped: exactly at (100 - p)%
            assert abs(r["x"] - r["W"] * (1 - r["p"] / 100)) < 1e-9, r
        else:   # 0% (or too little width for the bar's own half): the last bar at the right edge, as a 0-bar margin
            assert r["W"] * (1 - r["p"] / 100) >= r["x"] - 1e-9, r
    assert out["none"] == [0, 0]
    assert out["bars"] == 6
    assert out["pct"] == (1000 * 0.5 - 1) / 10 - 0.5


@needs_node
def test_at_50_percent_the_last_bar_stays_in_the_middle_through_a_trading_session():
    """Desktop and a phone's width: a first load, new bars, wheel zooms on a bar left of the last one and in the empty
    space right of it, zooming out, the + and - buttons, the sidebar and a phone turned, Reset view after a zoom: the
    latest bar at 50% each time, and the chart settles in a frame or two."""
    out = run("""
const out = {};
for (const W of [1200, 330]) {
  T.W = W; T.n = 1000; S.bars = Array(T.n).fill({}); T.bs = 6; T.yu = 6; T.opt = 6; amtRoomWas = null; amtRoomSet = null;
  const o = out[W] = {};
  firstLoad(); o.load = at(); o.loadAcross = across();
  for (let i = 0; i < 5; i++) newBar();
  o.newBars = at();
  wheel(W * 0.25, 3); o.zoomInLeft = at();
  wheel(W * 0.9, 3); o.zoomInRight = at();
  wheel(W * 0.1, -4); o.zoomOut = at();
  zoom(0.8); frame(); o.plus = at();
  zoom(1.25); frame(); zoom(1.25); frame(); o.minus = at();
  resize(W * 0.7); o.narrower = at();
  resize(W); o.wider = at();
  newBar(); o.newBarAfter = at();
  wheel(W * 0.3, 5); resetView(); frame(); o.reset = at(); o.resetAcross = across();
}
out.frames = frames;
console.log(JSON.stringify(out));""")
    for w in ("1200", "330"):
        o = out[w]
        for k in ("load", "newBars", "zoomInLeft", "zoomInRight", "zoomOut", "plus", "minus", "narrower", "wider",
                  "newBarAfter", "reset"):
            assert abs(o[k] - 0.5) < 0.002, (w, k, o[k])
        assert o["loadAcross"] == o["resetAcross"] == 163   # as many bars across as a fresh chart's
    assert out["frames"] <= 3


@needs_node
def test_the_percent_holds_at_40_and_60_and_zooming_back_and_forth_does_not_drift():
    """40% and 60% put the latest bar at 60% and 40% of the width, and keep it there through twenty wheel steps in and
    out on different bars (no drift)."""
    out = run("""
const out = {};
for (const p of [40, 60]) {
  S.cs.rightPct = p; T.W = 1000; T.n = 1000; S.bars = Array(T.n).fill({}); amtRoomWas = null; amtRoomSet = null;
  firstLoad();
  const xs = [at()];
  for (let i = 0; i < 10; i++) { wheel(100 + 80 * i, 2); xs.push(at()); }
  for (let i = 0; i < 10; i++) { wheel(900 - 80 * i, -2); xs.push(at()); }
  out[p] = xs;
}
console.log(JSON.stringify(out));""")
    assert all(abs(x - 0.6) < 0.002 for x in out["40"])
    assert all(abs(x - 0.4) < 0.002 for x in out["60"])


@needs_node
def test_a_chart_scrolled_back_keeps_its_view_and_go_to_live_lands_at_the_percent():
    """Scrolled back in time, a zoom is the library's own (on the bar under the cursor), a new bar and a resize move
    nothing; the margin option follows the zoom and width, so » (go to live) and End land the latest bar at the %: straight
    there (the library's animated scroll stops a frame short), so it follows the latest bars again."""
    out = run("""
firstLoad();
T.yu = -400; frame();   // dragged back 400 bars
const libZoom = (x, scale) => { const i = floatIndexAt(x); const bs = clampBs(T.bs + scale * T.bs / 10); return [bs, T.yu + i - (T.n - 1 + T.yu - (T.W - 1 - x) / bs)]; };
const want = libZoom(600, 3);
wheel(600, 3);
const near = (a, b) => Math.abs(a - b) < 1e-6;
const out = { zoomKept: near(T.yu, want[1]) && near(T.bs, want[0]), optionAtPct: near(T.opt, pctBars(T.W, T.bs, 50)) };
const yu = T.yu;
newBar(); out.newBarKept = near(T.yu, yu - 1);   // the library keeps the bars in view
resize(900); out.resizeKept = near(T.yu, yu - 1) && near(T.opt, pctBars(900, T.bs, 50));
goLive(); frame(); out.live = at(); out.animated = T.realTime;
wheel(T.W * 0.2, 3); out.followsAfter = at();   // following again: a zoom keeps it there
console.log(JSON.stringify(out));""")
    assert out == {"zoomKept": True, "optionAtPct": True, "newBarKept": True, "resizeKept": True, "live": 0.5, "animated": 0,
                   "followsAfter": 0.5}


@needs_node
def test_the_label_room_applies_on_top_and_the_percent_is_never_capped():
    """The AMT Toolkit's labels want 55 bars (capped at about a third of the bars in view when zoomed in): with 10% the
    labels' room wins (the max of the two); with 50%, zoomed in to a few dozen bars, the % is kept, not cut to the cap."""
    out = run("""
S.st.amt = true;
S.cs.rightPct = 10; T.W = 1200; firstLoad();
const view = () => { const r = ts.getVisibleLogicalRange(); return r.to - r.from; };
const out = { ten: { yu: T.yu, pct: pctBars(T.W, T.bs, 10), cap: Math.max(2, Math.floor(view() * AMT_ROOM)) } };
S.cs.rightPct = 50; amtLayout(); frame();
for (let i = 0; i < 12; i++) wheel(T.W * 0.2, 4);   // zoomed in: few bars in view
out.fifty = { at: at(), across: across(), cap: Math.max(2, Math.floor(view() * AMT_ROOM)), yu: T.yu };
console.log(JSON.stringify(out));""")
    ten = out["ten"]
    assert ten["yu"] == min(55, ten["cap"]) and ten["yu"] > ten["pct"]   # the labels' room, more than 10%
    fifty = out["fifty"]
    assert abs(fifty["at"] - 0.5) < 0.002 and fifty["yu"] > fifty["cap"]   # 50%, over the cap
    assert fifty["across"] < 60


@needs_node
def test_bars_mode_is_unchanged():
    """Right margin in bars (the default): a load is the latest 150 bars and 6 bars of margin, a zoom is the library's
    own (the latest bar moves), nothing is set on a resize; switching to % (scrolled back: the view stays; » then lands
    at the %) and back to bars puts the option back to 6."""
    out = run("""
S.cs.rightUnit = 'bars';
firstLoad();
const r = ts.getVisibleLogicalRange();
const out = { load: [r.from, r.to], opt: T.opt };
const x0 = at();
wheel(300, 3); out.zoomMoved = at() !== x0; out.optAfterZoom = T.opt;
const yu = T.yu; resize(800); out.resizeKept = T.yu === yu && !T.q.length;
out.zoomedPastEdge = at() > 1;   // zoomed in left of it: the latest bar out of view, scrolled back as today
S.cs.rightUnit = 'pct'; amtLayout(); frame(); out.pctWhileBack = at() > 1;   // the setting doesn't scroll it
goLive(); frame(); out.pct = at();   // »
S.cs.rightUnit = 'bars'; amtLayout(); frame(); out.back = [T.opt, T.yu];
T.yu = -50; goLive(); frame(); out.barsGoLive = T.realTime;   // in bars, » is the library's animated scroll, as before
console.log(JSON.stringify(out));""")
    assert out == {"load": [1000 - 150 - 6, 1000 + 6], "opt": 6, "zoomMoved": True, "optAfterZoom": 6, "resizeKept": True,
                   "zoomedPastEdge": True, "pctWhileBack": True, "pct": 0.5, "back": [6, 6], "barsGoLive": 1}


def test_the_setting_defaults_to_6_bars_and_takes_0_to_90_percent():
    """Chart settings: bars and 6 by default (nothing changes for anyone who doesn't switch), the % 0-90 (50 when
    switched); both kept in the chart settings, so per tab, Apply to all charts and chart templates carry them."""
    src = "\n".join(APP)
    defaults = re.search(r"const CS_DEFAULTS = \{(.*?)\};", src, re.S).group(1)
    assert "rightBars: 6" in defaults and "rightUnit: 'bars'" in defaults and "rightPct: 50" in defaults
    html = (WEB / "index.html").read_text()
    assert re.search(r'<input type="number" data-cs="rightPct" min="0" max="90" step="1"', html)
    assert re.search(r'<span class="seg" data-cs="rightUnit"[^>]*><button type="button" data-v="bars">bars</button>'
                     r'<button type="button" data-v="pct">% of width</button>', html)


@needs_node
def test_a_pinch_zooming_every_frame_keeps_the_last_bar_at_its_place():
    """A pinch (or a trackpad's zoom) moves the bar spacing every frame, before the library has drawn the margin the page
    set on the last one: the range read then is still the old one. The page counts the chart as following the latest bars
    from setting it there, not from that range (from it, every other step took the chart for scrolled back and kept the
    zoom's own view: on the phone the last bar ended at 58%). Either order of the page's check and the library's frame."""
    out = run("""
const out = {};
for (const checkFirst of [true, false]) {
  T.W = 318; T.n = 3000; S.bars = Array(T.n).fill({}); amtRoomWas = null; amtRoomSet = null; T.q = [];
  firstLoad();
  pinch(T.W * 0.3, 0.6, 18, checkFirst);
  const zin = at();
  pinch(T.W * 0.8, -0.5, 12, checkFirst);
  out[checkFirst ? 'checkFirst' : 'drawFirst'] = [zin, at(), +across().toFixed(0)];
}
console.log(JSON.stringify(out));""")
    for k in ("checkFirst", "drawFirst"):
        zin, zout, _ = out[k]
        assert abs(zin - 0.5) < 0.002 and abs(zout - 0.5) < 0.002, out
