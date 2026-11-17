"""If/Then Bias Table parity with NinjaTrader 1.0.0 (ninjatrader/TatankaIfThenBiasTable.cs): Price source "Input series"
reads an input series as NinjaTrader's Inputs[0][0] (the bars as a price type, or an indicator line on the chart), and
the background and border colors have an opacity (TatankaBiasStyle.Color: alpha times opacity). The price each source
reads and the colors' alpha are also cases of tests/ifthen_parity.py against the C#."""
import json

import pytest

from chartsrv import indicators
from test_hunt_web_chart import WEB, needs_node, run_app

IFTHEN = indicators.by_name("ifthen")


def test_only_the_background_and_border_colors_have_an_opacity():
    """The Position colors' alpha never shows in NinjaTrader: zones, lines and labels each set their own (Alpha)."""
    assert [n for n, x in IFTHEN.items() if x.get("alpha")] == ["BackgroundBrush", "BorderBrush"]
    assert IFTHEN["BackgroundBrush"]["default"] == "#060606"


def test_a_color_with_opacity_is_kept_as_rrggbbaa():
    assert indicators.clean("ifthen", {"BackgroundBrush": "#06060680", "BorderBrush": "#FF000040"}) == \
        {"BackgroundBrush": "#06060680", "BorderBrush": "#ff000040"}
    assert indicators.clean("ifthen", {"BackgroundBrush": "#060606FF"}) == {}   # fully opaque: NinjaTrader's default
    with pytest.raises(ValueError, match="#rrggbbaa"):
        indicators.clean("ifthen", {"BackgroundBrush": "#0606068"})
    with pytest.raises(ValueError, match="a #rrggbb color"):   # a Position color has none
        indicators.clean("ifthen", {"AboveBrush": "#f2364580"})


ADAPTER_JS = """
const CFG = [{ type: 'ema', len: 21 }, { type: 'boll', len: 14, dev: 2 }];   // the chart's indicators, and as built
const S = { symbol: 'ES', st: { ifthen: true }, bars: [{ o: 10, h: 14, l: 8, c: 12 }, { o: 12, h: 20, l: 11, c: 15 }], indCfg: CFG,
  inds: [{ c: CFG[0], vals: [[11], [13.5]] }, { c: CFG[1], vals: [[1, 2, 3], [18, 14, 10]] }],
  ind: { schema: { ifthen: { title: 'If/Then Bias Table', version: '1.0.0', settings: SCHEMA } }, overrides: {}, symbols: {},
    per_symbol: { ifthen: ['InstrumentLabel', 'RowsBlock'] }, share: {} }, ifData: null };
let TAB = { ifthen: { PriceSource: 'Input series', InputSeries: 'Bollinger 14 2: Lower' } };
const tabSet = () => TAB, saved = [], tabSetSave = (k, v) => saved.push([k, v]), setWhere = () => 'this chart';
const noop = () => {}, renderPlanLines = noop, setStudy = noop, instRemove = noop, indPutSymbol = async () => {};
const IND_DEFS = { ema: { lines: [['v', 'EMA']] }, boll: { lines: [['upper', 'Upper'], ['middle', 'Middle'], ['lower', 'Lower']] } };
const indTitle = (c) => (c.type === 'ema' ? `EMA ${c.len}` : `Bollinger ${c.len} ${c.dev}`), hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const VWAPS = [{ key: 've', at: 0 }, { key: 'vr', at: 2 }], instKeys = (b) => [b, ...Object.keys(S.st).filter((k) => k.startsWith(`${b}_`))];
/*APP*/
const a = indAdapter('ifthen'), row = a.schema()[0];
S.ifSet = { InputSeries: 'Bollinger 14 2: Lower' };
const out = { row: [row.name, row.group, row.choices], def: [a.defaults().InputSeries, a.ntDefaults().InputSeries],
  price: ['Input series', 'HLC3', 'Close'].map((src) => ifPrice(S.bars[1], src)) };
S.ifSet = { InputSeries: 'Median' }; out.median = ifPrice(S.bars[1], 'Input series');
S.ifSet = {}; out.none = ifPrice(S.bars[1], 'Input series');   // NinjaTrader's default input: the bars' Close
S.ifSet = { InputSeries: 'VWAP' }; out.gone = ifPrice(S.bars[1], 'Input series');   // an indicator taken off: Close
TAB = { ifthen: { InputSeries: 'VWAP' } };
out.kept = indAdapter('ifthen').schema()[0].choices.at(-1);   // still shown, as set
out.alpha = [hexA('#06060680'), hexA('#060606')];
(async () => {
  await a.save({ PriceSource: 'Input series', InputSeries: 'EMA 21' }, null);
  out.saved = saved;
  console.log(JSON.stringify(out));
})();
"""
PARTS = ["INST_RE", "baseOf", "instNo", "indSym", "indReady", "indDefaults", "llShared", "IND_NOTES", "same", "pickSet",
         "instFreeze", "IF_INPUT", "IF_BAR_INPUTS", "ifInputName", "ifNum", "ifStudyLines", "ifInputChoices", "ifInputValue",
         "ifPrice", "hexA", "indAdapter"]


