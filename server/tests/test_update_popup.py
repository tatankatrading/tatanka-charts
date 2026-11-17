"""A newer version on GitHub: a small card on the page until Later or Got it, in place of the 9-second note. The server
sends the newest few changes' titles with the count (Updates.public); web/app.js's card shows them, once per version
on each device (Got it: 'updateSeen', the note's key; Later: back in 24 hours, or at once for a newer version). Its
parts run in node with the page stubbed; then, where node has Playwright and its Chrome, the page itself on a sim
server with the update set in its hello and sent as a broadcast."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

from test_hunt13_ui import decl
from test_hunt_web_chart import app_part, needs_node, run_app
from test_review_fixes import build_market

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


# ---- the server: the newest five titles, each cut to 120 characters ---------------------------------------------------

def test_pages_hear_the_newest_five_changes_with_the_count(tmp_path, monkeypatch):
    """Connections lists up to 10 changes; the pages' message carries the newest 5 (each a commit's first line, up to
    120 characters) with the count and the newest one's id, and stays small."""
    commits = [{"sha": f"{i:040x}", "commit": {"message": f"Change {i}: " + "x" * 200 + "\n\nits body"}} for i in range(1, 13)]
    commits[-1]["commit"]["message"] = "<img src=x onerror=alert(1)>\n\nbody"   # the newest: as GitHub has it

    def handler(req):
        return httpx.Response(200, json={"status": "behind", "ahead_by": 12, "commits": commits})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), CHART_COMMIT=SHA)
        assert m.updates.public() == {"behind": None, "latest": "", "changes": []}   # not checked yet: nothing to show
        await m.updates.check()
        return m.updates.public(), m.updates.state
    pub, state = asyncio.run(go())
    assert len(state["changes"]) == 10   # Connections' list, as before
    assert pub["behind"] == 12 and pub["latest"] == f"{12:040x}"[:12]
    assert pub["changes"][0] == "<img src=x onerror=alert(1)>"   # newest first, as written (the page sets it as text)
    assert [c.split(":")[0] for c in pub["changes"][1:]] == ["Change 11", "Change 10", "Change 9", "Change 8"]
    assert len(pub["changes"]) == 5 and all(len(c) <= 120 for c in pub["changes"]) and len(pub["changes"][1]) == 120
    assert len(json.dumps({"type": "update", **pub})) < 1024   # small (titles in plain ASCII; \uXXXX for others)


def test_connections_test_finding_a_newer_version_tells_open_pages(tmp_path, monkeypatch):
    """Connections > Updates > Test checks now: a newer version found there reaches open pages at once (the
    6-hourly check would then find nothing new to send); the same answer again sends nothing."""
    from chartsrv import connections
    behind = {"n": 2}

    def handler(req):
        commits = [{"sha": f"{i:040x}", "commit": {"message": f"Change {i}"}} for i in range(1, behind["n"] + 1)]
        return httpx.Response(200, json={"status": "behind", "ahead_by": behind["n"], "commits": commits})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), CHART_COMMIT=SHA)
        sent = []
        monkeypatch.setattr(m, "broadcast", sent.append)
        r1 = await connections.test(m, "updates", {})
        r2 = await connections.test(m, "updates", {})
        behind["n"] = 3
        await connections.test(m, "updates", {})
        return r1, r2, sent
    r1, r2, sent = asyncio.run(go())
    assert r1["ok"] and r1["message"].startswith("2 updates to take") and r2["ok"]
    assert sent == [{"type": "update", "behind": 2, "latest": f"{2:040x}"[:12], "changes": ["Change 2", "Change 1"]},
                    {"type": "update", "behind": 3, "latest": f"{3:040x}"[:12], "changes": ["Change 3", "Change 2", "Change 1"]}]


# ---- the card's parts in node: when it shows, what it says, where -------------------------------------------------------

PLACE = ["updShort", "UPD_GAP", "updSlim", "updPlaceAt", "updAxisHere", "updFrames", "updAxisGrid", "updPlace"]
PARTS = ["UPD_LATER_MS", "updNow", "updMem", "updGet", "updSet", "updRoom", *PLACE, "updateNote", "updLater", "updWait",
         "updCheck", "updShow", "updHide", "updClose"]
CARD_JS = """
const els = {}, timers = [];
let now = 1.8e12, focused = null, tall = false;
const mk = (tag) => {
  const cls = new Set(), vars = {};
  return { tagName: tag.toUpperCase(), hidden: false, textContent: '', title: '', children: [], vars,
    set innerHTML(v) { throw new Error('innerHTML used'); }, insertAdjacentHTML() { throw new Error('HTML used'); },
    replaceChildren(...k) { this.children = k; }, append(...k) { this.children.push(...k); }, contains: () => false,
    classList: { contains: (c) => cls.has(c), toggle(c, v = !cls.has(c)) { if (v) cls.add(c); else cls.delete(c); }, add(c) { cls.add(c); }, remove(c) { cls.delete(c); } },
    style: { setProperty: (k, v) => { vars[k] = v; }, removeProperty: (k) => { delete vars[k]; } }, getBoundingClientRect: () => ({ top: 104 }),
    focus() { focused = this; } };
};
const $ = (s) => (els[s] ||= mk('div'));
$('#upd').hidden = true;
const bodyCls = new Set();
let cw = null, frames = [];   // the page's width where innerWidth lags (a phone turning); a layout's frames
const document = { createElement: mk, activeElement: null, body: { classList: { add: (c) => bodyCls.add(c), remove: (c) => bodyCls.delete(c) } },
  documentElement: { get clientWidth() { return cw ?? window.innerWidth; } }, querySelectorAll: (s) => (s === '#grid iframe' ? frames : []) };
const window = { innerWidth: 1280, matchMedia: (q) => ({ get matches() {
  const w = /^\\(max-width: (\\d+)px\\)$/.exec(q);
  return w ? document.documentElement.clientWidth <= Number(w[1]) : q === '(max-height: 520px)' && tall === 'short'; } }) };
const store = new Map();
/*LOCAL*/
const brand = { name: 'Tatanka' }, TK_CELL = /*CELL*/null;
let TK_MULTI = false;
Date.now = () => now;
const setTimeout = (f, ms) => { timers.push({ f, ms }); return timers.length; }, clearTimeout = () => {};
/*APP*/
const card = () => ({ shown: !$('#upd').hidden, on: bodyCls.has('upd-on'), count: $('#upd-count').textContent,
  list: $('#upd-list').hidden ? null : $('#upd-list').children.map((li) => li.children[0].textContent),
  more: $('#upd-more').hidden ? null : $('#upd-more').textContent, app: $('#upd-app').textContent });
const T = ['Newest change', 'Second', 'Third', 'Fourth', '<img src=x onerror=alert(1)>', 'Sixth'];
const says = () => timers.filter((t) => t.ms === 400).length;   // the live region's, a moment after it shows
const out = {};
"""
LOCAL_STUB = "const local = { get: (k, d) => (store.has(k) ? store.get(k) : d), set: (k, v) => store.set(k, String(v)) };"
# the app's own helper over a browser that keeps no site data: each read and write throws (blocked, full, a WebView
# without storage, old Safari's private mode), as its try/catch takes it
LOCAL_BLOCKED = """
const localStorage = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('QuotaExceededError'); } };
const tkCellKey = (k) => k, tkWaiting = () => false, tkInWorkspace = () => false;
let workspaces = null;
""" + "/*LOCALAPP*/"

