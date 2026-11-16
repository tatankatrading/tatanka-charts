"""Round 21's bug hunt, price alerts on stocks, series and formulas (free_alerts.py): what its reviews found, each fixed."""
import asyncio
import shutil
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_app import client  # noqa: F401  (the app on a database of the test's own)
from test_hunt5_quotekeys import LISTED
from test_review_fixes import FakeHttp, FakeNotifier, build_market

from chartsrv import backup, setup
from chartsrv import market as market_mod
from chartsrv import watchlists as wl
from chartsrv.alerts import AlertEngine
from chartsrv.config import Instrument, load_instruments, load_settings
from chartsrv.feeds.ninjatrader import ADDON_VERSION, NinjaTraderFeed
from chartsrv.free_alerts import FREE_LAST, free_symbol
from chartsrv.market import Market
from chartsrv.models import Tick
from chartsrv.store import Store

DAY = 86_400_000


def _free(m, symbol, price, direction="cross", repeat=False, **params):
    aid = m.store.add_alert(symbol, "price", {"price": price, "direction": direction, "free": True, **params}, repeat, "")
    m.alerts_changed()
    return aid


def _fired(m):
    fired = []
    m.alerts.on_fire = fired.append
    return fired


def _msgs(fired):
    return [e["message"] for e in fired]


# ---- c01/c20: a stock's alert and a live future with its ticker for a root never share the alert engine's symbol -----

def test_a_stocks_alert_is_paused_when_a_future_with_its_ticker_goes_live_and_never_fires_on_the_futures_trades(tmp_path, monkeypatch):
    """MetLife's MET crossing 85, set while MET was no chart here; then Micro Ether (MET1!) was opened, live at once. Its
    first trade (3000) crossed the stock's 85 from MetLife's last quote, and the alert went on running on the future's
    prices for good, while Yahoo's MetLife quotes were filed as Micro Ether's. Now the alert is paused (MET is the
    future's here now, as chart_symbol refuses one so), with a notice, and the stock is quoted no more."""
    asyncio.run(_met(tmp_path, monkeypatch))


async def _met(tmp_path, monkeypatch):
    m, notifier = build_market(tmp_path, monkeypatch, feed="ninjatrader")
    m.symbols._put(LISTED + [("MET", "MetLife", "stock", "NYSE", "", 0), ("SPY", "SPDR S&P 500", "etf", "NYSE Arca", "", 0)], "nasdaq")
    fired = _fired(m)
    assert free_symbol("MET", m.instruments, m.symbols.only_futures) == "MET"
    aid = _free(m, "MET", 85.0)
    fid = _free(m, "MET/SPY", 0.2)   # a formula with MetLife in it
    m.on_quote("MET", 80.0, 79.0, source="yahoo")
    m.on_quote("SPY", 600.0, 599.0, source="yahoo")
    m.on_quote("MET", 80.5, 79.0, source="yahoo")
    await m.add_future("MET")
    await asyncio.sleep(0)
    assert not m.alerts.alerts[aid].enabled and not m.alerts.alerts[fid].enabled
    assert [t for t, _ in notifier.sent] == ["Alert paused", "Alert paused"]
    assert "MET is the Micro Ether future's chart here now" in notifier.sent[0][1]
    assert "MET" not in m.free_alerts.keys and "MET" not in m.watch_keys   # the stock feeds stop quoting MetLife
    now = market_mod.now_ms()
    m.on_tick(Tick("MET", now, 3000.0, 1, 1))
    m.on_tick(Tick("MET", now + 1, 3001.0, 1, 1))
    assert fired == []
    s = load_settings()   # a restart: still paused
    m2 = Market(s, m.instruments, wl.load(m.store, s.config_dir), m.store, FakeNotifier(), FakeHttp())
    assert not m2.alerts.alerts[aid].enabled and m2.free_alerts.keys == {}


