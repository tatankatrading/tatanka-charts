"""A contract roll (ES 12-26 to ES 03-27): the jump by the roll's spread crosses nothing, for indicator alerts and the
AMT Toolkit's level alerts as for price alerts."""
import asyncio

from test_amt_alerts_per_symbol import _alerting
from test_app import client  # noqa: F401  (the app on a database of the test's own)
from test_review_fixes import build_market

from chartsrv import ind_alerts as ia
from chartsrv import market as market_mod
from chartsrv import sessions
from chartsrv.models import Tick


def test_an_indicator_alert_does_not_fire_on_the_roll(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_WORKERS", "0")

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        fired = []
        m.alerts.on_fire = fired.append
        t0 = market_mod.now_ms() - 30 * 60_000
        for i in range(30 * 6):   # 30 one-minute bars around 7000
            m.on_tick(Tick("ES", t0 + i * 10_000, 7000 + (i % 3) * 0.25, 1, 1))
        m.on_contract("ES", "ES 12-26")
        m.store.add_alert("ES", "ind", ia.clean_params({"ind": "price"}, {"value": 7030.0}, "1m", "bar", "cross"), True, "")
        m.store.add_alert("ES", "price", {"price": 7030.0, "direction": "cross"}, True, "")
        m.alerts_changed()
        for _ in range(500):
            if m.ind_alerts.watches and all(w.ready.is_set() for w in m.ind_alerts.watches.values()):
                break
            await asyncio.sleep(0.01)
        t = market_mod.now_ms()
        for k, px in enumerate((7000.25, 7000.5, 7000.25)):
            m.on_tick(Tick("ES", t + k, px, 1, 1))
            await asyncio.sleep(0.05)
        assert m.ind_alerts.states and fired == []
        m.on_contract("ES", "ES 03-27")
        m.on_tick(Tick("ES", t + 3, 7060.0, 1, 1))   # the new contract, 60 points up
        m.on_tick(Tick("ES", t + 4, 7061.0, 1, 1))
        assert fired == []
        m.on_tick(Tick("ES", t + 5, 7020.0, 1, 1))   # a real cross on the new contract
        m.on_tick(Tick("ES", t + 6, 7040.0, 1, 1))
        return [e["message"] for e in fired]
    assert any("crossed" in x and "7030" in x for x in asyncio.run(go()))


def test_the_amt_toolkits_level_alerts_do_not_fire_on_the_roll(tmp_path, monkeypatch):
    sessions.set_calendar({"days": []})
    monkeypatch.setenv("CHART_WORKERS", "0")

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        now = market_mod.now_ms()
        for i in range(2000):
            m.on_tick(Tick("ES", now - 120_000 + i, 7000 + i % 7 * 0.25, 1, 1))
        m.on_contract("ES", "ES 12-26")
        m.amt.merge_overrides({"LevelAlertMode": "Cross"}, "ES")
        m.amt.set_alerts("ES", True)
        await _alerting(m, "ES")
        m.amt.step()
        sent = []
        m.amt._emit = lambda sym, now, price, text: sent.append(text)
        for _ in range(2):
            m.on_tick(Tick("ES", market_mod.now_ms(), 7000.5, 1, 1))
        assert sent == []
        m.on_contract("ES", "ES 03-27")
        m.on_tick(Tick("ES", market_mod.now_ms(), 7060.0, 1, 1))
        m.amt.step()
        await asyncio.sleep(0)
        return sent
    assert asyncio.run(go()) == []


def test_a_gap_across_the_roll_is_not_alerted_nor_its_fill(monkeypatch):
    """Friday's RTH close on the old contract, a roll on Sunday, Monday's open on the new one: the 50 points are the
    roll's spread. Tuesday's gap (new contract to new contract) goes as usual."""
    import chartsrv.gaps_service as gs
    from chartsrv.gapfill import ROLLED
    from test_hunt5_rthstudies import GapMarket, gap_alerts_sent, ms
    real = GapMarket.__init__

    def rolled(self, split):
        real(self, split)
        self.store.set_setting(ROLLED, {"ES": ms(2026, 10, 18, 17)})
    monkeypatch.setattr(GapMarket, "__init__", rolled)
    body = {"on": True, "new": True, "fill": True, "RthStart": 83000, "RthEnd": 150000, "MinimumGapPoints": 0.0}
    assert gs and gap_alerts_sent(monkeypatch, {**body, "rth": True}) == [
        "Tue 08:31 [ES] GAP DOWN 10.00 pts | Open: 6040.00 | RTH close: 6050.00"]


def test_an_edited_alert_takes_the_old_ones_place_in_one_step_and_ids_are_never_reused(client):
    """An edit that changes what the alert watches: the new one replaces the old in one request (before: a POST, then a
    DELETE by id, which could delete the new one if it got the old id back, or leave both when the DELETE failed)."""
    a = client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 6000, "direction": "cross"}).json()
    client.delete(f"/api/alerts/{a['id']}")   # deleted on another device while this one's edit dialog is open
    b = client.post("/api/alerts", json={"symbol": "ES", "kind": "study", "study": "vwap", "band": 1,
                                          "replaces": a["id"]}).json()
    assert b["id"] != a["id"] and [x["id"] for x in client.get("/api/alerts").json()] == [b["id"]]
    c = client.post("/api/alerts", json={"symbol": "ES", "kind": "study", "study": "vwap", "band": 2,
                                          "replaces": b["id"]}).json()
    assert [x["id"] for x in client.get("/api/alerts").json()] == [c["id"]] and c["id"] > b["id"]
