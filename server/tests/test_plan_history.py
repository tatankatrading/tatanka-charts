"""Each symbol's If/Then block kept version by version (plan_history.py): what a plan said before a session however
it was edited after, from both writers (the Plan tab's PUT /api/ifthen and the Indicators dialog's RowsBlock), the
block saved before the history began counted from its own save time, GET /api/ifthen/{sym}/history?until=, and a
backup carrying it."""
import sqlite3

from fastapi.testclient import TestClient

from chartsrv import backup, plan_history
from chartsrv.indicators import IndicatorSettings
from chartsrv.store import Store

A = "D|7612.75|7612.75|Short at the prior high\nU|7519.25|7519.25|Long at the low"
B = A + "\nP|7560|7565|Pivot"
C = "D|7650|7660|Short the zone"


def store(tmp_path):
    return Store(tmp_path / "chart.db")


def hist(s, sym="ES"):
    return (s.get_setting(plan_history.SETTING, {}) or {}).get(sym, [])


def test_each_new_text_is_a_version_and_the_same_text_adds_nothing(tmp_path):
    s = store(tmp_path)
    assert plan_history.remember(s, "ES", A, 1000)
    assert not plan_history.remember(s, "ES", A, 2000)   # saving the same text again: nothing
    assert plan_history.remember(s, "ES", B, 3000)
    assert plan_history.remember(s, "ES", A, 4000)        # back to an older text: a new version, at its new time
    assert [(v["rows"], v["saved"]) for v in hist(s)] == [(A, 1000), (B, 3000), (A, 4000)]
    assert hist(s, "NQ") == []


def test_versions_have_increasing_times_even_on_one_millisecond(tmp_path):
    s = store(tmp_path)
    plan_history.remember(s, "ES", A, 5000)
    plan_history.remember(s, "ES", B, 5000)
    assert [v["saved"] for v in hist(s)] == [5000, 5001]


def test_only_the_newest_90_per_symbol(tmp_path):
    s = store(tmp_path)
    for i in range(100):
        plan_history.remember(s, "ES", f"D|{7000 + i}|{7000 + i}|row {i}", 1000 + i)
    plan_history.remember(s, "NQ", C, 50)
    h = hist(s)
    assert len(h) == plan_history.KEEP == 90
    assert h[0]["rows"].startswith("D|7010|") and h[-1]["rows"].startswith("D|7099|")
    assert len(hist(s, "NQ")) == 1


def test_the_block_saved_before_the_history_began_counts_from_its_own_time(tmp_path):
    s = store(tmp_path)
    s.set_setting("ifthen", {"ES": {"rows": A, "updated": 777}})   # saved by an older build: no history
    assert plan_history.versions(s, "ES") == [{"rows": A, "saved": 777}]   # read as a version already
    assert plan_history.remember(s, "ES", B, 9000)
    assert [(v["rows"], v["saved"]) for v in hist(s)] == [(A, 777), (B, 9000)]
    # the history has it now: a later save doesn't store it twice
    s.set_setting("ifthen", {"ES": {"rows": B, "updated": 9000}})
    plan_history.remember(s, "ES", C, 9500)
    assert [v["saved"] for v in hist(s)] == [777, 9000, 9500]


def test_an_unchanged_block_saved_again_keeps_its_own_first_save(tmp_path):
    """Save pressed again (or the Indicators dialog's OK) on a block saved before the history began: the block goes in
    at its own save time and nothing at the new time, so a plan saved before a session still counts for it."""
    s = store(tmp_path)
    s.set_setting("ifthen", {"ES": {"rows": A, "updated": 1000}})
    assert not plan_history.remember(s, "ES", A, 5000)
    assert hist(s) == [{"rows": A, "saved": 1000}]
    s.set_setting("ifthen", {"ES": {"rows": A, "updated": 5000}})   # the writer's new updated time
    assert plan_history.versions(s, "ES", 1500) == [{"rows": A, "saved": 1000}]
    assert not plan_history.remember(s, "ES", A, 6000)   # and again: still nothing
    assert hist(s) == [{"rows": A, "saved": 1000}]
    ind = IndicatorSettings(s)
    s.set_setting("ifthen", {"NQ": {"rows": C, "updated": 300}})
    ind.put_symbol("ifthen", "NQ", {"RowsBlock": C}, 9000)   # the dialog's OK, nothing changed
    assert plan_history.versions(s, "NQ", 500) == [{"rows": C, "saved": 300}]