STEPS_JS = """
updateNote({ behind: 7, latest: 'v1', changes: T });   // the hello's
out.first = card();
out.firstWhere = { side: $('#upd').classList.contains('side'), top: $('#upd').vars['--upd-top'] };
timers.splice(0).filter((t) => t.ms < 1000).forEach((t) => t.f());   // the live region, a moment after
out.said = $('#upd-say').textContent;
updateNote({ behind: 7, latest: 'v1', changes: T });   // a reconnect's hello: up, not said again
out.again = [card().shown, timers.length];
updateNote({ behind: 1, latest: 'v1', changes: ['Only one'] });
out.one = card();
updateNote({ behind: 4, latest: 'v1', changes: [] });   // titles not sent: the count alone
out.bare = card();
updateNote({ behind: 2, latest: 'v1', changes: T });   // the server's numbers disagreeing: no more titles than the count
out.fewer = card();
const long = 'x'.repeat(119) + '\\u{1F680}tail';   // cut at 120 characters: the emoji whole, not half of it
updateNote({ behind: 7, latest: 'v1', changes: [long] });
out.cut = card().list[0];
updateNote({ behind: 7, latest: 'v1', changes: T });
// Later: hidden, stored with its time, and a timer for its return with the page left open
updClose(true);
out.later = { card: card().shown, stored: JSON.parse(store.get('updateLater')), timer: timers.at(-1)?.ms };
updateNote({ behind: 7, latest: 'v1', changes: T });   // heard again (a reconnect): still put off
out.laterAgain = card().shown;
now += 24 * 3600e3 - 60e3;
updCheck();
out.at23h59 = card().shown;
now += 2 * 60e3;
timers.at(-1)?.f();   // the timer, a day on (none in a chart of a layout)
out.at24h = card().shown;
updClose(true);
// the stored time edited back a day (another window's Later: storage, not this page's own, is what counts then)
const l = JSON.parse(store.get('updateLater') || '{"at":0}');
l.at -= 24 * 3600e3 + 1;
store.set('updateLater', JSON.stringify(l));
delete updMem.updateLater;   // as the storage event does
updateNote({ behind: 7, latest: 'v1', changes: T });
out.edited = card().shown;
updClose(true);
updateNote({ behind: 9, latest: 'v2', changes: ['Newer', ...T] });   // a newer version: at once
out.newer = card();
// Got it: not for this version again, Later's time or not; a newer one shows
updClose(false);
out.gotIt = [card().shown, store.get('updateSeen')];
now += 30 * 24 * 3600e3;
updateNote({ behind: 9, latest: 'v2', changes: T });
out.gotItLater = card().shown;
updateNote({ behind: 10, latest: 'v3', changes: T });
out.gotItNewer = card().shown;
// nothing to take (none behind, not checked, no id): none, and an open one goes
updateNote({ behind: 0, latest: 'v3', changes: [] });
out.none = [card().shown, card().on];
updateNote({ behind: null, latest: '', changes: [] });
updateNote(undefined);
out.unknown = card().shown;
// a device that saw the 9-second note for this version
store.clear(); for (const k in updMem) delete updMem[k];
store.set('updateSeen', 'v9');
updateNote({ behind: 2, latest: 'v9', changes: T });
out.sawNote = card().shown;
updateNote({ behind: 3, latest: 'v10', changes: T });
out.sawNoteNewer = card().shown;
console.log(JSON.stringify(out));
"""

ROOM_JS = """
const at = (w, sideOff = false, short = false) => {   // the window's width, the side panel hidden, under 520 px tall
  window.innerWidth = w;
  $('main').classList.toggle('side-off', sideOff);
  tall = short ? 'short' : false;
  updateNote({ behind: 7, latest: 'v1', changes: T });
  const c = card();
  return { side: $('#upd').classList.contains('side'), n: c.list?.length ?? 0, count: c.count, more: c.more };
};
out.desk = at(1280);
out.deskOff = at(1280, true);
out.deskOffAt = [$('#upd').vars['--upd-r'], $('#upd').vars['--upd-w']];   // placed as it shows (no chart in this stub: a usual axis)
out.mid = at(700, true);
out.phone = at(390, true);
out.phonePage = at(390);   // a phone's panel page (the panel shown, not beside the chart): over it, as on the chart
out.short = at(1280, false, true);
out.shortOff = at(640, true, true);
out.land = at(844, false, true);
out.narrow = at(320, true);   // a phone under 360 px: the card is narrower (to the price axis), the count alone
timers.splice(0);
out.placed = [updPlace(), updPlace(), $('#upd').vars['--upd-r'], timers.length, $('#upd-say').textContent];   // placed again: not said again
// a phone turned from 844 x 390 to portrait, its browser still giving the width before (innerWidth) while the page and
// the style sheet are 390 wide: a phone's card, two titles, from the left gutter to the axis
cw = 390;
out.turning = { ...at(807, true), r: $('#upd').vars['--upd-r'], w: $('#upd').vars['--upd-w'] ?? null };
cw = null;
console.log(JSON.stringify(out));
"""

BLOCKED_JS = """
updateNote({ behind: 7, latest: 'v1', changes: T });
timers.splice(0).filter((t) => t.ms < 1000).forEach((t) => t.f());
out.first = [card().shown, $('#upd-say').textContent !== ''];
updClose(true);   // Later: nothing saved, yet it holds for this page
out.later = card().shown;
updateNote({ behind: 7, latest: 'v1', changes: T });   // a reconnect's hello
updCheck();
out.laterAgain = [card().shown, says(), $('#upd-say').textContent];
now += 24 * 3600e3 + 2e3;   // a day on: back, as with storage
updCheck();
out.dayOn = card().shown;
timers.splice(0);   // said again: shown again a day on
updClose(false);   // Got it
updateNote({ behind: 7, latest: 'v1', changes: T });
out.gotIt = [card().shown, says()];
updateNote({ behind: 8, latest: 'v2', changes: T });   // a newer version still shows
out.newer = card().shown;
console.log(JSON.stringify(out));
"""


