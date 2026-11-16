"""Several of a study on one chart, as NinjaTrader adds an indicator twice: the HTF EMA and the volume profiles (AAVP,
PVP, SVP) each up to 4, the others keyed 'pvp_2'... with their own settings over the same starting ones, their own
layer after the first's, kept with the chart's studies, and gone with their settings when taken off. The app.js parts
run in node with what they call stubbed."""
from test_hunt_web_chart import needs_node, run_app

HELPERS = ["NT_CHART_SET", "VP_KINDS", "STUDY_DEFAULTS", "ST_PRESET", "MULTI", "INST_MAX", "INST_RE", "baseOf", "instNo",
           "instKeys", "vpKeys", "studiesFrom"]


@needs_node
def test_the_keys_settings_and_layers_of_another_one():
    out = run_app(HELPERS + ["indDefaults", "indValues", "layerDefault", "layersClean"], """
const local = { get: () => '' };
const tab = { pvp_2: { Period: 'Monthly' } }, tabSet = () => tab;
const LAYER_DEFAULT = ['fills', 'amt', 'aavp', 'pvp', 'prof', 'hema', 'bars'];
/*APP*/
var S = { symbol: 'ES', st: studiesFrom({ _preset: 2, pvp: true, pvp_3: true, pvp_2: true, hema_2: false, hema_9: true, pvp_4: 'on', tpo_2: true }),
  ind: { schema: { pvp: { settings: [{ name: 'Period', default: 'Daily' }, { name: 'RowSize', default: 200 }] } },
    overrides: { pvp: { RowSize: 100 } }, symbols: {} } };
console.log(JSON.stringify({
  kept: Object.keys(S.st).filter((k) => INST_RE.test(k)).sort(),            // a bad one, or past 4, goes on load
  pvp: instKeys('pvp'), hema: instKeys('hema'), vp: vpKeys(), base: baseOf('pvp_3'), no: instNo('pvp_3'),
  first: indValues('pvp'), second: indValues('pvp_2'), third: indValues('pvp_3'),   // the same starting settings, its own over them
  layers: layersClean(['bars', 'pvp', 'amt']),   // another one: next to the first, as one not seen before
}));
""")
    assert out["kept"] == ["hema_2", "pvp_2", "pvp_3"]
    assert out["pvp"] == ["pvp", "pvp_2", "pvp_3"] and out["hema"] == ["hema", "hema_2"]
    assert out["vp"] == ["aavp", "pvp", "pvp_2", "pvp_3", "prof"] and out["base"] == "pvp" and out["no"] == 3
    assert out["first"] == {"Period": "Daily", "RowSize": 100} and out["third"] == out["first"]
    assert out["second"] == {"Period": "Monthly", "RowSize": 100}
    lay = out["layers"]
    assert lay.index("bars") < lay.index("pvp") < lay.index("amt")   # the order given kept
    assert lay.index("pvp_2") == lay.index("pvp") + 1 and lay.index("pvp_3") == lay.index("pvp") + 2
    assert lay.index("hema_2") == lay.index("hema") + 1 and sorted(lay) == sorted(set(lay))


