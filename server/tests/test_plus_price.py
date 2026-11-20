"""The + by the price axis keeps its price beside it: a tag on the price axis on the +'s row (the chart's own crosshair
label's look, which is off while the tag shows), the price its menu uses, there whenever the + is: on the chart, on
the +, past it on the axis, the moment after the pointer leaves, the 3 s after a finger lifts, and while its menu is
open; the menu opens beside the + and its tag, not over them. web/app.js's parts run in node with the page stubbed;
then, where node has Playwright and its Chrome, the page itself on a sim server with a day of ES's 1-minute bars (a
seeded random walk), with a mouse at 1400 x 800 and 390 x 844 and with a finger at 390 x 844.
PLUS_PRICE_SHOTS: where the screenshots of each state go (default: the test's temp dir)."""
import contextlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_hunt13_ui import decl, node
from test_hunt_web_chart import WEB, app_part, needs_node

ROOT = Path(__file__).resolve().parents[2]
HTML = (WEB / "index.html").read_text()


# ---- the page's parts: the tag's element and style -----------------------------------------------------------------------

def test_the_tag_lets_the_pointer_through_to_the_axis_and_sits_with_the_plus():
    """The tag is over the price axis: the pointer goes through it (the axis' drag, wheel, double-click and menu work on
    its row too); it stacks as the + does, hides with [hidden], says nothing to a screen reader (the + says what it
    does), and comes right after the + in the page."""
    assert decl("#ax-plus-px", "pointer-events") == "none"
    assert decl("#ax-plus-px", "position") == "absolute"
    assert decl("#ax-plus-px", "z-index") == decl("#ax-plus", "z-index")
    assert decl("#ax-plus-px[hidden]", "display") == "none"
    assert decl("#ax-plus-px", "border-radius") == "0 2px 2px 0"   # the library's label: square on the axis side
    plus = HTML.index('id="ax-plus"')
    tag = HTML.index('<div id="ax-plus-px" hidden aria-hidden="true"></div>')
    assert plus < tag and HTML[plus:tag].count("<") == 1


# ---- the + menu's place: beside the + and its tag -------------------------------------------------------------------------

MENU_JS = """
const style = {};
let size = { width: 240, height: 300 };
const height = () => Math.min(size.height, parseFloat(style.maxHeight));   // its max-height (it scrolls past it)
const m = { hidden: true, style, replaceChildren() {}, getBoundingClientRect: () => ({ width: size.width, height: height() }) };
const $ = (s) => (s === '#ctx' ? m : null);
let innerWidth = 1400, innerHeight = 800, ctxOwner = null, ctxBeside = null, left = 0;
const closeCtx = () => {}, plusLeft = () => { left++; };
/*APP*/
const at = (cx, cy, beside, w = 1400, h = 800, mh = 300) => {
  innerWidth = w; innerHeight = h; size = { width: 240, height: mh };
  showMenu(cx, cy, [], null, beside);
  const l = parseFloat(style.left), t = parseFloat(style.top);
  return { l, t, r: l + 240, b: t + height(), beside: ctxBeside };
};
const box = (l, t, r, b) => ({ left: l, top: t, right: r, bottom: b });
const out = {
  left: at(0, 0, box(976, 397, 1062, 418)),
  leftLow: at(0, 0, box(976, 700, 1062, 721)),
  tall: at(0, 0, box(976, 397, 1062, 418), 1400, 800, 542),
  below: at(0, 0, box(200, 100, 290, 121), 390, 844),
  above: at(0, 0, box(200, 700, 290, 721), 390, 844),
  cellLow: at(0, 0, box(221, 287, 307, 308), 315, 337, 540),   // a layout's cell: no room for it all, over or under
  cellHigh: at(0, 0, box(221, 32, 307, 53), 315, 337, 540),
  cellMid: at(0, 0, box(221, 158, 307, 179), 315, 337, 540),
  short: at(0, 0, box(976, 97, 1062, 118), 1400, 300, 542),   // a short window, room left of the +
};
ctxBeside = box(1, 1, 2, 2); left = 0;   // the + menu open, then a watchlist button's menu over it: the + goes
out.replaced = { ...at(500, 100, null), left };
out.right = at(500, 400, null);
out.rightEdge = at(1300, 700, null);
out.rightTall = at(500, 400, null, 1400, 300, 542);
console.log(JSON.stringify(out));
"""


