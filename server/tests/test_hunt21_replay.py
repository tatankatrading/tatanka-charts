"""Round 21's bug hunt, replay, footprint, journal and the Plan tab: what its reviews found, each fixed. The app.js
parts run in node with what they call stubbed."""
from datetime import datetime, timedelta

import pytest

from test_hunt13_layout import studies_part
from test_hunt_web_chart import WEB, app_client, app_lines, ct_ms, needs_node, run_app  # noqa: F401  (app_client: a fixture)
from test_redesign_journal_positions import E, P, T, _journal

from chartsrv.market import DAY_MS
from chartsrv.models import Tick


# ---- r21-c26: replay on tick, volume, range and Renko bars: nothing from the bar not shown yet on the last one shown -----

TRADEBAR_JS = """
const S = { rth: false, ext: null, spec: '500t', bars: [], replay: null };
const inst = () => ({}), ctWall = (t) => t, isTime = () => false, barIndexTo = () => -1, specSeconds = () => 0;
// the chart's logical index of a time: the bar opening at or before it
const drawings = { timeToLogical: (t) => { let i = S.bars.length - 1; while (i >= 0 && S.bars[i].t > t) i--; return i < 0 ? null : i; } };
/*APP*/
const rp = (data, idx, more) => ({ at: 4500, data, idx, kind: 'tick', period: 0, days: false, nextAt: null, endAt: null, ...more });
const data = [{ t: 1000 }, { t: 2000 }, { t: 3000 }, { t: 4000 }];
const out = {};
S.bars = data.slice(0, 3);
out.live = [1500, 3500, 4500].map(tradeBar);   // live: the last bar is forming, every trade since its open is its
S.replay = rp(data, 3);   // Go at 4500: the bar opening at 4000 holds the clock, so it is hidden; the one at 3000 ends at 4000
out.typed = [1500, 3500, 3999, 4000, 4400].map(tradeBar);
S.replay = rp(data.slice(0, 3), 3, { endAt: 3600 });   // every bar loaded shown, at the present: the last ends at the forming one's open
out.present = [3500, 3600, 4400].map(tradeBar);
S.replay = rp([{ t: 1000 }, { t: 2000 }, { t: 3000, e: 3300 }, { t: 4000 }], 3);   // a bar ending with its hours (e)
out.hours = [3200, 3300].map(tradeBar);
console.log(JSON.stringify(out));
"""


@needs_node
def test_replay_on_a_tick_chart_puts_no_fill_or_big_trade_from_the_hidden_bar_on_the_last_bar_shown():
    """Replay typed or jumped to a time inside a tick bar hides that bar, but the fills, a finished trade's exit and
    P&L and the big trades up to the clock were drawn on the last bar shown (tradeBar gave it every trade after its
    open). In replay that bar ends where the next one opens: a trade from then on is in no bar shown."""
    out = run_app(["tradeBar", "barEnd"], TRADEBAR_JS)
    assert out["live"] == [0, 2, 2]
    assert out["typed"] == [0, 2, 2, None, None]
    assert out["present"] == [2, None, None]
    assert out["hours"] == [2, None]


# ---- r21-c30: Big trades: no request (and no error toast) while a replay's bars are on their way --------------------

BIG_JS = """
const L = 1_794_465_000_000, H = 3_600_000;
const S = { st: { big: true }, ext: null, symbol: 'NQ', bars: [], replay: null, big: [], bigKey: '', bigFrom: 0, bigTo: 0 };
const TK_MULTI = false, asked = [], toasts = [], local = { get: (k, d) => d };
const chart = { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: 0, to: 9 }) }) };
const bigPrim = { update() {} }, toast = (m) => toasts.push(m);
const api = async (method, path) => {   // the server's check
  const q = new URLSearchParams(path.split('?')[1]), start = +q.get('start'), end = +q.get('end');
  asked.push([start, end]);
  if (!(end - start >= 0 && end - start <= 3 * 86_400_000)) throw new Error('start..end must be 0 to 3 days');
  return { trades: [] };
};
/*APP*/
(async () => {
  S.bars = Array.from({ length: 10 }, (_, k) => ({ t: L + k * 1000 }));   // the live chart, at its edge
  S.replay = { at: L - 39 * H };   // Go pressed: the clock is set, the replay's bars not here yet
  await bigFetch();
  const loading = { asked: asked.splice(0), toasts: toasts.splice(0) };
  S.bars = Array.from({ length: 10 }, (_, k) => ({ t: L - 40 * H + k * 60_000 }));   // they come (applyHistory asks again)
  await bigFetch();
  console.log(JSON.stringify({ loading, loaded: { asked, toasts } }));
})();
"""