@needs_node
def test_adding_and_taking_off_another_one():
    out = run_app(HELPERS + ["instAdd", "instPut", "instRemove", "instDrop"], """
const TK_MULTI = false, toasts = [], saved = [], changed = [], removed = [], detached = [];
const toast = (m) => toasts.push(m), tabSetSave = (k, d) => { saved.push([k, d]); if (d) tab[k] = d; else delete tab[k]; };
const tab = { pvp: { Period: 'Weekly' } }, tabSet = () => tab;
const studiesChanged = (k) => changed.push([k, S.st[k]]), setLayers = () => {}, indSets = () => {}, legend = () => {}, saveHidden = () => {};
const chart = { removeSeries: (s) => removed.push(s) }, candles = { detachPrimitive: (p) => detached.push(p) };
const vpPrims = {}, vpHeld = {}, vpBusy = {}, llPrims = {}, vpTouchMemo = {};
let stOpen = '';
/*APP*/
var S = { st: studiesFrom({ _preset: 2, pvp: true, hema: true }), stHidden: {}, hx: {}, vp: {}, vpErr: {}, vpLive: {}, lastBar: null };
const out = {};
out.a = instAdd('pvp');
out.b = instAdd('pvp');
vpPrims.pvp_2 = 'prim2';
S.hx.hema_2 = { line: 'line2' };
out.copied = saved.map(([k, d]) => [k, d && d.Period]);   // each starts from the first one's settings on this chart
instAdd('pvp');
out.full = instAdd('pvp');   // a fifth
out.toasts = toasts.slice();
S.st.hema_2 = true;
tab.pvp_2 = { Period: 'Monthly' };
S.stHidden.pvp_2 = true;
S.vpLive = { pvp: new Map([['a', [1]]]), pvp_2: new Map([['a', [2]]]) };   // their values on the bars formed live
vpTouchMemo.pvp_2 = { n: 2 };
instRemove('pvp_2');
out.live = [Object.keys(S.vpLive), Object.keys(vpTouchMemo)];
instRemove('hema_2');
out.after = [instKeys('pvp'), instKeys('hema'), 'pvp_2' in tab, 'pvp_2' in S.stHidden, detached, removed, 'pvp_2' in vpPrims];
out.again = instAdd('pvp');   // the free number again
instRemove('pvp');            // not the first: it is switched off, never taken off
out.first = 'pvp' in S.st;
console.log(JSON.stringify(out));
""")
    assert out["a"] == "pvp_2" and out["b"] == "pvp_3"
    assert out["copied"] == [["pvp_2", "Weekly"], ["pvp_3", "Weekly"]]
    assert out["full"] is None and any("Up to 4" in t for t in out["toasts"])
    assert out["after"] == [["pvp", "pvp_3", "pvp_4"], ["hema"], False, False, ["prim2"], ["line2"], False]
    assert out["live"] == [["pvp"], []]   # PVP 2's live values and touches go with it, the first one's stay
    assert out["again"] == "pvp_2" and out["first"]


DLG_JS = r"""
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
const saved = [], fails = [], kinds = [];
let keys = ['pvp', 'pvp_2'];
const mk = (key) => ({ key, presetKey: key.replace(/_\d$/, ''), title: key, version: '1', note: '', shown: () => keys.includes(key),
  show: (on) => { if (!on) { keys = keys.filter((k) => k !== key); dlg.refresh(); } },
  schema: () => [{ name: 'RowSize', group: '1', label: 'Row size', type: 'int', default: 200 }],
  defaults: () => ({ RowSize: 200 }), overrides: () => ({}), perSymbol: () => ({}), symbolNames: [],
  save: async (d) => saved.push([key, d]) });
const root = el();
root.hidden = true;
const dlg = createIndicatorsDialog({ root, local: { get: (k, d) => d, set() {} }, toast() {}, fail: (e) => fails.push(e.message),
  symbol: () => 'ES', ask: async () => null, allCharts: async () => 0, adapters: () => keys.map(mk),
  templates: () => ({ names: (k) => { kinds.push(k); return []; }, data: () => null }) });
const out = {};
(async () => {
  dlg.open(true, 'pvp_2');
  out.kinds = [...new Set(kinds)];
  mk('pvp_2').show(false);   // unticked in the dialog's list while it is the one shown
  out.title = root.sel['.ind-title'].textContent;
  await root.sel['.ind-ok'].onclick();
  out.after = [root.hidden, fails.slice(), saved.slice()];
  keys = ['pvp', 'pvp_2'];
  dlg.open(true, 'pvp');   // drafts for both (the list counts each one's changes)
  keys = ['pvp'];          // taken off by another chart of the layout, nothing redrawn
  await root.sel['.ind-ok'].onclick();
  out.other = [root.hidden, fails.slice()];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


@needs_node
def test_the_indicators_dialog_with_one_taken_off_and_presets_by_kind():
    import json
    import subprocess

    from test_hunt_web_chart import WEB
    js = DLG_JS.replace("/*IND*/", (WEB / "indicators.js").read_text())
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().split("\n")[-1])
    assert out["kinds"] == ["ind:pvp"]               # PVP 2's presets are PVP's (the server takes ind:[a-z]+ only)
    assert out["title"].startswith("pvp")           # the dialog shows the first one now
    assert out["after"] == [True, [], []]           # OK closes, nothing failed, nothing to save
    assert out["other"] == [True, []]


@needs_node
def test_a_tab_switch_with_other_instances_still_asks_again_for_what_changed():
    out = run_app(HELPERS + ["indSnap", "applyStudyState", "instFollow"], """