def _apart(a, b):
    """Two boxes ({l, t, r, b}) that do not overlap."""
    return a["r"] <= b["l"] or b["r"] <= a["l"] or a["b"] <= b["t"] or b["b"] <= a["t"]


@needs_node
def test_the_plus_menu_opens_beside_the_plus_and_its_tag():
    """The + menu opened at the +'s right edge and was moved back into the window: over the axis, the tag and the +'s
    row. Now its right edge is 4 px left of the + and its top on the +'s row (moved up to fit the window, still left of
    them); with no room left of them (a narrow window, the + far left), under their row, or over it near the bottom.
    The right-click menu still opens where it was clicked."""
    out = node(MENU_JS.replace("/*APP*/", app_part("showMenu")))
    plus = {"l": 976, "t": 397, "r": 1062, "b": 418}
    assert out["left"]["r"] == 976 - 4 and out["left"]["t"] == 397 and _apart(out["left"], plus)
    assert out["leftLow"]["r"] == 972 and out["leftLow"]["b"] == 800 - 4   # moved up to fit, still left of the +
    assert out["tall"]["t"] == 800 - 4 - 542 and out["tall"]["r"] == 972
    low = {"l": 200, "t": 100, "r": 290, "b": 121}
    assert out["below"]["t"] == 121 + 4 and _apart(out["below"], low) and out["below"]["l"] >= 4
    high = {"l": 200, "t": 700, "r": 290, "b": 721}
    assert out["above"]["b"] == 700 - 4 and _apart(out["above"], high)
    assert all(out[k]["beside"] for k in ("left", "below", "above"))
    assert (out["right"]["l"], out["right"]["t"], out["right"]["beside"]) == (500, 400, None)   # the right-click menu
    assert (out["rightEdge"]["l"], out["rightEdge"]["t"]) == (1400 - 240 - 4, 800 - 300 - 4)
    # a layout's short, narrow cell: over the row (near its bottom) or under it (near its top, or in the middle where
    # there is more room under), no taller than the room there, so never over the + or its tag
    for k, row in (("cellLow", (221, 287, 307, 308)), ("cellHigh", (221, 32, 307, 53)), ("cellMid", (221, 158, 307, 179))):
        r = {"l": row[0], "t": row[1], "r": row[2], "b": row[3]}
        assert _apart(out[k], r) and out[k]["t"] >= 4 and out[k]["b"] <= 337 - 4, (k, out[k])
    assert out["cellLow"]["b"] == 287 - 4 and out["cellLow"]["t"] == 4
    assert out["cellHigh"]["t"] == 53 + 4 and out["cellHigh"]["b"] == 337 - 4
    assert out["cellMid"]["t"] == 179 + 4
    assert (out["short"]["t"], out["short"]["b"], out["short"]["r"]) == (4, 300 - 4, 972)   # left of it, the window's height
    assert (out["rightTall"]["t"], out["rightTall"]["b"]) == (4, 300 - 4)   # any menu: no taller than the window
    assert out["replaced"]["left"] == 1 and out["replaced"]["beside"] is None


# ---- the chart's own label: off while the tag shows, then as it was ---------------------------------------------------------

LABEL_JS = """
let opts = { crosshair: { horzLine: { labelVisible: true } } }, applied = [];
const chart = { options: () => JSON.parse(JSON.stringify(opts)),
  applyOptions: (o) => { applied.push(o.crosshair.horzLine.labelVisible); opts.crosshair.horzLine.labelVisible = o.crosshair.horzLine.labelVisible; } };
let plusLabelWas = null;
/*APP*/
const out = [];
plusLabel(false); plusLabel(false); out.push(opts.crosshair.horzLine.labelVisible);
plusLabel(true); plusLabel(true); out.push(opts.crosshair.horzLine.labelVisible);
opts.crosshair.horzLine.labelVisible = false;   // turned off elsewhere: it stays off
plusLabel(false); plusLabel(true); out.push(opts.crosshair.horzLine.labelVisible);
console.log(JSON.stringify({ out, applied }));
"""


@needs_node
def test_the_charts_own_label_is_off_while_the_tag_shows_and_back_after():
    """One price on the +'s row: the chart's crosshair label is off while the tag is up (once, not on every move), and
    the setting it had comes back when the + goes."""
    out = node(LABEL_JS.replace("/*APP*/", app_part("plusLabel")))
    assert out == {"out": [False, True, False], "applied": [False, True, False, False]}


