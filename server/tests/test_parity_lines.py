"""Level Lines parity with NinjaTrader 1.1.1 (ninjatrader/TatankaLevelLines.cs): labels of off-tick lines at their own
price as BuildLabelText writes it (Treasuries too), WPF's short hex colors (#RGB, #ARGB) and its named colors, a stroke's
opacity on its line and label, the label font's Bold and Italic."""
import json
import subprocess

import pytest

from chartsrv import indicators
from test_hunt_web_chart import WEB, needs_node, run_app

LINES = indicators.by_name("lines")


def studies(js: str):
    """Runs js in node after web/studies.js (its functions in scope); what it logs last is the result (JSON)."""
    src = f"""
const vm = require('vm'), fs = require('fs');
const ctx = {{ console, document: {{ createElement: () => ({{ getContext: () => ({{}}) }}) }}, window: {{}}, navigator: {{}} }};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({json.dumps(str(WEB / 'studies.js'))}, 'utf8') + '\\n' + {json.dumps(js)}, ctx);"""
    r = subprocess.run(["node", "-e", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().split("\n")[-1])


@needs_node
def test_short_hex_and_wpf_names():
    """ColorConverter.ConvertFromString: #RGB and #ARGB double each digit (alpha first), as #RRGGBB and #AARRGGBB;
    names in any case, but only WPF's (no grey spellings, rebeccapurple, CSS keywords or system colors)."""
    out = studies("""console.log(JSON.stringify(['#F0F', '#8F0F', '#ff00ff', '#80FF00FF', ' #0f0 ', 'Magenta', 'DARKGRAY',
  'grey', 'darkslategrey', 'rebeccapurple', 'inherit', 'currentcolor', 'Canvas', '#F0', '#F0F0F', 'Transparent', ''].map(wpfColor)));""")
    assert out == ["#ff00ff", "rgba(255, 0, 255, 0.5333333333333333)", "#ff00ff", "rgba(255, 0, 255, 0.5019607843137255)",
                   "#00ff00", "magenta", "darkgray", None, None, None, None, None, None, None, None, "transparent", None]


@needs_node
def test_a_level_with_a_short_hex_color_takes_it():
    out = studies("""const d = { color: '#ff00ff', dash: 'Solid', width: 2 };
console.log(JSON.stringify(parseLevelLines('7845.75|VAH|#0F0|Dash|1, 7830|POC|#80F0, 7810|VAL|grey', d).map((l) => l.color)));""")
    assert out == ["#00ff00", "rgba(0, 255, 0, 0.5333333333333333)", "#ff00ff"]   # grey is no WPF color: the default


@needs_node
def test_off_tick_labels_as_ninjatrader_writes_them():
    """BuildLabelText: on a tick FormatPrice; off it, "0." with the decimals FormatPrice writes (at least 1) and up to
    4 more. A Treasury's FormatPrice (110'165) has no ".": its off-tick line reads 110.50781, not a 32nd."""
    out = run_app(["llPrice"], """
let I = {};
const inst = () => I, bondTick = (s) => (s === 'ZN' ? 0.015625 : 0);
const px = (p) => (I.symbol === 'ZN' ? bond32(p, I.tick_size) : Number(p).toFixed(I.decimals));
function bond32(p, tick) {   // web/studies.js
  const per = Math.round(1 / tick), n = Math.round(Math.abs(p) / tick), whole = Math.floor(n / per);
  const x32 = (n - whole * per) * tick * 32, t = Math.floor(x32 + 1e-9);
  const tail = tick < 1 / 32 ? String(Math.floor((x32 - t) * 10 + 1e-9)) : '';
  return `${p < 0 && n ? '-' : ''}${whole}'${String(t).padStart(2, '0')}${tail}`;
}
/*APP*/
const at = (i, ps) => { I = i; return ps.map(llPrice); };
console.log(JSON.stringify({
  zn: at({ symbol: 'ZN', tick_size: 0.015625, decimals: 6 }, [110.515625, 110.5078125, 110.51, 110.123456789]),
  gc: at({ symbol: 'GC', tick_size: 0.1, decimals: 1 }, [2000.1, 2000.15, 2000.123456789]),
  ym: at({ symbol: 'YM', tick_size: 1, decimals: 0 }, [45000, 45000.5, 45000.123456]),
  es: at({ symbol: 'ES', tick_size: 0.25, decimals: 2 }, [5027.625, 7845.1, 7845.123456789, 7845.5]),
}));
""")
    assert out == {"zn": ["110'165", "110.50781", "110.51", "110.12346"],
                   "gc": ["2000.1", "2000.15", "2000.12346"],
                   "ym": ["45000", "45000.5", "45000.12346"],
                   "es": ["5027.625", "7845.10", "7845.123457", "7845.50"]}


RENDER = """
const calls = [];
const ctx = { set globalAlpha(v) { this._a = v; }, get globalAlpha() { return this._a; }, _a: 1, font: '',
  save() {}, restore() { this._a = 1; }, setLineDash() {}, beginPath() {}, moveTo() {}, lineTo() {}, fillRect(x, y, w, h) { calls.push(['box', this._a]); },
  stroke() { calls.push(['line', this._a, this.strokeStyle]); }, fillText(t) { calls.push(['text', t, this._a, this.font]); },
  measureText: (t) => ({ width: t.length * 6 }) };
const series = { priceToCoordinate: (p) => 500 - (p - 7800) * 4 };
const s = { RangeTop: 7850, RangeBottom: 7800, RangeTopLabel: 'TOP', RangeBottomLabel: 'BOT', ShowRangeMid: true, RangeMidLabel: 'MID',
  RangeStroke: { color: '#ffa500', dash: 'Solid', width: 2, opacity: 40 }, RangeMidStroke: { color: '#ffa500', dash: 'Dash', width: 1 },
  FillRange: false, CustomLevels: '7830|POC|#80FF00FF', DefaultStroke: { color: '#ff00ff', dash: 'Solid', width: 2, opacity: 10 },
  LabelFont: FONT, ShowPriceInLabel: false, LabelsOnRight: true, LabelAboveLine: true, LabelBackground: true };
drawLevelLines(ctx, { width: 800, height: 600 }, series, s, (p) => String(p));
console.log(JSON.stringify(calls));
"""


@needs_node
def test_stroke_opacity_on_its_line_and_label_not_the_background():
    """The range stroke at 40%: its two lines and their labels at 0.4, the label background as always (black 60%).
    The midpoint's stroke with no opacity saved (from before there was one) is NinjaTrader's 100. A custom level is
    a new Stroke at 100, whatever the Default line's opacity: only its color's own alpha (#80FF00FF) shows."""
    calls = studies(RENDER.replace("FONT", "{ family: 'Arial', size: 11 }"))
    lines = [c for c in calls if c[0] == "line"]
    assert [c[1] for c in lines] == [0.4, 0.4, 1, 1]   # top, bottom, mid, the custom level
    assert lines[3][2] == "rgba(255, 0, 255, 0.5019607843137255)"
    text = {c[1]: c[2] for c in calls if c[0] == "text"}
    assert text == {"TOP": 0.4, "BOT": 0.4, "MID": 1, "POC": 1}
    assert {c[1] for c in calls if c[0] == "box"} == {1}


@needs_node
def test_label_font_bold_and_italic():
    calls = studies(RENDER.replace("FONT", "{ family: 'Consolas', size: 13, bold: true, italic: true }"))
    assert {c[3] for c in calls if c[0] == "text"} == {"italic bold 13px Consolas, sans-serif"}
    calls = studies(RENDER.replace("FONT", "{ family: 'Arial', size: 11, bold: true }"))
    assert {c[3] for c in calls if c[0] == "text"} == {"bold 11px Arial, sans-serif"}


def test_schema_opacity_on_the_range_strokes_and_font_style():
    """The range strokes have NinjaTrader's opacity (100); the Default line has none (each custom level is drawn at
    100 in NinjaTrader). The label font: Bold and Italic off, as SimpleFont("Arial", 11)."""
    assert LINES["RangeStroke"]["opacity"] and LINES["RangeMidStroke"]["opacity"]
    assert LINES["RangeStroke"]["default"] == {"color": "#ffa500", "dash": "Solid", "width": 2.0, "opacity": 100}
    assert "opacity" not in LINES["DefaultStroke"] and "opacity" not in LINES["DefaultStroke"]["default"]
    assert LINES["LabelFont"]["default"] == {"family": "Arial", "size": 11, "bold": False, "italic": False}
    hema = indicators.by_name("hema")["PlotHTFEMA"]
    assert "opacity" not in hema and "opacity" not in hema["default"]


def test_settings_keep_opacity_and_font_style():
    got = indicators.clean("lines", {"RangeStroke": {"color": "#FFA500", "dash": "Solid", "width": 2, "opacity": 35},
                                     "LabelFont": {"family": "Arial", "size": 11, "bold": True, "italic": False}})
    assert got == {"RangeStroke": {"color": "#ffa500", "dash": "Solid", "width": 2.0, "opacity": 35},
                   "LabelFont": {"family": "Arial", "size": 11, "bold": True, "italic": False}}
    # NinjaTrader's defaults are not kept; one saved before (no opacity, no bold) reads as them
    assert indicators.clean("lines", {"RangeStroke": {"color": "#ffa500", "dash": "Solid", "width": 2},
                                      "LabelFont": {"family": "Arial", "size": 11}}) == {}
    for bad in (101, -1, 50.5, True, "50"):
        with pytest.raises(ValueError, match="opacity 0 to 100"):
            indicators.clean("lines", {"RangeMidStroke": {"color": "#ffa500", "dash": "Dash", "width": 1, "opacity": bad}})
    with pytest.raises(ValueError, match="bold and italic"):
        indicators.clean("lines", {"LabelFont": {"family": "Arial", "size": 11, "bold": 1}})
    # a stroke this app draws no opacity for doesn't keep one
    assert indicators.clean("hema", {"PlotHTFEMA": {"color": "#808080", "dash": "Solid", "width": 2, "opacity": 50}}) == {}


# ---- Sharing: a micro shows its full-size contract's levels; a second Level Lines has this chart's own -------------

class Mem:
    """A store's settings only."""

    def __init__(self, **kw):
        self.s = json.loads(json.dumps(kw))

    def get_setting(self, k, d=None):
        return json.loads(json.dumps(self.s[k])) if k in self.s else d

    def set_setting(self, k, v):
        self.s[k] = json.loads(json.dumps(v))


def test_a_micro_shares_its_full_size_contracts_levels():
    """TatankaLevelLines.cs ShareKey/MicroPairs: MES with ES, MNQ with NQ, ... under the full-size root. The If/Then
    block (no sharing in NinjaTrader) stays the micro's own."""
    assert indicators.SHARE["lines"] == {"MES": "ES", "MNQ": "NQ", "MYM": "YM", "M2K": "RTY", "MCL": "CL", "MGC": "GC",
                                         "SIL": "SI", "MHG": "HG"}
    st = Mem(ind_symbol={})
    ind = indicators.IndicatorSettings(st)
    assert ind.put_symbol("lines", "MES", {"RangeTop": 6950, "CustomLevels": "6900|POC"}) == {"RangeTop": 6950, "CustomLevels": "6900|POC"}
    assert st.s["ind_symbol"] == {"lines": {"ES": {"RangeTop": 6950, "CustomLevels": "6900|POC"}}}
    assert ind.value("lines", "RangeTop", "MES") == ind.value("lines", "RangeTop", "ES") == 6950
    ind.put_symbol("lines", "ES", {"RangeBottom": 6800})   # the ES chart edits them: MES shows the same
    assert ind.value("lines", "RangeBottom", "MES") == 6800
    ind.put_symbol("ifthen", "MES", {"InstrumentLabel": "MES"})
    assert ind.symbols["ifthen"] == {"MES": {"InstrumentLabel": "MES"}}
    assert ind.snapshot()["share"] == {"lines": indicators.SHARE["lines"]}


def test_a_micros_levels_saved_before_move_to_its_full_size_contract():
    """Levels a micro had as its own: moved to the full-size root (where its chart reads them now) when that has none;
    when it has, both show the full-size ones and the micro's are set aside (ind_symbol_left, shown on the Plan tab).
    Once: the start after records it (ind_share)."""
    st = Mem(ind_symbol={"lines": {"MNQ": {"RangeTop": 25300}, "MES": {"RangeTop": 6950}, "ES": {"RangeTop": 7000}}})
    ind = indicators.IndicatorSettings(st)
    assert st.s["ind_symbol"]["lines"] == {"NQ": {"RangeTop": 25300}, "ES": {"RangeTop": 7000}}
    assert st.s["ind_symbol_left"] == {"lines": {"MES": {"RangeTop": 6950}}} and st.s["ind_share"] == 1
    assert ind.value("lines", "RangeTop", "MNQ") == 25300 and ind.value("lines", "RangeTop", "MES") == 7000
    assert ind.snapshot()["left"] == {"lines": {"MES": {"RangeTop": 6950}}}
    st = Mem(ind_symbol={"lines": {"ES": {"RangeTop": 7000}}})
    indicators.IndicatorSettings(st)
    assert st.s == {"ind_symbol": {"lines": {"ES": {"RangeTop": 7000}}}, "ind_share": 1}   # nothing to move


def test_levels_cleared_on_the_full_size_contract_stay_cleared_after_a_restart():
    """ES and MES both had levels before they shared. ES's show on both; cleared on the Plan tab (every value back at
    its default, so ES's entry goes), they stay cleared through a restart: MES's old ones never come back as ES's.
    Dismissed, the set-aside ones go."""
    st = Mem(ind_symbol={"lines": {"ES": {"RangeTop": 6921, "RangeBottom": 6905},
                                   "MES": {"RangeTop": 6925, "RangeBottom": 6901, "CustomLevels": "6915.00|MES ONLY|Cyan"}}})
    ind = indicators.IndicatorSettings(st)
    assert ind.put_symbol("lines", "ES", {"RangeTop": 0, "RangeBottom": 0, "CustomLevels": ""}) == {}
    assert st.s["ind_symbol"] == {"lines": {}}
    ind = indicators.IndicatorSettings(st)   # the restart (an auto-update)
    assert ind.value("lines", "RangeTop", "ES") == ind.value("lines", "RangeTop", "MES") == 0
    assert ind.value("lines", "CustomLevels", "MES") == "" and st.s["ind_symbol"] == {"lines": {}}
    assert ind.left == {"lines": {"MES": {"RangeTop": 6925, "RangeBottom": 6901, "CustomLevels": "6915.00|MES ONLY|Cyan"}}}
    assert ind.forget_left("lines", "MES") and not ind.forget_left("lines", "MES")
    assert st.s["ind_symbol_left"] == {} and indicators.IndicatorSettings(st).left == {}


INST_JS = """
const S = { symbol: 'MES', st: { lines: true, lines_2: true },
  ind: { schema: { lines: { title: 'Level Lines', version: '1.1.1', settings: SCHEMA } }, overrides: { lines: { ShowRangeMid: false } },
    symbols: { lines: { ES: { RangeTop: 6950, RangeBottom: 6850, CustomLevels: '6900|POC' }, MES: { RangeTop: 1 } } },
    per_symbol: { lines: ['CustomLevels', 'RangeBottom', 'RangeTop'] }, share: { lines: { MES: 'ES', MNQ: 'NQ' } } } };
let TAB = { lines_2: { RangeTop: 6990, CustomLevels: '6975|MINE' }, lines: { CustomLevels: 'a preset of Level Lines 2' } };
const tabSet = () => TAB, saved = [], tabSetSave = (k, v) => saved.push([k, v]), setWhere = () => 'this chart';
const noop = () => {}, renderPlanLines = noop, setStudy = noop, instRemove = noop, indPutSymbol = async () => {};
/*APP*/
const pick = (o) => ({ top: o.RangeTop, bot: o.RangeBottom, lv: o.CustomLevels, mid: o.ShowRangeMid });
const a1 = indAdapter('lines'), a2 = indAdapter('lines_2');
const out = { first: pick(indValues('lines')), second: pick(indValues('lines_2')), es: pick(indValues('lines', 'ES')),
  names: [a1.symbolNames, a2.symbolNames], label: [a1.symbolLabel(), llShared('NQ'), llShared('CL')], notes: [a1.note, a2.note],
  per: a1.perSymbol().RangeTop };
(async () => {
  await a1.save({ ShowRangeMid: true, CustomLevels: 'x', RangeTop: 5 }, null);
  await a2.save({ RangeTop: 6991, CustomLevels: '6975|MINE' }, null);
  out.saved = saved;
  console.log(JSON.stringify(out));
})();
"""
INST_PARTS = ["INST_RE", "baseOf", "instNo", "indSym", "indReady", "indDefaults", "indValues", "llShared", "IND_NOTES", "same",
              "pickSet", "instFreeze", "indAdapter"]


@needs_node
def test_level_lines_on_a_micro_and_a_second_one_with_this_charts_own():
    """MES shows ES's range and levels (its own old ones unused); Level Lines 2 has the chart's own range and levels
    (Sharing Off), the first one's look; the first never keeps the symbol's settings as the chart's."""
    out = run_app(INST_PARTS, INST_JS.replace("SCHEMA", json.dumps(indicators.SCHEMA["lines"]["settings"])))
    assert out["first"] == {"top": 6950, "bot": 6850, "lv": "6900|POC", "mid": False}   # not a stray one the tab holds
    assert out["second"] == {"top": 6990, "bot": 0, "lv": "6975|MINE", "mid": False}
    assert out["es"]["top"] == 6950 and out["per"] == 6950
    assert out["names"] == [["CustomLevels", "RangeBottom", "RangeTop"], []]
    assert out["label"] == ["ES/MES", "NQ/MNQ", "CL"]
    assert "share them" in out["notes"][0] and "Sharing mode Off" in out["notes"][1]
    assert out["saved"] == [["lines", {"ShowRangeMid": True}], ["lines_2", {"RangeTop": 6991, "CustomLevels": "6975|MINE"}]]


@needs_node
def test_a_second_level_lines_is_added_layered_and_taken_off():
    """Level Lines 2 is another of a study as HTF EMA 2: its own key and layer next to the first's; taken off, its
    drawing goes and its settings with it."""
    from test_multi_studies import HELPERS
    out = run_app(HELPERS + ["instAdd", "instPut", "instRemove", "instDrop", "layerDefault", "layersClean"], """
const TK_MULTI = false, toasts = [], saved = [], changed = [], detached = [], removed = [];
const toast = (m) => toasts.push(m), tab = {}, tabSet = () => tab, tabSetSave = (k, v) => { saved.push([k, v]); if (v) tab[k] = v; else delete tab[k]; };
const studiesChanged = (k) => changed.push([k, S.st[k]]), setLayers = () => {}, indSets = () => {}, legend = () => {}, saveHidden = () => {};
const chart = { removeSeries: (s) => removed.push(s) }, candles = { detachPrimitive: (p) => detached.push(p) };
const vpPrims = {}, vpHeld = {}, vpBusy = {}, llPrims = {}, vpTouchMemo = {};
const LAYER_DEFAULT = ['fills', 'lines', 'amt', 'bars'];
let stOpen = '';
/*APP*/
var S = { st: studiesFrom({ _preset: 2, lines: true }), stHidden: {}, hx: {}, vp: {}, vpErr: {}, vpLive: {}, lastBar: null };
const out = { add: instAdd('lines') };
llPrims.lines_2 = 'llprim2';
tab.lines_2 = { CustomLevels: '6975|MINE' };
out.layers = layersClean(['bars', 'lines', 'amt']);
instRemove('lines_2');
out.after = [Object.keys(S.st).filter((k) => k.startsWith('lines')), detached, 'lines_2' in llPrims, 'lines_2' in tab];
console.log(JSON.stringify(out));
""")
    assert out["add"] == "lines_2"
    assert out["layers"].index("lines_2") == out["layers"].index("lines") + 1
    assert out["after"] == [["lines"], ["llprim2"], False, False]


from test_amt import client  # noqa: E402,F401  (the app on the simulator, as a fixture)


def test_the_api_saves_a_micros_level_lines_as_its_full_size_contracts(client):  # noqa: F811
    r = client.put("/api/indicators/lines/MES", json={"RangeTop": 6950, "RangeBottom": 6850})
    assert r.status_code == 200 and r.json() == {"RangeTop": 6950, "RangeBottom": 6850}
    got = client.get("/api/indicators").json()
    assert got["symbols"]["lines"] == {"ES": {"RangeTop": 6950, "RangeBottom": 6850}}
    assert got["share"]["lines"]["MES"] == "ES"
    assert client.put("/api/indicators/ifthen/MES", json={"InstrumentLabel": "MES"}).status_code == 200
    assert client.get("/api/indicators").json()["symbols"]["ifthen"]["MES"] == {"InstrumentLabel": "MES"}


def test_a_micro_alone_saves_its_level_lines_and_dismisses_the_set_aside_ones(client, monkeypatch):  # noqa: F811
    """An MES chart opened on its own (ES not an instrument here): its levels save as ES's, asked as MES (what the page
    sends) or as ES; a symbol with neither is unknown. The If/Then block is never shared."""
    import chartsrv.app as A
    m = A.M()
    monkeypatch.delitem(m.instruments, "ES")
    r = client.put("/api/indicators/lines/MES", json={"RangeTop": 6950})
    assert r.status_code == 200 and r.json() == {"RangeTop": 6950}
    assert client.put("/api/indicators/lines/ES", json={"RangeBottom": 6850}).status_code == 200
    assert client.get("/api/indicators").json()["symbols"]["lines"] == {"ES": {"RangeTop": 6950, "RangeBottom": 6850}}
    assert client.put("/api/indicators/ifthen/ES", json={"InstrumentLabel": "ES"}).status_code == 404
    assert client.put("/api/indicators/lines/ZZZ", json={"RangeTop": 1}).status_code == 404
    assert client.delete("/api/indicators/lines/MES/left").status_code == 404   # none set aside
    m.ind.left = {"lines": {"MES": {"RangeTop": 6925}}}
    assert client.get("/api/indicators").json()["left"] == {"lines": {"MES": {"RangeTop": 6925}}}
    r = client.delete("/api/indicators/lines/mes/left")
    assert r.status_code == 200 and r.json() == {"left": {}} and m.ind.left == {}


SAVE_JS = """
const S = { symbol: 'MES', ifData: null, ind: { symbols: {}, share: { lines: { MES: 'ES' } },
  left: { lines: { MES: { RangeTop: 6925.5, CustomLevels: '6915|MES ONLY' }, MNQ: { RangeTop: 1 } } } } };
const asked = [], els = {}, $ = (q) => (els[q] ??= { hidden: true, textContent: '', value: '' });
const api = async (method, path, body) => { asked.push([method, path]); return method === 'DELETE' ? { left: {} } : body; };
const indCache = () => {}, indApply = () => {}, renderPlanIf = () => {}, biasParse = () => [], fail = (e) => { throw e; };
const llRangeShown = (p) => String(p);
/*APP*/
/*DISMISS*/
(async () => {
  await indPutSymbol('lines', { RangeTop: 6950 });
  await indPutSymbol('ifthen', { InstrumentLabel: 'MES' });
  renderLlLeft();
  const out = { asked: [...asked], symbols: S.ind.symbols, shown: [els['#pl-ll-left'].hidden, els['#pl-ll-left-what'].textContent,
    els['#pl-ll-left-text'].value] };
  S.symbol = 'ES';
  renderLlLeft();
  out.onEs = els['#pl-ll-left'].hidden;
  S.symbol = 'CL';
  renderLlLeft();
  out.onCl = els['#pl-ll-left'].hidden;
  S.symbol = 'MES';
  await els['#pl-ll-left-x'].onclick();
  out.after = [asked.at(-1), els['#pl-ll-left'].hidden];
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_the_page_saves_a_micros_levels_as_its_own_symbol_and_shows_the_set_aside_ones():
    """The PUT names the chart's own symbol (the full-size one may not be an instrument here); the answer is kept as the
    full-size contract's. A micro's set-aside levels show on its and its full-size contract's Plan tab until dismissed."""
    from test_hunt_web_chart import app_lines
    out = run_app(["baseOf", "INST_RE", "instNo", "indSym", "indSymbolSaved", "indPutSymbol", "llLeft", "renderLlLeft"],
                  SAVE_JS.replace("/*DISMISS*/", app_lines("$('#pl-ll-left-x').onclick", "};")))
    assert out["asked"] == [["PUT", "/api/indicators/lines/MES"], ["PUT", "/api/indicators/ifthen/MES"]]
    assert out["symbols"] == {"lines": {"ES": {"RangeTop": 6950}}, "ifthen": {"MES": {"InstrumentLabel": "MES"}}}
    assert out["shown"] == [False, "MES had levels of its own before it shared ES's. They are not shown: copy what you "
                                   "want into the boxes above.", "Range top 6925.5\n6915|MES ONLY"]
    assert out["onEs"] is False and out["onCl"] is True
    assert out["after"] == [["DELETE", "/api/indicators/lines/MES/left"], True]


# ---- Apply to all charts on the first Level Lines with a Level Lines 2 preset loaded ------------------------------------

ALL_JS = r"""
function el(tag = 'div') {
  return { tagName: tag.toUpperCase(), children: [], get options() { return this.children; }, style: {}, dataset: {}, hidden: false,
    textContent: '', className: '', value: '', id: '', sel: {}, classList: { toggle() {}, add() {}, remove() {} },
    append(...k) { this.children.push(...k); }, replaceChildren(...k) { this.children = k; }, addEventListener() {}, setAttribute() {},
    focus() {}, remove() {}, querySelectorAll: () => [],
    querySelector(s) { return s.startsWith('.set') || s.startsWith('.ind-item') || s === '.chg' ? null : (this.sel[s] ||= el()); } };
}
function Option(text, value) { this.text = text; this.value = value; }
const document = { createElement: (t) => el(t), activeElement: null }, window = { innerWidth: 1400 };
/*IND*/
const TK_MULTI = false, calls = [], toasts = [], store = {}, noop = () => {};
const local = { get: (k, d) => store[k] ?? d, set: (k, v) => { store[k] = v; } };
const rawLocal = (k, d) => { try { return JSON.parse(local.get(k, '')) ?? d; } catch { return d; } };
const toast = (t) => toasts.push(t), fail = (e) => toasts.push(`FAIL ${e.message}`);
const PER = ['CustomLevels', 'RangeBottom', 'RangeTop'], OWN = { RangeTop: 6912, RangeBottom: 6892, CustomLevels: '6902|MINE' };
const tab = (set) => ({ own: { st: { lines: true, lines_2: true }, set } });
const S = { symbol: 'ES', tab: 0, st: { lines: true, lines_2: true }, sharedSet: {}, tabs: [tab({ lines_2: OWN }), tab({ lines_2: OWN })],
  ind: { schema: { lines: { title: 'Tatanka Level Lines', version: '1.1.1', settings: SCHEMA } }, overrides: {},
    symbols: { lines: { ES: { RangeTop: 6950, RangeBottom: 6850, CustomLevels: '6900|POC' } } }, per_symbol: { lines: PER },
    share: { lines: { MES: 'ES' } } } };
const ownTab = () => S.tabs[S.tab], tabSet = () => ownTab().own.set || {};
const tabSetSave = (k, v) => { const set = { ...tabSet() }; if (v && Object.keys(v).length) set[k] = v; else delete set[k]; ownTab().own = { ...ownTab().own, set }; };
const [saveTabs, cellPost, indCache, indApply, setStudy, instRemove, renderPlanLines] = Array(7).fill(noop);
const setWhere = () => 'this chart', indPutSymbol = async () => {};
let refuse = [];
const api = async (method, path, body) => {   // PUT /api/indicators/{key}, as the server: a symbol's own setting is refused
  calls.push([method, path, JSON.parse(JSON.stringify(body))]);
  const bad = Object.keys(body).find((n) => PER.includes(n) || refuse.includes(n));
  if (bad) throw new Error(`unknown setting '${bad}'`);
  return body;
};
const T = { kinds: {}, names: (k) => Object.keys(T.kinds[k] || {}), data: (k, n) => T.kinds[k]?.[n], del: noop,
  save: async (k, n, d) => { (T.kinds[k] ||= {})[n] = JSON.parse(JSON.stringify(d)); } };
TK_INST_FREEZE
/*APP*/
const root = el(); root.hidden = true;
const dlg = createIndicatorsDialog({ root, local, toast, fail, symbol: () => S.symbol, ask: async () => 'L2 preset', templates: () => T,
  adapters: () => ['lines', 'lines_2'].map(indAdapter), allCharts: async () => 0 });
const sets = () => S.tabs.map((t) => t.own.set);
(async () => {
  const out = {};
  dlg.open(true, 'lines_2');
  await root.sel['.ind-tpl'].onchange({ target: { value: 's' } });   // Presets: Save as preset...
  out.preset = T.data('ind:lines', 'L2 preset');
  dlg.open(false);
  dlg.open(true, 'lines');
  await root.sel['.ind-tpl'].onchange({ target: { value: 'a:L2 preset' } });   // loaded into the first Level Lines
  await root.sel['.ind-all'].onclick();   // Apply to all charts
  out.loaded = { calls: calls.splice(0), toasts: toasts.splice(0), sets: sets(), start: S.ind.overrides.lines };
  // the first one's draft holding them anyway (an older page's preset): never sent, Level Lines 2 keeps them
  await indAdapter('lines').applyAll({ ...OWN, ShowRangeMid: false });
  out.direct = { calls: calls.splice(0), sets: sets(), start: S.ind.overrides.lines };
  // a refused save: every tab as it was (Level Lines 2's own Show range mid off equals the new starting setting)
  S.ind.overrides.lines = {};
  for (const t of S.tabs) t.own = { ...t.own, set: { lines_2: { ...OWN, ShowRangeMid: false } } };
  refuse = ['ShowRangeMid'];
  const before = JSON.stringify(sets());
  try { await indAdapter('lines').applyAll({ ShowRangeMid: false }); } catch (e) { out.refused = e.message; }
  out.kept = JSON.stringify(sets()) === before;
  refuse = [];
  await indAdapter('lines').applyAll({ ShowRangeMid: false });   // taken: Level Lines 2 now has it from the starting ones
  out.taken = sets();
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_apply_to_all_with_a_level_lines_2_preset_keeps_its_range_and_levels():
    """A preset saved from Level Lines 2 holds its range and levels (that one's own). Loaded into the first Level Lines
    and applied to all charts, they were sent as starting settings: the server refused them (400 "unknown setting
    'RangeTop'"), but every tab had already dropped Level Lines 2's range and levels as equal to them. Now loading it
    leaves the symbol's out, Apply to all charts never sends them, and a refused save puts every tab back."""
    from test_hunt21_web import _layout_part
    js = ALL_JS.replace("/*IND*/", (WEB / "indicators.js").read_text()).replace("TK_INST_FREEZE", _layout_part("tkInstFreeze"))
    out = run_app(INST_PARTS + ["indPut"], js.replace("SCHEMA", json.dumps(indicators.SCHEMA["lines"]["settings"])))
    own = {"RangeTop": 6912, "RangeBottom": 6892, "CustomLevels": "6902|MINE"}
    assert out["preset"] == {"settings": own}
    assert out["loaded"]["calls"] == [["PUT", "/api/indicators/lines", {}]]
    assert not [t for t in out["loaded"]["toasts"] if t.startswith("FAIL")], out["loaded"]["toasts"]
    assert out["loaded"]["sets"] == [{"lines_2": own}, {"lines_2": own}] and out["loaded"]["start"] == {}
    assert out["direct"]["calls"] == [["PUT", "/api/indicators/lines", {"ShowRangeMid": False}]]
    assert out["direct"]["sets"] == [{"lines_2": {**own, "ShowRangeMid": True}}] * 2   # it showed the mid line: kept
    assert out["refused"] == "unknown setting 'ShowRangeMid'" and out["kept"] is True
    assert out["taken"] == [{"lines_2": own}, {"lines_2": own}]
