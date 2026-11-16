"""Price alerts on a stock, a series or a formula of symbols: none is a live chart here, so each runs on its quotes
(free_alerts.py), a formula from its parts' last quotes and a live future's part from its last trade."""
import asyncio

from test_app import client  # noqa: F401  (the app on a database of the test's own)
from test_review_fixes import build_market

from chartsrv import market as market_mod
from chartsrv.free_alerts import free_fmt
from chartsrv.models import Tick


def test_the_api_takes_price_alerts_on_a_stock_or_a_formula_and_no_others(client):
    r = client.post("/api/alerts", json={"symbol": "aapl", "kind": "price", "price": 230.5, "direction": "cross"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["symbol"] == "AAPL" and a["params"] == {"price": 230.5, "direction": "cross", "free": True}
    r = client.post("/api/alerts", json={"symbol": "es/nq", "kind": "price", "price": 0.35, "direction": "above"})
    assert r.status_code == 201 and r.json()["symbol"] == "ES1!/NQ1!"   # as its chart is written
    r = client.post("/api/alerts", json={"symbol": "VIXM/VIXY", "kind": "price", "price": 0.6, "price2": 0.5,
                                         "direction": "enter"})
    assert r.status_code == 201 and r.json()["params"] == {"price": 0.5, "price2": 0.6, "direction": "enter", "free": True}
    for body in ({"symbol": "AAPL", "kind": "level"}, {"symbol": "AAPL", "kind": "study", "study": "vwap"},
                 {"symbol": "VIXM/", "kind": "price", "price": 1}, {"symbol": "ES1!", "kind": "price", "price": 1}):
        assert client.post("/api/alerts", json=body).status_code == 400, body
    aid = a["id"]   # dragged on its chart: moved
    r = client.patch(f"/api/alerts/{aid}", json={"price": 231.25})
    assert r.status_code == 200 and r.json()["params"]["price"] == 231.25 and r.json()["params"]["free"]


def test_a_stocks_alert_fires_on_its_quotes_and_its_symbol_is_quoted_while_it_is_on(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    fired = []
    m.alerts.on_fire = fired.append
    assert "ZTS" not in m.watch_keys   # on no list
    aid = m.store.add_alert("ZTS", "price", {"price": 230.5, "direction": "cross", "free": True}, False, "")
    m.alerts_changed()
    assert "ZTS" in m.watch_keys
    m.on_quote("ZTS", 230.0, 229.0, source="yahoo")
    m.on_quote("ZTS", 230.25, 229.0, source="yahoo")
    assert fired == []
    m.on_quote("ZTS", 230.75, 229.0, source="yahoo")
    assert [e["message"] for e in fired] == ["ZTS crossed above 230.50 (last 230.75)"]
    assert not m.alerts.alerts[aid].enabled   # once
    m.store.delete_alert(aid)
    m.alerts_changed()
    assert "ZTS" not in m.watch_keys


def test_a_formulas_alert_fires_on_its_parts_quotes_and_a_live_futures_trades(tmp_path, monkeypatch):
    asyncio.run(_formula(tmp_path, monkeypatch))


async def _formula(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    fired = []
    m.alerts.on_fire = fired.append
    m.store.add_alert("VIXM/VIXY", "price", {"price": 0.5, "direction": "cross", "free": True}, True, "")
    m.store.add_alert("ES1!/NQ1!", "price", {"price": 0.35, "direction": "below", "free": True}, False, "")
    m.alerts_changed()
    assert {"VIXM", "VIXY", "ES1!", "NQ1!"} <= m.watch_keys
    m.on_quote("VIXM", 10.0, 10.0, source="yahoo")
    m.on_quote("VIXY", 25.0, 25.0, source="yahoo")   # 0.4: its first value
    m.on_quote("VIXY", 15.0, 25.0, source="yahoo")   # 0.6667
    assert [e["message"] for e in fired] == ["VIXM/VIXY crossed above 0.50 (last 0.666667)"]
    now = market_mod.now_ms()
    m.on_tick(Tick("NQ", now, 20000.0, 1, 1))
    m.on_tick(Tick("ES", now + 1, 7200.0, 1, 1))   # 0.36
    m.on_tick(Tick("ES", now + 2, 6990.0, 1, 1))   # 0.3495
    assert fired[-1]["message"] == "ES1!/NQ1! crossed below 0.35 (last 0.3495)"
    m.store.add_alert("ES1!/NQ1!", "price", {"price": 0.345, "direction": "cross", "free": True}, False, "")
    m.alerts_changed()
    m.on_contract("ES", "ES 12-26")
    m.on_contract("ES", "ES 03-27")   # the roll: the jump crosses nothing
    assert "ES1!/NQ1!" not in m.free_alerts.last
    n = len(fired)
    m.on_tick(Tick("ES", now + 3, 6880.0, 1, 1))   # 0.344, the new contract's first trade: where it looks from
    m.on_tick(Tick("ES", now + 4, 6881.0, 1, 1))
    assert len(fired) == n


def test_a_price_with_no_instruments_decimals_is_written_to_six_significant_digits():
    assert [free_fmt(v) for v in (230.12, 230.0, 0.623412345, 98765.43, -0.5, 0)] == \
        ["230.12", "230.00", "0.623412", "98765.43", "-0.50", "0.00"]