@needs_node
def test_big_trades_ask_nothing_while_the_replays_bars_are_on_their_way():
    """A Big trades request due as replay started went with the live bars' times and the replay clock: start after
    end, so the server refused it and an error toast showed. The replay's bars ask again when they come."""
    out = run_app(["BIG_DEFAULT", "bigMins", "bigMin", "bigOn", "bigFetch"], BIG_JS)
    L, H = 1_794_465_000_000, 3_600_000
    assert out["loading"] == {"asked": [], "toasts": []}
    assert out["loaded"] == {"asked": [[L - 40 * H, L - 39 * H]], "toasts": []}


# ---- r21-c27: a trade with no side takes the tick rule from the trades before the request, not from nothing -----------

def test_a_bars_footprint_with_no_sides_is_the_same_whichever_bars_it_is_asked_with(app_client):
    """The tick rule started afresh on every request: the first trade with no side counted as a buy, and the ones at
    its price after it too. So a bar's bid x ask depended on whether the bar before it was asked with it, and the
    chart keeps each bar's answer by where it was in a request. Now the trades before the request set it, as in the
    market: the stored ones and those still in the write buffer."""
    t0 = ct_ms(2026, 10, 21, 10, 0)
    ticks = [Tick("ES", t0 + 1000, 100.25, 1, 0), Tick("ES", t0 + 2000, 100.0, 3, 0),   # a downtick: a sell
             Tick("ES", t0 + 61_000, 100.0, 17, 0), Tick("ES", t0 + 62_000, 100.25, 4, 0),   # unchanged: a sell; up: a buy
             Tick("ES", t0 + 119_000, 100.25, 1, -1)]   # a side given (hit the bid)
    with app_client(ticks) as c:
        def bar(starts, k):
            body = {"symbol": "ES", "starts": starts, "end": starts[-1] + 60_000}
            return c.post("/api/footprint", json=body).json()["bars"][k]["rows"]
        b, b2, b3 = t0 + 60_000, t0 + 120_000, t0 + 180_000
        assert bar([b], 0) == bar([t0, b], 1) == [[400, 17, 0], [401, 1, 4]]
        # the next trades, not written yet: unchanged after the sell, sells too
        c.app.state.market.tick_buf.extend([Tick("ES", t0 + 121_000, 100.25, 5, 0), Tick("ES", t0 + 181_000, 100.25, 2, 0)])
        assert bar([b2], 0) == bar([b, b2], 1) == [[401, 5, 0]]
        assert bar([b3], 0) == bar([b2, b3], 1) == [[401, 2, 0]]