def test_a_database_with_a_stocks_alert_on_a_live_futures_root_has_it_paused_at_the_start(tmp_path, monkeypatch, in_session):
    """Instruments saved with SIL (Micro Silver) while a Silver Miners ETF alert on SIL was on, then a restart: index()
    keyed the alert as SIL1! and the ETF's alert ran on the future's trades for good. r21m-03/09: paused so, it got no
    notice (only a future added while the server runs sent one), though ALERTS.md says it does: the user's alert just
    stopped. It now gets the same notice once the server runs (its start), and only then: the next start finds it off."""
    m, _ = build_market(tmp_path, monkeypatch)
    aid = m.store.add_alert("SIL", "price", {"price": 55.0, "direction": "cross", "free": True}, True, "")
    s = load_settings()
    inst = {**load_instruments(s.config_dir), "SIL": Instrument("SIL", "Micro Silver", 0.005, 1000)}
    notifier = FakeNotifier()
    m2 = Market(s, inst, wl.load(m.store, s.config_dir), m.store, notifier, FakeHttp())
    fired = _fired(m2)
    assert not m2.alerts.alerts[aid].enabled and "SIL1!" not in m2.free_alerts.keys
    now = market_mod.now_ms()
    for i, px in enumerate((54.80, 54.90, 55.05)):
        m2.on_tick(Tick("SIL", now + i, px, 1, 1))
    assert fired == []

    async def start(m):
        await m.start([])
        await asyncio.sleep(0.05)
        await m.stop()
    paused = lambda n: [x for x in n.sent if x[0] == "Alert paused"]   # noqa: E731  (not the start's others: a disk low)
    assert paused(notifier) == []   # (no event loop yet)
    asyncio.run(start(m2))
    assert paused(notifier) == [("Alert paused", "Your alert on SIL is paused: SIL is the Micro Silver future's chart here "
                                                 "now, not the stock it was set on.")]
    again = FakeNotifier()
    asyncio.run(start(Market(s, inst, wl.load(m.store, s.config_dir), m.store, again, FakeHttp())))
    assert paused(again) == [] and not m.store.list_alerts()[0]["enabled"]


def test_the_engine_keeps_a_free_alert_apart_from_a_live_futures_trades_of_the_same_symbol(tmp_path):
    store = Store(tmp_path / "t.db")
    fired = []
    eng = AlertEngine(store, {"MET": Instrument("MET", "Micro Ether", 0.5, 0.1)}, None, fired.append)
    store.add_alert("MET", "price", {"price": 85.0, "direction": "cross", "free": True}, True, "")
    store.add_alert("MET", "price", {"price": 3005.0, "direction": "cross"}, True, "")
    eng.reload()
    eng.on_tick(Tick("MET", 1_000, 80.0, 0, 0), free=True)   # MetLife
    eng.on_tick(Tick("MET", 2_000, 3000.0, 1, 1))             # Micro Ether
    eng.on_tick(Tick("MET", 3_000, 80.5, 0, 0), free=True)
    assert fired == []
    eng.on_tick(Tick("MET", 4_000, 3010.0, 1, 1))
    eng.on_tick(Tick("MET", 5_000, 86.0, 0, 0), free=True)
    assert [e["message"] for e in fired] == ["MET crossed above 3005.0 (last 3010.0)", "MET crossed above 85.00 (last 86.00)"]


def test_restarting_a_paused_stock_alert_whose_ticker_is_a_live_future_is_refused(client):  # noqa: F811
    from chartsrv.app import M
    m = M()

    def paused():   # on the app's thread (the database's)
        aid = m.store.add_alert("ES", "price", {"price": 50.0, "direction": "cross", "free": True}, False, "")
        m.store.update_alert(aid, enabled=False)
        m.alerts_changed()
        return aid
    aid = client.portal.call(paused)
    r = client.patch(f"/api/alerts/{aid}", json={"enabled": True})
    assert r.status_code == 400 and "ES is a live future here now" in r.text
    assert client.patch(f"/api/alerts/{aid}", json={"price": 51.0}).status_code == 400
    assert not m.alerts.alerts[aid].enabled


# ---- c03/c22: a quote-only future's roll (the add-on's C|ROOT|contract|quote) crosses no price alert -------------------

def test_a_quote_only_futures_roll_crosses_no_alert_on_it_nor_a_formulas_with_it(tmp_path, monkeypatch):
    """ZN on a list, not charted: NinjaTrader quotes it. At the roll the add-on says so (C|ZN|ZN 03-27|quote) and sends
    the old contract's last price again until the new one's comes. The jump fired 'ZN1! crossed above 111.00'."""
    m, _ = build_market(tmp_path, monkeypatch)
    assert "ZN" not in m.instruments and "ZB" not in m.instruments
    fired = _fired(m)
    _free(m, "ZN1!", 111.0)
    _free(m, "ZN1!/ZB1!", 0.95)
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    for line in ("Q|ZB|116.5|116", "Q|ZN|110.5|110.4", "Q|ZN|110.515625|110.4", "C|ZN|ZN 03-27|quote",
                 "Q|ZN|110.515625|110.4",   # the old contract's price, sent again
                 "Q|ZB|116.53125|116",      # another part moves meanwhile: no value worked out from the old contract's
                 "Q|ZN|111.25|110.4", "Q|ZN|111.265625|110.4"):
        feed._line(m, line.encode())
    assert fired == [] and feed.stats["bad_lines"] == 0
    feed._line(m, b"Q|ZN|110.9|110.4")   # a real cross on the new contract
    assert _msgs(fired) == ["ZN1! crossed below 111.00 (last 110.90)"]