def _card(cell="null", steps=STEPS_JS, blocked=False):
    js = (CARD_JS + steps).replace("/*CELL*/null", cell)
    if blocked:
        js = js.replace("/*LOCAL*/", LOCAL_BLOCKED.replace("/*LOCALAPP*/", app_part("local")))
    return run_app(PARTS, js.replace("/*LOCAL*/", LOCAL_STUB))


@needs_node
def test_the_card_says_the_count_and_the_newest_five_titles_as_text():
    out = _card()
    assert out["first"] == {"shown": True, "on": True, "count": "7 changes:", "app": "Tatanka Charts", "more": "and 2 more",
                            "list": ["Newest change", "Second", "Third", "Fourth", "<img src=x onerror=alert(1)>"]}
    assert out["firstWhere"] == {"side": True, "top": "112px"}   # in the side panel's column; its top under the header
    # heard, the focus left alone, with the key that goes to it
    assert out["said"] == "Update available: 7 changes, in a card at the bottom of the page. Alt+U goes to it."
    assert out["again"] == [True, 0]
    assert out["one"]["count"] == "1 change:" and out["one"]["list"] == ["Only one"] and out["one"]["more"] is None
    assert out["bare"]["count"] == "4 changes." and out["bare"]["list"] is None and out["bare"]["more"] is None
    assert out["fewer"]["count"] == "2 changes:" and out["fewer"]["list"] == ["Newest change", "Second"] and out["fewer"]["more"] is None
    assert out["cut"] == "x" * 119 + "\U0001F680"


@needs_node
def test_later_is_back_after_a_day_or_at_once_for_a_newer_version_and_got_it_until_a_newer_one():
    out = _card()
    assert out["later"]["card"] is False and out["later"]["stored"] == {"v": "v1", "at": 1.8e12}
    assert out["later"]["timer"] == 24 * 3600e3 + 1000   # a page left open shows it again by itself
    assert out["laterAgain"] is False and out["at23h59"] is False and out["at24h"] is True
    assert out["edited"] is True
    assert out["newer"]["shown"] is True and out["newer"]["count"] == "9 changes:" and out["newer"]["list"][0] == "Newer"
    assert out["gotIt"] == [False, "v2"] and out["gotItLater"] is False and out["gotItNewer"] is True
    assert out["none"] == [False, False] and out["unknown"] is False


@needs_node
def test_a_device_that_saw_the_note_for_this_version_sees_nothing():
    out = _card()
    assert out["sawNote"] is False and out["sawNoteNewer"] is True


@needs_node
def test_a_chart_of_a_layout_never_shows_it():
    """Each chart of a layout is this page in a frame, and hears the same hello and broadcasts: only the page around
    them shows the card."""
    out = _card("1")
    assert out["first"]["shown"] is False and out["gotItNewer"] is False and out["newer"]["shown"] is False


@needs_node
def test_over_the_chart_it_is_shorter_and_on_a_short_screen_the_count_alone():
    """Beside the chart, in the side panel's column, it has room for five titles. Over the chart (the panel hidden,
    or a window 760 px wide or less) three, a phone's two, the rest counted; under 520 px tall the count alone, with a
    full stop (All changes lists them)."""
    out = _card(steps=ROOM_JS)
    assert out["desk"] == {"side": True, "n": 5, "count": "7 changes:", "more": "and 2 more"}
    assert out["deskOff"] == {"side": False, "n": 3, "count": "7 changes:", "more": "and 4 more"}
    assert out["mid"] == {"side": False, "n": 3, "count": "7 changes:", "more": "and 4 more"}
    assert out["phone"] == {"side": False, "n": 2, "count": "7 changes:", "more": "and 5 more"}
    assert out["phonePage"] == {"side": False, "n": 2, "count": "7 changes:", "more": "and 5 more"}
    assert out["short"] == {"side": True, "n": 0, "count": "7 changes.", "more": None}
    assert out["shortOff"] == {"side": False, "n": 0, "count": "7 changes.", "more": None}
    assert out["land"] == {"side": True, "n": 0, "count": "7 changes.", "more": None}
    assert out["narrow"] == {"side": False, "n": 0, "count": "7 changes.", "more": None}
    assert out["placed"][2:] == ["80px", 0, ""]   # no chart measured in this stub: a usual axis' 72 px and 8 kept clear
    assert out["deskOffAt"] == ["80px", "360px"]   # placed as it shows, not only as the chart moves
    # turning: the page's width, not the one innerWidth still gives
    assert out["turning"] == {"side": False, "n": 2, "count": "7 changes:", "more": "and 5 more", "r": "80px", "w": None}


# A layout of three charts across at 860 px, the panel hidden (the bottom row): the card inside the right-hand chart
# is under 240 px wide, so the count alone; at 1280 px its titles again. Shown again for that, never said again.
SLIM_JS = """
TK_MULTI = true;
const R = (left, right, top, bottom) => ({ left, right, top, bottom, width: right - left, height: bottom - top });
const fr = (l, r) => ({ contentWindow: {}, getBoundingClientRect: () => R(l, r, 423, 800) });
$('main').classList.toggle('side-off', true);
window.innerWidth = 860;
frames = [fr(0, 284), fr(287, 571), fr(574, 858)];
updateNote({ behind: 7, latest: 'v1', changes: T });
const at = () => ({ ...card(), r: $('#upd').vars['--upd-r'], w: $('#upd').vars['--upd-w'], says: says() });
out.slim = at();
window.innerWidth = 1280;
frames = [fr(0, 424), fr(427, 851), fr(854, 1278)];
updPlace();   // the window widened: its charts say so
out.wide = at();
updPlace();
out.again = at();
console.log(JSON.stringify(out));
"""


@needs_node
def test_inside_a_narrow_chart_of_a_layout_the_count_alone():
    out = _card(steps=SLIM_JS)
    # 858 - 72 - 8 = 778 its right; its left no further than 574 + 8: 196 px
    assert out["slim"] == {"shown": True, "on": True, "count": "7 changes.", "list": None, "more": None, "app": "Tatanka Charts",
                           "r": "82px", "w": "196px", "says": 1}
    assert out["wide"]["list"] == ["Newest change", "Second", "Third"] and out["wide"]["w"] == "336px" and out["wide"]["says"] == 1
    assert out["again"] == out["wide"]


# ---- over the chart: short of its price axis ------------------------------------------------------------------------------

