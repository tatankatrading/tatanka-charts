"""Round 19: what a script (not the page) could send that broke things: an AMT setting of Infinity stored with its
alerts switched off (every AMT update and GET /api/amt failing after, through restarts), AMT rows finer than a tick
(a 90-day load of minutes), D/W/M older history asked for years at once, and numbers too large for the database."""
from test_app import client  # noqa: F401  (the app on a database of the test's own)
from test_older_history import minutes, ms

from chartsrv import amt
from chartsrv.store import Store


def test_amt_alert_settings_are_checked_when_switched_off_too(client):
    raw = {"content-type": "application/json"}   # (Python's JSON reader takes Infinity; a script can send it)
    r = client.put("/api/amt/alerts/ES", content='{"on": false, "set": {"lvl_thickness": Infinity}}', headers=raw)
    assert r.status_code == 200 and r.json() == {"on": False}   # off: the settings that don't check left out (r20-a4)
    assert client.get("/api/amt").status_code == 200
    r = client.put("/api/amt/alerts/ES", content='{"on": true, "set": {"lvl_thickness": Infinity}}', headers=raw)
    assert r.status_code == 400   # on: refused
    r = client.put("/api/amt/settings", content='{"lvl_thickness": Infinity}', headers=raw)
    assert r.status_code == 400


def test_amt_rows_are_never_finer_than_a_tick(client):
    from chartsrv.app import M
    m = M()
    s = m.amt.settings_for("ES", {"amt_vauto": False, "amt_vres": True, "amt_res_val": 0.0001, "tick_manual_val": 0.001})
    assert s.amt_res_val == 0.25 and s.tick_manual_val == 0.25
    assert m.amt.settings_for("ES", {"amt_res_val": 1.0}).amt_res_val == 1.0
    assert amt.DEFAULTS["amt_res_val"] == 0.25


def test_older_history_bounds(client, tmp_path):
    Store(tmp_path / "chart.db").insert_bars_if_missing("ES", minutes(ms(2026, 10, 19, 17), ms(2026, 10, 23, 16)))
    before = ms(2026, 10, 23, 8, 30)
    for spec in ("12M", "52W", "365D", "1D"):   # years of minutes: in parts (the page asks again), never an error
        r = client.get(f"/api/older?symbol=ES&spec={spec}&before={before}&count=5000")
        assert r.status_code == 200, (spec, r.text)
    assert client.get(f"/api/older?symbol=ES&spec=30m&before={2 ** 63}&count=10").status_code == 422
    assert client.get(f"/api/older?symbol=ES&spec=30m&before=-5&count=10").status_code == 422


def test_alert_numbers_too_large_are_refused(client):
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 1e308}).status_code == 422
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 6000,
                                            "replaces": 2 ** 70}).status_code == 422
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 6000,
                                            "expires": 2 ** 70}).status_code == 422
    assert client.delete(f"/api/alerts/{2 ** 70}").status_code == 404


def test_templates_take_known_kinds_and_a_total_size(tmp_path, monkeypatch):
    import pytest

    from chartsrv import templates as tp
    from chartsrv.templates import Templates
    t = Templates(Store(tmp_path / "t.db"))
    with pytest.raises(ValueError):
        t.put("chart\n", "a", {"x": 1})
    monkeypatch.setattr(tp, "MAX_TOTAL", 50_000)
    big = {"x": "y" * 15_000}
    for i in range(3):
        t.put("chart", f"n{i}", big)
    with pytest.raises(ValueError):
        t.put("chart", "n3", big)
    assert sorted(t.all()["chart"]) == ["n0", "n1", "n2"]
    monkeypatch.setattr(tp, "MAX_KINDS", 2)
    t.put("draw:line", "a", {"x": 1})
    with pytest.raises(ValueError):
        t.put("draw:ray", "a", {"x": 1})


def test_a_body_past_the_largest_one_the_app_sends_is_refused(client, monkeypatch):
    from chartsrv import app as appmod
    monkeypatch.setattr(appmod, "BODY_MAX", 1000)
    r = client.put("/api/templates/chart/a", json={"data": {"x": "y" * 2000}})
    assert r.status_code == 413
    assert client.put("/api/templates/chart/a", json={"data": {"x": "y"}}).status_code == 200
