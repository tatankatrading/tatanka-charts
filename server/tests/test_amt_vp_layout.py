"""The AMT Toolkit's table and warning beside the volume profiles' texts: the data window's row (the legend's second
line, long with the profiles' POC, VAH and VAL) wraps before a table in the top right corner instead of running under
it, and the profiles' notices at the bottom left are never written over the Toolkit's warning there. The app.js parts
run in node with what they call stubbed."""
from test_hunt_web_chart import needs_node, run_app

DASH_JS = """
const els = {
  '#amt-dash': { hidden: false, style: { setProperty() {} }, classList: { toggle() {} },
    querySelector: (s) => (s === '.rows' ? { children: Array(9).fill(0) } : { textContent: 'Tatanka AMT v1.2.4' }) },
  '#amt-warn': { hidden: false, style: {} }, '#dwin': { style: {} }, '#overlay': { offsetHeight: 40 } };
const $ = (q) => els[q];
let W = 1100;
const chart = { priceScale: () => ({ width: () => 0 }), timeScale: () => ({ width: () => W }), paneSize: () => ({ height: 700 }) };
const legendBox = () => ({ right: 300, bottom: 40 }), measureLegend = legendBox, getComputedStyle = () => ({ fontFamily: 'x' });
const S = { amt: { style: { tbl_pos: 'Top Right' } } };
/*APP*/
const out = {};
const at = (name, f) => { f(); placeAmtDash(); out[name] = [els['#dwin'].style.maxWidth, els['#amt-dash'].style.left]; };
at('topRight', () => {});
at('large', () => { S.amt.style.tbl_size = 'Large'; });   // 18 px: 450 wide
at('topLeft', () => { S.amt.style = { tbl_pos: 'Top Left' }; });
at('bottomRight', () => { S.amt.style = { tbl_pos: 'Bottom Right' }; });
at('middleRight', () => { S.amt.style = { tbl_pos: 'Middle Right' }; });
at('again', () => { S.amt.style = { tbl_pos: 'Top Right' }; });
at('phone', () => { W = 400; });
at('hidden', () => { W = 1100; els['#amt-dash'].hidden = true; });
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_data_window_row_wraps_before_the_amt_table_in_the_top_right_corner():
    out = run_app(["AMT_FS", "amtDashAt", "placeAmtDash"], DASH_JS)
    assert out["topRight"] == ["min(100%, 724px)", "740px"]   # 1100 - 310 - 10 wide: 8 px before it
    assert out["large"] == ["min(100%, 624px)", "640px"]
    assert out["topLeft"][0] == out["bottomRight"][0] == out["middleRight"][0] == ""   # not beside the row
    assert out["again"] == ["min(100%, 724px)", "740px"]
    assert out["phone"][0] == ""      # a phone's table goes below the legend, or beside it where there is room
    assert out["hidden"][0] == ""


NOTICE_JS = """
const warn = { hidden: true, textContent: '', offsetHeight: 0 };
const els = { '#amt-warn': warn, '#draw-bar': { hidden: true } }, $ = (q) => els[q];
const document = { createElement: () => ({ getContext: () => ({}) }) };
const chart = { timeScale: () => ({ width: () => 1000 }) }, VP_FONT = '11px x', VP_FONT_PX = 11;
const vpWrap = (ctx, text) => text.split('/');   // a notice's lines: as many as it has parts here
let notes = { aavp: 'AAVP: load history', pvp: 'PVP: first period', pvp_2: 'PVP: first period', prof: 'SVP: no trades' };
const vpKeys = () => ['aavp', 'pvp', 'pvp_2', 'prof'], vpBase = (k) => ({ kind: k.split('_')[0], key: k, d: null });
const vpMessage = () => null, vpNotice = (s) => notes[s.key] || null;
const legendBox = () => ({ right: 300, bottom: 40 });
/*APP*/
const out = {};
const ys = (name) => { forgetMeasures(); out[name] = vpKeys().map((k) => Math.round(vpState(k).noticeY)); };
ys('none');
Object.assign(warn, { hidden: false, textContent: 'Loading daily ATR: load at least 30 trading days.', offsetHeight: 16 });
ys('oneLine');
warn.offsetHeight = 32;
ys('twoLines');
warn.offsetHeight = 64;
ys('four');
warn.textContent = '';   // shown, but nothing in it
ys('empty');
// a phone: each notice on two lines (they go up 15 px from where they are written), the warning on two
notes = { aavp: 'AAVP: load history/from before', pvp: 'PVP: first period/may be incomplete', pvp_2: 'PVP: first period/may be incomplete', prof: null };
warn.textContent = 'Loading daily ATR'; warn.offsetHeight = 32;
ys('wrapped');
warn.hidden = true;
ys('wrappedNoWarn');
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_profiles_notices_go_above_the_amt_warning_and_each_other():
    """The AMT warning is 64 px up from the bottom with its lines down from there; the notices went up from 23 px, 20
    apart, so a third profile's (63 px) was written over its first line, and on a phone a notice on two lines over the
    one above it. Each now goes 20 px above the top line of the one before, and one that would be over the warning
    goes above it."""
    out = run_app(["warnSeen", "forgetMeasures", "amtWarnHeight", "vpMeasure", "vpState"], NOTICE_JS)
    assert out["none"] == [23, 43, 63, 83]
    assert out["oneLine"] == [23, 43, 84, 104]
    assert out["twoLines"] == [23, 84, 104, 124]
    assert out["four"] == [84, 104, 124, 144]
    assert out["empty"] == [23, 43, 63, 83]
    assert out["wrapped"] == [84, 119, 153, 188]   # AAVP's at 23 would reach 38 px up, into the warning (32 to 64)
    assert out["wrappedNoWarn"] == [23, 58, 92, 127]
