"""Round 22, studies on a live chart: If/Then's Input series through Apply to all charts and on a layout's page, the
Plan tab's Level Lines edits between a micro and its full-size contract, and the docs' #ARGB example."""
import json
import re

from chartsrv import indicators
from test_hunt_web_chart import WEB, app_lines, needs_node, run_app
from test_parity_ifthen import _layout
from test_parity_lines import studies

DOCS = WEB.parent / "docs"

# ---- h22-studies-1: Apply to all charts keeps the Input series picked in the dialog as this chart's ------------------

APPLY_ALL_JS = """
const TK_MULTI = false, TK_CELL = null, workspaces = null, store = {}, puts = [], noop = () => {};
const local = { get: (k, d) => store[k] ?? d, set: (k, v) => { store[k] = v; } };
const S = { symbol: 'ES', tab: 0, st: { ifthen: true }, sharedSet: {}, tabs: [{ id: 'a', own: { st: {}, set: {} } }],
  ind: { schema: { ifthen: { title: 'If/Then Bias Table', version: '1.0.0', settings: SCHEMA } }, overrides: {}, symbols: {},
    per_symbol: { ifthen: ['InstrumentLabel', 'RowsBlock'] }, share: {} }, ifData: null };
const saveTabs = noop, indApply = noop, cellPost = noop, renderAmtMenu = noop, amtFollow = noop, indDlg = { refresh: noop };
const instFreeze = () => null, setWhere = () => 'this chart', setStudy = noop, instRemove = noop, renderPlanLines = noop;
const indPutSymbol = async () => {}, indPut = async (k, v) => { puts.push(v); S.ind.overrides[k] = v; };
LAYOUT
/*APP*/
const a = indAdapter('ifthen');
// as the dialog's Apply to all charts: its draft (this chart's settings with the edits, applied or not), then every chart
const all = async (draft) => { await a.applyAll(draft); await studyAll('ifthen', null); return S.tabs[0].own.set; };
(async () => {
  const out = {};
  out.picked = [await all({ PriceSource: 'Input series', InputSeries: 'HTF EMA 2' }), puts[0]];   // none of its own before
  out.untouched = await all({ ...a.overrides() });   // the dialog showing what it has
  S.tabs[0].own.set = { ifthen: { InputSeries: 'Open', AtrLength: 20 } };
  out.back = await all({ AtrLength: 20 });   // set back to Close: the dialog's draft has none
  S.tabs[0].link = true; S.sharedSet = { ifthen: { InputSeries: 'Low' } };   // a linked tab: the chart's shared set
  await all({ PriceSource: 'Input series', InputSeries: 'EMA 21' });
  out.linked = [S.tabs[0].own.set, S.sharedSet, JSON.parse(store.indSet ?? 'null')];
  console.log(JSON.stringify(out));
})();
"""
PARTS = ["INST_RE", "baseOf", "instNo", "indSym", "indReady", "indDefaults", "llShared", "IND_NOTES", "same", "pickSet",
         "IF_INPUT", "IF_BAR_INPUTS", "ifInputName", "ifStudyLines", "ifInputChoices", "ownTab", "tabSet", "studyStore",
         "tabSetSave", "studyAll", "studyAllHere", "indAdapter"]


@needs_node
def test_apply_to_all_charts_keeps_the_input_series_picked_in_the_dialog_on_this_chart():
    """Picked in the dialog, then Apply to all charts without Apply: Price source "Input series" went to every chart
    while the pick was thrown away (the starting settings have no Input series, and every chart keeps its own as
    saved), so this chart's If/Then read Close. It is saved as this chart's first, and Close puts it back to none."""
    js = APPLY_ALL_JS.replace("SCHEMA", json.dumps(indicators.SCHEMA["ifthen"]["settings"]))
    out = run_app(PARTS, js.replace("LAYOUT", _layout("TK_CHART_ONLY", "tkStudyOmit")))
    assert out["picked"] == [{"ifthen": {"InputSeries": "HTF EMA 2"}}, {"PriceSource": "Input series"}]
    assert out["untouched"] == {"ifthen": {"InputSeries": "HTF EMA 2"}}
    assert out["back"] == {}
    assert out["linked"] == [{}, {"ifthen": {"InputSeries": "EMA 21"}}, {"ifthen": {"InputSeries": "EMA 21"}}]


# ---- h22-studies-2: a layout's page offers the chart last clicked's indicator lines as Input series ------------------

HOST_JS = """
const S = { st: { hema: true }, indCfg: [{ type: 'ema', len: 21 }, { type: 'boll', len: 14, dev: 2 }], inds: [] };
const IND_DEFS = { ema: { lines: [['v', 'EMA']] }, boll: { lines: [['upper', 'Upper'], ['middle', 'Middle'], ['lower', 'Lower']] } };
const indTitle = (c) => (c.type === 'ema' ? `EMA ${c.len}` : `Bollinger ${c.len} ${c.dev}`), VWAPS = [];
const instKeys = (b) => [b, ...Object.keys(S.st).filter((k) => k.startsWith(`${b}_`))];
/*APP*/
console.log(JSON.stringify(ifInputChoices().slice(7)));
"""