# ---- the price held while the pointer is on the + or its tag --------------------------------------------------------------

HOLD_JS = """
const S = { bars: [{ t: 0, c: 6800 }], cs: { fontSize: 12 } };
const plus = { hidden: false, dataset: { price: '6800.3' }, style: {}, offsetWidth: 20, offsetHeight: 20 };
const tag = { hidden: false, style: {}, textContent: '6800.25' };
const $ = (s) => (s === '#ax-plus' ? plus : s === '#ax-plus-px' ? tag : { clientWidth: 800 });
let top = 7000;   // the price scale: the price at the pane's top row, 1 point a pixel
const candles = { coordinateToPrice: (y) => top - y, priceToCoordinate: (p) => top - p }, px = (p) => p.toFixed(2), snap = (p) => Math.round(p * 4) / 4;
const held = [], L = { CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2, MagnetOHLC: 3 } };
const plusLook = () => ({ dpr: 1, h: 21, w: 66 }), plusLabel = () => {}, plusOff = () => {}, plusLater = () => {}, plusSizeNow = () => '';
const chart = { paneSize: () => ({ width: 740, height: 600 }), priceScale: () => ({ width: () => 0 }), options: () => ({ crosshair: { mode: 0 } }),
  setCrosshairPosition: (price, t) => held.push([price, t]) };
const requestAnimationFrame = () => 1, timeOf = (b) => b.t;
let plusHide = 0, plusTouch = false, ctxBeside = null, plusOver = true, plusOnTag = false, plusY = 199.7, plusBar = null, plusT = 5, plusSize = '', plusRaf = 1;
/*APP*/
const out = [], now = () => [S.hoverPrice, String(plus.dataset.price), tag.textContent, plus.style.top, held.splice(0)];
S.plusXhair = true;   // the pointer on the +: the crosshair held at its price
alertPlus({ time: 1, point: { x: 1, y: 199.6 }, paneIndex: 0 });   // the library's echo, a pixel rounded (or a tick)
out.push(now());
top = 7012;   // the scale moved 12 points (a new high, the axis wheeled): the library tells the row it kept
alertPlus({ time: 1, point: { x: 1, y: 199.7 }, paneIndex: 0 });
out.push(now());
ctxBeside = {};   // its menu open, saying its price: the scale moves again
top = 7020;
alertPlus({ time: 1, point: { x: 1, y: 199.7 }, paneIndex: 0 });
out.push(now());
alertPlus({ time: 2, point: { x: 1, y: 150 }, paneIndex: 0, sourceEvent: {} });   // the pointer crossing the plot to the menu
out.push(now());
alertPlus({ time: 2, point: undefined, sourceEvent: undefined });   // and leaving it, onto the menu
out.push(now());
ctxBeside = null; S.plusXhair = false; plusOver = false;
alertPlus({ time: 1, point: { x: 1, y: 150 }, paneIndex: 0, sourceEvent: {} });   // the pointer itself, on the chart
out.push(now());
top = 7024;   // the scale moves under it: the price its row has now
plusCheck();
out.push(now());
S.hoverPrice = null;   // the pointer (or a finger) has left the chart: the + stays a moment, with the price it had
top = 7030;
plusCheck();
out.push(now());
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_price_held_for_the_plus_stays_true_to_its_row():
    """While the pointer is on the + or its tag, the crosshair is held at the +'s price, and the library tells it back
    from the pixel row it rounded it to (and again on a tick): the + took that row's price, a tick off now and then, so
    its tag and menu changed as the pointer moved from the + to the axis. The held price stays. When the scale really
    moves (a pixel or more), the + stays under the pointer with the price its row has now, and the line is held there;
    with its menu open, the + goes to the row of the menu's price. The pointer crossing the plot or leaving it while the
    menu is open takes the held line away: it is held again at once, at the same price and bar. In the moment the +
    stays after the pointer or a finger leaves, it keeps the price it had and goes to that price's row."""
    out = node(HOLD_JS.replace("/*APP*/", "\n".join(app_part(f) for f in ("alertPlus", "plusAt", "plusCheck", "plusHoldNow", "plusLineY"))))
    assert out[0] == [6800.3, "6800.3", "6800.25", "190.5px", []]
    assert out[1] == [6812.3, "6812.3", "6812.25", "190.5px", [[6812.3, 5]]]   # the row's price now, held there
    assert out[2] == [6812.3, "6812.3", "6812.25", "198.5px", [[6812.3, 5]]]   # the menu's price, at its row now
    assert out[3] == [6812.3, "6812.3", "6812.25", "198.5px", [[6812.3, 5]]]   # held again at once, on its bar
    assert out[4] == [6812.3, "6812.3", "6812.25", "198.5px", [[6812.3, 5]]]
    assert out[5] == [6870, "6870", "6870.00", "140.5px", []]
    assert out[6] == [6874, "6874", "6874.00", "140.5px", []]   # on the chart: its row's price
    assert out[7] == [None, "6874", "6874.00", "146.5px", []]   # left: the price it had, at that price's row


