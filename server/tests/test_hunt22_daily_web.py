"""Round 22 of the bug hunt, daily use of the chart page (web/app.js, style.css): the phone app opens on the chart, not
on the panel page it was left on, and bars loaded while the chart has no width wait for it before their view is set;
a symbol or timeframe typed right after the last Enter is no longer lost; each chart of a layout keeps its drawing
tools as a column beside it; and an iPhone turned sideways starts with the AMT table folded. The app.js parts run in
node with what they call stubbed; the style sheet's rules are read from web/style.css."""
import json
import subprocess

from test_hunt13_ui import decl
from test_hunt_web_chart import APP, app_lines, app_part, needs_node, run_app

PHONE = "@media (max-width: 760px)"


def _node(js: str):
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


def _statement(first: str) -> str:
    """The top-level statement of web/app.js starting with `first`: that line, or the lines to the next '});'."""
    start = next(i for i, line in enumerate(APP) if line.startswith(first))
    line = APP[start]
    if line.rstrip().endswith(");") and line.count("(") == line.count(")"):
        return line
    return "\n".join(APP[start:next(i for i in range(start, len(APP)) if APP[i] == "});") + 1])


def _side_start() -> str:
    """What the page runs at start after pageUi: the panel's first page and the resize listener (to its block's end)."""
    start = next(i for i in range(next(i for i, line in enumerate(APP) if line.startswith("function pageUi()")), len(APP))
                 if APP[i] == "}") + 1
    listener = next(i for i in range(start, len(APP)) if APP[i].startswith("  let wasPhone"))
    return "\n".join(APP[start:next(i for i in range(listener, len(APP)) if APP[i] == "}") + 1])


# ---- the phone app reopened on a panel page: it opens on the chart, the page it was left on is not brought back ------

SIDE_JS = """
const classes = () => { const on = new Set(); return { on, toggle: (c, v = !on.has(c)) => (v ? on.add(c) : on.delete(c), v), contains: (c) => on.has(c) }; };
const elOf = () => ({ classList: classes(), textContent: '', title: '', setAttribute() {} });
let els = {}, store = {}, listeners = {};
const $ = (s) => (els[s] ||= elOf()), document = { body: elOf() };
const local = { get: (k, d) => store[k] ?? d, set: (k, v) => { store[k] = v; } }, pageUi = () => {};
const window = { innerWidth: 0, addEventListener: (t, f) => { (listeners[t] ||= []).push(f); } };
let TK_CELL = null, TK_MULTI = false;
/*APP*/
function START() {
/*START*/
}
const open = (width, keys = {}, cell = null) => {   // the page loads, its storage as the last visit left it
  els = {}; document.body = elOf(); listeners = {}; store = { ...keys }; TK_CELL = cell; window.innerWidth = width;
  START();
};
const page = () => document.body.classList.contains('side-page');
const turn = (width) => { window.innerWidth = width; (listeners.resize || []).forEach((f) => f()); };
const out = {};
open(390, { sideOpenPhone: '1' });   // left on the Watchlist page
out.reopened = page();
open(844, { sideOpenPhone: '1', sideOpen: '1' });   // opened in landscape (a computer's layout: the panel beside the chart)
out.landscape = page();
turn(390);
out.thenUpright = page();
open(390);   // this visit: the Watchlist tapped, turned and back: its page again
setSide(true);
turn(844); turn(390);
out.sameVisit = page();
open(1500, { sideOpen: '0' });   // a computer keeps its panel as it was
out.pcFolded = page();
open(1500, { sideOpen: '1' });
out.pcOpen = page();
open(500, { sideOpenPhone: '1' }, 1);   // a chart of a layout leaves the page's setting alone
out.cellKeeps = store.sideOpenPhone;
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_phone_app_opens_on_the_chart_not_on_the_page_it_was_left_on():
    """sideOpenPhone stayed '1' when the app was left on a panel page, so it opened there again, its chart built and its
    bars' view set with no width (CHARTS.md: 'The app opens on the chart'). A phone now opens on the chart, also when
    opened in landscape and then turned; a page chosen in this visit still comes back when the phone is turned."""
    js = SIDE_JS.replace("/*APP*/", "\n".join(app_part(p) for p in ("phoneLayout", "sideKey", "setSide")))
    out = _node(js.replace("/*START*/", _side_start()))
    assert out == {"reopened": False, "landscape": True, "thenUpright": False, "sameVisit": True, "pcFolded": False,
                   "pcOpen": True, "cellKeeps": "1"}


# ---- bars loaded while the chart has no width: their view waits for the width -------------------------------------

# applyHistory as written, where every name it uses that the test doesn't give is a stub that takes anything; the
# library's time scale as it behaves: a range set gives the bars the spacing the last width it had allows (none before
# it ever had one), at least 0.5 px.
HOLD_JS = r"""
const stub = new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => 0 : k === Symbol.unscopables ? undefined : stub), apply: () => stub });
const T = { width: 0, seen: 0, spacing: 6, right: null, sized: [] };
const ts = {
  width: () => T.width, options: () => ({ rightOffset: 5, barSpacing: T.spacing }),
  setVisibleLogicalRange(r) { T.spacing = Math.max(0.5, T.seen / (r.to - r.from)); T.right = r.to; },
  getVisibleLogicalRange: () => (T.right == null || !T.seen ? null : { from: T.right - T.seen / T.spacing, to: T.right }),
  subscribeSizeChange: (f) => T.sized.push(f),
};
const S = { symbol: 'ES', spec: '5m', rth: false, seqTime: new Map(), fp: new Map(), dsmaLive: new Map(), instruments: { ES: {} },
  st: {}, cs: {}, holidays: {}, tabs: [{ id: 'a' }], tab: 0, views: {}, viewWant: null, replay: null };