const indDlg = { open() {} }, studiesChanged = () => {}, indClean = (c) => c, IND_MAX = 10, indsChanged = () => {};
const setLayers = () => {}, renderStudiesMenu = () => {}, legend = () => {}, instRemove = () => {};
let tab = { tpo: { Period: 30 } };
const indSets = () => { S.tpoSet = tab.tpo; }, instDrop = (k) => { delete S.st[k]; indSets(); };
const got = [], indApply = (was) => got.push(was.tpo);
/*APP*/
var S = { st: studiesFrom({ _preset: 2, pvp: true, pvp_2: true }), indCfg: [], tpoSet: { Period: 30 } };
tab = { tpo: { Period: 15 } };   // the other tab's
applyStudyState({ st: { _preset: 2, pvp: true }, inds: [], layers: [], hidden: {} });
console.log(JSON.stringify({ was: got[0], st: Object.keys(S.st).filter((k) => INST_RE.test(k)) }));
""")
    assert out == {"was": {"Period": 30}, "st": []}   # drawn with TPO 30: indApply sees 15 as a change


@needs_node
def test_the_layout_page_takes_the_active_charts_instances_and_ninjatraders_set_none():
    out = run_app(HELPERS + ["hostActive"], """
const sets = [], indSets = () => sets.push(Object.keys(S.st).filter((k) => INST_RE.test(k)).join()), renderStudiesMenu = () => {};
const indDlg = { refresh() {} }, layersClean = (a) => a, chartSym = () => false, IND_MAX = 10, indClean = (c) => c, SPEC_RE = /x/;
/*APP*/
var S = { st: { pvp: true, pvp_2: true, pvp_3: true }, indCfg: [], layers: [] };
hostActive({ st: { pvp: true, hema: true } });   // chart 2: none
const two = Object.keys(S.st);
hostActive({ st: { pvp: true, hema_2: false } });
console.log(JSON.stringify({ two, three: Object.keys(S.st), sets }));
""")
    assert out["two"] == ["pvp", "hema"] and out["three"] == ["pvp", "hema", "hema_2"]
    assert out["sets"] == ["", "hema_2"]


@needs_node
def test_own_indicators_on_the_open_linked_tab_take_what_it_shows():
    out = run_app(["setTabLink"], """
const shared = { st: { tpo: true }, set: { hema: { HtfPeriod: 240 } } }, applied = [];
const tabSet = () => (S.tabs[S.tab].link ? shared.set : S.tabs[S.tab].own.set);   // as app.js: unlinked reads its own
const studySnapshot = () => ({ st: { ...shared.st }, set: JSON.parse(JSON.stringify(tabSet())) }), sharedStudies = () => shared;
const saveTabs = () => {}, renderTabs = () => {}, amtFollow = () => {}, toast = () => {}, applyStudyState = (t) => applied.push(t);
/*APP*/
var S = { tabs: [{ link: true }], tab: 0 };
setTabLink(0, false);
console.log(JSON.stringify({ tab: S.tabs[0], applied }));
""")
    assert out["tab"] == {"own": {"st": {"tpo": True}, "set": {"hema": {"HtfPeriod": 240}}}}
    assert out["applied"] == [out["tab"]["own"]]


@needs_node
def test_a_preset_or_template_leaves_no_study_it_turns_off_hidden():
    out = run_app(["unhideOff"], """
const saved = [], saveHidden = () => saved.push({ ...S.stHidden });
/*APP*/
var S = { stHidden: { tpo: true, pvp: true, fp: 'bid' } };
unhideOff({ tpo: false, pvp: true, fp: 'off', gap: false });
console.log(JSON.stringify({ hidden: S.stHidden, saved: saved.length }));
""")
    assert out == {"hidden": {"pvp": True}, "saved": 1}


def test_the_amt_profile_buttons_are_not_taken_for_a_studys():
    from test_hunt_web_chart import WEB
    html, app = (WEB / "index.html").read_text(), (WEB / "app.js").read_text()
    assert 'id="amt-period"' in html and "data-st" not in html.split('id="amt-period"')[1].split(">")[0]
    assert "querySelectorAll('#st-menu .seg button')" not in app and "querySelectorAll('#st-menu .seg')" not in app