def test_the_replaced_block_alone_when_the_new_text_is_the_newest_version(tmp_path):
    s = store(tmp_path)
    plan_history.remember(s, "ES", B, 100)
    s.set_setting("ifthen", {"ES": {"rows": A, "updated": 50}})   # a restore put an older block back
    assert not plan_history.remember(s, "ES", B, 200)
    assert [(v["rows"], v["saved"]) for v in hist(s)] == [(A, 50), (B, 100)]


def test_versions_until(tmp_path):
    s = store(tmp_path)
    for t, rows in ((1000, A), (2000, B), (3000, C)):
        plan_history.remember(s, "ES", rows, t)
    s.set_setting("ifthen", {"ES": {"rows": C, "updated": 3000}})
    assert [v["saved"] for v in plan_history.versions(s, "ES", 2500)] == [1000, 2000]
    assert [v["saved"] for v in plan_history.versions(s, "ES", 2000)] == [1000, 2000]   # saved at the moment: counts
    assert [v["saved"] for v in plan_history.versions(s, "ES")] == [1000, 2000, 3000]
    assert plan_history.versions(s, "ES", 999) == []


def test_bad_stored_values_are_left_out(tmp_path):
    s = store(tmp_path)
    s.set_setting(plan_history.SETTING, {"ES": [{"rows": A, "saved": 10}, {"rows": 5, "saved": 11}, "x", {"rows": B}]})
    assert plan_history.versions(s, "ES") == [{"rows": A, "saved": 10}]
    s.set_setting(plan_history.SETTING, ["not", "a", "dict"])
    assert plan_history.remember(s, "ES", C, 20) and hist(s) == [{"rows": C, "saved": 20}]


def test_the_indicators_dialog_records_too(tmp_path):
    s = store(tmp_path)
    ind = IndicatorSettings(s)
    s.set_setting("ifthen", {"ES": {"rows": A, "updated": 400}})   # an older build's save
    ind.put_symbol("ifthen", "ES", {"RowsBlock": B}, 1000)
    ind.put_symbol("ifthen", "ES", {"RowsBlock": B}, 2000)   # the same text: nothing
    ind.put_symbol("ifthen", "ES", {"InstrumentLabel": "ES"}, 3000)   # no block: nothing
    assert [(v["rows"], v["saved"]) for v in hist(s)] == [(A, 400), (B, 1000)]
    assert s.get_setting("ifthen")["ES"] == {"rows": B, "updated": 2000}


def test_the_plan_tab_and_the_endpoint(tmp_path, monkeypatch, in_session):
    (tmp_path / "logs").mkdir()
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_SIM_WARMUP_MIN", "20")
    from chartsrv.app import app
    with TestClient(app) as c:
        assert c.get("/api/ifthen/ES/history").json() == {"symbol": "ES", "versions": []}
        assert c.put("/api/ifthen/ES", json={"rows": A}).status_code == 200
        t1 = c.get("/api/ifthen/ES").json()["updated"]
        assert c.put("/api/ifthen/ES", json={"rows": A}).status_code == 200   # the same text again
        assert c.put("/api/indicators/ifthen/ES", json={"RowsBlock": B}).status_code == 200
        v = c.get("/api/ifthen/ES/history").json()["versions"]
        assert [x["rows"] for x in v] == [A, B] and v[0]["saved"] == t1 and v[1]["saved"] > t1
        assert [x["rows"] for x in c.get(f"/api/ifthen/ES/history?until={t1}").json()["versions"]] == [A]
        assert c.get(f"/api/ifthen/ES/history?until={t1 - 1}").json()["versions"] == []
        assert c.get("/api/ifthen/NOPE/history").status_code == 404


def test_a_backup_carries_the_history(tmp_path):
    s = store(tmp_path)
    plan_history.remember(s, "ES", A, 1000)
    f = backup.make(tmp_path / "chart.db", tmp_path / "bk")
    db = sqlite3.connect(f)
    try:
        row = db.execute("SELECT value FROM settings WHERE key=?", (plan_history.SETTING,)).fetchone()
    finally:
        db.close()
    assert row and A.split("\n")[0] in row[0]