def test_an_rth_charts_first_bar_takes_the_tick_rule_from_the_trade_before_it_not_the_last_session_or_nothing(app_client):
    """On an RTH chart the night's trades were left out before the tick rule: the open's first trade with no side
    compared with the session before's close when asked with its last bar, and counted as a buy when asked alone. It
    takes it from the trade just before it, as every chart of the symbol does."""
    close, first = ct_ms(2026, 10, 20, 14, 59), ct_ms(2026, 10, 21, 8, 31)
    ticks = [Tick("ES", close, 100.0, 1, 0), Tick("ES", ct_ms(2026, 10, 20, 18, 0), 100.5, 2, 0),
             Tick("ES", ct_ms(2026, 10, 21, 8, 0), 100.25, 3, 0),   # the night's last trade: a downtick, a sell
             Tick("ES", first, 100.25, 7, 0)]   # unchanged after it: a sell
    starts, end = [ct_ms(2026, 10, 20, 14, 55), ct_ms(2026, 10, 21, 8, 30)], ct_ms(2026, 10, 21, 8, 35)
    with app_client(ticks) as c:
        def bars(starts):
            return c.post("/api/footprint", json={"symbol": "ES", "starts": starts, "end": end, "rth": True}).json()["bars"]
        both = bars(starts)
        assert both[0]["rows"] == [[400, 0, 1]]   # the night is in neither bar
        assert both[1]["rows"] == bars(starts[1:])[0]["rows"] == [[401, 7, 0]]


# ---- r21-c28: If/Then in replay: the daily ATR as it stood at the replay clock ---------------------------------------

def _ifthen_days():
    """A Tuesday two weeks or more back, and ES 1-minute bars on every weekday from 40 days before it to yesterday:
    each day's range 20 points before it, 80 from it on."""
    from chartsrv.models import Bar
    from chartsrv.sessions import CT
    today = datetime.now(CT).date()
    p = today - timedelta(days=14)
    while p.weekday() != 1:
        p -= timedelta(days=1)
    rows, day = [], p - timedelta(days=40)
    while day < today:
        if day.weekday() < 5:
            r = 80 if day >= p else 20
            for h, m in ((8, 30), (12, 0)):
                t = ct_ms(day.year, day.month, day.day, h, m)
                rows.append(("ES", Bar(t=t, o=5000.0, h=5000.0 + r / 2, l=5000.0 - r / 2, c=5000.0, v=10)))
        day += timedelta(days=1)
    return p, rows


def test_if_then_in_replay_gets_the_daily_atr_as_it_stood_at_the_replay_clock(tmp_path, monkeypatch):
    """GET /api/ifthen had no `until`: replay drew the Position colors and zones with today's ATR, taken over every
    day after the replay date too. It now takes the days before the replay clock's trade date, and whether that day
    had traded by then (before its first minute, the ATR is the day's before the last one), as it stood."""
    from fastapi.testclient import TestClient

    from chartsrv import sessions
    from chartsrv.app import app
    from chartsrv.store import Store
    p, rows = _ifthen_days()
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_FEED", "none")
    Store(tmp_path / "chart.db").write([], rows)
    nxt = p + timedelta(days=1)
    at = lambda d, h, m=0: ct_ms(d.year, d.month, d.day, h, m)   # noqa: E731
    with TestClient(app) as c:
        sessions.set_calendar({"days": []})   # the dates are the test's: no holiday among them

        def atr(until=None, **q):
            d = c.get("/api/ifthen/ES", params={"atr": 3, **({"until": until} if until else {}), **q}).json()
            return round(d["atr"], 6), d.get("today")
        replay = atr(at(p, 10))   # its own daily bars, read for it
        assert replay == (20.0, True)
        live = atr()
        assert live[0] > 70
        assert atr(at(p, 10)) == replay   # the live ones kept, only the days before it
        assert atr(at(p, 17)) == (20.0, False)   # the next trade date opened, no minute yet: the day before the last
        assert atr(at(nxt, 9)) == (40.0, True)   # traded: the last day's, the first 80-point day in it
        assert atr(at(p, 10), rth="true") == (20.0, True) and atr(at(nxt, 9), rth="true") == (40.0, True)