# ---- c04/c21: a series' alert is checked when its value arrives, not at its observation's date ------------------------

def test_a_series_alert_fires_and_expires_on_the_clock_not_on_its_observations_date(tmp_path, monkeypatch):
    """FRED's DGS10 for Friday comes on Monday, stamped Friday 00:00 CT: an alert that expired Friday evening fired on
    Monday all the same, and every alert's event and last fired time showed the observation's date."""
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    clock = {"t": 1_794_600_000_000}   # a Friday afternoon
    monkeypatch.setattr(market_mod, "now_ms", lambda: clock["t"])
    expired = _free(m, "FRED:DGS10", 4.5, expires=clock["t"] + 3_600_000)
    _free(m, "FRED:DGS2", 3.5)
    obs = clock["t"] - 2 * DAY
    m.on_quote("FRED:DGS10", 4.40, 4.38, source="fred", at=obs)
    m.on_quote("FRED:DGS2", 3.40, 3.38, source="fred", at=obs)
    clock["t"] += 3 * DAY   # Monday: Friday's values come
    m.on_quote("FRED:DGS10", 4.60, 4.40, source="fred", at=obs + DAY)
    m.on_quote("FRED:DGS2", 3.60, 3.40, source="fred", at=obs + DAY)
    assert _msgs(fired) == ["FRED:DGS2 crossed above 3.50 (last 3.60)"]
    assert fired[0]["ts"] == clock["t"] and m.alerts.alerts[fired[0]["alert_id"]].last_fired == clock["t"]
    assert not m.alerts.alerts[expired].enabled


