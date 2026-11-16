"""Round 20's bug hunt: what its reviews found, each fixed."""
import time

from test_deep_history import market, open_minutes, store  # noqa: F401  (a Market on a database of the test's own)
from test_hunt10_hold import Writer

from chartsrv.feeds.ninjatrader import NinjaTraderFeed
from chartsrv.gapfill import filling


def _nt(m):
    nt = NinjaTraderFeed("127.0.0.1", 0, "t")
    nt.writer, nt.stats["version"], nt.stats["connected"] = Writer(), 20, True
    m.feed_objs.append(nt)
    return nt


def _owners(m, own):
    m.connections.owners = lambda: own
    m._owners_changed()


# ---- r20-a1: handed back while NinjaTrader's last request was still answered, the stretch is asked at its Z ----------

def test_a_symbol_handed_back_mid_answer_has_its_stretch_asked_at_the_answers_end_and_its_alerts_held_till_it_comes(market):
    """A plugin took ES while NinjaTrader's request for its stretch was out, then handed it back: the stretch (from where
    the stored data stops to NinjaTrader's first live trade) waits for that request's Z. It was worked out again then,
    from what was stored: the live trades since the hand-back, a second ago, so nothing was asked and the alerts let
    go against bars two days old. Now the stretch held is asked, and the alerts wait for its answer."""
    m = market
    gf = m.gapfill
    now = int(time.time() * 1000)
    store(m, "ES", open_minutes(now - 3 * 86_400_000, 300))
    nt = _nt(m)
    assert any(g.startswith("GAP|ES|") for g in gf.requests(now, live=True, minutes=True, contracts=True, older=True))
    _owners(m, {"ES": "lines"})
    assert "ES" in gf.nt_out
    _owners(m, {})
    start = gf.windows["ES"].start
    assert gf.windows["ES"].hold and filling(m, "ES")
    t0 = int(time.time() * 1000)
    for k in range(3):   # NinjaTrader's live trades from the hand-back on
        nt._line(m, f"T|ES|{t0 + k * 1000}|6000.25|1|1\n".encode())
    nt.writer.lines.clear()
    nt._line(m, b"Z|ES|ticks|0|ok\n")   # the request given up on: answered
    gap = [ln for ln in nt.writer.lines if ln.startswith("GAP|ES|") and not ln.endswith("|older")]
    assert gap and gap[0].startswith(f"GAP|ES|{start}|"), nt.writer.lines
    assert int(gap[0].split("|")[3]) <= t0 + 1000   # up to the first live trade (whole seconds)
    assert filling(m, "ES")   # held till it comes
    t = open_minutes(start + 60_000, 1)[0]
    nt._line(m, f"Y|ES|{t}|6000|6001|5999|6000.5|5\n".encode())
    nt._line(m, b"Z|ES|minutes|1|ok\n")
    m.flush_for_read()
    assert m.store.db.execute("SELECT COUNT(*) FROM bars_1m WHERE symbol='ES' AND t=?", (t,)).fetchone()[0] == 1
    assert not filling(m, "ES")


# ---- r20-a2/a4: AMT alert settings stored before the check, and switching alerts off with a chart's stale ones -------