# The page, stubbed: the card, the chart's box (#chart), the chart as the library sized it (chartElement, can be a
# pixel narrower) and its price axis' width, main's box, and a layout's frames (#grid iframe) with what each said.
PLACE_JS = """
let TK_MULTI = false, TK_CELL = null;
const R = (left, right, top = 0, bottom = 800) => ({ left, right, top, bottom, width: right - left, height: bottom - top });
let chartBox = R(37, 1242), libBox = R(37, 1241), mainBox = R(0, 1280), psW = 72, frames = [];
const box = { hidden: false, side: false, vars: {}, rect: R(801, 1161, 536, 734),
  classList: { contains: (c) => c === 'side' && box.side },
  style: { setProperty: (k, v) => { box.vars[k] = v; }, removeProperty: (k) => { delete box.vars[k]; } },
  getBoundingClientRect: () => box.rect };
const dom = { '#upd': box, '#chart': { getBoundingClientRect: () => chartBox }, main: { getBoundingClientRect: () => mainBox } };
const $ = (s) => dom[s];
const chart = { chartElement: () => ({ getBoundingClientRect: () => libBox }), priceScale: (id) => ({ width: () => (id === 'left' ? 0 : psW) }) };
const posted = [];
let cw = null;   // the page's width where innerWidth lags (a phone turning)
const window = { innerWidth: 1280, innerHeight: 800, parent: { postMessage: (m, o) => posted.push([m, o]) },
  matchMedia: (q) => ({ get matches() { const w = /^\\(max-width: (\\d+)px\\)$/.exec(q); return !!w && document.documentElement.clientWidth <= Number(w[1]); } }) };
const location = { origin: 'http://127.0.0.1:8000' };
const document = { querySelectorAll: (s) => (s === '#grid iframe' ? frames : []), documentElement: { get clientWidth() { return cw ?? window.innerWidth; } } };
let updNow = null;
const shown = [], updShow = (u) => { shown.push(u.latest); updPlace(); };   // shown again (the titles or the count alone)
/*APP*/
const frame = (l, r, t, b) => ({ contentWindow: {}, getBoundingClientRect: () => R(l, r, t, b) });
const placed = () => { updPlace(); return { ...box.vars }; };
const out = {};
"""

MATH_JS = """
const at = (m, vw, phone = false) => updPlaceAt(m, vw, phone);
out.desk = at({ plot: 37, axis: 1169, edge: 1242 }, 1280);   // 1280 x 800, the panel hidden: ES's 72 px axis
out.nq = at({ plot: 37, axis: 1161, edge: 1242 }, 1280);   // NQ's 25000.00: 80 px
out.half = at({ plot: 622.5, axis: 1170.5, edge: 1242 }, 1280);   // a frame on a half pixel: the edge rounded inward
out.mid = at({ plot: 0, axis: 628, edge: 700 }, 700);   // 601-760 px: the drawing tools a row over the chart
out.room = at({ plot: 300, axis: 600, edge: 672 }, 1280);   // the plot's left edge nearer than 360 px: as wide as there is room
out.min = at({ plot: 400, axis: 600, edge: 672 }, 1280);   // under 240 px of room: 240 px all the same, the axis kept clear
out.tiny = at({ plot: 0, axis: 200, edge: 272 }, 1280);   // and never past 16 px from the window's left
// a layout's chart: never past its own left edge (8 px in), where the next chart's axis ends, 240 px or not
out.frame = at({ plot: 611, axis: 786, edge: 858, left: 574 }, 860);   // three across at 860: 196 px
out.frameWide = at({ plot: 891, axis: 1206, edge: 1278, left: 854 }, 1280);   // at 1280: room to its plot
out.frameHalf = at({ plot: 622.5, axis: 806.5, edge: 878.5, left: 622.5 }, 1280);   // its edge on a half pixel: inward
out.framePhone = at({ plot: 281, axis: 488, edge: 560, left: 281 }, 560, false);   // a layout on a narrow window: still its chart's
out.notDrawn = at({ plot: 37, axis: null, edge: 1242 }, 1280);   // no axis to measure: a usual one's 72 px
out.none = at(null, 1280);   // nothing at all: from the window's right
out.phone = at({ plot: 0, axis: 318, edge: 390 }, 390, true);   // a phone: from the 16 px gutter on the left, to the axis
out.phone320 = at({ plot: 0, axis: 240, edge: 320 }, 320, true);
out.phonePage = at({ plot: 0, axis: null, edge: 390 }, 390, true);   // the chart behind a panel page
out.bad = at({ plot: NaN, axis: 'x', edge: undefined }, 390, true);
console.log(JSON.stringify(out));
"""


@needs_node
def test_over_the_chart_it_stops_8_px_short_of_the_price_axis():
    """The card's right edge 8 px left of the price axis' left edge (rounded inward), whatever its width; its left edge
    no further than the chart's plot (360 px at most), unless that leaves under 240 px; a phone's from its 16 px gutter.
    No axis to measure: a usual one's 72 px kept clear, never the window's full width."""
    out = run_app(PLACE, PLACE_JS + MATH_JS)
    assert out["desk"] == {"right": 119, "width": 360}   # its right edge at 1161: the axis at 1169
    assert out["nq"] == {"right": 127, "width": 360}   # wider prices: 8 px further left
    assert out["half"] == {"right": 118, "width": 360}   # at 1162, 8.5 px short of 1170.5
    assert out["mid"] == {"right": 80, "width": 360}
    assert out["room"] == {"right": 688, "width": 284}   # 308 to 592: the plot's edge plus 8
    assert out["min"] == {"right": 688, "width": 240}
    assert out["tiny"] == {"right": 1088, "width": 176}
    assert out["frame"] == {"right": 82, "width": 196}   # 582 to 778: the chart beside it (its axis to 571) clear
    assert out["frameWide"] == {"right": 82, "width": 299}   # 891 + 8 to 1198
    assert out["frameHalf"] == {"right": 482, "width": 167} and 1280 - 482 - 167 >= 622.5 + 8
    assert out["framePhone"] == {"right": 80, "width": 191}   # 289 to 480, not from a phone's gutter over the left chart
    assert out["notDrawn"] == {"right": 118, "width": 360}   # 1242 - 72 - 8
    assert out["none"] == {"right": 80, "width": 360}
    assert out["phone"] == {"right": 80, "width": None}
    assert out["phone320"] == {"right": 88, "width": None}
    assert out["phonePage"] == {"right": 80, "width": None} and out["bad"] == {"right": 80, "width": None}