def test_an_expired_alert_switches_itself_off_with_no_trade_or_value_to_check_it_on(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    now = market_mod.now_ms()
    aid = _free(m, "FRED:UNRATE", 4.5, expires=now + 60_000)
    fut = m.store.add_alert("ES", "price", {"price": 9000.0, "direction": "cross", "expires": now + 60_000}, False, "")
    m.alerts_changed()
    sent = []
    m.broadcast = sent.append
    m.alerts.expire(now + 30_000)
    assert m.alerts.alerts[aid].enabled and sent == []
    m.alerts.expire(now + 60_000)
    assert not m.alerts.alerts[aid].enabled and not m.alerts.alerts[fut].enabled
    assert {a["id"]: a["enabled"] for a in m.store.list_alerts()} == {aid: False, fut: False}
    assert [x["type"] for x in sent] == ["alerts"]


# ---- c05/c23: Greater/Less than true already fires; a cross during a restart fires ------------------------------------

def test_greater_than_already_true_fires_on_the_first_value_and_on_the_value_shown_when_set(tmp_path, monkeypatch):
    """UNRATE at 4.2 and an alert 'greater than 4.0': FRED re-sends 4.2 every 15 minutes and nothing fired until the
    next month's release. Now it fires on its first value, and one set on a value already quoted fires at once."""
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    _free(m, "FRED:UNRATE", 4.0, "gt")
    m.on_quote("FRED:UNRATE", 4.2, 4.1, source="fred", at=market_mod.now_ms() - 40 * DAY)
    assert _msgs(fired) == ["FRED:UNRATE above 4.00 (last 4.20)"]
    _free(m, "AAPL", 300.0)   # primed by a cross alert
    m.on_quote("AAPL", 230.0, 229.0, source="yahoo")
    m.on_quote("AAPL", 230.5, 229.0, source="yahoo")
    _free(m, "AAPL", 200.0, "gt")   # set on the value shown now
    _free(m, "AAPL", 230.25, "cross")   # a cross: the value shown is only where it looks from
    assert _msgs(fired)[1:] == ["AAPL above 200.00 (last 230.50)"]
    m.on_quote("AAPL", 230.5, 229.0, source="yahoo")
    assert len(fired) == 2


def test_a_cross_while_the_server_was_restarting_fires_on_the_first_value_after(tmp_path, monkeypatch):
    """As a future's alert does from its last stored trade: ZTS 230.25 before the restart, 231 after, over 230.5."""
    m, _ = build_market(tmp_path, monkeypatch)
    _free(m, "ZTS", 230.5)
    _free(m, "ES1!/NQ1!", 0.36)
    m.on_quote("ZTS", 230.0, 229.0, source="yahoo")
    m.on_quote("ZTS", 230.25, 229.0, source="yahoo")
    now = market_mod.now_ms()
    m.on_tick(Tick("NQ", now, 20000.0, 1, 1))
    m.on_tick(Tick("ES", now + 1, 7100.0, 1, 1))   # 0.355
    asyncio.run(m.stop())
    m2, _ = build_market(tmp_path, monkeypatch)
    m2.seed()
    fired = _fired(m2)
    m2.on_quote("ZTS", 231.0, 229.0, source="yahoo")
    m2.on_tick(Tick("ES", now + 2, 7300.0, 1, 1))   # 0.365
    assert _msgs(fired) == ["ZTS crossed above 230.50 (last 231.00)", "ES1!/NQ1! crossed above 0.36 (last 0.365)"]


# ---- c18: an alert paused, edited or made again starts afresh: no cross from while it was off -------------------------

def test_an_alert_back_on_never_fires_on_a_cross_from_while_it_was_off(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    aid = _free(m, "ZTS", 250.0)
    m.on_quote("ZTS", 240.0, 239.0, source="yahoo")
    m.on_quote("ZTS", 240.5, 239.0, source="yahoo")
    m.store.update_alert(aid, enabled=False)
    m.alerts_changed()
    m.on_quote("ZTS", 255.0, 239.0, source="yahoo")   # quoted no more, or on a list: either way unchecked
    m.store.update_alert(aid, enabled=True)
    m.alerts_changed()
    m.on_quote("ZTS", 260.25, 239.0, source="yahoo")
    assert fired == []
    m.on_quote("ZTS", 249.0, 239.0, source="yahoo")
    assert _msgs(fired) == ["ZTS crossed below 250.00 (last 249.00)"]
    m.on_quote("ZTS", 221.0, 239.0, source="yahoo")
    m.store.delete_alert(aid)
    m.alerts_changed()
    _free(m, "ZTS", 235.0)   # made again days later, ZTS at 240 by then
    m.on_quote("ZTS", 240.0, 239.0, source="yahoo")
    assert len(fired) == 1


def test_a_formula_alert_back_on_never_fires_on_a_cross_from_while_it_was_off(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    aid = _free(m, "ES1!*1", 6905.0)
    now = market_mod.now_ms()
    m.on_tick(Tick("ES", now, 6900.0, 1, 1))
    m.on_tick(Tick("ES", now + 1, 6900.25, 1, 1))
    m.store.update_alert(aid, enabled=False)
    m.alerts_changed()
    m.on_tick(Tick("ES", now + 2, 6902.5, 1, 1))
    m.store.update_alert(aid, enabled=True, params={"price": 6901.25, "direction": "cross", "free": True})
    m.alerts_changed()
    m.on_tick(Tick("ES", now + 3, 6902.75, 1, 1))
    assert fired == []


# ---- c19: a live part's roll: no value from the old contract's last trade until the new one trades ----------------------

def test_a_formula_with_a_rolled_future_waits_for_its_new_contracts_first_trade(tmp_path, monkeypatch, in_session):
    """ES1!/NQ1! at 0.344 against 0.3445; ES rolls; an NQ trade (or ES's own quote) came before the new ES contract's
    first trade, worked the formula out from the old contract's last trade, and that trade's jump fired it. (r21m-10: its
    trades and rolls on a clock in a session, in_session: run 16:00-16:29 CT, or Friday 16:00-17:00, the new contract's
    trades snapped back before its roll, which the server leaves out, and the expected cross never came.)"""
    asyncio.run(_roll(tmp_path, monkeypatch))


async def _roll(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    _free(m, "ES1!/NQ1!", 0.3445)
    now = market_mod.now_ms()
    m.on_contract("ES", "ES 12-26")
    m.on_contract("NQ", "NQ 12-26")
    m.on_tick(Tick("NQ", now, 20000.0, 1, 1))
    m.on_tick(Tick("ES", now + 1, 6880.0, 1, 1))
    m.on_tick(Tick("ES", now + 2, 6881.0, 1, 1))   # 0.34405
    m.on_contract("ES", "ES 03-27")
    m.on_tick(Tick("NQ", now + 3, 20000.25, 1, 1))
    m.on_quote("ES1!", 6881.0, 6870.0)   # the add-on's Q line: the old contract's price
    m.on_tick(Tick("ES", now + 4, 6921.0, 1, 1))   # the new contract's first trade
    m.on_tick(Tick("ES", now + 5, 6921.25, 1, 1))
    assert fired == []
    m.on_contract("ES", "ES 06-27")   # both roll: whichever trades first waits for the other's new contract
    m.on_contract("NQ", "NQ 03-27")
    m.on_tick(Tick("ES", now + 6, 6960.0, 1, 1))
    m.on_tick(Tick("NQ", now + 7, 20250.0, 1, 1))   # 0.343704
    assert fired == []
    m.on_tick(Tick("ES", now + 8, 6990.0, 1, 1))   # 0.345185: a real cross
    assert _msgs(fired) == ["ES1!/NQ1! crossed above 0.3445 (last 0.345185)"]


# ---- r21m-02: the values the simulator made up for the stock and formula alerts go when it is left -------------------

def _on_sim(tmp_path, monkeypatch):
    """An install on the simulator with an AAPL alert (crossing 250) and a formula one: the values they last checked,
    saved over a restart (FREE_LAST), are the simulator's made-up prices (AAPL 270.81, ES1!/SPY 12.0)."""
    m, _ = build_market(tmp_path, monkeypatch, feed="sim")
    _free(m, "AAPL", 250.0)
    _free(m, "ES1!/SPY", 10.0)
    m.on_quote("AAPL", 270.81, 269.0, source="sim")
    m.on_quote("SPY", 600.0, 599.0, source="sim")
    m.on_tick(Tick("ES", market_mod.now_ms(), 7200.0, 1, 1))
    m._save_free_alerts()
    m.flush()
    assert m.store.get_setting(FREE_LAST) == {"AAPL": 270.81, "ES1!/SPY": 12.0}
    return m


def _restarted(m, s):
    m2 = Market(s, m.instruments, wl.load(m.store, s.config_dir), m.store, FakeNotifier(), FakeHttp())
    m2.seed()
    return m2, _fired(m2)


def test_leaving_the_simulator_drops_the_values_its_alerts_last_checked(tmp_path, monkeypatch, in_session):
    """Leave the simulator deleted its trades but kept the values its stock and formula alerts last checked: at the next
    start AAPL's alert was checked from the made-up 270.81, and the first real quote, 230.50, fired 'crossed below
    250' (a one-shot used up); the formula's likewise. They go with the rest of its market data (emptied: the wipe
    checks the settings' count)."""
    m = _on_sim(tmp_path, monkeypatch)
    m.store.set_setting(setup.LEAVE_SIM, "pending")
    s = setup.leave_sim(m.store, load_settings(), out=lambda *_: None)
    assert s.feed == "ninjatrader" and m.store.get_setting(setup.LEAVE_SIM) == "done"
    assert m.store.get_setting(FREE_LAST) == {}
    m2, fired = _restarted(m, s)
    assert m2.alerts.free_px == {}
    m2.on_quote("AAPL", 230.50, 229.0, source="yahoo")
    m2.on_quote("SPY", 690.0, 689.0, source="yahoo")
    m2.on_tick(Tick("ES", market_mod.now_ms(), 6900.0, 1, 1))   # 10.0
    assert fired == [] and all(a["enabled"] for a in m.store.list_alerts())
    m2.on_quote("AAPL", 251.0, 229.0, source="yahoo")   # a real cross still fires
    assert _msgs(fired) == ["AAPL crossed above 250.00 (last 251.00)"]


def test_a_restore_that_leaves_the_simulator_drops_the_values_its_alerts_last_checked(tmp_path, monkeypatch, in_session):
    """A real install's backup restored onto a PC still on the simulator: the restore keeps this install's values the
    alerts last checked (backup.KEPT), here the simulator's made-up AAPL. Leaving the simulator, it drops them at once,
    and the restored AAPL alert is not checked from them at the next start."""
    real = tmp_path / "real"
    real.mkdir()
    st = Store(real / "chart.db")
    setup.init(st)
    st.add_alert("AAPL", "price", {"price": 250.0, "direction": "cross", "free": True}, False, "")
    st.close()
    bk = backup.make(real / "chart.db", tmp_path / "backups", feed="ninjatrader")
    (tmp_path / "pc").mkdir()
    m = _on_sim(tmp_path / "pc", monkeypatch)
    path = m.store.path
    m.store.close()
    said = []
    backup.restore(path, bk, on_sim=True, say=said.append)
    assert any("leaves the simulator" in x for x in said)
    st = Store(path)
    assert st.get_setting(FREE_LAST) == {} and st.get_setting(setup.LEAVE_SIM) == "pending"
    s = setup.leave_sim(st, load_settings(), out=lambda *_: None)
    m2 = Market(s, m.instruments, wl.load(st, s.config_dir), st, FakeNotifier(), FakeHttp())
    m2.seed()
    fired = _fired(m2)
    m2.on_quote("AAPL", 230.50, 229.0, source="yahoo")
    assert fired == [] and [a["symbol"] for a in st.list_alerts()] == ["AAPL"]


# ---- c25: a formula's alert symbol as its chart has it ----------------------------------------------------------------

def test_a_formula_alerts_symbol_is_written_as_its_chart_writes_it(client):  # noqa: F811
    from chartsrv.app import M
    m = M()
    for typed, want in (("vixm / vixy", "VIXM/VIXY"), ("NASDAQ:AAPL/NASDAQ:MSFT", "AAPL/MSFT"),
                        ("es / nq", "ES1!/NQ1!"), ("( gc1! / si1! ) * 100", "(GC1!/SI1!)*100"),
                        ("FRED:DGS10 - FRED:DGS2", "FRED:DGS10-FRED:DGS2"), ("cme:6e/cme:6b", "6E1!/6B1!"),
                        ("VIXM/VIXY", "VIXM/VIXY")):
        assert free_symbol(typed, m.instruments, m.symbols.only_futures) == want, typed
    r = client.post("/api/alerts", json={"symbol": "vixm / vixy", "kind": "price", "price": 1.5, "direction": "cross"})
    assert r.status_code == 201 and r.json()["symbol"] == "VIXM/VIXY"


# ---- review of c23: a future only quoted keeps no value over a restart (its roll then is never seen) -------------------

def test_a_restart_across_a_quote_only_futures_roll_crosses_no_alert(tmp_path, monkeypatch):
    """The PC off overnight across ZN's roll date (NinjaTrader too): on its start the add-on quotes the new contract,
    and an older add-on never said so. ZN1!'s value from before the restart was the jump's previous value, and 'ZN1!
    crossed above 111.00' fired, alone or in a formula. A future only quoted keeps no value over a restart: its first
    value after is only where its alerts look from (a live future's roll is known: its C line, set against the stored
    contract). A stock's still does (test_a_cross_while_the_server_was_restarting_fires_on_the_first_value_after)."""
    m, _ = build_market(tmp_path, monkeypatch)
    _free(m, "ZN1!", 111.0)
    _free(m, "ZN1!/ZB1!", 0.95)
    _free(m, "ZTS", 230.5)
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    for line in ("Q|ZB|116.5|116", "Q|ZN|110.5|110.4", "Q|ZN|110.515625|110.4"):
        feed._line(m, line.encode())
    m.on_quote("ZTS", 230.25, 229.0, source="yahoo")
    asyncio.run(m.stop())
    assert set(m.store.get_setting("free_alert_last", {})) == {"ZTS"}
    m2, _ = build_market(tmp_path, monkeypatch)
    m2.seed()
    fired = _fired(m2)
    feed2 = NinjaTraderFeed("127.0.0.1", 0, "")
    for line in ("Q|ZB|116.6|116", "Q|ZN|111.25|110.4", "Q|ZN|111.265625|110.4"):   # each on its new contract
        feed2._line(m2, line.encode())
    m2.on_quote("ZTS", 231.0, 229.0, source="yahoo")
    assert _msgs(fired) == ["ZTS crossed above 230.50 (last 231.00)"]
    feed2._line(m2, b"Q|ZN|110.9|110.4")   # a real cross after
    assert _msgs(fired)[1:] == ["ZN1! crossed below 111.00 (last 110.90)"]


def test_the_contract_ninjatrader_quotes_a_listed_future_on_is_set_against_the_one_stored(tmp_path, monkeypatch):
    """Add-on 22 names a watchlist future's contract (C|ZN|ZN 12-26|quote) when it first quotes it and on each connect,
    not only at a roll: NinjaTrader closed at ZN's roll date (the server running) started on the new contract with no
    word, the jump crossed 111, and the change stayed set against the old contract's settlement. The same contract
    again is no roll: a cross while NinjaTrader was away fires. Kept over a restart."""
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    _free(m, "ZN1!", 111.0)
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    for line in ("C|ZN|ZN 12-26|quote", "S|ZN|110.25", "Q|ZN|110.5|110.4", "Q|ZN|110.75|110.4"):
        feed._line(m, line.encode())
    assert m.quote_contracts == {"ZN": "ZN 12-26"} and m.settlements.saved.get("ZN")
    for line in ("C|ZN|ZN 12-26|quote", "Q|ZN|111.25|110.4"):   # a reconnect, the same contract: a real cross
        feed._line(m, line.encode())
    assert _msgs(fired) == ["ZN1! crossed above 111.00 (last 111.25)"] and m.settlements.saved.get("ZN")
    _free(m, "ZN1!", 110.0)
    for line in ("Q|ZN|110.875|110.4", "C|ZN|ZN 03-27|quote", "Q|ZN|109.75|110.4"):   # NinjaTrader's start on the next one
        feed._line(m, line.encode())
    assert len(fired) == 1 and not m.settlements.saved.get("ZN")
    assert m.store.get_setting("quote_contracts", {}) == {"ZN": "ZN 03-27"} and feed.stats["bad_lines"] == 0
    feed._line(m, b"Q|ZN|110.25|110.4")
    assert _msgs(fired)[1:] == ["ZN1! crossed above 110.00 (last 110.25)"]
    s = load_settings()
    m2 = Market(s, m.instruments, wl.load(m.store, s.config_dir), m.store, FakeNotifier(), FakeHttp())
    assert m2.quote_contracts == {"ZN": "ZN 03-27"}


# ---- review of c03/c22: the add-on sends none of the old contract's numbers after a quote's roll ----------------------

HARNESS = Path(__file__).resolve().parent / "addon_csharp"
ROOT = Path(__file__).resolve().parents[2]
ROLL_SCENARIOS = {"roll": "ZT", "reconnect": "TN"}
mono = pytest.mark.skipif(not (shutil.which("mcs") and shutil.which("mono")), reason="needs mono")


@pytest.fixture(scope="module")
def quote_rolls(tmp_path_factory):
    """Per scenario of QuoteHarness.cs: each connection's lines for its root (HELLO first), and its marks. The test's
    server hangs up on the "#|hang up" line, as a restart of the server would."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    conns = []

    def read(conn):
        got = []
        conns.append(got)
        with conn, conn.makefile("r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == "#|hang up":
                    conn.shutdown(socket.SHUT_RDWR)
                    return
                got.append(line.strip())

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=read, args=(conn,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    work = tmp_path_factory.mktemp("quote_rolls")
    src = (ROOT / "ninjatrader" / "TatankaChartFeed.cs").read_text(encoding="utf-8")
    src = src.replace("Port     = 8801;", f"Port     = {port};").replace("QuoteLiveMs = 60000;", "QuoteLiveMs = 2500;")
    (work / "TatankaChartFeed.cs").write_text(src, encoding="utf-8")
    exe = work / "quotes.exe"
    r = subprocess.run(["mcs", "-langversion:Latest", "-r:System.Core.dll", f"-out:{exe}", str(HARNESS / "Stubs.cs"),
                        str(work / "TatankaChartFeed.cs"), str(HARNESS / "QuoteHarness.cs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    def run(name):
        r = subprocess.run(["mono", str(exe), name, ROLL_SCENARIOS[name]], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return {row[2:].split()[0]: int(row.split()[2]) for row in r.stdout.splitlines() if row.startswith("# ")}
    try:
        with ThreadPoolExecutor(len(ROLL_SCENARIOS)) as pool:
            marks = dict(zip(ROLL_SCENARIOS, pool.map(run, ROLL_SCENARIOS)))
    finally:
        srv.close()
    out = {}
    for name, root in ROLL_SCENARIOS.items():
        mine = [c for c in conns if any(ln.split("|")[1:2] == [root] for ln in c)]
        out[name] = ([[c[0]] + [ln for ln in c if ln.split("|")[1:2] == [root]] for c in mine], marks[name])
    return out


@mono
def test_the_addon_sends_none_of_the_old_contracts_numbers_after_a_quotes_roll(quote_rolls):
    """At NinjaTrader's roll date the add-on moves a watchlist future's quote to the next contract and sends C|..|quote.
    Its last price was the old contract's, kept until the new one's first trade, and the old subscription delivered
    the old contract's trades until NinjaTrader removed its handler: the first Q line after the C line carried an old
    price never quoted before, the server took it as the new contract's first, and the new one's first trade crossed
    by the spread. Now its numbers go at the roll and the old contract's data is left out: every Q line after the C line
    is the new contract's (its prior close too). The contract is named when first quoted as well (add-on 22)."""
    (conn,), _ = quote_rolls["roll"]
    assert conn[0] == f"HELLO||{ADDON_VERSION}" and ADDON_VERSION >= 22
    cs = [(i, ln) for i, ln in enumerate(conn) if ln.startswith("C|ZT|")]
    names = list(dict.fromkeys(ln for _, ln in cs))   # first quoted (and the connect's), then the roll
    assert len(names) == 2 and all(n.endswith("|quote") for n in names), names
    roll = next(i for i, ln in cs if ln == names[1])
    before = [ln.split("|") for ln in conn[:roll] if ln.startswith("Q|")]
    after = [ln.split("|") for ln in conn[roll:] if ln.startswith("Q|")]
    assert len(before) >= 2 and all(float(q[2]) < 111 and q[3] == "110.25" for q in before)
    assert len(after) >= 1 and all(float(q[2]) >= 111.5 and q[3] == "111.25" for q in after), after


@mono
def test_the_addon_names_each_quoted_futures_contract_on_connect(quote_rolls):
    """Its roll while the server was off (restarting, or not yet running when NinjaTrader started) is seen: the
    connect's lines name the contract quoted, right after HELLO, before any Q line."""
    conns, _ = quote_rolls["reconnect"]
    assert len(conns) == 2, conns
    first, again = conns
    contract = next(ln for ln in first if ln.startswith("C|TN|"))
    assert contract.endswith("|quote") and again[0].startswith("HELLO|") and again[1] == contract
    assert any(ln.startswith("Q|TN|") for ln in again[2:])


# ---- review of c05: a one-shot Greater than that fired, restarted, is checked at once on the value shown ---------------

def test_a_fired_one_shot_restarted_is_checked_at_once_on_the_value_shown(tmp_path, monkeypatch):
    """UNRATE gt 4.0 fired on 4.2 and switched itself off; restarted, it stayed silent until UNRATE changed (weeks): the
    index still had it as it was, so it was not fresh, and every FRED cycle re-sent 4.2, dropped as unchanged. Now
    whatever the alert engine starts afresh (new, edited, or back on after firing, an expiration or a pause) is."""
    m, _ = build_market(tmp_path, monkeypatch)
    fired = _fired(m)
    aid = _free(m, "FRED:UNRATE", 4.0, "gt")
    cid = _free(m, "AAPL", 230.0)
    m.on_quote("FRED:UNRATE", 4.2, 4.1, source="fred", at=market_mod.now_ms() - 40 * DAY)
    m.on_quote("AAPL", 229.0, 228.0, source="yahoo")
    m.on_quote("AAPL", 231.0, 228.0, source="yahoo")
    assert _msgs(fired) == ["FRED:UNRATE above 4.00 (last 4.20)", "AAPL crossed above 230.00 (last 231.00)"]
    assert not m.alerts.alerts[aid].enabled and not m.alerts.alerts[cid].enabled
    m.on_quote("AAPL", 229.5, 228.0, source="yahoo")   # back below, while it is off
    m.store.update_alert(aid, enabled=True)   # Restart
    m.store.update_alert(cid, enabled=True)
    m.alerts_changed()
    assert _msgs(fired)[2:] == ["FRED:UNRATE above 4.00 (last 4.20)"]   # a cross looks from the value shown now
    _free(m, "MSFT", 500.0)   # another change: nothing new for these
    m.on_quote("FRED:UNRATE", 4.2, 4.1, source="fred", at=market_mod.now_ms() - 40 * DAY)
    m.on_quote("AAPL", 229.5, 228.0, source="yahoo")
    assert len(fired) == 3
    m.on_quote("AAPL", 230.5, 228.0, source="yahoo")
    assert _msgs(fired)[3:] == ["AAPL crossed above 230.00 (last 230.50)"]
