"""On a phone a finger never turns the price axis's Auto off: nearly every pan goes a little up or down, and the
mouse rule (TradingView's: a drag clearly up or down moves the chart and turns Auto off) turned it off on almost
every swipe. The block of web/app.js runs in node with what it calls stubbed."""
import json
import subprocess

from test_hunt_web_chart import app_lines, needs_node

JS = """
const listeners = { el: {}, win: {} };
const el = { addEventListener: (t, f) => { (listeners.el[t] ||= []).push(f); }, getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 600 }) };
const $ = () => el, window = { addEventListener: (t, f) => { (listeners.win[t] ||= []).push(f); } };
const calls = [];
let auto = true;
const PSM = { Normal: 0, Logarithmic: 1, Percentage: 2 }, S = { lock: null };
const PS = () => ({ width: () => 60, options: () => ({ autoScale: auto, mode: PSM.Normal }), getVisibleRange: () => ({ from: 100, to: 200 }),
  setAutoScale: (v) => { auto = v; calls.push(v); } });
const chart = { paneSize: () => ({ height: 500 }), options: () => ({ handleScroll: { pressedMouseMove: true, vertTouchDrag: true } }) };
const psSetRange = () => {}, psUi = () => {};
/*APP*/
const fire = (where, t, e) => (listeners[where][t] || []).forEach((f) => f(e));
const drag = (pointerType) => {
  auto = true; calls.length = 0;
  const base = { pointerId: 1, isPrimary: true, button: 0, pointerType };
  fire('el', 'pointerdown', { ...base, clientX: 100, clientY: 100 });
  fire('win', 'pointerdown', { ...base, clientX: 100, clientY: 100 });
  for (const [x, y] of [[104, 130], [110, 180], [112, 260]]) fire('win', 'pointermove', { ...base, clientX: x, clientY: y });
  fire('win', 'pointerup', { ...base });
  return { auto, calls: [...calls] };
};
console.log(JSON.stringify({ touch: drag('touch'), pen: drag('pen'), mouse: drag('mouse') }));
"""


@needs_node
def test_a_finger_drag_up_or_down_keeps_auto_on_and_the_mouse_still_turns_it_off():
    block = app_lines("// A mouse drag on the chart that goes clearly up or down", "}")
    r = subprocess.run(["node", "-e", JS.replace("/*APP*/", block)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().split("\n")[-1])
    assert out["touch"] == {"auto": True, "calls": []}
    assert out["pen"] == {"auto": True, "calls": []}
    assert out["mouse"] == {"auto": False, "calls": [False]}