HERE_JS = """
out.desk = placed();   // the chart as the library drew it: a pixel narrower than its box
psW = 80;
out.nq = placed();
psW = 72;
chartBox = R(37, 900);   // the panel shown and the chart's box smaller, the library not resized yet: the smaller
out.moving = placed();
libBox = R(37, 900);
out.moved = placed();
psW = 0; chartBox = R(37, 1242); libBox = R(37, 1241);   // not drawn yet
out.notDrawn = placed();
window.innerWidth = 390; psW = 72; chartBox = R(0, 390); libBox = R(0, 390); mainBox = R(0, 390);   // a phone
out.phone = placed();
window.innerWidth = 807; cw = 390;   // turned from 844 x 390: innerWidth not yet the page's width
out.turning = placed();
window.innerWidth = 390; cw = null;
chartBox = R(0, 0, 0, 0); libBox = R(0, 0, 0, 0);   // its chart behind a panel page
out.phonePage = placed();
window.innerWidth = 1280; chartBox = R(37, 1242); libBox = R(37, 1241); mainBox = R(0, 1280);
box.side = true; box.vars = {};   // in the side panel's column: as it was
out.side = placed();
box.side = false; box.hidden = true;
out.hidden = placed();
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_axis_is_measured_from_the_chart_as_it_is_drawn():
    """updPlace reads the chart: its price axis' width from the library and its left edge from where the chart is drawn
    (a chart box resized before the library resized the chart: the narrower of the two, never over the axis). The
    side panel's column, or a hidden card: nothing changed."""
    out = run_app(PLACE, PLACE_JS + HERE_JS)
    assert out["desk"] == {"--upd-r": "119px", "--upd-w": "360px"}
    assert out["nq"] == {"--upd-r": "127px", "--upd-w": "360px"}
    assert out["moving"] == {"--upd-r": "460px", "--upd-w": "360px"} and out["moved"] == out["moving"]
    assert out["notDrawn"] == {"--upd-r": "118px", "--upd-w": "360px"}
    assert out["phone"] == {"--upd-r": "80px"} and out["phonePage"] == {"--upd-r": "80px"}   # width: from the left gutter
    assert out["turning"] == out["phone"]   # the page's width (the style sheet's), not innerWidth's
    assert out["side"] == {} and out["hidden"] == {}


GRID_JS = """
TK_MULTI = true;
mainBox = R(0, 1280);
// 2 x 2 at 1280 x 800, the panel hidden; the card at the bottom right
const f = [frame(0, 619.5, 44, 420), frame(622.5, 1242, 44, 420), frame(0, 619.5, 423, 800), frame(622.5, 1242, 423, 800)];
frames = f;
box.rect = R(802, 1162, 484, 682);
out.early = placed();   // no chart has said yet (loading): a usual axis kept clear in the one under it
updFrames.set(f[3].contentWindow, { axis: 80, plot: 0 });   // the bottom right one: NQ
updFrames.set(f[1].contentWindow, { axis: 100, plot: 0 });   // the one above it: not under the card
updFrames.set(f[2].contentWindow, { axis: 200, plot: 0 });   // the bottom left one: not the right-hand column
out.said = placed();
box.rect = R(802, 1162, 300, 682);   // taller, over both of the right-hand column's
out.tall = placed();
box.rect = R(802, 1162, 484, 682);
f[3] = frame(622.5, 1242, 423, 800);   // that chart's frame swapped for another: the one before's word doesn't count
out.swapped = placed();
updFrames.set(f[3].contentWindow, { axis: 72, plot: 37 });
out.again = placed();
// 2 small, 1 large on the right ('3r'): the large one, top to bottom, is the one under it
frames = [frame(503, 1242, 44, 800), frame(0, 500, 44, 420), frame(0, 500, 423, 800)];
updFrames.set(frames[0].contentWindow, { axis: 72, plot: 0 });
out.large = placed();
// 3 x 2 at 760: narrow charts, the plot's edge under 240 px from the axis
window.innerWidth = 760;
frames = [frame(482, 721, 423, 800), frame(241, 479, 423, 800), frame(0, 238, 423, 800)];
updFrames.set(frames[0].contentWindow, { axis: 72, plot: 37 });
box.rect = R(400, 641, 484, 682);
updNow = { latest: 'v1' };
out.narrow = placed();
out.narrowShown = shown.splice(0);   // under 240 px: shown again for the count alone, once
out.narrowAgain = [placed(), shown.splice(0)];
// three across at 860 (each about 284 px)
window.innerWidth = 860;
frames = [frame(0, 284, 44, 800), frame(287, 571, 44, 800), frame(574, 858, 44, 800)];
updFrames.set(frames[2].contentWindow, { axis: 72, plot: 37 });
box.rect = R(500, 778, 484, 682);
out.three = placed();
// at 1280: 360 px again, the titles again
window.innerWidth = 1280;
frames = [frame(0, 424, 44, 800), frame(427, 851, 44, 800), frame(854, 1278, 44, 800)];
updFrames.set(frames[2].contentWindow, { axis: 72, plot: 37 });
out.threeWide = placed();
out.threeWideShown = shown.splice(0);
// narrower is taller: the card placed for the bottom right chart alone reaches the one above it once narrower, whose
// axis is wider (NQ): placed again for both
frames = [frame(900, 1242, 44, 420), frame(900, 1242, 423, 800)];
updFrames.set(frames[0].contentWindow, { axis: 100, plot: 0 });
updFrames.set(frames[1].contentWindow, { axis: 72, plot: 0 });
box.rect = R(882, 1162, 484, 682);
const rect0 = box.getBoundingClientRect;
box.getBoundingClientRect = () => (parseFloat(box.vars['--upd-w']) < 300 ? R(0, 0, 300, 682) : box.rect);
box.vars = {};
out.taller = placed();
box.getBoundingClientRect = rect0;
updNow = null;
frames = [];
out.noFrames = placed();
console.log(JSON.stringify(out));
"""


@needs_node
def test_in_a_layout_it_stops_short_of_the_axis_of_the_chart_it_is_over():
    """A layout's card is the page's, over the charts' frames: it clears the price axis of each chart in the right-hand
    column it is over, as each said (updAxisSay); a chart not heard from yet, or in a frame just swapped in, keeps a
    usual axis clear."""
    out = run_app(PLACE, PLACE_JS + GRID_JS)
    assert out["early"] == {"--upd-r": "118px", "--upd-w": "360px"}   # 1242 - 72 - 8
    assert out["said"] == {"--upd-r": "126px", "--upd-w": "360px"}   # 1242 - 80 - 8
    assert out["tall"] == {"--upd-r": "146px", "--upd-w": "360px"}   # the one above too: its wider axis
    assert out["swapped"] == {"--upd-r": "118px", "--upd-w": "360px"} and out["again"] == out["swapped"]
    assert out["large"] == {"--upd-r": "118px", "--upd-w": "360px"}
    # 721 - 72 - 8 = 641 its right; its left at 490, 8 px in from its chart's (the middle chart's axis ends at 479)
    assert out["narrow"] == {"--upd-r": "119px", "--upd-w": "151px"} and 760 - 119 - 151 >= 482 + 8
    assert out["narrowShown"] == ["v1"] and out["narrowAgain"] == [out["narrow"], []]
    assert out["three"] == {"--upd-r": "82px", "--upd-w": "196px"} and 860 - 82 - 196 >= 574 + 8
    assert out["threeWide"] == {"--upd-r": "82px", "--upd-w": "299px"} and out["threeWideShown"] == ["v1"]
    # 254 px wide for the bottom one alone (1162 - 908), taller then: over the top one too, 1242 - 100 - 8 = 1134
    assert out["taller"] == {"--upd-r": "146px", "--upd-w": "226px"}
    assert out["noFrames"] == {"--upd-r": "80px", "--upd-w": "360px"}