# ---- the page itself: Chrome through Playwright, on a sim server with bars ------------------------------------------------

def _playwright() -> str:
    """Why the page can't be driven here, or '' (node with Playwright: its Chrome is checked when it starts)."""
    if not shutil.which("node"):
        return "needs node"
    r = subprocess.run(["node", "-e", "require.resolve('playwright')"], capture_output=True, text=True, check=False)
    return "" if r.returncode == 0 else "needs Playwright for node"


def _bars(n: int = 400) -> list:
    """ES's 1-minute bars of a Monday's regular session from 08:30 CT: a random walk on the tick from 6800, the same
    every run."""
    from chartsrv.models import Bar
    rng, c = random.Random(13), 6800.0
    t0 = int(datetime(2026, 11, 16, 8, 30, tzinfo=timezone(timedelta(hours=-6))).timestamp() * 1000)
    out = []
    for i in range(n):
        o = c
        c = o + rng.choice((-1, 1)) * rng.randint(0, 6) * 0.25
        out.append(Bar(t=t0 + i * 60_000, o=o, h=max(o, c) + rng.randint(0, 3) * 0.25, l=min(o, c) - rng.randint(0, 3) * 0.25,
                       c=c, v=rng.randint(50, 900)))
    return out


@contextlib.contextmanager
def _sim_server(data: Path):
    """A sim server on a free port over a fresh data dir: config/*.json copied in, the bars above in its database, the
    setup guide marked done, no update check; only this server is stopped after."""
    from chartsrv.store import Store
    for sub in ("config", "levels", "backups", "feeds"):
        (data / sub).mkdir(parents=True)
    for f in (ROOT / "config").glob("*.json"):
        shutil.copy(f, data / "config" / f.name)
    st = Store(data / "chart.db")
    st.insert_bars_if_missing("ES", _bars())
    st.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHART_")}
    env.update({"CHART_DB": str(data / "chart.db"), "CHART_CONFIG_DIR": str(data / "config"), "CHART_FEED": "sim",
                "CHART_QUOTES_FEED": "sim", "CHART_LEVELS_DIR": str(data / "levels"), "CHART_SIM_WARMUP_MIN": "1",
                "CHART_SYMBOLS_ONLINE": "0", "CHART_RESTART_EXEC": "0", "CHART_DB_WATCH": "0", "CHART_UPDATE_REPO": "none",
                "CHART_BACKUP_DIR": str(data / "backups"), "CHART_FEEDS_DIR": str(data / "feeds")})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(data / "server.log", "w")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "chartsrv.app:app", "--host", "127.0.0.1", "--port", str(port),
                            "--loop", "asyncio"], cwd=ROOT / "server", env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(120):
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        req = urllib.request.Request(f"{base}/api/setup", data=b'{"step":0,"done":true}', method="PUT",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        yield port
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
        log.close()


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """One walk of the page (plus_price_page.js) for the tests below: what each step saw."""
    if _playwright():
        pytest.skip(_playwright())
    tmp = tmp_path_factory.mktemp("plus")
    shots = Path(os.environ.get("PLUS_PRICE_SHOTS") or tmp / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    with _sim_server(tmp / "data") as port:
        r = subprocess.run(["node", str(Path(__file__).with_name("plus_price_page.js")), str(port), str(shots)],
                           capture_output=True, text=True, timeout=600, check=False)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().split("\n")[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    assert not out.get("error"), out.get("error")
    assert out["errors"] == []
    return out


def _mid(r):
    return (r["t"] + r["b"]) / 2


def _with_plus(s, where):
    """The + and its tag up, the tag right of the + (1 px apart, no overlap) on its row (the same middle), the price
    the + will use and the menu says (px(snap(price))), whole in its box; the chart's own crosshair label off: one price
    on the row. Inside the price pane, and inside the chart across."""
    p, t = s["pr"], s["tr"]
    assert s["plus"] and s["tag"], where
    assert s["text"] == s["price"] and s["text"], where
    assert s["label"] is False, where
    assert 0 < t["l"] - p["r"] <= 2 and _apart(p, t), (where, p, t)
    assert abs(_mid(p) - _mid(t)) <= 0.5, (where, p, t)
    assert s["fits"], where
    assert s["pane"]["t"] - 0.01 <= t["t"] and t["b"] <= s["pane"]["b"] + 0.01, (where, t, s["pane"])
    assert s["pane"]["t"] - 0.01 <= p["t"] and p["b"] <= s["pane"]["b"] + 0.01, (where, p, s["pane"])
    assert t["r"] <= s["chart"]["r"] + 0.01, (where, t, s["chart"])


def _on_axis(s, where):
    """The tag on the price axis, from its left edge (as the chart's own label)."""
    assert abs(s["tr"]["l"] - s["axis"]["l"]) < 0.01 and s["tr"]["r"] <= s["axis"]["r"] + 0.01, (where, s["tr"], s["axis"])


def _gone(s, where):
    """The + and its tag hidden, the chart's own label back."""
    assert not s["plus"] and not s["tag"] and s["label"] is True, where


def _menu_beside(s, where):
    """Its menu open: beside the + and its tag (over neither), inside the window, saying the tag's price."""
    m = s["menu"]
    assert m, where
    assert _apart(m, s["pr"]) and _apart(m, s["tr"]), (where, m, s["pr"], s["tr"])
    assert m["l"] >= 4 - 0.01 and m["t"] >= 4 - 0.01 and m["r"] <= s["vw"] - 4 + 0.01 and m["b"] <= s["vh"] - 4 + 0.01, (where, m)
    assert s["menuPrice"] == s["text"], where
    _with_plus(s, where)


def _look(s):
    """The tag's look is the chart's crosshair label's: its color (white text on a dark one, black on a light one),
    the chart's font and size."""
    lk = s["look"]
    assert lk["size"] == lk["want"]["size"] and lk["family"] == lk["want"]["family"], lk
    rgb = tuple(int(lk["want"]["bg"][i:i + 2], 16) for i in (1, 3, 5))
    assert lk["bg"] == "rgb({}, {}, {})".format(*rgb), lk
    assert lk["color"] == ("rgb(0, 0, 0)" if 0.199 * rgb[0] + 0.687 * rgb[1] + 0.114 * rgb[2] > 160 else "rgb(255, 255, 255)"), lk


@pytest.mark.parametrize("run", ["desktop", "phoneMouse"])
def test_with_a_mouse_the_price_stays_next_to_the_plus(page, run):
    """With a mouse (1400 x 800, and 390 x 844): the tag beside the + on the chart, on the + (the crosshair held at its
    price, as before), past it on the price axis (also after the moment the + stays), back on it, its menu open and the
    pointer on the menu, and off the chart with the menu still open; one price on that row each time (in the axis, with
    the tag hidden for the look, none of the chart's own label's color, which is seen when the label is on), the same
    price from the + on, and not one frame without it or with the chart's own label as the pointer goes from the chart
    to the + and the axis and the menu. It goes as before: 400 ms after the menu closes with the pointer elsewhere, after
    the pointer goes off its row along the axis, and the moment after the pointer leaves the chart; on a pane below,
    none (at once)."""
    o = page[run]
    for k in ("onChart", "onPlus", "onAxis", "onAxisLater", "backOnPlus", "menu", "onMenu", "menuStays", "closing",
              "axisOffRow", "again", "leaving", "light"):
        _with_plus(o[k], f"{run} {k}")
        _on_axis(o[k], f"{run} {k}")
    for k in ("onChart", "onAxisLater", "onMenu"):
        assert o[k]["pixels"] == 0, f"{run} {k}: the chart's own label is on the +'s row"
    assert o["onChart"]["control"] > 100   # the same look finds the chart's own label when it is on
    for k in ("onPlus", "onAxis", "onAxisLater", "backOnPlus", "menu", "onMenu"):
        assert o[k]["held"] and o[k]["text"] == o["onPlus"]["text"], (run, k)
    assert all(f[0] and not f[1] for f in o["frames"]), f"{run}: a frame without the tag, or with the chart's label"
    texts = [f[2] for f in o["frames"]]   # from the + on: its price, not another
    assert set(texts[texts.index(o["onPlus"]["text"]):]) == {o["onPlus"]["text"]}, run
    assert all(not (f[0] and f[1]) for f in o["leaveFrames"]), run   # never both
    for k in ("menu", "onMenu", "menuStays"):
        _menu_beside(o[k], f"{run} {k}")
    assert abs(o["menu"]["menu"]["r"] - (o["menu"]["pr"]["l"] - 4)) < 0.01, run   # its right edge just left of the +
    assert o["menu"]["menu"]["t"] <= o["menu"]["pr"]["t"] + 0.01
    for k in ("closed", "axisOffRowLater", "left"):
        _gone(o[k], f"{run} {k}")
    _look(o["onChart"])
    _look(o["light"])
    # the right-click menu: where it was clicked (moved into the window), its price the pointer's
    r = o["right"]
    assert abs(r["menu"]["l"] - min(r["at"][0], r["vw"] - r["menu"]["w"] - 4)) < 0.01
    assert abs(r["menu"]["t"] - max(4, min(r["at"][1], r["vh"] - r["menu"]["h"] - 4))) < 0.01
    # a price wider than the axis: the tag whole inside the chart, over the plot's edge, the + left of it
    w = o["wide"]
    _with_plus(w, f"{run} wide")
    assert w["tr"]["l"] < w["axis"]["l"] and abs(w["tr"]["r"] - w["chart"]["r"]) < 0.01 and w["text"] == "1234567.25"
    # near the pane's top and bottom: inside the pane, on the axis, beside the +; its menu beside them
    for k in ("top", "bottom"):
        _with_plus(o[k]["at"], f"{run} {k}")
        _on_axis(o[k]["at"], f"{run} {k}")
        _menu_beside(o[k]["menu"], f"{run} {k} menu")
    assert o["top"]["at"]["tr"]["t"] - o["top"]["at"]["pane"]["t"] < 1
    assert o["bottom"]["at"]["pane"]["b"] - o["bottom"]["at"]["tr"]["b"] < 1
    # a pane below: no + there, at once (not the moment it stays after the pointer leaves the chart), and the chart's
    # own crosshair label is on for that pane's crosshair; back on the price pane, the + and its price again
    _with_plus(o["abovePane"], f"{run} above a pane below")
    _gone(o["lowerPane"], f"{run} on a pane below")
    _with_plus(o["backUp"], f"{run} back from a pane below")


def _on_its_row(s, where):
    """The +'s price is the axis' price on the tag's row (its row within a pixel of the tag's middle), and the crosshair
    held at it is drawn there."""
    assert s["priceRow"] is not None and abs(s["priceRow"] - _mid(s["tr"])) <= 1, (where, s["priceRow"], s["tr"])
    assert s["held"] and any(abs(r - _mid(s["tr"])) <= 1.5 for r in s["line"]), (where, s["line"], s["tr"])


@pytest.mark.parametrize("run", ["desktop", "phoneMouse"])
def test_when_the_scale_moves_the_plus_price_and_line_stay_true(page, run):
    """The price scale moving under the + (the range shifted, a new high on the last bar with autoscale, the wheel over
    the price axis on the tag) left the + and its tag at their row with the old price, and the held line went to that
    price's new row: the axis read another price there. Now, with the pointer on the + or its tag, they stay under it
    and take the price their row has now; with its menu open, they go to the row of the menu's price. The held line is
    on their row either way, also after the pointer crossed the plot on its way to the menu."""
    o = page[run]
    _on_its_row(o["onMenu"], f"{run} onMenu")
    m = o["menuRescaled"]
    _menu_beside(m, f"{run} menu, the scale moved")
    _on_its_row(m, f"{run} menu, the scale moved")
    assert m["text"] == o["onMenu"]["text"] and abs(_mid(m["tr"]) - _mid(o["onMenu"]["tr"])) > 10, run
    for k, pre, on in (("rescaled", "rescalePre", "pr"), ("newHigh", None, "pr"), ("wheeled", "wheelPre", "tr")):
        s = o[k]
        _with_plus(s, f"{run} {k}")
        _on_axis(s, f"{run} {k}")
        _on_its_row(s, f"{run} {k}")
        x, y = s["at"]   # still under the pointer
        assert s[on]["l"] <= x <= s[on]["r"] and s[on]["t"] <= y <= s[on]["b"], (run, k, s["at"], s[on])
        if pre:
            assert s["text"] != o[pre]["text"], (run, k)   # the scale did move: another price on that row


def test_a_watchlist_menu_taking_the_plus_menus_place_lets_the_plus_go(page):
    """The watchlist's buttons open their menu over the + menu without closing it first: the + and its tag stayed, and
    the chart's own crosshair label stayed off, until the pointer next crossed the chart. They go as when the + menu
    closes."""
    o = page["desktop"]
    assert o["wlMenu"]["menu"] and not o["wlMenu"]["plusMenu"]
    _gone(o["wlClosed"], "watchlist menu closed")
    assert not o["wlClosed"]["held"]


@pytest.mark.parametrize("run", ["desktop", "phoneMouse"])
def test_in_magnet_mode_the_charts_label_stays_on_its_line_off_the_tags_row(page, run):
    """Magnet mode puts the chart's crosshair line on the open, high, low or close nearest the pointer, and the +'s row
    is the pointer's: with the chart's label off whenever the + showed, that line had no price. Now its label is on
    where it is not on the tag's row, and off where it would be (the tag the one price there)."""
    f = page[run]["magnetFar"]
    assert f["plus"] and f["tag"] and f["text"] == f["price"], run
    assert f["dist"] >= f["tr"]["h"], (run, f["dist"])   # the line well off the tag's row
    assert abs(f["mine"] - f["rows"][0]) <= 1.5, (run, f["mine"], f["rows"])   # where the library puts it
    assert f["label"] is True and f["labelAtLine"] > 100 and f["labelAtTag"] == 0, run
    m = page[run]["magnetOn"]
    _with_plus(m, f"{run} magnet, on the line's row")
    assert any(abs(r - _mid(m["tr"])) <= 1.5 for r in m["rows"]) and m["labelAtTag"] == 0, run


def test_in_a_layouts_short_narrow_chart_the_plus_menu_stays_off_the_plus(page):
    """A layout of 6 at 1280 x 720 (cells 315 x 337): with no room left of the + and no room under or over its row for
    the whole menu, the menu was moved up into the cell, over the +, its tag and the price axis, and cut off at the
    bottom. Now it takes the room under or over the row and scrolls, beside them in the middle and near the top and
    bottom. Two charts: the pointer from one into the next in two moves keeps the second one's + (the first one's late
    word that its crosshair went is not this one's); with the + menu open in one and the pointer on another, the first
    keeps its menu, its + and price and the line held at them, not the other's linked crosshair."""
    c = page["cells"]
    for k in ("mid", "bottom", "top"):
        _menu_beside(c[k], f"cell {k}")
        assert c[k]["scroll"][0] > c[k]["scroll"][1], k   # taller than the room: it scrolls
    _with_plus(c["across"], "cell across")
    s = c["otherChart"]
    _with_plus(s, "the menu open, the pointer on another chart")
    assert s["plusMenu"] and s["held"] and not s["linked"]
    assert any(abs(r - _mid(s["tr"])) <= 1.5 for r in s["line"]), (s["line"], s["tr"])
    assert s["other"]["plus"]


def test_with_a_finger_the_price_stays_next_to_the_plus(page):
    """A phone (390 x 844, touch): a long press shows the crosshair, the + (30 px) and its tag; after the finger lifts
    the crosshair is gone and the + stays 3 s, with its price beside it; a tap on the + opens its menu beside them (they
    stay while it is open, past the 3 s), a tap away closes it, and they go. In the middle and at the pane's top and
    bottom edges."""
    o = page["phoneTouch"]
    for k in ("mid", "top", "bottom"):
        r = o[k]
        for step in ("pressed", "lifted", "lifted1s", "menu", "closed"):
            if step == "closed" and not r[step]["plus"]:
                continue   # the tap that closed the menu is a touch: the + may stay its 3 s
            _with_plus(r[step], f"touch {k} {step}")
            _on_axis(r[step], f"touch {k} {step}")
            assert r[step]["text"] == r["pressed"]["text"], (k, step)
        assert r["pressed"]["pr"]["r"] - r["pressed"]["pr"]["l"] == 30   # a finger's +, as before
        _menu_beside(r["menu"], f"touch {k} menu")
        _gone(r["gone"], f"touch {k} gone")
    _with_plus(o["mid"]["menuLater"], "touch menu open past 3 s")
    _menu_beside(o["mid"]["menuLater"], "touch menu open past 3 s")