const bars = Array.from({ length: 1000 }, (_, i) => ({ t: 1_700_000_000_000 + i * 300_000, o: 1, h: 1, l: 1, c: 1, v: 1, seq: i + 1 }));
const out = {};
const known = { S, T, ts, bars, out, chart: { timeScale: () => ts }, viewKey: () => `${S.symbol} ${S.spec} ${S.rth}`, TK_MULTI: false,
  VIEWS_MAX: 60, VIEWS_KEY: 'tabViews', local: { get: (k, d) => d, set() {} } };
with (new Proxy(known, { has: (t, k) => k in t || !(k in globalThis), get: (t, k) => (k in t ? t[k] : k === Symbol.unscopables ? undefined : stub) })) {
  (function () {
    /*APP*/
    const look = () => {
      const r = ts.getVisibleLogicalRange();
      return { held: !!S.viewHeld, want: S.viewWant?.w ?? null, spacing: +T.spacing.toFixed(2), view: r && [+(r.to - r.from).toFixed(1), r.to],
        saved: S.views.a?.w ?? null };
    };
    const show = (w) => { T.width = T.seen = w; T.sized.forEach((f) => f(w, 300)); };
    const load = (want, w) => {
      Object.assign(T, { width: w, seen: w, spacing: 6, right: null });
      S.views = {}; S.viewWant = want; S.viewHeld = false;
      applyHistory({ kind: 'time', bars });
    };
    // a phone's panel page: the tab's view from before (40 bars) waits, nothing is saved meanwhile, and the chart shown
    // gets it
    load({ key: 'ES 5m false', w: 40, ro: 5, auto: true }, 0);
    viewSave(true);
    out.saved = { loaded: look() };
    show(318);
    viewSave(true);
    out.saved.shown = look();
    load(null, 0);   // no view kept: the latest 150 bars and the margin, once it has its width
    out.fresh = { loaded: look() };
    show(318);
    out.fresh.shown = look();
    load({ key: 'ES 5m false', w: 40, ro: 5, auto: true }, 318);   // the chart in view: at once
    out.inView = look();
  })();
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_bars_loaded_while_the_chart_has_no_width_get_their_view_when_it_has_one():
    """The phone app opened on a panel page built its chart 0 px wide; applyHistory set the tab's view (or the 150-bar
    reset) on it and the library put the bars 0.5 px apart: tapping Chart showed 636 bars squeezed under the axis, the
    first bar in view loaded older history ('all the stored history is loaded') and that view was saved for the next
    time. Now the view waits, the tab's kept for it (nothing saves over it), and is set once the chart has a width."""
    parts = ["BAR_CAP", "olderReset", "applyHistory", "viewLoad", "latestRange", "viewApply", "viewSnap", "viewTimer", "viewSave"]
    src = "\n".join(app_part(p) for p in parts) + "\n" + _statement("chart.timeScale().subscribeSizeChange(")
    out = _node(HOLD_JS.replace("/*APP*/", src))
    assert out["saved"]["loaded"] == {"held": True, "want": 40, "spacing": 6, "view": None, "saved": None}
    assert out["saved"]["shown"] == {"held": False, "want": None, "spacing": 7.95, "view": [40, 1004], "saved": 40}
    assert out["fresh"]["loaded"]["held"] is True and out["fresh"]["loaded"]["view"] is None
    assert out["fresh"]["shown"] == {"held": False, "want": None, "spacing": 1.99, "view": [160, 1005], "saved": None}
    assert out["inView"] == {"held": False, "want": None, "spacing": 7.95, "view": [40, 1004], "saved": None}


# A journal trade picked on a phone's Journal page (the chart has had a width, now 0): HOLD_JS's harness, its time scale
# keeping a range set while the chart is hidden.
JOURNAL_HOLD_JS = HOLD_JS.split("/*APP*/")[0] + r"""
    /*APP*/
    S.instruments.NQ = {};
    const look = () => { const r = ts.getVisibleLogicalRange(); return { held: !!S.viewHeld, goTo: S.goTo ?? null, view: r && [r.from, r.to] }; };
    const show = (w) => { T.width = w; T.sized.forEach((f) => f(w, 300)); };
    const load = (sym) => { S.symbol = sym; S.viewWant = null; applyHistory({ kind: 'time', bars }); };
    const hide = () => { Object.assign(T, { width: 0, seen: 318, spacing: 6, right: null }); };
    // another symbol's trade: its bars come in while the Journal page is up; tapping Chart shows the trade, not the
    // latest 150 bars
    hide();
    journalShow({ symbol: 'NQ', entry_ts: bars[500].t });
    load('NQ');
    out.other = { loaded: look() };
    show(318);
    out.other.shown = look();
    // the same symbol's trade while its view waits: after that view
    hide();
    load('NQ');
    journalShow({ symbol: 'NQ', entry_ts: bars[300].t + 1000 });
    out.same = { picked: look() };
    show(318);
    out.same.shown = look();
    // another symbol picked before the trade's chart was shown: the trade is not looked for on it
    hide();
    journalShow({ symbol: 'ES', entry_ts: bars[200].t });
    S.symbol = 'NQ';
    show(318);
    load('NQ');
    out.stale = look();
  })();
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_a_journal_trade_picked_on_a_phones_journal_page_is_shown_when_the_chart_is():
    """On a phone the Journal page leaves the chart 0 px wide. A trade on another symbol switched it there, and its bars
    came in while the chart was hidden: their view waited for the width (viewLoad), but the scroll to the trade did
    not, so tapping Chart showed the latest 150 bars instead of the trade. The scroll now waits with the view and goes
    after it. A trade picked for one symbol is not looked for on another one picked since."""
    from test_hunt13_layout import studies_part
    parts = ["BAR_CAP", "olderReset", "applyHistory", "viewLoad", "latestRange", "viewApply", "viewSnap", "viewTimer", "viewSave",
             "journalShow", "goToTime"]
    src = (studies_part("barIndexTo") + "\n" + "\n".join(app_part(p) for p in parts) + "\n"
           + _statement("chart.timeScale().subscribeSizeChange("))
    out = _node(JOURNAL_HOLD_JS.replace("/*APP*/", src))
    ms = 1_700_000_000_000 + 500 * 300_000
    assert out["other"]["loaded"] == {"held": True, "goTo": {"sym": "NQ", "ms": ms}, "view": None}
    assert out["other"]["shown"] == {"held": False, "goTo": None, "view": [460, 560]}
    assert out["same"]["picked"]["held"] is True and out["same"]["picked"]["goTo"]["sym"] == "NQ"
    assert out["same"]["shown"] == {"held": False, "goTo": None, "view": [260, 360]}
    assert out["stale"] == {"held": False, "goTo": None, "view": [845, 1005]}   # the latest 150 bars and the margin


# ---- quick symbol/timeframe typing: what is typed right after the last Enter is kept -------------------------------

QUICK_JS = r"""
let now = 0, tid = 0;
const timers = new Map();
const setTimeout = (f, ms) => { timers.set(++tid, { f, at: now + ms }); return tid; }, clearTimeout = (i) => { timers.delete(i); };
const wait = (ms) => {   // the clock moves on, the timers due meanwhile firing in turn
  const end = now + ms;
  for (;;) {
    const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    timers.delete(due[0]); now = due[1].at; due[1].f();
  }
  now = end;
};
const document = { activeElement: null, body: { id: 'BODY' } };
const el = (id) => ({ id, listeners: {}, value: '', placeholder: '', classList: { toggle() {} },
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
  focus() { document.activeElement = this; },
  blur() { if (document.activeElement === this) { document.activeElement = document.body; (this.listeners.blur || []).forEach((f) => f()); } } });
const box = el('quick'), inp = el('quick-in');
let shown = false;   // as a browser: hiding the box takes the focus from its text box
Object.defineProperty(box, 'hidden', { get: () => !shown, set: (v) => { shown = !v; if (v && document.activeElement === inp) inp.blur(); } });
const $ = (s) => ({ '#quick': box, '#quick-in': inp }[s]);
let done = [];
const S = { instruments: { ES: {}, NQ: {}, GC: {}, CL: {} } };
const setSymbol = (s) => done.push(s), setSpec = (s) => done.push(s), toast = (m) => done.push(`toast: ${m}`), cmpAdd = () => {};
const quickSearch = { refresh() {}, clear() {}, active: () => null }, quickFormula = () => false, chartSym = () => null;
/*APP*/
// a key: into the box while it has the focus, else to the page, where a letter or digit opens the box
const key = (k) => {
  if (document.activeElement === inp) {
    if (k === 'Enter' || k === 'Escape') inp.listeners.keydown.forEach((f) => f({ key: k, preventDefault() {} }));
    else inp.value += k;
  } else if (/^[a-z0-9]$/i.test(k)) quickOpen(k);
};
const typed = (steps) => {   // [ms before, key]...
  done = []; now = 0; timers.clear(); document.activeElement = document.body; shown = false;
  for (const [ms, k] of steps) { wait(ms); key(k); }
  wait(500);
  return { done, shown, focus: document.activeElement.id, value: inp.value };
};
const out = {};
for (const gap of [200, 50, 20]) out[`tf${gap}`] = typed([[0, '1'], [300, 'Enter'], [gap, '5'], [300, 'Enter']]);
for (const gap of [200, 50]) out[`sym${gap}`] = typed([[0, 'G'], [40, 'C'], [300, 'Enter'], [gap, 'N'], [60, 'Q'], [300, 'Enter']]);
done = []; quickOpen('E'); inp.blur(); wait(150);   // clicked away: the box goes
out.away = shown;
console.log(JSON.stringify(out));
"""


@needs_node
def test_a_symbol_or_timeframe_typed_right_after_the_last_enter_is_not_lost():
    """Enter blurs the box and the blur hid it 100 ms later. A key within those 100 ms opened the box again, the old
    timer then hid it, the focus fell to the page and the rest went nowhere: '1' Enter '5' Enter stayed on 1m, 'GC'
    Enter 'NQ' Enter on GC (the box opened again with only 'Q'). The box opening again cancels the old timer, and the
    timer hides the box only when its text box has not the focus again."""
    parts = ["SPEC_RE", "quickCompare", "quickOpen", "quickClose", "quickSpec"]
    src = "\n".join(app_part(p) for p in parts) + "\n" + _statement("$('#quick-in').addEventListener('keydown'") + "\n" \
        + _statement("$('#quick-in').addEventListener('blur'")
    out = _node(QUICK_JS.replace("/*APP*/", src))
    closed = {"shown": False, "focus": "BODY"}
    for gap in (200, 50, 20):
        assert out[f"tf{gap}"] == {"done": ["1m", "5m"], "value": "5", **closed}, gap
    for gap in (200, 50):
        assert out[f"sym{gap}"] == {"done": ["GC", "NQ"], "value": "NQ", **closed}, gap
    assert out["away"] is False


# ---- a chart of a layout keeps its drawing tools as a column beside it ---------------------------------------------

BAR_JS = """
const wrap = { id: 'chart-wrap', before(b) { b.parentElement = 'main'; }, insertBefore(b) { b.parentElement = wrap; } };
const on = new Set(), bar = { parentElement: wrap, classList: { toggle: (c, v) => (v ? on.add(c) : on.delete(c)) } };
const $ = (s) => ({ '#draw-bar': bar, '#chart-wrap': wrap, '#replay-bar': {} }[s]);
const phoneWidth = { matches: false };
let TK_CELL = null;
/*APP*/
const out = [];
for (const [cell, narrow] of [[0, true], [1, true], [2, false], [null, false], [null, true]]) {
  TK_CELL = cell; phoneWidth.matches = narrow;
  placeDrawBar();
  out.push({ cell, narrow, col: on.has('col'), overChart: bar.parentElement === wrap });
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_each_chart_of_a_layout_keeps_its_drawing_tools_beside_it_however_narrow_its_frame():
    """'2 side by side' on 1500 px with the panel open: each frame 584 px wide, under the phone's 760, so the first
    chart (drawBar '1' from the single chart) put its tools in a phone's row over the chart's bottom, covering the Gap
    table's rows and the AMT notice's second line (CHARTS.md: on a computer they cover none of the chart). A chart of a
    layout (only on a window 760 px or wider) keeps the column; the page on a phone keeps its row."""
    out = run_app(["placeDrawBar"], BAR_JS)
    assert [(r["cell"], r["narrow"], r["col"], r["overChart"]) for r in out] == [
        (0, True, True, False), (1, True, True, False), (2, False, True, False), (None, False, True, False),
        (None, True, False, True)]
    # the phone's rules lay the bar out as a row and the page as a column in a frame under 760 px too: a chart of a
    # layout puts both back
    assert decl("main", "flex-direction", PHONE) == "column" and decl("#draw-bar", "flex-direction", PHONE) == "row"
    assert decl("body.cell main", "flex-direction") == "row"
    assert decl("body.cell #draw-bar.col", "flex-direction") == "column"


# ---- an iPhone turned sideways starts with the AMT table folded ----------------------------------------------------

DASH_JS = """
const rows = { children: [], replaceChildren(...k) { this.children = k; } };
const on = new Set(), dash = { hidden: false, classList: { toggle: (c, v) => (v ? on.add(c) : on.delete(c)) }, querySelector: (s) => (s === '.rows' ? rows : {}) };
const warn = { hidden: false, textContent: '' };
const $ = (s) => ({ '#amt-dash': dash, '#amt-warn': warn }[s]);
const document = { createElement: () => ({ className: '', style: {}, append() {} }) };
let store = {};
const local = { get: (k, d) => store[k] ?? d }, window = { innerWidth: 0 };
let screen = null;
const S = { st: { amt: true, amtDash: true }, ext: null,
  amt: { ready: true, warning: '', style: {}, dash: { labels: Array(8).fill('k'), values: Array(8).fill('v'), colors: Array(8).fill(0) } } };
const argb = () => '', amtTzWarning = () => '', placeAmtDash = () => {};
/*APP*/
const out = {};
for (const [name, w, sw, sh, kept] of [['phoneUpright', 390, 390, 844, ''], ['phoneTurned', 844, 390, 844, ''], ['pc', 1500, 1920, 1080, ''],
  ['pcNarrowWindow', 700, 1920, 1080, ''], ['phoneTurnedOpened', 844, 390, 844, '0']]) {
  window.innerWidth = w; screen = { width: sw, height: sh }; store = { amtCollapsed: kept };
  renderAmtDash();
  out[name] = [on.has('collapsed'), rows.children.length];
}
console.log(JSON.stringify(out));
"""


@needs_node
def test_an_iphone_turned_sideways_starts_with_the_amt_table_folded():
    """An iPhone 13 turned (844 x 390) gets the computer's layout, so the table, folded only under 760 px, opened with
    its 8 rows over two thirds of a 278 px high chart, the AMT notice written over its last row (CHARTS.md: on a phone
    the table starts folded). A phone by its screen starts folded either way up, to its title as upright; a computer
    as before, and a table the owner opened stays open."""
    out = run_app(["renderAmtDash"], DASH_JS)
    assert out == {"phoneUpright": [True, 0], "phoneTurned": [True, 0], "pc": [False, 8], "pcNarrowWindow": [True, 0],
                   "phoneTurnedOpened": [False, 8]}


def test_the_folded_amt_tab_shows_its_title_only_also_on_a_phone_turned_sideways():
    """Folded to its tab on a phone turned sideways (wider than 760 px, where the phone rules that hide the table's
    site do not apply), the tab showed the table's site line written over its title. The tab is its title only."""
    assert decl("#amt-dash .site", "display", PHONE) == "none"   # upright, as before
    assert decl("#amt-dash.pill .site", "display") == "none"


# ---- an alert heard on a page not yet tapped or clicked: no vibration asked for ------------------------------------

VIBRATE_JS = """
const asked = [];
const S = { events: [] }, renderEvents = () => {}, firedShow = () => {}, toast = () => {}, chime = () => {}, alertSound = () => '';
let TK_CELL = null;
function heard(navigator, m) {
  switch (m.type) {
/*CASE*/
  }
}
const nav = (active) => ({ ...(active === undefined ? {} : { userActivation: { hasBeenActive: active } }), vibrate: (ms) => asked.push([active ?? null, ms]) });
const m = { type: 'alert', event: { alert_id: 1, message: 'ES touched P 6896.00' } };
heard(nav(false), m);   // a page just reloaded (an update, a layout picked): the browser refuses it and logs an error
heard(nav(true), m);
heard(nav(undefined), m);   // a browser without userActivation
heard({}, m);   // nor vibrate (an iPhone)
TK_CELL = 0; heard(nav(true), m);   // a chart of a layout: its page asks
console.log(JSON.stringify(asked));
"""


@needs_node
def test_an_alert_heard_before_the_page_is_tapped_asks_for_no_vibration():
    """An alert heard by a page not tapped or clicked since it loaded (a layout's page right after it was picked, its
    charts taking the clicks) called navigator.vibrate, which Chrome refuses then and logs as a console error. It is
    asked for only once the page has had a tap or click."""
    case = app_lines("    case 'alert':", "      break;")
    out = _node(VIBRATE_JS.replace("/*CASE*/", case))
    assert out == [[True, 200], [None, 200]]