SAY_JS = """
TK_CELL = 3;
window.innerWidth = 619; window.innerHeight = 377;
chartBox = R(37, 619); libBox = R(37, 619); psW = 72;
updAxisSay();
updAxisSay();   // nothing changed: nothing said
psW = 80;   // NQ
updAxisSay();
window.innerHeight = 300;   // its frame shorter: said (the page places the card again)
updAxisSay();
psW = 0;   // its chart not drawn (a reload)
updAxisSay();
out.posted = posted;
console.log(JSON.stringify(out));
"""


@needs_node
def test_a_chart_of_a_layout_says_where_its_axis_is_when_that_changes():
    """Each chart of a layout tells the page around the charts (same origin only) how far in from its right edge its
    price axis starts and where its plot starts, once per change, and when its frame is resized."""
    out = run_app(PLACE + ["updAxisWas", "updAxisSay"], PLACE_JS + SAY_JS)
    o = "http://127.0.0.1:8000"
    msg = lambda axis, h: [{"tk": True, "type": "axis", "axis": axis, "plot": 37, "w": 619, "h": h}, o]
    assert out["posted"] == [msg(72, 377), msg(80, 377), msg(80, 300), msg(None, 300)]


HEARD_JS = """
TK_MULTI = true;
const f = [frame(0, 619.5, 423, 800), frame(622.5, 1242, 423, 800)];
frames = f;
box.rect = R(802, 1162, 484, 682);
const o = location.origin, said = (d, source = f[1].contentWindow, origin = o) => { updAxisHeard({ origin, source, data: { tk: true, type: 'axis', ...d } }); return { ...box.vars }; };
out.heard = said({ axis: 80, plot: 37 });   // the bottom right chart on NQ
out.otherOrigin = said({ axis: 120, plot: 37 }, f[1].contentWindow, 'http://evil.example');
const stray = {};   // a window that isn't one of #grid's frames (a popup, a frame taken out of the grid)
out.notInGrid = said({ axis: 120, plot: 37 }, stray);
out.strayKept = updFrames.has(stray);
out.notAxis = (updAxisHeard({ origin: o, source: f[1].contentWindow, data: { tk: true, type: 'updReach', axis: 120 } }), { ...box.vars });
out.junk = said({ axis: '120', plot: { x: 1 } });   // not numbers: as not said (a usual axis)
out.stored = updFrames.get(f[1].contentWindow);
console.log(JSON.stringify(out));
"""


@needs_node
def test_the_page_around_a_layout_hears_only_its_own_charts():
    """updAxisHeard: a chart's word counts from this page's origin and from a frame of #grid only; what isn't a number is
    taken as not said; each word heard places the card again."""
    out = run_app(PLACE + ["updAxisHeard"], PLACE_JS + HEARD_JS)
    assert out["heard"] == {"--upd-r": "126px", "--upd-w": "360px"}   # 1242 - 80 - 8
    assert out["otherOrigin"] == out["heard"] and out["notInGrid"] == out["heard"] and out["notAxis"] == out["heard"]
    assert out["strayKept"] is False   # not kept either
    assert out["junk"] == {"--upd-r": "118px", "--upd-w": "360px"} and out["stored"] == {"axis": None, "plot": None}


def test_the_card_is_placed_again_as_the_axis_can_move():
    """The events that place it again (Chrome sees them work; CI has no Chrome): the chart's size, the fonts its prices
    are drawn in, each chart of a layout's frame resized and its word heard by the page around them, frames coming and
    going, the window resized or the panel shown or hidden, and as it shows."""
    app = (ROOT / "web" / "app.js").read_text()
    for line in ["chart.timeScale().subscribeSizeChange(updAxisMoved);", "document.fonts?.addEventListener?.('loadingdone', updAxisMoved);",
                 "if (TK_CELL !== null) window.addEventListener('resize', updAxisMoved);", "  window.addEventListener('message', updAxisHeard);",
                 "  new MutationObserver(updPlace).observe($('#grid'), { childList: true });", "window.addEventListener('resize', updFit);",
                 "new MutationObserver(updFit).observe($('main'), { attributes: true, attributeFilter: ['class'] });"]:
        assert f"\n{line}\n" in app, line
    assert "updPlace();" in app_part("updShow")


@needs_node
def test_later_and_got_it_hold_where_the_browser_keeps_no_site_data():
    """Storage full, site data blocked or a WebView without it: local.set saves nothing, and the card read storage
    again at once and came straight back, said again each time. The page's own choice holds for the page."""
    out = _card(steps=BLOCKED_JS, blocked=True)
    assert out["first"] == [True, True]
    assert out["later"] is False and out["laterAgain"] == [False, 0, ""]
    assert out["dayOn"] is True
    assert out["gotIt"] == [False, 0] and out["newer"] is True


def test_the_style_sheet_keeps_it_small_and_off_the_chart():
    """What the page test sees in Chrome, read from web/style.css where Playwright isn't (CI): beside the chart in the
    side panel's column, else small over it (at most 45% of the screen's height, a title a line, scrolling rather than
    squeezing a part to nothing) and short of its price axis (where app.js puts it; a usual axis' 72 px and 8 kept
    clear before), a phone's card 16 px in from the left with finger-sized buttons, All changes on a line of its own
    where the buttons don't fit beside it, no slide-in with reduced motion, and over the charts of a layout aside only
    for a chart's dialog or menu."""
    assert decl("#upd", "overflow-y") == "auto" and "45dvh" in decl("#upd", "max-height") and "var(--upd-top" in decl("#upd", "max-height")
    assert decl("#upd", "right") == "var(--upd-r, calc(80px + env(safe-area-inset-right)))"
    assert decl("#upd", "width") == "var(--upd-w, min(360px, calc(100% - 96px)))"
    assert decl("#upd > *", "flex") == "none"
    assert decl("#upd-list li span", "white-space") == "nowrap" and decl("#upd-list li span", "text-overflow") == "ellipsis"
    assert decl("#upd.side", "width") == "min(360px, calc(var(--side-w, 330px) - 16px))" and decl("#upd.side", "right") == "8px"
    assert decl("#upd .upd-ft", "flex-wrap") == "wrap" and decl("#upd .upd-ft .link", "margin-right") == "auto"
    assert decl("#upd .upd-bt", "display") == "flex" and decl("#upd .upd-bt", "margin-left") == "auto"   # the two together, at the right
    phone, own = "@media (max-width: 600px)", "body:not(.multi) #upd"   # a layout's window as narrow: inside its chart
    assert decl(own, "left", phone).startswith("calc(16px") and decl(own, "right", phone) is None   # its right: the axis'
    assert decl(own, "width", phone) == "auto"
    for media in ("@media (max-width: 760px)", phone):
        for sel in ("#upd", "body.multi #upd", "body.multi #upd:not(.side)"):
            assert decl(sel, "right", media) is None and decl(sel, "width", media) is None and decl(sel, "left", media) is None
    assert decl("#upd .upd-ft button", "min-height", "@media (max-width: 600px), (hover: none)") == "40px"
    assert decl("#upd", "animation", "@media (prefers-reduced-motion: reduce)") == "none"
    assert decl("body.cell-over #upd:not(.side)", "visibility") == "hidden" and decl("body.cell-note #upd", "visibility") is None
    assert decl("#upd .upd-ft .primary:focus-visible", "outline-offset") == "2px"