@needs_node
def test_input_series_reads_the_bars_or_an_indicator_line_on_the_chart():
    """Its own row in the dialog (NinjaTrader's Data Series group), Close by default, its choices the bars' price
    types and each line of this chart's indicators; Price source "Input series" reads it at the last bar."""
    out = run_app(PARTS, ADAPTER_JS.replace("SCHEMA", json.dumps(indicators.SCHEMA["ifthen"]["settings"])))
    assert out["row"] == ["InputSeries", "Data Series", ["Close", "Open", "High", "Low", "Median", "Typical", "Weighted", "EMA 21",
                                                          "Bollinger 14 2: Upper", "Bollinger 14 2: Middle", "Bollinger 14 2: Lower"]]
    assert out["def"] == ["Close", "Close"]
    assert out["price"] == [10, (20 + 11 + 15) / 3.0, 15]
    assert out["median"] == 15.5 and out["none"] == 15 and out["gone"] == 15
    assert out["kept"] == "VWAP"
    assert out["alpha"] == [[6, 6, 6, 128 / 255], [6, 6, 6, 1]]
    assert out["saved"] == [["ifthen", {"PriceSource": "Input series", "InputSeries": "EMA 21"}]]


STUDY_LINES_JS = """
const S = { symbol: 'ES', st: { ifthen: true, ve: true, vr: true, b1: true, b2: false, hema: true, hema_2: true, dsma: true, aavp: true },
  bars: [{ o: 10, h: 14, l: 8, c: 12, st: [11, 0.5, null, null] }, { o: 12, h: 20, l: 11, c: 15, st: [13, 1, 14.5, 0.25] }],
  inds: [], hx: { hema: { vals: [NaN, 13.75] }, hema_2: { vals: [NaN, null] } }, dsmaVals: [[1, 2], [3, 4], [5, 6], [7, 8]] };
const VWAPS = [{ key: 've', at: 0 }, { key: 'vr', at: 2 }], IND_DEFS = {}, indTitle = () => '';
const instKeys = (b) => [b, ...Object.keys(S.st).filter((k) => k.startsWith(`${b}_`))];
/*APP*/
const at = (name, i = 1) => { const v = ifInputValue(name, i); return Number.isNaN(v) ? 'NaN' : v; };
const out = { choices: ifInputChoices().slice(7), values: {} };
for (const n of out.choices) out.values[n] = [at(n, 0), at(n)];
S.st.vr = false; S.st.hema_2 = false; S.st.dsma = false;
out.off = [ifInputChoices().slice(7), at('VWAP (RTH)'), at('HTF EMA 2')];   // taken off the chart: the bars' Close
console.log(JSON.stringify(out));
"""


@needs_node
def test_input_series_also_takes_the_vwap_htf_ema_and_daily_sma_lines_on_the_chart():
    """NinjaTrader's Input series can be any indicator on the chart: here also the session and RTH VWAP (with the bands
    shown), each HTF EMA and the four Daily SMA x4 plots, by name, read at the chart's bar."""
    out = run_app(["INST_RE", "baseOf", "instNo", "IF_BAR_INPUTS", "ifInputName", "ifNum", "ifStudyLines", "ifInputChoices",
                   "ifInputValue"], STUDY_LINES_JS)
    assert out["choices"] == ["VWAP (session)", "VWAP (session): +1σ", "VWAP (session): −1σ", "VWAP (RTH)", "VWAP (RTH): +1σ",
                              "VWAP (RTH): −1σ", "HTF EMA", "HTF EMA 2", "Daily SMA x4: DailySMA1", "Daily SMA x4: DailySMA2",
                              "Daily SMA x4: DailySMA3", "Daily SMA x4: DailySMA4"]
    assert out["values"] == {"VWAP (session)": [11, 13], "VWAP (session): +1σ": [11.5, 14], "VWAP (session): −1σ": [10.5, 12],
                             "VWAP (RTH)": ["NaN", 14.5], "VWAP (RTH): +1σ": ["NaN", 14.75], "VWAP (RTH): −1σ": ["NaN", 14.25],
                             "HTF EMA": ["NaN", 13.75], "HTF EMA 2": ["NaN", "NaN"], "Daily SMA x4: DailySMA1": [1, 2],
                             "Daily SMA x4: DailySMA2": [3, 4], "Daily SMA x4: DailySMA3": [5, 6], "Daily SMA x4: DailySMA4": [7, 8]}
    assert out["off"] == [["VWAP (session)", "VWAP (session): +1σ", "VWAP (session): −1σ", "HTF EMA"], 15, 15]


