"""⚙ Chart's "Price bars in front of studies" (on by default): the candles draw over every study whatever the layer
order, your fills and big-trade bubbles still over them; off, the layer order is as set."""
from test_hunt_web_chart import needs_node, run_app


@needs_node
def test_the_bars_draw_in_front_of_every_study_but_the_trade_marks():
    out = run_app(["BARS_UNDER", "barsForward", "layerPlan"], """
const layerOn = () => true, isLineLayer = (id) => ['hema', 've'].includes(id);
const S = { cs: { barsFront: true } };
/*APP*/
const order = ['fills', 'pvp', 'amt', 'hema', 'big', 'bars', 've', 'right'];
const on = layerPlan(order);
S.cs.barsFront = false;
const off = layerPlan(order);
console.log(JSON.stringify({ on: on.order, onBack: [on.backLines, on.bottom], off: off.order, offTop: off.top }));
""")
    assert out["on"] == ["fills", "big", "bars", "hema", "ve", "pvp", "amt", "right"]
    assert out["onBack"] == [["hema", "ve"], ["pvp", "amt", "right"]]   # every study behind the candles: lines, then shading
    assert out["off"][:3] == ["fills", "pvp", "amt"] and out["off"].index("bars") > out["off"].index("pvp")


@needs_node
def test_a_study_moved_with_the_bars_in_front_keeps_where_the_bars_are_kept():
    out = run_app(["BARS_UNDER", "barsForward", "layerPlan", "layerMove"], """
const layerOn = () => true, isLineLayer = (id) => id === 'hema', toasts = [], toast = (m) => toasts.push(m), stRender = () => {};
const stItem = (id) => ({ name: id });
const S = { cs: { barsFront: true }, layers: ['fills', 'amt', 'pvp', 'hema', 'bars', 'tpo'] };
const setLayers = (a) => { S.layers = a; };
/*APP*/
const shown = layerPlan(S.layers).order;              // fills, bars, hema (a line: first behind them), amt, pvp, tpo
const ids = shown.slice(); ids.splice(ids.indexOf('pvp'), 1); ids.splice(ids.indexOf('amt'), 0, 'pvp');
layerMove(ids, 'pvp');                                  // PVP above AMT
const swapped = S.layers.slice(), quiet = toasts.length;
const up = layerPlan(S.layers).order.filter((id) => id !== 'tpo'); up.splice(1, 0, 'tpo');   // TPO dropped over the bars
layerMove(up, 'tpo');
console.log(JSON.stringify({ swapped, quiet, after: S.layers, toast: toasts.at(-1) }));
""")
    assert out["swapped"] == ["fills", "pvp", "amt", "hema", "bars", "tpo"]   # only PVP moved: the bars, HTF EMA as kept
    assert out["quiet"] == 0
    assert out["after"].index("tpo") < out["after"].index("bars")            # kept over them, for when it is unticked
    assert "Price bars in front" in out["toast"]


@needs_node
def test_the_price_bars_row_dragged_with_the_bars_in_front_changes_nothing():
    out = run_app(["BARS_UNDER", "barsForward", "layerPlan", "layerMove"], """
const layerOn = () => true, isLineLayer = (id) => id === 'hema', toasts = [], toast = (m) => toasts.push(m), stRender = () => {};
const stItem = (id) => ({ name: id });
const S = { cs: { barsFront: true }, layers: ['fills', 'amt', 'pvp', 'hema', 'bars', 'tpo'] };
const setLayers = (a) => { S.layers = a; };
/*APP*/
const ids = layerPlan(S.layers).order.slice(), i = ids.indexOf('bars');
ids.splice(i, 1); ids.splice(i + 1, 0, 'bars');   // ArrowDown on Price bars
layerMove(ids, 'bars');
console.log(JSON.stringify({ layers: S.layers, toast: toasts.at(-1) }));
""")
    assert out["layers"] == ["fills", "amt", "pvp", "hema", "bars", "tpo"] and "Price bars in front" in out["toast"]