# ---- the page itself: Chrome through Playwright, on a sim server ----------------------------------------------------------

def _playwright() -> str:
    """Why the page can't be driven here, or '' (node with Playwright: its Chrome is checked when it starts)."""
    if not shutil.which("node"):
        return "needs node"
    r = subprocess.run(["node", "-e", "require.resolve('playwright')"], capture_output=True, text=True)
    return "" if r.returncode == 0 else "needs Playwright for node"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sim_server(tmp_path):
    """A sim server on a free port over a temp data dir (config/*.json copied in, the setup guide marked done, no update
    check of its own); only this server is stopped after."""
    for sub in ("config", "levels", "backups", "feeds"):
        (tmp_path / sub).mkdir()
    for f in (ROOT / "config").glob("*.json"):
        shutil.copy(f, tmp_path / "config" / f.name)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHART_")}
    env.update({"CHART_DB": str(tmp_path / "chart.db"), "CHART_CONFIG_DIR": str(tmp_path / "config"), "CHART_FEED": "sim",
                "CHART_QUOTES_FEED": "sim", "CHART_LEVELS_DIR": str(tmp_path / "levels"), "CHART_SIM_WARMUP_MIN": "1",
                "CHART_SYMBOLS_ONLINE": "0", "CHART_RESTART_EXEC": "0", "CHART_DB_WATCH": "0", "CHART_UPDATE_REPO": "none",
                "CHART_BACKUP_DIR": str(tmp_path / "backups"), "CHART_FEEDS_DIR": str(tmp_path / "feeds")})
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
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


def _clear(ax):
    """Over the chart, short of its price axis: the card's right edge 8 px left of the axis' (to a pixel), the axis
    beside the card the chart's from top to bottom, and the live price's label and its countdown there too."""
    assert not ax["side"], ax
    assert ax["axis"] - 9.5 <= ax["right"] <= ax["axis"] - 7, ax
    assert ax["column"] == ["chart"] and ax["label"] == ["chart"] * 5 and ax["beside"], ax


def _apart(ax):
    """The chart of a layout left of the one the card is over: its axis, across its width down the card's height, and
    its live price's label and countdown moved to the card's height, the chart's; the card's left edge right of it."""
    assert ax["column"] == ["chart"] and ax["label"] == ["chart"] * 5 and ax["beside"], ax
    assert ax["left"] >= ax["axisR"] + 8, ax


def _fits(fit, finger=False):
    """Nothing past the window's edge or the card's, nothing scrolled away or cut, no button over another; a finger's
    size where it says."""
    assert fit["page"] <= 0 and fit["hidden"] <= 1 and fit["out"] == [] and fit["cut"] == [] and not fit["overlap"], fit
    assert not finger or min(fit["buttons"]) >= 40, fit