def _app(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir(exist_ok=True)
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_SIM_WARMUP_MIN", "20")
    monkeypatch.delenv("CHART_DISCORD_WEBHOOK", raising=False)
    monkeypatch.delenv("CHART_NTFY_URL", raising=False)
    from fastapi.testclient import TestClient
    from chartsrv.app import app
    return TestClient(app, raise_server_exceptions=False)


def test_an_amt_alert_setting_stored_as_infinity_before_the_check_no_longer_breaks_the_amt(tmp_path, monkeypatch, in_session):
    """Round 19 refused Infinity in a chart's AMT settings; one stored by the version before (alerts switched off with
    it) still made GET /api/amt answer 500 on every page load, through restarts. Now it is cleaned out at the start."""
    from chartsrv.store import Store
    st = Store(tmp_path / "chart.db")
    st.set_setting("amt_alerts", {"ES": {"on": False, "set": {"lvl_thickness": float("inf")}}, "NQ": {"on": True}, "CL": 7})
    st.close()
    with _app(tmp_path, monkeypatch) as c:
        r = c.get("/api/amt")
        assert r.status_code == 200, r.text[:200]
        cfg = Store(tmp_path / "chart.db").get_setting("amt_alerts", {})
        assert cfg["ES"] == {"on": False} and cfg["NQ"] == {"on": True} and "CL" not in cfg


def test_amt_alerts_switch_off_with_settings_that_no_longer_check(tmp_path, monkeypatch, in_session):
    """The page sends the chart's settings when it switches the alerts off: settings that no longer check (stale) made
    it 400, and the alerts stayed on. Off, they are left out; on, still refused."""
    with _app(tmp_path, monkeypatch) as c:
        assert c.put("/api/amt/alerts/ES", json={"on": True}).status_code == 200
        bad = {"lvl_thickness": "wide"}
        assert c.put("/api/amt/alerts/ES", json={"on": True, "set": bad}).status_code == 400
        r = c.put("/api/amt/alerts/ES", json={"on": False, "set": bad})
        assert r.status_code == 200 and r.json() == {"on": False}


# ---- r20-a3: a channel that failed once tries a later alert in full; a 429 is waited out before the next -------------

def test_an_alert_raised_after_a_channels_outage_is_tried_in_full_and_a_429_is_waited_out(monkeypatch):
    """After one message failed, every later one to that channel had one try till one went through: an alert hours
    later lost to a single 502. Only the ones queued behind the failure try once now. And while failing, a 429 went
    back without its wait, so each one queued behind it was refused too."""
    import asyncio

    import httpx

    from chartsrv import notify
    slept, real = [], asyncio.sleep

    async def nosleep(s):   # no wait, but a turn for the others (as a real post's wait gives)
        slept.append(s)
        await real(0)
    monkeypatch.setattr(notify.asyncio, "sleep", nosleep)

    async def go():
        plan = [503] * 6 + [502, 200]
        posts = []

        def handler(req):
            posts.append(1)
            return httpx.Response(plan.pop(0) if plan else 200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            n = notify.Notifier(c, "https://discord.example/webhook", None)
            first = await n.send("t", "outage")
            later = await n.send("t", "hours later, one 502")
        return first, later, len(posts)
    assert asyncio.run(go()) == ("", "discord", 8)

    async def burst():
        answers = [httpx.Response(503)] * 6 + [httpx.Response(429, json={"retry_after": 7})] + [httpx.Response(200)] * 2
        posts = []

        def handler(req):
            posts.append(1)
            return answers.pop(0)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            n = notify.Notifier(c, "https://discord.example/webhook", None)
            out = await asyncio.gather(*(n.send("t", f"m{k}") for k in range(3)))
        return out, len(posts)
    slept.clear()
    out, posts = asyncio.run(burst())
    assert out == ["", "", "discord"] and posts == 8 and 7 in slept


# ---- r20-a5: a 365D chart paged back to the start of the stored data gets its oldest bar ------------------------------

def test_long_calendar_bars_paged_back_to_the_first_stored_day_keep_the_oldest_bar(tmp_path):
    """365D asks for one bar at a time (the calendar window): the oldest, opening before the window, was dropped as
    a part of a bar, though nothing is stored before it to be the rest."""
    from datetime import datetime, timedelta

    from chartsrv import sessions
    from chartsrv.bars import make_series, parse_spec
    from chartsrv.models import Bar
    from chartsrv.older import build_older
    from chartsrv.store import Store, iter_bars_1m
    sessions.set_calendar({"days": []})
    path = tmp_path / "c.db"
    st = Store(path)
    rows, day, k = [], datetime(2017, 1, 3).date(), 0
    while day <= datetime(2026, 11, 6).date():
        if day.weekday() < 5:
            for h in (2, 10, 14):
                t = int(datetime(day.year, day.month, day.day, h, tzinfo=sessions.CT).timestamp() * 1000)
                rows.append(("ES", Bar(t=t, o=3000 + k * .25, h=3001 + k * .25, l=2999 + k * .25, c=3000.5 + k * .25, v=10)))
                k += 1
        day += timedelta(days=1)
    st.write([], rows)
    for spec in ("365D", "1M"):
        sp = parse_spec(spec)
        ref = make_series(sp, 0.25, 2, 10 ** 6, ("08:30", "15:00"), group="equity")
        ref.bulk = True
        for b in iter_bars_1m(path, "ES", 0, 1 << 62):
            ref.on_bar(b)
        ref.end_bulk()
        whole = [(b.t, b.o, b.h, b.l, b.c, b.v) for b in ref.bars]
        got, before, nxt, more = [], whole[-1][0], None, True
        for _ in range(100):
            if not more:
                break
            q = nxt if nxt is not None else before
            r = build_older(path, "ES", sp, 0.25, 2, ("08:30", "15:00"), False, "equity", q, 200, 10 ** 12)
            bars = [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in r["bars"] if b["t"] < before]
            nxt = None if bars else r["next"]
            more = r["more"] and (bool(bars) or (nxt is not None and nxt < q))
            if bars:
                got, before = bars + got, bars[0][0]
        assert got == whole[:-1], spec


# ---- r20-a6: a restore keeps the higher of the backup's and this database's highest alert id -------------------------

def test_a_restore_onto_a_fresh_install_keeps_the_backups_highest_alert_id(tmp_path):
    """alert_id_high was kept as this database's, set or not: restored onto a fresh install (none), the backup's went,
    and ids of alerts deleted before the backup could be given again."""
    from chartsrv import backup
    from chartsrv.store import Store
    old = Store(tmp_path / "old.db")
    for k in range(5):
        old.add_alert("ES", "price", {"price": 7000.0 + k, "direction": "cross"}, False, "")
    for a in old.list_alerts()[:4]:
        old.delete_alert(a["id"])   # 1-4 deleted: the backup's highest is 5, its alerts table holds 5 only
    old.close()
    bk = backup.make(tmp_path / "old.db", tmp_path / "bk")
    fresh = Store(tmp_path / "fresh.db")
    fresh.close()
    backup.restore(tmp_path / "fresh.db", bk)
    st = Store(tmp_path / "fresh.db")
    st.delete_alert(5)
    assert st.add_alert("ES", "price", {"price": 7100.0, "direction": "cross"}, False, "") == 6
    st.close()
    here = Store(tmp_path / "here.db")   # and onto one that gave more ids since: its own stay
    for k in range(9):
        here.add_alert("NQ", "price", {"price": 20000.0 + k, "direction": "cross"}, False, "")
    here.close()
    backup.restore(tmp_path / "here.db", bk)
    st = Store(tmp_path / "here.db")
    assert st.add_alert("ES", "price", {"price": 7100.0, "direction": "cross"}, False, "") == 10


# ---- r20-b1/b2/b3: quotes: an older fill-in never replaces a newer price; a re-sent old trade holds nothing; the
# marker ages; a future's quote carries its tick's decimals --------------------------------------------------------------

def test_a_fill_in_sources_older_quote_never_replaces_a_newer_price_shown(tmp_path, monkeypatch):
    """NinjaTrader's quotes stop a minute after the close; 2 minutes on, Yahoo's ZC=F (10 minutes behind) took the
    symbol and put back a price from before the close, every session."""
    from test_review_fixes import build_market

    from chartsrv import market as mk
    m, _ = build_market(tmp_path, monkeypatch)
    m.set_watchlists({"lists": [{"name": "a", "items": ["ZC1!"]}]})
    clock = [int(time.time() * 1000)]
    monkeypatch.setattr(mk, "now_ms", lambda: clock[0])
    m.on_quote("ZC1!", 450.25, 448.0)
    clock[0] += mk.QUOTE_HOLD_MS + 1000
    m.on_quote("ZC1!", 447.50, 448.0, None, source="yahoo", at=clock[0] - 10 * 60_000)
    assert m.quotes["ZC1!"]["last"] == 450.25 and m.quotes["ZC1!"]["src"] == "ninjatrader"
    m.on_quote("ZC1!", 451.0, 448.0, None, source="yahoo", at=clock[0] - 5_000)   # newer than it: shown
    assert m.quotes["ZC1!"]["last"] == 451.0


def test_a_feed_re_sending_an_old_trade_lets_a_fresher_source_in_and_never_puts_it_back(tmp_path, monkeypatch):
    """Alpaca re-sends a thin stock's last IEX trade (25 minutes old) every 5 s: it held the symbol, Yahoo's fresh
    quote never showed, and the row said live with a price 28 minutes old."""
    from test_review_fixes import build_market

    from chartsrv import market as mk
    m, _ = build_market(tmp_path, monkeypatch)
    m.set_watchlists({"lists": [{"name": "a", "items": ["SMID"]}]})
    t0 = int(time.time() * 1000)
    clock = [t0]
    monkeypatch.setattr(mk, "now_ms", lambda: clock[0])
    for _ in range(40):
        m.on_quote("SMID", 20.00, 19.50, None, source="alpaca", at=t0 - 25 * 60_000)
        m.on_quote("SMID", 20.40, 19.50, 1e6, source="yahoo", at=clock[0] - 20_000)
        clock[0] += 5_000
    assert m.quotes["SMID"]["src"] == "yahoo" and m.quotes["SMID"]["last"] == 20.40


def test_a_quotes_marker_turns_closed_once_its_feed_has_been_quiet_an_hour(tmp_path, monkeypatch):
    from test_review_fixes import build_market

    from chartsrv import market as mk
    m, _ = build_market(tmp_path, monkeypatch)
    m.set_watchlists({"lists": [{"name": "a", "items": ["AAPL", "SPY"]}]})
    clock = [int(time.time() * 1000)]
    monkeypatch.setattr(mk, "now_ms", lambda: clock[0])
    m.on_quote("AAPL", 250.0, 248.0, 10, source="ibkr")   # IBKR's carry no time of their own
    m.on_quote("SPY", 600.0, 598.0, 10, source="yahoo")   # (Yahoo's without one: delayed, and stays so)
    assert m.quotes["AAPL"]["rt"] == "live" and m.quotes["SPY"]["rt"] == "delayed"
    clock[0] += 30 * 60_000
    m.remark_quotes()
    assert m.quotes["AAPL"]["rt"] == "live" and m.quotes["SPY"]["rt"] == "delayed"
    clock[0] += 31 * 60_000
    m.quotes_dirty.clear()
    m.remark_quotes()
    assert m.quotes["AAPL"]["rt"] == "closed" and m.quotes["SPY"]["rt"] == "closed" and m.quotes_dirty == {"AAPL", "SPY"}
    assert m.hello()["quotes"][0]["rt"] == "closed"


def test_a_futures_quote_carries_its_ticks_decimals(tmp_path, monkeypatch):
    from test_review_fixes import build_market
    m, _ = build_market(tmp_path, monkeypatch)
    m.set_watchlists({"lists": [{"name": "a", "items": ["RB1!", "6S1!", "SPY"]}]})
    m.on_quote("RB1!", 2.1534, 2.1602, source="yahoo")
    m.on_quote("6S1!", 1.25065, 1.2501, source="yahoo")
    m.on_quote("SPY", 600.0, 598.0, source="yahoo")
    assert m.quotes["RB1!"]["dec"] == 4 and m.quotes["6S1!"]["dec"] == 5 and "dec" not in m.quotes["SPY"]


# ---- r20-b4/b5/b8: settlements: every CBOT/NYMEX market searchable has a window; grains' counts from their 19:00
# open; a settlement heard after a day missed is that day's --------------------------------------------------------------

def _ct(*a):
    from datetime import datetime

    from chartsrv.sessions import CT
    return int(datetime(*a, tzinfo=CT).timestamp() * 1000)


def test_more_markets_have_a_settlement_window_and_grains_count_theirs_from_their_evening_open(tmp_path, monkeypatch):
    from test_review_fixes import build_market
    m, _ = build_market(tmp_path, monkeypatch)
    st = m.settlements
    assert {"KE", "ZO", "ZR", "ZQ", "QG"} <= set(st.markets)
    st.on_feed("ZC", 430.0, now=_ct(2026, 11, 9, 14, 0))    # Monday's
    st.on_feed("ZC", 441.0, now=_ct(2026, 11, 10, 14, 0))   # Tuesday's
    got = {}
    for h, mi in ((16, 0), (17, 30), (18, 59), (19, 1)):
        st.cache.clear()
        got[(h, mi)] = st.reference("ZC", _ct(2026, 11, 10, h, mi))["price"]
    assert got == {(16, 0): 430.0, (17, 30): 430.0, (18, 59): 430.0, (19, 1): 441.0}


def test_a_settlement_heard_soon_after_a_window_with_a_day_missed_is_the_missed_days(tmp_path, monkeypatch):
    """Monday's saved, the PC off Tuesday; NinjaTrader connects Wednesday 14:10 CT (before CME publishes Wednesday's)
    with Tuesday's: it was stamped Wednesday's, and from 14:20 to 17:00 the change was a two-day one."""
    from test_review_fixes import build_market
    m, _ = build_market(tmp_path, monkeypatch)
    st = m.settlements
    st.on_feed("ZN", 112.0, now=_ct(2026, 11, 9, 16, 0))
    st.on_feed("ZN", 112.5, now=_ct(2026, 11, 11, 14, 10))
    st.on_feed("ZN", 113.0, now=_ct(2026, 11, 11, 14, 20))
    st.cache.clear()
    assert st.reference("ZN", _ct(2026, 11, 11, 15, 30))["price"] == 112.5


# ---- r20-b3/b7: the watchlist: a future's tick decimals, small prices' significant digits; a formula typed with
# spaces in the + box is one formula -------------------------------------------------------------------------------------

def test_the_add_box_takes_a_formula_typed_with_spaces_as_one_and_rows_show_enough_decimals():
    import shutil

    import pytest
    if not shutil.which("node"):
        pytest.skip("needs node")
    from test_hunt4_watchlist import node
    out = node("""
const q = (s, last, prev, src, dec) => ({ s, last, prev, chg: last - prev, pct: (last - prev) / prev * 100, ts: 1, rt: 'delayed', src, ...(dec != null ? { dec } : {}) });
const quotes = { AAPL: q('AAPL', 250, 245, 'yahoo'), MSFT: q('MSFT', 500, 490, 'yahoo'), 'RB1!': q('RB1!', 2.1534, 2.1602, 'yahoo', 4),
  SHIBUSD: q('SHIBUSD', 0.00001234, 0.00001201, 'coinbase'), NVDA: q('NVDA', 180, 178, 'yahoo') };
const w = makeWatchlist(WEB, { quotes, env: { api: async (m, p, b) => JSON.parse(JSON.stringify(b)) } });
w.list.setDoc({ lists: [{ id: 'main', name: 'Main', items: [] }], flags: {}, names: {} });
w.root.querySelector('#wl-add-in').value = 'AAPL / MSFT, rb1! shibusd nvda';
w.root.querySelector('#wl-addbox').onsubmit({ preventDefault() {} });
await settle();
out(w.root.querySelector('.wl-body').children.map((r) => [r.querySelector('.tk').textContent, r.querySelector('.last').textContent]));""")
    assert out == [["AAPL/MSFT", "0.5000"], ["RB1!", "2.1534"], ["SHIBUSD", "0.00001234"], ["NVDA", "180.00"]]


# ---- r20-b6: Compare: a free-data symbol the server refuses at this timeframe is said once, not every 30 s ------------

def test_a_compared_symbol_the_server_refuses_is_not_asked_again_every_30_s():
    import shutil

    import pytest
    if not shutil.which("node"):
        pytest.skip("needs node")
    from test_hunt_web_ui import app_js, run_app_js
    out = run_app_js(app_js("async function cmpExt("), """
const toasts = [], timers = [];
Object.assign(ctx, { toasts, timers, S: { spec: '30s', rth: false }, calSpec: () => false, cmpAlign: () => [], cmpLegend() {},
  toast: (m) => toasts.push(m), setTimeout: (f, ms) => { timers.push(ms); return 1; } });
vm.runInContext(/*APP*/ + '\\nthis.cmpExt = cmpExt;', ctx);
const run = async (status) => {
  ctx.api = async () => { const e = new Error(`HTTP ${status}`); e.status = status; throw e; };
  const c = { sym: 'AAPL', bars: [], series: { setData() {} } };
  ctx.cmpLines = [c];
  vm.runInContext('var cmpLines = this.cmpLines, api = this.api;', ctx);
  timers.length = 0;
  await ctx.cmpExt(c);
  return [toasts.length, timers.length];
};
out([await run(400), await run(503)]);""")
    assert out == [[1, 0], [2, 1]]   # refused: said, not asked again; the server failing: asked again in 30 s


def test_symbol_search_takes_a_symbol_written_with_its_exchange(tmp_path):
    """"NASDAQ:AAPL" or "CME_MINI:ES1!" (TradingView's way) found nothing, though Enter charted it."""
    from test_symbols import db
    d = db(tmp_path)
    assert d.query("CME_MINI:ES1!")[0]["symbol"] == "ES1!" and d.query("cme_mini:eurusd")[0]["symbol"] == "EURUSD"
    assert d.query("FRED:DGS10")[0]["symbol"] == "FRED:DGS10"   # a source's prefix stays


# ---- r20-c2: the axis tags go around the chart's live price label (and the countdown under it), never over it ---------

def test_axis_tags_stack_around_the_last_price_label_and_its_countdown():
    import json
    import shutil
    import subprocess

    import pytest
    if not shutil.which("node"):
        pytest.skip("needs node")
    from test_hunt_web_ui import WEB
    js = """
const src = require('fs').readFileSync(%s, 'utf8');
const i = src.indexOf('const axisStack = {'), j = src.indexOf('\\n};', i) + 3;
eval(src.slice(i, j).replace('const axisStack', 'globalThis.axisStack'));
const run = (ys, anchor) => {
  axisStack.owners.clear(); axisStack.anchor = anchor;
  const v = axisStack.views({}, ys.map((y) => ({ coordinate: () => y })));
  axisStack.laid = false; axisStack.layout();
  return v.map((t) => Math.round(axisStack.pos.get(t)));
};
console.log(JSON.stringify([run([300], () => [300, 2]), run([325], () => [300, 2]), run([295, 305], () => [300, 1]),
  run([100], () => [300, 2]), run([300], () => null)]));""" % json.dumps(str(WEB / "studies.js"))
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    # the tag at the price goes above the label (the countdown's slot under it kept); one under the countdown below it;
    # two around a label without a countdown either side of it; one far away and one with no label where they are
    assert out == [[281], [338], [281, 319], [100], [300]]