IFTHEN_JS = """
const asked = [];
const S = { symbol: 'ES', instruments: { ES: {} }, ifSet: { AtrLength: 14 }, ifData: null, ifRows: [], rth: false, replay: null };
let todayAt = 0;   // the server: when the replay day's first minute is in
const wait = (ms) => new Promise((r) => setTimeout(r, ms)), slow = new Set();
const api = async (method, path) => {
  asked.push(path);
  const until = Number(new URLSearchParams(path.split('?')[1]).get('until')) || null;
  if (slow.has(until)) await wait(30);
  return { rows: '', atr: until, today: until == null || until >= todayAt };
};
const indReady = () => true, biasParse = () => [], amtLayout = () => {}, ifPrim = { update() {} }, renderPlanIf = () => {};
const renderIfInfo = () => {}, indDlg = { refresh() {} }, $ = () => ({});
const msToCtInput = (ms) => new Date(ms - 6 * 3600e3).toISOString().slice(0, 16);   // this test's CT: UTC-6
/*APP*/
(async () => {
  const ct = (d, h, m = 0, s = 0) => Date.UTC(2026, 9, d, h + 6, m, s);
  const out = { stale: [] };
  await ifRequest();   // live: no clock
  S.replay = { at: ct(29, 10) };
  await ifRequest();
  const step = (d, h, m, s) => { S.replay.at = ct(d, h, m, s); return ifStale(); };
  out.stale.push(step(29, 10, 5), step(29, 16));   // the same trade date: asked already
  todayAt = ct(29, 17, 1);
  out.stale.push(step(29, 17));   // 17:00: the next trade date
  await ifRequest();
  out.today = S.ifData.today;
  out.stale.push(step(29, 17, 0, 30), step(29, 17, 1));   // no trade in it yet: asked again once a minute has passed
  await ifRequest();
  out.stale.push(step(29, 17, 5));
  // playing fast: an answer for an earlier clock that comes after a later one's is not shown
  slow.add(ct(30, 9));
  S.replay.at = ct(30, 9);
  const first = ifRequest();
  S.replay.at = ct(30, 9, 5);
  await ifRequest();
  await first;
  out.shown = S.ifData.atr === ct(30, 9, 5);
  out.asked = asked.map((p) => p.replace(/until=\\d+/, 'until=…'));
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_if_then_in_replay_asks_for_the_replay_clocks_atr_and_again_on_a_new_trade_date():
    """The chart asked for the live ATR in replay. It now sends the replay clock, asks again when the clock reaches
    another trade date (and until that day's first minute is in), and shows only the newest clock's answer."""
    out = run_app(["replayUntil", "ifHeard", "ifStale", "ifRequest", "tradeDate"], IFTHEN_JS)
    assert out["asked"][0] == "/api/ifthen/ES?atr=14"
    assert out["asked"][1:] == ["/api/ifthen/ES?atr=14&until=…"] * 5
    assert out["stale"] == [False, False, True, False, True, False]
    assert out["today"] is False and out["shown"] is True


# ---- r21-c29: the journal lists a trade still open from before the range ---------------------------------------------

def test_a_trade_still_open_from_before_the_range_is_in_the_journal(tmp_path, monkeypatch):
    """Long 2 since yesterday, still held: Today said "No trades in this period." with no "+1 open", though the 7-day
    view listed it and open_since named it. A trade still open is in every range, however long ago it began."""
    lines = [E(T - DAY_MS, 1, 2, 5000, 1), P(T - DAY_MS, 2, 5000)]
    today, week = _journal(tmp_path, monkeypatch, lines, (T, T + DAY_MS), (T - 7 * DAY_MS, T + DAY_MS))
    for r in (today, week):
        assert [(t["side"], t["qty"], t["entry_ts"], t["open"]) for t in r["trades"]] == [("long", 2, T - DAY_MS, True)]
        assert r["stats"]["open"] == 1 and r["stats"]["trades"] == 0 and r["open_since"] == T - DAY_MS


# ---- r21-c31: the importer's --help says what a NinjaTrader tick export's footprint shows -----------------------------

def test_the_importers_help_says_a_tick_export_without_sides_takes_the_tick_rule():
    """It said such files' footprints show volume only; they show a bid x ask split by the tick rule."""
    from chartsrv import databento
    doc = " ".join(databento.__doc__.split())
    assert "volume only" not in doc and "footprints split it by the tick rule" in doc


# ---- r21-c32: the Plan tab's Level Lines range takes 32nds on a Treasury chart -------------------------------------

PLAN_LL_JS = """
const ids = {}, document = { activeElement: null };
const $ = (q) => (ids[q] ||= { dataset: {}, value: '', textContent: '', inputMode: '' });
const BOND_TICK = { ZN: 1 / 64 }, bondTick = (r) => BOND_TICK[r] || 0;
const S = { symbol: 'ZN', llSet: {}, st: { lines: true },
  instruments: { ZN: { symbol: 'ZN', tick_size: 1 / 64, decimals: 6 }, ES: { symbol: 'ES', tick_size: 0.25, decimals: 2 } } };
const inst = () => S.instruments[S.symbol], puts = [], toasts = [];
const indReady = () => true, levelLinesOf = () => [], toast = (m) => toasts.push(m), fail = (e) => toasts.push(e.message);
const setStudy = () => {}, indPutSymbol = async (key, v) => { puts.push([v.RangeTop, v.RangeBottom]); Object.assign(S.llSet, v); };
const llShared = () => S.symbol, renderLlLeft = () => {}, indSym = (key, sym = S.symbol) => sym;
/*APP*/
/*STUDIES*/
/*PLAN*/
(async () => {
  const save = async (top, bot) => {
    $('#pl-ll-top').value = top; $('#pl-ll-bot').value = bot; puts.length = 0; toasts.length = 0;
    await $('#pl-ll-save').onclick();
    return [puts[0] ?? null, toasts[0], $('#pl-ll-top').value, $('#pl-ll-bot').value, $('#pl-ll-top').inputMode];
  };
  const out = { zn: await save("110'165", '109-08'), znPlain: await save('110.515625', ''), znOff: await save('110.51171875', ''),
    znJunk: await save("110'1x", '') };
  S.symbol = 'ES';
  out.es = await save('6850.25', '6800');
  out.esJunk = await save("6850'16", '');
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_the_plan_tabs_level_lines_range_takes_32nds_on_a_treasury_chart():
    """The range boxes were number boxes read with Number(): on ZN, 110'165 was stored as 110165 (the browser drops
    the apostrophe) and 110-165 cleared the range, while the levels box beside them takes 32nds. They are text boxes
    now, read as the levels box reads a price, and a saved range on the tick shows in 32nds there."""
    html = (WEB / "index.html").read_text()
    for box in ("pl-ll-top", "pl-ll-bot"):
        tag = html[html.rindex("<input", 0, html.index(f'id="{box}"')):]
        assert 'type="number"' not in tag[:tag.index(">")]
    plan = app_lines("const LL_BOXES", "};")
    js = PLAN_LL_JS.replace("/*PLAN*/", plan).replace("/*STUDIES*/", studies_part("parse32") + studies_part("bond32"))
    out = run_app(["markDirty", "fillBox", "px"], js)
    refused = "Range top and bottom: a price, or empty for none"
    assert out["zn"] == [[110.515625, 109.25], "Level Lines saved for ZN", "110'165", "109'080", "text"]
    assert out["znPlain"] == [[110.515625, 0], "Level Lines saved for ZN", "110'165", "", "text"]
    assert out["znOff"] == [[110.51171875, 0], "Level Lines saved for ZN", "110.51171875", "", "text"]   # off the tick: as typed
    assert out["znJunk"] == [None, refused, "110'1x", "", "text"]
    assert out["es"] == [[6850.25, 6800], "Level Lines saved for ES", "6850.25", "6800", "decimal"]
    assert out["esJunk"] == [None, refused, "6850'16", "", "decimal"]


# ---- r21-c32 review: the Paste levels dialog's range boxes take 32nds on a Treasury chart too ------------------------

PASTE_RANGE_JS = r"""
const add = async (F, levels, top, bottom) => {   // the dialog open, typed in, Add pressed
  const box = F.ui.paste, n = F.toasts.length;
  box.hidden = false;
  box.querySelector('textarea').value = levels;
  box.querySelector('[data-k=top]').value = top; box.querySelector('[data-k=bottom]').value = bottom;
  box.querySelector('[data-k=replace]').checked = true;   // each Add replaces the last pasted set
  await box.querySelector('[data-act=add]').onclick(); await ticks();
  return F.toasts.slice(n);
};
const shown = (sym) => [...table(sym).values()].map((d) => (d.type === 'rect' ? ['rect', d.pts[0].p, d.pts[1].p] : [d.type, d.pts[0].p]));
const open = async (F) => { const b = el(); b.dataset.act = 'paste'; await F.ui.bar.fire('click', { target: b }); };
const zn = frame('ZN', { bars: minutes(50, 110.5), tick: 1 / 64 }), out = {};
await zn.open(); await open(zn);
out.znMode = ['top', 'bottom'].map((k) => zn.ui.paste.querySelector(`[data-k=${k}]`).inputMode);
for (const [name, top, bottom] of [['apos', "110'165", "109'08"], ['dash', '110-165', '109-08'], ['half', "110'16.5", "109'08.0"],
  ['dec', '110.515625', '109.25']]) {
  out[name] = [await add(zn, "110'160|POC", top, bottom), shown('ZN')];
}
out.znBad = [await add(zn, "110'00|VAL", "110'40", "109'08"), shown('ZN'), zn.ui.paste.hidden];   // a 32nd above 31: refused, the last set stays
out.znWords = [await add(zn, "110'00|VAL", "110'16x", ''), shown('ZN')];
const es = frame('ES', { bars: minutes(50, 6820) });
await es.open(); await open(es);
out.esMode = ['top', 'bottom'].map((k) => es.ui.paste.querySelector(`[data-k=${k}]`).inputMode);
out.es = [await add(es, '6830|POC', '6850.25', '6,800'), shown('ES')];
out.esBad = [await add(es, '6830|POC', "6850'16", '6800'), shown('ES')];
out.esNone = [await add(es, '6831|POC', ' ', ''), shown('ES')];   // empty: no range, the levels alone
console.log(JSON.stringify(out));"""


@needs_node
def test_paste_levels_range_boxes_take_32nds_on_a_treasury_chart_and_refuse_what_is_no_price():
    """The Paste levels range boxes were number boxes read with Number(): on ZN, 110'165 was drawn at 110165 (the
    browser drops the apostrophe) and 110-165 drew no range, with no word, while the levels box beside them takes
    32nds. They are text boxes now (a keypad with the apostrophe on a Treasury), read as a level is, and a box that
    holds no price is refused with the dialog left open."""
    from test_hunt15_drawings import run
    html = (WEB / "index.html").read_text()
    dialog = html[html.index('id="draw-paste"'):html.index("</div>", html.index('data-act="add"'))]
    for k in ("top", "bottom"):
        tag = dialog[dialog.rindex("<input", 0, dialog.index(f'data-k="{k}"')):]
        assert 'type="number"' not in tag[:tag.index(">")]
    out = run(PASTE_RANGE_JS)
    refused = ["Range top and bottom: a price, or empty for none"]
    assert out["znMode"] == ["text", "text"] and out["esMode"] == ["decimal", "decimal"]
    for name in ("apos", "dash", "half", "dec"):
        assert out[name] == [["Added 1 level"], [["hline", 110.5], ["rect", 110.515625, 109.25]]], name
    assert out["znBad"] == [refused, [["hline", 110.5], ["rect", 110.515625, 109.25]], False]
    assert out["znWords"] == [refused, [["hline", 110.5], ["rect", 110.515625, 109.25]]]
    assert out["es"] == [["Added 1 level"], [["hline", 6830], ["rect", 6850.25, 6800]]]
    assert out["esBad"] == [refused, [["hline", 6830], ["rect", 6850.25, 6800]]]
    assert out["esNone"] == [["Added 1 level"], [["hline", 6831]]]