@needs_node
def test_a_layouts_page_offers_the_active_charts_indicator_lines_as_input_series():
    """The page around a layout's charts has the chart last clicked's indicator list but builds none of them: its
    Indicators dialog offered the HTF EMAs and VWAPs but not that chart's EMA 21 or Bollinger lines."""
    out = run_app(["INST_RE", "baseOf", "instNo", "IF_BAR_INPUTS", "ifInputName", "ifNum", "ifStudyLines", "ifInputChoices"], HOST_JS)
    assert out == ["EMA 21", "Bollinger 14 2: Upper", "Bollinger 14 2: Middle", "Bollinger 14 2: Lower", "HTF EMA"]


# ---- h22-studies-3: the docs' see-through green is #80F0 (#8F0F is magenta) -----------------------------------------

@needs_node
def test_the_docs_see_through_color_example_reads_as_it_says():
    """WPF's #ARGB is alpha first: #8F0F is #88FF00FF, a see-through magenta, not the green the docs said."""
    text = " ".join((DOCS / "INDICATORS.md").read_text().split())
    examples = re.findall(r"`(#[0-9A-Fa-f]{3,8})` is a see-through (\w+)", text)
    assert examples
    rgb = {"green": "0, 255, 0", "magenta": "255, 0, 255", "red": "255, 0, 0", "blue": "0, 0, 255"}
    got = studies(f"console.log(JSON.stringify({json.dumps([c for c, _ in examples])}.map(wpfColor)));")
    for (_, name), color in zip(examples, got):
        assert color.startswith(f"rgba({rgb[name]}, "), (name, color)


# ---- h22-studies-4: the Plan tab's Level Lines edits stay between a micro and its full-size contract ----------------

PLAN_JS = """
const els = {}, noop = () => {};
const box = (id) => (els[id] ||= { id, value: '', dataset: {}, textContent: '', classList: { toggle() {} } });
const document = { activeElement: null }, $ = box;
const LINES = { ES: { RangeTop: 6900, RangeBottom: 6800, CustomLevels: '6850|SAVED' }, NQ: { RangeTop: 25300, RangeBottom: 24800, CustomLevels: '25000|POC' } };
const S = { symbol: 'ES', ind: { schema: { lines: {} }, share: { lines: { MES: 'ES', MNQ: 'NQ' } } } };
Object.defineProperty(S, 'llSet', { get: () => LINES[indSym('lines')] });
const indReady = () => true, levelLinesOf = () => [], bondTick = () => 0, inst = () => ({ tick_size: 0.25 });
const llShared = () => S.symbol, renderLlLeft = noop, setStudy = noop, toast = noop, fail = noop, indPutSymbol = async () => {};
/*APP*/
/*PLAN*/
const boxes = LL_BOXES.map(box);
const type = (el, v) => { el.value = v; el.oninput({ target: el }); };
const shown = () => boxes.map((e) => [String(e.value), !!e.dataset.dirty]);
const go = (sym) => { S.symbol = sym; renderPlanLines(false); return shown(); };
renderPlanLines(true);
type(boxes[0], '6950'); type(boxes[2], '6850|SAVED, 6875|TYPED');
const out = { mes: go('MES'), es: go('ES') };
type(boxes[2], '6850|SAVED, 6960|ON MES');
out.mesTyped = go('MES');
out.nq = go('NQ');   // another symbol's levels: replaced
type(boxes[2], '25000|POC, 25100|TYPED');
out.mnq = go('MNQ');
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_plan_tabs_level_lines_edits_stay_between_a_micro_and_its_full_size_contract():
    """Since 1.1.1 MES shows and saves ES's levels, but a switch between them took the boxes for another symbol's
    and put the saved levels back over what was typed. Another symbol's levels (NQ) still replace them."""
    plan = app_lines("const LL_BOXES", "};")
    out = run_app(["INST_RE", "baseOf", "indSym", "markDirty", "fillBox"], PLAN_JS.replace("/*PLAN*/", plan))
    typed = [["6950", True], ["6800", False], ["6850|SAVED, 6875|TYPED", True]]
    assert out["mes"] == typed and out["es"] == typed
    assert out["mesTyped"] == [["6950", True], ["6800", False], ["6850|SAVED, 6960|ON MES", True]]
    assert out["nq"] == [["25300", False], ["24800", False], ["25000|POC", False]]
    assert out["mnq"] == [["25300", False], ["24800", False], ["25000|POC, 25100|TYPED", True]]