@pytest.mark.skipif(bool(_playwright()), reason=_playwright() or "-")
def test_the_card_on_the_page(sim_server):
    r = subprocess.run(["node", str(Path(__file__).with_name("update_popup_page.js")), str(sim_server)],
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().split("\n")[-1])
    if "skip" in out:
        pytest.skip(out["skip"])
    assert not out.get("error"), out.get("error")
    assert out["errors"] == []   # no page error, and no alert(): the title with an <img> in it stayed text

    # on load: bottom right, in the side panel's column (none of the chart under it), under the header; the count,
    # five titles and the rest counted, how to take it; the server's texts as text
    load = out["load"]
    assert load["shown"] and load["count"] == "7 changes:" and load["more"] == "and 2 more"
    assert load["list"] == ["Newest change: the update card", "Second change", "Third change", "Fourth change", "<img src=x onerror=alert(1)>"]
    assert load["imgs"] == 0
    assert load["how"] == ("Run update.cmd on the PC that runs Tatanka Charts (Mac or Linux: ./update.sh). "
                           "It says at the end if NinjaTrader needs anything.")
    assert load["side"] and load["vw"] - load["rect"]["right"] == 8 and load["rect"]["bottom"] < load["vh"] and load["rect"]["width"] <= 360
    assert load["rect"]["left"] >= load["plotRight"] and load["rect"]["top"] >= load["mainTop"]
    # a dialog, not modal, named by its title; said politely, the focus left where it was; its buttons
    assert out["aria"] == {"role": "dialog", "modal": None, "labelled": "Update available", "focus": True, "anim": "upd-in",
                           "keys": "Alt+U", "buttons": ["All changes", "Later", "Got it"],
                           "say": ["status", "polite", "Update available: 7 changes, in a card at the bottom of the page. Alt+U goes to it."]}
    assert out["chartClick"] is True   # the chart takes a click beside it; the card stays
    assert out["altU"] == {"id": "upd-ok", "visible": True} and out["altUBack"] == "upd-later"   # Alt+U: a key away
    # the side panel hidden: over the chart, at the bottom right short of the price axis, 360 px wide, shorter: three
    # titles, under half the screen
    off, on = out["sideOff"], out["sideOn"]
    assert not off["side"] and off["rect"]["width"] == 360 and off["list"] == load["list"][:3] and off["more"] == "and 4 more"
    assert off["rect"]["height"] <= 0.45 * off["vh"] and off["rect"]["top"] >= off["mainTop"]
    _clear(out["axisOff"])
    _fits(out["fitOff"])
    # NQ's prices (25000.00): a wider axis, and the card as much further left; ES again: back
    nq, es = out["axisNQ"], out["axisES"]
    assert nq["sym"] == "NQ" and nq["axisW"] > out["axisOff"]["axisW"] and es["sym"] == "ES"
    _clear(nq)
    _clear(es)
    assert abs((out["axisOff"]["right"] - nq["right"]) - (nq["axisW"] - out["axisOff"]["axisW"])) <= 1
    assert abs(es["right"] - out["axisOff"]["right"]) <= 1
    assert on["side"] and on["list"] == load["list"]   # the panel shown: in its column as before
    _clear(out["axisOff2"])   # hidden again: short of the axis again
    assert out["escMenu"] == {"menu": False, "card": True}   # a menu open: Esc closes it first
    assert out["tab"] == {"id": "upd-later", "visible": True, "outline": "solid"}   # Tab moves through it, seen
    # Esc inside it is Later: gone, and not back on a reload ...
    assert out["esc"]["shown"] is False and json.loads(out["esc"]["later"])["v"] == "aaaaaaaaaaaa" and out["escFocus"]
    assert out["laterReload"] is False
    assert out["after24h"] is True   # ... until a day later
    assert out["laterClick"]["shown"] is False
    # a newer version, heard live: at once, the focus where it was
    assert out["newer"]["shown"] and out["newer"]["count"] == "9 changes:" and out["newer"]["list"][0] == "A newer change"
    assert out["newer"]["more"] == "and 4 more" and out["newerFocus"] == "ctab-add"
    # Got it: gone, not on a reload, back for a newer version
    assert out["gotIt"]["shown"] is False and out["gotIt"]["seen"] == "bbbbbbbbbbbb"
    assert out["gotItReload"] is False and out["gotItNewer"] is True
    # All changes: Connections at its Updates card, App opened for it (not kept open), the focus there
    assert out["allChanges"]["open"] and out["allChanges"]["app"] and out["allChanges"]["inView"] and out["allChanges"]["focus"]
    assert "App" not in (out["allChanges"]["kept"] or "")
    assert out["fired"]["firedBottom"] <= out["fired"]["updTop"]   # a fired alert's panel above it, not over it
    assert out["sawNote"] is False   # 'updateSeen' already this version (the old note's): nothing

    # a layout of four charts: one card, the page's; none in the charts, on load or on a broadcast
    assert out["layout"] == {"frames": 4, "multi": True, "shown": [1, 0, 0, 0, 0]}
    assert out["layoutLive"] == [1, 0, 0, 0, 0]
    assert out["layoutCard"]["side"] and out["layoutCard"]["rect"]["left"] >= out["layoutCard"]["plotRight"]   # beside the charts
    assert out["layoutAltU"] == {"focus": "upd-ok", "back": "IFRAME", "shown": False}   # from a chart, and back to it

    # storage that won't save: Later, Got it and Esc close it for this page, and it isn't said again
    bl = out["blocked"]
    assert bl["load"] and not bl["later"]["shown"] and bl["later"]["say"] == "" and bl["again"] is False and bl["newer"] is True
    assert not bl["gotIt"]["shown"] and not bl["esc"]["shown"]
    # short windows: the count alone, the card's top under the header
    for sh in (out["short"], out["land"], out["landOff"]["card"]):
        assert sh["shown"] and sh["count"] == "7 changes." and sh["list"] == [] and sh["more"] is None and sh["rect"]["top"] >= sh["mainTop"]
    assert out["land"]["side"] and not out["short"]["side"]
    # the phone turned, its panel hidden: over the chart, short of the axis, a finger's size
    _clear(out["landOff"]["axis"])
    _fits(out["landOff"]["fit"], finger=True)
    # and turned back to portrait: a phone's card again from the 16 px gutter to the axis, two titles (not the width
    # the browser gave while it zoomed to the new one)
    tu = out["turned"]
    assert tu["card"]["shown"] and not tu["card"]["side"] and tu["card"]["rect"]["left"] == 16 and len(tu["card"]["list"]) == 2
    _clear(tu["axis"])
    _fits(tu["fit"], finger=True)
    # 601-760 px: over the chart, 360 px wide, short of the axis
    mid = out["mid"]
    assert mid["card"]["shown"] and mid["card"]["rect"]["width"] == 360 and len(mid["card"]["list"]) == 3
    _clear(mid["axis"])
    _fits(mid["fit"])
    # 320 x 568: 16 px in from the left, to the axis (NQ's too); the count alone; All changes a line of its own
    p320 = out["p320"]
    assert p320["card"]["shown"] and p320["card"]["rect"]["left"] == 16 and p320["card"]["count"] == "7 changes." and p320["card"]["list"] == []
    _clear(p320["axis"])
    _fits(p320["fit"], finger=True)
    assert p320["fit"]["allLine"]
    assert out["p320NQ"]["axis"]["sym"] == "NQ"
    _clear(out["p320NQ"]["axis"])
    _fits(out["p320NQ"]["fit"], finger=True)

    # a layout of four charts, the panel hidden: over the charts, short of the bottom right chart's axis, as that chart
    # says; on NQ (wider) and reloaded, still
    g = out["gridOff"]
    assert g["card"]["shown"] and not g["card"]["side"]
    _clear(g["axis"])
    _fits(g["fit"])
    assert out["gridNQ"]["sym"] == "NQ" and out["gridNQ"]["axisW"] > g["axis"]["axisW"] and out["gridNQ"]["right"] < g["axis"]["right"]
    _clear(out["gridNQ"])
    assert out["gridReload"]["sym"] == "NQ"
    _clear(out["gridReload"])
    # its window made as narrow as a phone, the charts still side by side: inside the bottom right one, not from a
    # phone's gutter over the bottom left one's axis
    g = out["grid560"]
    assert g["card"]["shown"] and not g["card"]["side"] and g["card"]["rect"]["left"] > 16
    _clear(g["br"])
    _apart(g["left"])
    _fits(g["fit"])
    # three charts across on a window under 1000 px: inside the right-hand chart (the one left of it clear), the count
    # alone; 1280 px wide again: its titles
    for key in ("g32", "g3c"):
        g, wide = out[key], out[f"{key}Wide"]
        assert g["card"]["shown"] and not g["card"]["side"] and g["card"]["count"] == "9 changes." and g["card"]["list"] == [], g["card"]
        _clear(g["br"])
        _apart(g["left"])
        _fits(g["fit"])
        assert len(wide["card"]["list"]) == 3
        _clear(wide["br"])

    # a phone, 390 px: across the bottom from 16 px in to the price axis, nothing past the screen's edge, finger-sized
    # buttons; over its Watchlist page (no chart to measure) a usual axis' 72 px and 8 kept clear, the chart again: its
    phone, more = out["phone"], out["phoneMore"]
    assert phone["shown"] and phone["rect"]["left"] == 16
    _clear(out["phoneAxis"])
    _fits(out["phoneFit"], finger=True)
    assert out["phonePage"]["shown"] and out["phonePage"]["vw"] - out["phonePage"]["rect"]["right"] == 80
    _clear(out["phoneBack"])
    assert phone["rect"]["height"] <= 200 and len(phone["list"]) == 2 and phone["more"] == "and 5 more"   # small over the chart
    assert more["scroll"] == [390, 390, 390] and more["wide"] == []
    assert min(more["buttons"]) >= 40
    assert more["anim"] == "none"   # reduced motion: no slide in