def _layout(*names):
    """Top-level functions and const lines of web/layout.js as written there."""
    src, out = (WEB / "layout.js").read_text(), []
    for name in names:
        i = src.index(f"function {name}(") if f"function {name}(" in src else src.index(f"const {name} ")
        out.append(src[i:src.index("\n}\n", i) + 3] if src[i:].startswith("function") else src[i:src.index("\n", i)])
    return "\n".join(out)


APPLY_ALL_JS = """
const TK_MULTI = false, saved = [], store = {}, posted = [];
const local = { get: (k, d) => store[k] ?? d, set: (k, v) => { store[k] = v; } };
const S = { tab: 0, st: { ifthen: true }, sharedSet: { ifthen: { PriceSource: 'Input series', InputSeries: 'Low' }, tpo: { X: 1 } }, tabs: [
  { own: { st: {}, set: { ifthen: { PriceSource: 'Input series', InputSeries: 'High', ShowTable: true } } } },   // this chart's open tab
  { own: { st: {}, set: { ifthen: { AtrLength: 20 } } } },                                                    // none of its own: all go
  { own: { st: {}, set: { ifthen: { InputSeries: 'EMA 21' } } } },                                            // only its own: kept as is
  { link: true }] };
const ownTab = () => S.tabs[S.tab], saveTabs = () => saved.push(1), indApply = () => {}, cellPost = (m) => posted.push(m);
const indDlg = { refresh() {} }, instFreeze = () => {}, renderAmtMenu = () => {}, amtFollow = () => {};
LAYOUT
/*APP*/
const third = S.tabs[2].own.set;
studyAllHere('ifthen');
console.log(JSON.stringify({ tabs: S.tabs.map((t) => t.own?.set ?? null), shared: S.sharedSet, stored: JSON.parse(store.indSet),
  saved: saved.length, same: S.tabs[2].own.set === third, omit: [tkStudyOmit({ tpo: {} }, 'tpo'), tkStudyOmit({ a: 1 }, 'tpo')] }));
"""


@needs_node
def test_apply_to_all_charts_keeps_each_charts_input_series():
    """Input series is each chart's own (its choices are the chart's indicators): Apply to all charts puts Price source
    and the rest on every chart but leaves each chart's Input series, this chart's too, as the dialog shows it."""
    out = run_app(["studyAllHere"], APPLY_ALL_JS.replace("LAYOUT", _layout("TK_CHART_ONLY", "tkStudyOmit")))
    assert out["tabs"] == [{"ifthen": {"InputSeries": "High"}}, {}, {"ifthen": {"InputSeries": "EMA 21"}}, None]
    assert out["shared"] == out["stored"] == {"ifthen": {"InputSeries": "Low"}, "tpo": {"X": 1}}
    assert out["saved"] == 1 and out["same"] is True and out["omit"] == [{}, {"a": 1}]


@needs_node
def test_apply_to_all_charts_keeps_the_input_series_of_other_charts_and_workspaces():
    """layout.js's studyAll takes the study's settings off every chart position and saved workspace, but not each
    one's Input series."""
    from test_hunt_web_ui import node
    tab = lambda s: {"id": "a", "sym": "ES", "spec": "5m", "own": {"st": {"ifthen": True}, "set": s}}   # noqa: E731
    keys = {"chartTabs": json.dumps([tab({"ifthen": {"InputSeries": "High", "AtrLength": 20}})]),
            "c1.chartTabs": json.dumps([tab({"ifthen": {"AtrLength": 20}})]),
            "c1.indSet": json.dumps({"ifthen": {"InputSeries": "Open", "PriceSource": "Input series"}}),
            "c1.studies": json.dumps({"ifthen": True})}
    other = {"id": "Y", "name": "Swing", "updated": 1001, "by": "pc", "data": {"layout": "1", "keys": {
        "chartTabs": json.dumps([tab({"ifthen": {"InputSeries": "EMA 21", "ShowTable": True}})])}}}
    main = {"id": "X", "name": "Day", "updated": 1000, "by": "pc", "data": {"layout": "1", "keys": keys}}
    out = node(f"""
const srv = makeServer([{json.dumps(main)}, {json.dumps(other)}]), t = makeTimers();
const B = makeBrowser(WEB, {{ keys: {{ ...{json.dumps(keys)}, wsId: 'X', wsStamp: '1000' }} }});
const w = srv.connect(B.open({{ api: srv.api, timers: t }}));
await w.ws.start();
const n = await w.ws.studyAll('ifthen', -1, null);
const sets = (json) => JSON.parse(json).map((x) => x.own?.set ?? null);
out({{ n, here: sets(B.shared.get('chartTabs')), c1: sets(B.shared.get('c1.chartTabs')), c1shared: JSON.parse(B.shared.get('c1.indSet')),
  other: sets(srv.get('Y').data.keys.chartTabs) }});""")
    assert out["n"] == 1
    assert out["here"] == [{"ifthen": {"InputSeries": "High"}}] and out["c1"] == [{}]
    assert out["c1shared"] == {"ifthen": {"InputSeries": "Open"}} and out["other"] == [{"ifthen": {"InputSeries": "EMA 21"}}]
