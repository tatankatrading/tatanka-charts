"""How often an alert fires: Only once, Every time (as before), or Once every N minutes (params.every_min, with repeat).

Price and line alerts take N in place of Every time's minute; indicator alerts take it on top of once a bar; level,
study and If/Then alerts wait per level or row: one that fired stays quiet for N minutes and until it has re-armed as
before, the others fire meanwhile. The tapes are the Nov 16 panel's (NQ S2 five times in 7 s, the ES If/Then zone five
times in 2.6 min). Also: a level or study alert's re-arm distance can be changed (it was set at create only)."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_hunt_web_chart import WEB, app_lines, needs_node, run_app

from chartsrv import drawings
from chartsrv.alerts import AFRESH, REPEAT_COOLDOWN_MS, WAITS, AlertEngine
from chartsrv.config import Instrument
from chartsrv.levels import LevelSet
from chartsrv.models import Tick
from chartsrv.store import Store

ES = Instrument("ES", "E-mini S&P 500", 0.25, 50)
NQ = Instrument("NQ", "E-mini Nasdaq-100", 0.25, 20)
CT = ZoneInfo("America/Chicago")
MIN = 60_000
# invented levels: NQ S1 27460.00, S2 27425.00; ES S1 7490.00 (the prices in the panel)
NQ_LV = LevelSet("NQ", 27500.0, (27550.0, 27600.0), (27460.0, 27425.0, 27390.0), "2026-11-16")
ES_LV = LevelSet("ES", 7510.0, (7530.0, 7550.0), (7490.0, 7470.0), "2026-11-16")
ZONE = ("P|7490.50|7519.25|November's VAL at the base; it held Nov 4 and Nov 10 and lines up with NQ's Friday low.\\n"
        "Down one: Friday's cover was erased after hours.")
LEVEL = {"levels": "all", "tolerance_ticks": 0, "rearm_ticks": 8}
ROWS = {"rows": "all", "tolerance_ticks": 0, "rearm_ticks": 8}


def ms(hms: str, frac: int = 0) -> int:
    h, m, s = map(int, hms.split(":"))
    return int(datetime(2026, 11, 16, h, m, s, tzinfo=CT).timestamp() * 1000) + frac


class FakeLevels:
    def __init__(self, sets):
        self.sets = dict(sets)

    def get(self, sym):
        return self.sets.get(sym)


class Rig:
    """An alert engine on its own store, with NQ's and ES's daily levels and If/Then plans."""

    def __init__(self, tmp_path, plans: dict[str, str] | None = None):
        self.store = Store(tmp_path / "chart.db")
        if plans:
            self.store.set_setting("ifthen", {s: {"rows": b, "updated": 1} for s, b in plans.items()})
        self.levels = FakeLevels({"NQ": NQ_LV, "ES": ES_LV})
        self.fired: list[dict] = []
        self.eng = self.engine()

    def engine(self) -> AlertEngine:   # a start (again: a restart, on the same store)
        return AlertEngine(self.store, {"ES": ES, "NQ": NQ}, self.levels, self.fired.append)

    def add(self, sym, kind, params, repeat=True, every=None) -> int:
        i = self.store.add_alert(sym, kind, {**params, **({"every_min": every} if every else {})}, repeat, "")
        self.eng.reload()
        return i

    def run(self, sym, path):
        for ts, px in path:
            self.eng.on_tick(Tick(sym, ts, px, 1, 0))

    def paste(self, sym, block):   # PUT /api/ifthen/{sym}
        blocks = self.store.get_setting("ifthen", {}) or {}
        blocks[sym] = {"rows": block, "updated": 2}
        self.store.set_setting("ifthen", blocks)
        self.eng.ifthen_changed(sym, block)

    def got(self) -> list[tuple[str, str]]:
        return [(datetime.fromtimestamp(e["ts"] / 1000, CT).strftime("%H:%M:%S"), e["message"].split(" (last ")[0])
                for e in self.fired]


# NQ: four 8-tick (2.00 pt) excursions in 7 s, each back through S2 27425.00, then chop on it
NQ_PATH = [
    (ms("09:09:40"), 27430.00),                                        # first look: 20 ticks away, armed
    (ms("09:09:44", 0), 27426.00), (ms("09:09:44", 100), 27425.00),    # 1
    (ms("09:09:44", 300), 27426.50), (ms("09:09:44", 500), 27427.00),  # 8 ticks off: re-armed
    (ms("09:09:44", 700), 27426.00), (ms("09:09:44", 900), 27425.00),  # 2
    (ms("09:09:45", 200), 27426.00), (ms("09:09:45", 400), 27427.25),
    (ms("09:09:45", 600), 27425.25), (ms("09:09:45", 700), 27424.75),  # 3
    (ms("09:09:46"), 27424.00), (ms("09:09:47"), 27422.75),
    (ms("09:09:48"), 27424.00), (ms("09:09:49"), 27425.00),            # 4
    (ms("09:09:50"), 27427.00),
    (ms("09:09:51", 0), 27425.25), (ms("09:09:51", 200), 27424.75),    # 5
    *[(ms("09:09:52") + i * 250, p) for i, p in enumerate([27425.0, 27424.5, 27425.5, 27424.75, 27425.0, 27426.0] * 8)],
]
S2 = "NQ touched S2 27425.00"


def test_every_time_is_as_it_was_and_once_every_5_minutes_sends_one(tmp_path):
    """The panel's NQ burst: Every time sends its 5 rows, as before. Once every 5 minutes sends the first only: S2 is
    then quiet for 5 minutes however often price re-arms it and comes back."""
    every_time = Rig(tmp_path / "a")
    every_time.add("NQ", "level", LEVEL)
    every_time.run("NQ", NQ_PATH)
    assert every_time.got() == [("09:09:44", S2), ("09:09:44", S2), ("09:09:45", S2), ("09:09:49", S2), ("09:09:51", S2)]
    r = Rig(tmp_path / "b")
    r.add("NQ", "level", LEVEL, every=5)
    r.run("NQ", NQ_PATH)
    assert r.got() == [("09:09:44", S2)]


def test_a_level_waits_its_minutes_and_until_it_has_re_armed(tmp_path):
    """After the wait, a level re-armed meanwhile (price went 8 ticks away and came back) fires on the first touch:
    price still on it fires on its next trade. One that never re-armed since it fired stays quiet past the wait."""
    r = Rig(tmp_path)
    r.add("NQ", "level", LEVEL, every=5)
    r.run("NQ", NQ_PATH)                                                # fired 09:09:44.1, re-armed, chopping on S2
    r.run("NQ", [(ms("09:14:44"), 27425.0)])                            # 4:59.9 later: still waiting
    assert len(r.fired) == 1
    r.run("NQ", [(ms("09:14:44", 100), 27425.25), (ms("09:14:45"), 27425.0)])   # 5 minutes on: still on it, fires
    assert r.got()[1:] == [("09:14:45", S2)]
    chop = [27426.0, 27424.0, 27425.0, 27426.75, 27423.25]             # within 8 ticks: not re-armed
    r.run("NQ", [(ms("09:15:00") + i * 20_000, p) for i, p in enumerate(chop * 6)])   # to 09:24:40, past the wait
    assert len(r.fired) == 2
    r.run("NQ", [(ms("09:25:00"), 27428.0), (ms("09:25:01"), 27425.0)])   # re-armed (12 ticks) and back: fires
    assert r.got()[2:] == [("09:25:01", S2)]


def test_the_wait_is_per_level_others_fire_meanwhile(tmp_path):
    """S2 waiting does not quiet S1 or any other level of the same alert."""
    r = Rig(tmp_path)
    r.add("NQ", "level", LEVEL, every=15)
    r.run("NQ", NQ_PATH)
    r.run("NQ", [(ms("09:10:30"), 27445.0), (ms("09:10:31"), 27460.0)])   # up to S1, a minute into S2's wait
    r.run("NQ", [(ms("09:10:40"), 27430.0), (ms("09:10:41"), 27425.0)])   # back to S2: still waiting
    assert r.got() == [("09:09:44", S2), ("09:10:31", "NQ touched S1 27460.00")]


def test_the_wait_holds_through_a_state_reset_new_levels_and_a_restart(tmp_path):
    """What starts a level's state afresh (new levels, an edit, a restart) takes a first look that arms it a tick off
    the level, so chop on it fires again at once with Every time. Once every N minutes keeps its wait through all of
    them: a level by its price (S2 renamed S3 when a level is put in above it is the same level), saved for a start.
    (A roll clears the waits: test_a_roll_starts_the_waits_afresh.)"""
    chop = [(ms("09:09:44") + i * 500, p) for i, p in enumerate([27425.25, 27425.0] * 8)]

    def burst(rig):   # the state lost every 2 trades (a restart) while price chops a tick off S2
        for k in range(0, len(chop), 2):
            rig.eng = rig.engine()
            rig.run("NQ", chop[k:k + 2])

    every_time, waits = Rig(tmp_path / "a"), Rig(tmp_path / "b")
    every_time.add("NQ", "level", LEVEL)
    waits.add("NQ", "level", LEVEL, every=5)
    for r in (every_time, waits):
        r.run("NQ", [(ms("09:09:40"), 27430.0)])
        burst(r)
    assert len(every_time.fired) == 8 and len(waits.fired) == 1

    r = waits   # new levels: one put in between S1 and S2, so S2 (27425) is S3 now; and an edit of the alert
    new = LevelSet("NQ", 27500.0, NQ_LV.resistance, (27460.0, 27440.0, 27425.0, 27390.0), "2026-11-16")
    r.levels.sets["NQ"] = new
    r.eng.reset_levels("NQ", NQ_LV, new)
    r.store.update_alert(1, params={**LEVEL, "every_min": 5, "tolerance_ticks": 1})   # another band: a first look
    r.eng.reload()
    r.run("NQ", [(ms("09:10:00"), 27426.0), (ms("09:10:01"), 27425.0)])
    assert len(r.fired) == 1
    r.store.update_alert(1, params={**LEVEL, "every_min": 5})
    r.eng = r.engine()                                                  # a restart: the waits come back from the store
    assert r.store.get_setting(WAITS) == [[[1, "level", 27425 * 4], ms("09:09:44", 500)]]
    r.run("NQ", [(ms("09:11:00"), 27430.0), (ms("09:11:01"), 27425.0)])
    assert len(r.fired) == 1
    r.run("NQ", [(ms("09:15:00"), 27430.0), (ms("09:15:01"), 27425.0)])   # 5 minutes on: fires, as S3
    assert r.got()[1:] == [("09:15:01", "NQ touched S3 27425.00")]


def test_waits_saved_badly_are_left_out(tmp_path):
    store = Store(tmp_path / "chart.db")
    store.set_setting(WAITS, [[[1, "level", 5], 10], "x", [[], 3], [["a"], 4], [[2, "study"], "y"], [[3, "study"], 7]])
    eng = AlertEngine(store, {"ES": ES}, None, lambda e: None)
    assert eng.waits == {}                                              # none of them names an alert stored
    store.add_alert("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 0, "rearm_ticks": 8}, True, "")
    store.add_alert("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 0, "rearm_ticks": 8}, True, "")
    store.add_alert("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 0, "rearm_ticks": 8}, True, "")
    eng = AlertEngine(store, {"ES": ES}, None, lambda e: None)
    assert eng.waits == {(1, "level", 5): 10, (3, "study"): 7}
    store.set_setting(WAITS, {"not": "a list"})
    assert AlertEngine(store, {"ES": ES}, None, lambda e: None).waits == {}


# ---- If/Then rows: the ES zone two ticks above S1 ---------------------------------------------------------------------
ES_PATH = [
    (ms("09:09:30"), 7486.00),                             # first look: armed
    (ms("09:09:52"), 7489.00), (ms("09:09:53"), 7490.00),
    (ms("09:09:55"), 7490.50),                             # into the zone from below: fires
    (ms("09:10:30"), 7489.25), (ms("09:11:00"), 7488.50),  # 8 ticks under it: re-armed
    (ms("09:11:25"), 7490.50),                             # 2
    (ms("09:11:33"), 7488.25), (ms("09:11:41"), 7490.50),  # 3
    (ms("09:12:10"), 7488.50), (ms("09:12:23"), 7490.50),  # 4
    (ms("09:12:28"), 7488.50), (ms("09:12:33"), 7490.50),  # 5
]
ENTERED = "ES entered 7490.50–7519.25"


def test_an_if_then_row_waits_by_itself(tmp_path):
    """The panel's zone, Every time: 5 rows. Once every 5 minutes: the first, then the zone waits while the plan's
    other rows (a line at 7495) fire as usual. The zone, re-armed and back in it meanwhile, sends nothing for that:
    price still in it when its 5 minutes are up did not just come in, so it fires on the next entry. A new paste that
    keeps the row (its LO and HI as written, the text changed) keeps its wait."""
    every_time = Rig(tmp_path / "a", {"ES": ZONE})
    every_time.add("ES", "ifthen", ROWS)
    every_time.run("ES", ES_PATH)
    assert [t for t, m in every_time.got() if m.startswith(ENTERED)] == ["09:09:55", "09:11:25", "09:11:41", "09:12:23", "09:12:33"]

    r = Rig(tmp_path / "b", {"ES": ZONE + "\nL|7495|7495|the line"})
    r.add("ES", "ifthen", ROWS, every=5)
    r.run("ES", ES_PATH)
    r.run("ES", [(ms("09:13:00"), 7493.0), (ms("09:13:10"), 7495.0)])   # the line: fires, the zone still waits
    r.paste("ES", ZONE.replace("Friday's cover", "the cover") + "\nL|7495|7495|the line")
    r.run("ES", [(ms("09:14:00"), 7491.0), (ms("09:14:54"), 7490.75)])   # zone row kept: still waiting
    r.run("ES", [(ms("09:14:56"), 7490.75), (ms("09:15:30"), 7505.0)])  # 5 minutes on, in it all along: no entry
    assert len(r.fired) == 2
    r.run("ES", [(ms("09:16:00"), 7490.25), (ms("09:16:10"), 7490.50)])  # out a tick and back in: an entry
    said = [(t, m.split(":")[0] if m.startswith(ENTERED) else m) for t, m in r.got()]
    assert said == [("09:09:55", ENTERED), ("09:13:10", "ES touched 7495.00: the line"), ("09:16:10", ENTERED)]


# ---- study alerts: the one level they watch ------------------------------------------------------------------------
def test_a_study_alert_waits_its_minutes(tmp_path):
    r = Rig(tmp_path)
    r.eng.studies = lambda sym, p, ts: (7400.0, 1)   # (level, its session)
    r.add("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 0, "rearm_ticks": 4}, every=15)
    swings = [7402.0, 7400.0] * 10                   # 8 ticks away and back: re-armed each time
    r.run("ES", [(ms("09:00:00") + i * 30_000, p) for i, p in enumerate(swings)])   # 10 minutes of it
    assert [t for t, _ in r.got()] == ["09:00:30"]
    r.run("ES", [(ms("09:15:30"), 7402.0), (ms("09:15:31"), 7400.0)])
    assert [t for t, _ in r.got()] == ["09:00:30", "09:15:31"]


# ---- price and line alerts: N minutes in place of Every time's one --------------------------------------------------
def _price(tmp_path, every=None, repeat=True, **params):
    r = Rig(tmp_path)
    r.add("ES", "price", params, repeat=repeat, every=every)
    return r


def test_every_time_price_alert_fires_at_most_once_a_minute(tmp_path):
    """As before (REPEAT_COOLDOWN_MS): an alert saved with no every_min."""
    r = _price(tmp_path, price=7000.0, direction="gt")
    r.run("ES", [(ms("09:00:00") + i * 1000, 7001.0) for i in range(150)])
    assert [t for t, _ in r.got()] == ["09:00:00", "09:01:00", "09:02:00"] and REPEAT_COOLDOWN_MS == MIN


def test_price_alert_once_every_n_minutes(tmp_path):
    """Greater than: true all along, sent once every 5 minutes. Crossing: one cross in 5 minutes; a cross meanwhile is
    dropped, not sent when the time is up."""
    r = _price(tmp_path / "gt", every=5, price=7000.0, direction="gt")
    r.run("ES", [(ms("09:00:00") + i * 10_000, 7001.0) for i in range(72)])   # 12 minutes
    assert [t for t, _ in r.got()] == ["09:00:00", "09:05:00", "09:10:00"]
    r = _price(tmp_path / "x", every=5, price=7000.0, direction="cross")
    r.run("ES", [(ms("09:00:00") + i * 40_000, 6999.0 if i % 2 else 7001.0) for i in range(20)])   # a cross each 40 s
    assert [t for t, _ in r.got()] == ["09:00:40", "09:06:00", "09:11:20"]   # (09:05:20's cross: 4:40 on)


def test_only_once_ignores_a_wait_and_stops(tmp_path):
    r = _price(tmp_path, every=5, repeat=False, price=7000.0, direction="gt")   # (the API never saves this)
    r.run("ES", [(ms("09:00:00") + i * MIN, 7001.0) for i in range(12)])
    assert len(r.fired) == 1 and r.eng.alerts[1].enabled is False


def test_drawn_line_once_every_n_minutes(tmp_path):
    d = drawings.clean({"type": "hline", "pts": [{"t": 0, "p": 7000.0}],
                        "alert": {"on": True, "repeat": True, "every_min": 15}}, "l1")
    assert d["alert"] == {"on": True, "direction": "cross", "repeat": True, "every_min": 15}
    params = drawings.alert_params(d)
    assert params["every_min"] == 15
    r = Rig(tmp_path)
    r.store.add_alert("ES", "line", params, True, "")
    r.eng.reload()
    r.run("ES", [(ms("09:00:00") + i * 60_000, 6999.0 if i % 2 else 7001.0) for i in range(40)])   # a cross a minute
    assert [t for t, _ in r.got()] == ["09:01:00", "09:16:00", "09:31:00"]


def test_a_drawn_lines_every_min_is_checked_and_kept_with_repeat_only():
    line = {"type": "hline", "pts": [{"t": 0, "p": 7000.0}]}
    once = drawings.clean({**line, "alert": {"on": True, "repeat": False, "every_min": 5}}, "a")
    assert once["alert"] == {"on": True, "direction": "cross", "repeat": False}
    assert "every_min" not in drawings.alert_params(once)
    for bad in (0, 1441, 2.5, "5", True):
        with pytest.raises(ValueError, match="every_min"):
            drawings.clean({**line, "alert": {"on": True, "repeat": True, "every_min": bad}}, "a")


# ---- indicator alerts: a gap on top of once a bar -------------------------------------------------------------------
def test_indicator_alert_once_every_n_minutes_on_top_of_once_a_bar(tmp_path):
    """ind_alerts.check says when a bar's crossing fires (once a bar); Once every N minutes then holds back those within
    N minutes of the last fire, kept in the store (last_fired), so through a restart too. Bar close at the session's
    end (on_shut) the same."""
    r = Rig(tmp_path)
    r.add("ES", "ind", {"a": {"ind": "price"}, "b": {"value": 7000.0}, "spec": "1m", "on": "close", "direction": "cross"},
          every=5)
    bars = [ms("09:00:00") + i * MIN for i in range(12)]   # a crossing on each 1-minute bar
    r.eng.indicators = lambda a, tk: "ES price crossed 7000.00"
    r.run("ES", [(t, 7000.0) for t in bars[:4]])
    r.eng = r.engine()
    r.eng.indicators = lambda a, tk: "ES price crossed 7000.00"
    r.run("ES", [(t, 7000.0) for t in bars[4:]])
    assert [t for t, _ in r.got()] == ["09:00:00", "09:05:00", "09:10:00"]
    r.eng.on_shut(Tick("ES", ms("09:14:00"), 7000.0, 0, 0))
    assert len(r.fired) == 3
    r.eng.on_shut(Tick("ES", ms("09:15:00"), 7000.0, 0, 0))
    assert len(r.fired) == 4
    every_bar = Rig(tmp_path / "b")
    every_bar.add("ES", "ind", {"a": {"ind": "price"}, "b": {"value": 7000.0}, "spec": "1m", "on": "close",
                                "direction": "cross"})
    every_bar.eng.indicators = lambda a, tk: "ES price crossed 7000.00"
    every_bar.run("ES", [(t, 7000.0) for t in bars])
    assert len(every_bar.fired) == 12


# ---- edits: how often, or how far it re-arms, changed alone is no new condition ---------------------------------------
def test_changing_how_often_or_the_re_arm_keeps_a_level_quiet(tmp_path):
    """An alert edited starts afresh (a first look: armed a tick off the level), which is right for a new condition.
    Its trigger or its re-arm distance changed alone is not one: S2, just fired, stays quiet until price has been the
    (new) distance away."""
    r = Rig(tmp_path)
    r.add("NQ", "level", LEVEL)
    r.run("NQ", [(ms("09:09:40"), 27430.0), (ms("09:09:44"), 27425.0)])
    assert len(r.fired) == 1
    r.store.update_alert(1, params={**LEVEL, "rearm_ticks": 40})
    r.eng.reload()
    r.run("NQ", [(ms("09:09:45"), 27425.25), (ms("09:09:46"), 27425.0), (ms("09:09:50"), 27427.0),
                 (ms("09:09:51"), 27425.0)])                            # 8 ticks away is no longer enough
    assert len(r.fired) == 1
    r.store.update_alert(1, params={**LEVEL, "rearm_ticks": 40, "every_min": 1})
    r.eng.reload()
    r.run("NQ", [(ms("09:11:00"), 27425.25), (ms("09:11:01"), 27425.0)])   # a minute on, never re-armed: quiet
    assert len(r.fired) == 1
    r.store.update_alert(1, params={**LEVEL, "tolerance_ticks": 1})   # a new band: afresh
    r.eng.reload()
    r.run("NQ", [(ms("09:11:02"), 27425.5), (ms("09:11:03"), 27425.0)])   # armed two ticks off: fires
    assert len(r.fired) == 2


# ---- starting afresh: moved, edited or turned back on, an alert has no wait from before ------------------------------
def test_a_moved_price_alert_has_no_wait_from_its_old_price(tmp_path):
    """Once every 60 minutes, fired at 7000, then moved to 7100 (dragged on the chart: PATCH price, which turns it on).
    A cross of 7100 five minutes on fires: its wait ran from the 7000 alert, so that cross was dropped without a word and
    nothing came for the hour. From its own fire it waits as it should, through a restart too."""
    r = _price(tmp_path, every=60, price=7000.0, direction="cross")
    r.run("ES", [(ms("09:00:00"), 6999.0), (ms("09:00:01"), 7001.0)])
    r.store.update_alert(1, params={"price": 7100.0, "direction": "cross", "every_min": 60}, enabled=True)
    r.eng.reload()
    assert r.store.get_setting(WAITS) == [[[1, AFRESH], ms("09:00:01")]]   # saved: a restart now keeps it afresh
    r.eng = r.engine()
    r.run("ES", [(ms("09:05:00"), 7099.0), (ms("09:05:01"), 7101.0)])
    r.run("ES", [(ms("09:06:00"), 7099.0), (ms("09:06:01"), 7101.0)])   # within its own wait: dropped
    r.eng = r.engine()
    r.run("ES", [(ms("09:30:00"), 7099.0), (ms("09:30:01"), 7101.0)])   # still, after a restart
    assert [t for t, _ in r.got()] == ["09:00:01", "09:05:01"]
    r.run("ES", [(ms("10:05:00"), 7099.0), (ms("10:05:02"), 7101.0)])
    assert [t for t, _ in r.got()] == ["09:00:01", "09:05:01", "10:05:02"]


def test_a_price_alerts_wait_holds_when_only_how_often_changes(tmp_path):
    """Its trigger changed alone (Once every 60 to 15 min) is no new condition: it waits from its last alert."""
    r = _price(tmp_path, every=60, price=7000.0, direction="gt")
    r.run("ES", [(ms("09:00:00"), 7001.0)])
    r.store.update_alert(1, params={"price": 7000.0, "direction": "gt", "every_min": 15})
    r.eng.reload()
    r.run("ES", [(ms("09:10:00"), 7001.0), (ms("09:15:00"), 7001.0)])
    assert [t for t, _ in r.got()] == ["09:00:00", "09:15:00"]


def test_a_moved_drawn_line_has_no_wait_from_before(tmp_path):
    """A line alerting at most once a day, moved after it fired (the drawing saved: _sync_drawing_alert turns it on
    with its new points): its editor says armed, and it is. Before, it was deaf for up to 24 hours."""
    line = {"t1": 0, "p1": 7000.0, "t2": 1, "p2": 7000.0, "left": True, "right": True, "type": "hline",
            "direction": "cross", "drawing": "d1", "every_min": 1440}
    r = Rig(tmp_path)
    r.store.add_alert("ES", "line", line, True, "")
    r.eng.reload()
    r.run("ES", [(ms("09:00:00"), 6999.0), (ms("09:00:01"), 7001.0)])
    r.store.update_alert(1, params={**line, "p1": 7050.0, "p2": 7050.0}, enabled=True)
    r.eng.reload()
    r.run("ES", [(ms("09:10:00"), 7049.0), (ms("09:10:01"), 7051.0), (ms("09:11:00"), 7049.0)])
    assert [t for t, _ in r.got()] == ["09:00:01", "09:10:01"]          # (09:11's cross: within its own day)


def test_an_alert_turned_back_on_has_no_wait_from_before(tmp_path):
    """Only once, fired, then restarted as Once every 60 minutes (the dialog's Save): the next cross fires. Paused and
    turned back on, the same. A Daily levels alert too: S2, which fired before the restart, fires on its next touch."""
    r = _price(tmp_path / "price", repeat=False, price=7000.0, direction="above")
    r.run("ES", [(ms("09:00:00"), 6999.0), (ms("09:00:01"), 7001.0)])
    assert r.eng.alerts[1].enabled is False
    r.store.update_alert(1, enabled=True, repeat=True, params={"price": 7000.0, "direction": "above", "every_min": 60})
    r.eng.reload()
    r.run("ES", [(ms("09:03:00"), 6999.0), (ms("09:03:01"), 7001.0)])   # afresh: fires
    r.run("ES", [(ms("09:04:00"), 6999.0), (ms("09:04:01"), 7001.0)])   # its own wait
    for on in (False, True):                                            # paused, and back on
        r.store.update_alert(1, enabled=on)
        r.eng.reload()
    r.run("ES", [(ms("09:05:00"), 6999.0), (ms("09:05:01"), 7001.0)])
    assert [t for t, _ in r.got()] == ["09:00:01", "09:03:01", "09:05:01"]

    lv = Rig(tmp_path / "level")
    lv.add("NQ", "level", LEVEL, repeat=False)
    lv.run("NQ", [(ms("09:09:40"), 27430.0), (ms("09:09:44"), 27425.0)])   # S2: fired, and the alert stops
    lv.store.update_alert(1, enabled=True, repeat=True, params={**LEVEL, "every_min": 15})
    lv.eng.reload()
    lv.run("NQ", [(ms("09:10:00"), 27430.0), (ms("09:10:01"), 27425.0)])   # a first look 20 ticks off, then S2
    assert lv.got() == [("09:09:44", S2), ("09:10:01", S2)]


# ---- what a wait holds back -----------------------------------------------------------------------------------------
def test_a_moving_study_level_touches_as_with_every_time_when_no_wait_runs(tmp_path):
    """VWAP steps up so its band (Touch within 2) takes in the last print as price steps a tick out of it: a touch for
    Every time, and for Once every 5 minutes with no wait running too (such a touch was left out whenever the alert had
    a wait set, held back or not)."""
    for every in (None, 5):
        r = Rig(tmp_path / str(every))
        lv = {"v": 99.0}
        r.eng.studies = lambda sym, p, ts: (lv["v"], 1)
        r.add("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 2, "rearm_ticks": 8}, every=every)
        r.run("ES", [(ms("09:00:00"), 100.0)])                          # 1.00 away: armed
        lv["v"] = 99.4
        r.run("ES", [(ms("09:00:01"), 100.0)])                          # band 98.90-99.90: no touch
        lv["v"] = 99.6
        r.run("ES", [(ms("09:00:02"), 100.25)])                         # band 99.10-100.10 holds 100.00
        assert len(r.fired) == 1, every


def test_two_names_at_one_price_are_one_level_with_a_wait(tmp_path):
    """P and S1 both at 27425: Every time sends one for each name, as before. Once every 5 minutes sends one: the other
    name goes quiet with it and re-arms with it, so price sat on 27425 past the wait does not send it then either."""
    two = LevelSet("NQ", 27425.0, (27550.0,), (27425.0, 27390.0), "2026-11-16")
    sat = [(ms("09:00:01") + i * 30_000, 27425.0 if i % 2 else 27425.25) for i in range(1, 16)]   # to 09:07:31
    for every, want in ((None, ["NQ touched P 27425.00", "NQ touched S1 27425.00"]), (5, ["NQ touched P 27425.00"])):
        r = Rig(tmp_path / str(every))
        r.levels.sets["NQ"] = two
        r.add("NQ", "level", LEVEL, every=every)
        r.run("NQ", [(ms("09:00:00"), 27430.0), (ms("09:00:01"), 27425.0), *sat])
        assert [m for _, m in r.got()] == want, every
    r.run("NQ", [(ms("09:08:00"), 27428.0), (ms("09:08:01"), 27425.0)])   # re-armed (12 ticks) and back: one again
    assert r.got() == [("09:00:01", "NQ touched P 27425.00"), ("09:08:01", "NQ touched P 27425.00")]


def test_an_if_then_zone_sat_in_through_its_wait_fires_on_the_next_entry_only(tmp_path):
    """The reviewer's tape: in at 1 s (fires), out 10 ticks and back in at 60 s (held), then deep in the zone till the
    wait is over. It sent "entered ... (last 7510.00)" at 302 s, price having entered nothing; now it fires when price
    comes back in. A line still touched when its wait is over fires on its next trade there (price is on it)."""
    t0 = ms("09:00:00")
    r = Rig(tmp_path / "zone", {"ES": "P|7490.50|7519.25|zone"})
    r.add("ES", "ifthen", ROWS, every=5)
    deep = [(t0 + 120_000 + i * 10_000, 7505.0 + i % 3) for i in range(17)]
    r.run("ES", [(t0 - 1000, 7486.0), (t0 + 1000, 7491.0), (t0 + 30_000, 7488.0), (t0 + 60_000, 7491.0), *deep,
                 (t0 + 302_000, 7510.0)])
    assert [e["ts"] - t0 for e in r.fired] == [1000]
    r.run("ES", [(t0 + 400_000, 7490.0), (t0 + 401_000, 7491.0)])     # out a tick and back: an entry
    assert [e["ts"] - t0 for e in r.fired] == [1000, 401_000]

    line = Rig(tmp_path / "line", {"ES": "L|7495|7495|the line"})
    line.add("ES", "ifthen", ROWS, every=5)
    line.run("ES", [(t0 - 1000, 7493.0), (t0 + 1000, 7495.0), (t0 + 30_000, 7497.0), (t0 + 60_000, 7495.0),
                    (t0 + 299_000, 7495.0), (t0 + 302_000, 7495.0)])
    assert [e["ts"] - t0 for e in line.fired] == [1000, 302_000]


def test_a_wait_stamped_ahead_of_the_trades_never_holds(tmp_path):
    """A clock stepped back, or a bad tick time: a level's wait, or a price alert's last fire, stamped after the trade
    would never end (it silenced the level for days). Neither holds."""
    r = Rig(tmp_path / "level")
    r.add("NQ", "level", LEVEL, every=5)
    r.store.set_setting(WAITS, [[[1, "level", 27425 * 4], ms("09:00:00") + 10 * 86_400_000]])
    r.eng = r.engine()
    r.run("NQ", [(ms("09:00:00"), 27430.0), (ms("09:00:01"), 27425.0)])
    assert r.got() == [("09:00:01", S2)]
    p = _price(tmp_path / "price", every=60, price=7000.0, direction="gt")
    p.store.update_alert(1, last_fired=ms("09:00:00") + 86_400_000)
    p.eng = p.engine()
    p.run("ES", [(ms("09:00:00"), 7001.0)])
    assert len(p.fired) == 1


def test_a_print_stamped_just_before_the_last_fire_is_within_its_wait(tmp_path):
    """Prints a few ms out of order, or a formula's quote stamped by the server's clock just behind a future's trade
    time: a cross back stamped 5 ms before an Every time price alert fired is within its minute, as before the waits
    (it sent a second alert). A level's wait the same: S1 re-armed and touched on a print stamped before it fired is
    held, not sent. (Stamped further ahead than a minute, a fire holds nothing: the test above.)"""
    r = _price(tmp_path / "price", price=7500.0, direction="cross")
    r.run("ES", [(ms("09:00:00"), 7499.0), (ms("09:00:01"), 7501.0), (ms("09:00:01") - 5, 7499.0)])
    r.run("ES", [(ms("09:00:59"), 7501.0), (ms("09:01:02"), 7499.0)])   # the minute is up: the next cross fires
    assert [e["ts"] for e in r.fired] == [ms("09:00:01"), ms("09:01:02")]

    lv = Rig(tmp_path / "level")
    lv.add("ES", "level", {**LEVEL, "levels": ["S1"], "rearm_ticks": 1}, every=5)
    lv.run("ES", [(ms("09:00:00"), 7491.0), (ms("09:00:01"), 7490.0), (ms("09:00:01") - 3, 7490.25),
                  (ms("09:00:01") - 2, 7490.0)])                          # out of order: re-armed and back on S1
    assert [e["ts"] for e in lv.fired] == [ms("09:00:01")]
    lv.run("ES", [(ms("09:05:01"), 7490.0)])                            # held: still on S1 when the wait is over
    assert [e["ts"] for e in lv.fired] == [ms("09:00:01"), ms("09:05:01")]


def test_a_moved_alert_stays_afresh_through_a_print_stamped_far_ahead(tmp_path):
    """A price alert set to once an hour fires and is moved: its old fire is no wait (AFRESH). One print stamped two
    days ahead (a PC clock set wrong for a moment) fires an Every time level alert, whose new wait dropped every wait
    a day older than that print, AFRESH too: the moved alert waited from its old fire again and its first cross at the
    new price sent nothing. Through a restart too (the waits as saved)."""
    r = _price(tmp_path, every=60, price=7500.0, direction="cross")
    r.add("ES", "level", {**LEVEL, "levels": ["R2"]})                   # Every time, R2 7550
    r.run("ES", [(ms("09:00:00"), 7499.0), (ms("09:00:01"), 7501.0)])   # the price alert fires
    r.store.update_alert(1, params={"price": 7495.0, "direction": "cross", "every_min": 60}, enabled=True)
    r.eng.reload()
    ahead = ms("09:00:03") + 2 * 86_400_000
    r.run("ES", [(ms("09:00:02"), 7545.0), (ahead, 7550.0)])            # R2 fires on the bad print
    r.eng = r.engine()
    r.run("ES", [(ms("09:00:04"), 7496.0), (ms("09:00:05"), 7494.0)])   # the moved alert crosses its new price
    assert [(e["alert_id"], e["ts"]) for e in r.fired] == [(1, ms("09:00:01")), (2, ahead), (1, ms("09:00:05"))]


def test_a_moved_alert_that_fires_again_waits_from_that_fire_even_in_the_same_ms(tmp_path):
    """AFRESH named the fire before the move by its time. Moved (or rolled) and firing again on prints stamped the same
    ms as that old fire (a feed's backlog, a sweep), the new fire had that time too, so it was taken for the old one:
    no wait, and Greater than fired on every print of that ms. It fires once, then waits from that fire."""
    for every in (None, 60):
        r = _price(tmp_path / str(every), every=every, price=7000.0, direction="gt")
        t = ms("09:00:00")
        r.run("ES", [(t, 7001.0)])
        r.store.update_alert(1, params={"price": 6999.75, "direction": "gt", **({"every_min": every} if every else {})},
                             enabled=True)
        r.eng.reload()
        r.run("ES", [(t, 7001.0), (t, 7002.0), (t, 7001.5), (t + 30_000, 7001.0)])
        assert [e["ts"] for e in r.fired] == [t, t], every
        assert (1, AFRESH) not in r.eng.waits


def test_a_level_re_armed_and_touched_again_in_one_ms_is_held(tmp_path):
    """A sweep stamps several prints with one ms: S1 fires, price runs the re-arm distance and comes back, all in that
    ms. The second touch is held by the wait, not taken for another name at S1's price on the same trade (which goes
    quiet with the one that fired), so price still on S1 when the wait is over fires. Two names at one price touched
    again in that ms still send one alert after it."""
    t = ms("09:00:01")
    sat = [(t + k * 30_000, 7490.0) for k in range(1, 5)]              # on S1 past the 1-minute wait
    r = Rig(tmp_path / "one")
    r.add("ES", "level", {**LEVEL, "levels": ["S1"], "rearm_ticks": 1}, every=1)
    r.run("ES", [(ms("09:00:00"), 7491.0), (t, 7490.0), (t, 7490.25), (t, 7490.0), *sat])
    assert [e["ts"] - t for e in r.fired] == [0, 60_000]

    two = Rig(tmp_path / "two")
    two.levels.sets["ES"] = LevelSet("ES", 7490.0, (7530.0,), (7490.0, 7470.0), "2026-11-16")   # P and S1 at 7490
    two.add("ES", "level", {**LEVEL, "rearm_ticks": 1}, every=1)
    two.run("ES", [(ms("09:00:00"), 7491.0), (t, 7490.0), (t, 7490.25), (t, 7490.0), *sat])
    assert [(e["ts"] - t, e["message"].split(" (last")[0]) for e in two.fired] == [
        (0, "ES touched P 7490.00"), (60_000, "ES touched P 7490.00")]


def test_a_roll_starts_the_waits_afresh(tmp_path):
    """Prices jump by the spread at a roll, so the new contract at a level's, a row's or a price alert's number is
    another place. S1, an If/Then line and a price alert, each once an hour, fire on the old contract; the new one
    trades 50 points up, then falls through all three within the hour: each fires (their waits held them back,
    through a restart too). A price alert moved before the roll stays afresh. Every time keeps its minute, as before."""
    r = Rig(tmp_path, {"ES": "L|7470|7470|the line"})
    r.add("ES", "level", {**LEVEL, "levels": ["S1"]}, every=60)        # 1: S1 7490
    r.add("ES", "ifthen", ROWS, every=60)                               # 2: the line at 7470
    r.add("ES", "price", {"price": 7500.0, "direction": "cross"}, every=60)   # 3
    r.add("ES", "price", {"price": 7520.0, "direction": "cross"}, every=60)   # 4: moved to 7480 after it fires
    r.run("ES", [(ms("09:00:00"), 7525.0), (ms("09:00:01"), 7515.0), (ms("09:00:02"), 7499.0),
                 (ms("09:00:03"), 7490.0), (ms("09:00:04"), 7470.0)])
    assert [e["alert_id"] for e in r.fired] == [4, 3, 1, 2]
    r.store.update_alert(4, params={"price": 7480.0, "direction": "cross", "every_min": 60}, enabled=True)
    r.eng.reload()
    r.eng.rolled("ES")
    r.eng = r.engine()
    r.run("ES", [(ms("09:10:00"), 7540.0), (ms("09:20:00"), 7499.0), (ms("09:20:01"), 7490.0),
                 (ms("09:20:02"), 7479.0), (ms("09:20:03"), 7470.0)])
    assert [(e["alert_id"], e["ts"]) for e in r.fired[4:]] == [
        (3, ms("09:20:00")), (1, ms("09:20:01")), (4, ms("09:20:02")), (2, ms("09:20:03"))]

    et = _price(tmp_path / "every_time", price=7000.0, direction="gt")
    et.run("ES", [(ms("09:00:00"), 7001.0)])
    et.eng.rolled("ES")
    et.run("ES", [(ms("09:00:10"), 7051.0), (ms("09:01:00"), 7051.0)])
    assert [e["ts"] for e in et.fired] == [ms("09:00:00"), ms("09:01:00")]


def test_waits_saved_with_a_nested_key_are_left_out(tmp_path):
    """A wait whose key holds a list or a dict (a corrupt or foreign row: no API writes this setting) is left out, as
    anything else saved badly is: it raised TypeError (unhashable) out of AlertEngine, and the server did not start."""
    store = Store(tmp_path / "chart.db")
    store.add_alert("ES", "study", {"study": "vwap", "band": 0, "tolerance_ticks": 0, "rearm_ticks": 8}, True, "")
    store.set_setting(WAITS, [[[1, [2, 3]], 5], [[1, {"a": 1}], 6], [[1, "level", True], 7], [[1, "level", None], 8],
                              [[1, "study"], 9], [[1, "ifthen", 7470.0, 7470.0, 0], 10]])
    eng = AlertEngine(store, {"ES": ES}, None, lambda e: None)
    assert eng.waits == {(1, "study"): 9, (1, "ifthen", 7470.0, 7470.0, 0): 10}


# ---- the API ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_FEED", "none")
    from fastapi.testclient import TestClient

    from chartsrv.app import app
    with TestClient(app) as c:
        yield c


def test_api_creates_each_kind_once_every_n_minutes(client):
    """every_min repeats: it turns repeat on where the kind's default is Only once (price, indicator), and asking for
    Only once with it is refused. 0 or none is Every time (or Only once): saved as before, with no every_min."""
    ind = {"a": {"ind": "ema", "len": 9}, "b": {"ind": "price"}, "spec": "5m"}
    for kind, extra in (("price", {"price": 7000}), ("level", {}), ("study", {"study": "vwap"}), ("ifthen", {}),
                        ("ind", ind)):
        a = client.post("/api/alerts", json={"symbol": "ES", "kind": kind, "every_min": 15, **extra})
        assert a.status_code == 201, a.text
        assert a.json()["repeat"] is True and a.json()["params"]["every_min"] == 15, (kind, a.json())
    plain = client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 7000, "every_min": 0}).json()
    assert plain["repeat"] is False and "every_min" not in plain["params"]
    lv = client.post("/api/alerts", json={"symbol": "ES", "kind": "level"}).json()
    assert lv["repeat"] is True and lv["params"] == {"levels": "all", "tolerance_ticks": 0, "rearm_ticks": 8}
    r = client.post("/api/alerts", json={"symbol": "ES", "kind": "level", "every_min": 5, "repeat": False})
    assert r.status_code == 400 and "Only once" in r.text
    for bad in (-1, 1441, 2.5, "x", True, "5", 5.0):   # whole minutes as JSON has them, as a drawn line's
        assert client.post("/api/alerts", json={"symbol": "ES", "kind": "level", "every_min": bad}).status_code == 422, bad
    stock = client.post("/api/alerts", json={"symbol": "AAPL", "kind": "price", "price": 200, "direction": "gt",
                                             "every_min": 60})
    assert stock.status_code == 201 and stock.json()["params"] == {"price": 200.0, "direction": "gt", "free": True,
                                                                    "every_min": 60}


def test_api_edits_how_often(client):
    """PATCH every_min sets it (and repeat), 0 clears it (Every time); Only once drops it; a drawn line's is set on the
    line. Changed alone, it keeps the alert's state (it is not restarted, its id and last fire stay)."""
    a = client.post("/api/alerts", json={"symbol": "ES", "kind": "level", "repeat": False}).json()
    b = client.patch(f"/api/alerts/{a['id']}", json={"every_min": 30}).json()
    assert b["repeat"] is True and b["params"]["every_min"] == 30 and b["id"] == a["id"]
    c = client.patch(f"/api/alerts/{a['id']}", json={"every_min": 0}).json()
    assert c["repeat"] is True and "every_min" not in c["params"]
    client.patch(f"/api/alerts/{a['id']}", json={"every_min": 7})
    d = client.patch(f"/api/alerts/{a['id']}", json={"repeat": False}).json()
    assert d["repeat"] is False and "every_min" not in d["params"]
    r = client.patch(f"/api/alerts/{a['id']}", json={"repeat": False, "every_min": 5})
    assert r.status_code == 400
    for bad in (1441, True, "5"):
        assert client.patch(f"/api/alerts/{a['id']}", json={"every_min": bad}).status_code == 422, bad
    e = client.patch(f"/api/alerts/{a['id']}", json={"repeat": True, "every_min": 1440, "note": "n"}).json()
    assert e["repeat"] is True and e["params"]["every_min"] == 1440 and e["note"] == "n"

    m = client.app.state.market   # a drawn line's alert: its trigger is the drawing's
    put = client.put("/api/drawings/ES/ln1", json={"type": "hline", "pts": [{"t": 0, "p": 7000.0}],
                                                    "alert": {"on": True, "repeat": True, "every_min": 5}})
    assert put.status_code == 200, put.text
    line = next(x for x in client.get("/api/alerts").json() if x["kind"] == "line")
    assert line["repeat"] is True and line["params"]["every_min"] == 5
    for bad in ({"every_min": 10}, {"repeat": False}, {"repeat": True}):   # the drawing would say otherwise
        r = client.patch(f"/api/alerts/{line['id']}", json=bad)
        assert r.status_code == 400 and "on the line" in r.text, bad
    c2 = client.portal.call(lambda: m.store.update_alert(line["id"], enabled=False, last_fired=123))   # it fired, Only once
    assert c2 is True
    client.portal.call(m.alerts_changed)
    client.put("/api/drawings/ES/ln1", json={"type": "hline", "pts": [{"t": 0, "p": 7000.0}],
                                             "alert": {"on": True, "repeat": True, "every_min": 5}, "style": {"color": "#ff0000"}})
    line = next(x for x in client.get("/api/alerts").json() if x["kind"] == "line")
    assert line["enabled"] is False                                     # its color changed: still stopped
    client.put("/api/drawings/ES/ln1", json={"type": "hline", "pts": [{"t": 0, "p": 7000.0}],
                                             "alert": {"on": True, "repeat": True, "every_min": 60}})
    line = next(x for x in client.get("/api/alerts").json() if x["kind"] == "line")
    assert line["params"]["every_min"] == 60 and line["enabled"] is True   # how often changed on the line: restarted,
    assert line["last_fired"] == 123                                    # as the dialog's Save restarts one
    assert client.portal.call(lambda: m.alerts.waits.get((line["id"], AFRESH))) == 123   # ...afresh: no wait from it
    client.put("/api/drawings/ES/ln1", json={"type": "hline", "pts": [{"t": 0, "p": 7001.0}],
                                             "alert": {"on": True, "repeat": False, "every_min": 60}})
    line = next(x for x in client.get("/api/alerts").json() if x["kind"] == "line")
    assert line["enabled"] is True and line["repeat"] is False and "every_min" not in line["params"]   # moved: re-armed


def test_api_patches_the_re_arm_distance(client):
    """The re-arm distance was set at create only: PATCH dropped rearm_ticks without a word, so an alert's could never
    be changed (the dialog's Save could not send it). Now it is saved, for level, study and If/Then alerts; others
    refuse it."""
    for kind, extra in (("level", {}), ("study", {"study": "poc"}), ("ifthen", {})):
        a = client.post("/api/alerts", json={"symbol": "ES", "kind": kind, **extra}).json()
        assert a["params"]["rearm_ticks"] == 8
        b = client.patch(f"/api/alerts/{a['id']}", json={"rearm_ticks": 40})
        assert b.status_code == 200 and b.json()["params"]["rearm_ticks"] == 40, (kind, b.text)
        assert client.patch(f"/api/alerts/{a['id']}", json={"rearm_ticks": 0}).status_code == 422
    p = client.post("/api/alerts", json={"symbol": "ES", "kind": "price", "price": 7000}).json()
    assert client.patch(f"/api/alerts/{p['id']}", json={"rearm_ticks": 12}).status_code == 400


def test_api_keeps_an_old_alert_as_it_was(client):
    """An alert saved before there was a choice (no every_min) reads and runs as it did; an unrelated edit adds none."""
    m = client.app.state.market
    i = client.portal.call(lambda: m.store.add_alert("ES", "price", {"price": 7000.0, "direction": "gt"}, True, ""))
    client.portal.call(m.alerts_changed)
    a = client.patch(f"/api/alerts/{i}", json={"note": "x", "expires": int(time.time() * 1000) + 86_400_000}).json()
    assert a["repeat"] is True and set(a["params"]) == {"price", "direction", "expires"}


# ---- the page: the Alerts tab's text, the dialog's Trigger, what Save sends -------------------------------------------
LIST_JS = """
const S = { holidays: [], instruments: { ES: {} } }, OPS = { cross: 'crossing', gt: 'greater than' }, CHANNEL = new Set();
const fmtSym = (s, v) => Number(v).toFixed(2), indLineName = (x) => x.ind, studyName = (p) => p.study;
const ifRowsText = () => 'all rows', drawingLinePrice = () => null, ax = { full: { format: () => '' } }, zs = () => '';
/*APP*/
const A = (kind, repeat, params) => ({ symbol: 'ES', kind, repeat, params, note: '' });
console.log(JSON.stringify([
  A('price', false, { price: 1, direction: 'cross' }), A('price', true, { price: 1, direction: 'gt' }),
  A('price', true, { price: 1, direction: 'gt', every_min: 5 }), A('level', true, { levels: 'all', rearm_ticks: 40, every_min: 60 }),
  A('ifthen', true, { rows: 'all', rearm_ticks: 8, every_min: 1 }), A('study', false, { study: 'vwap', rearm_ticks: 8 }),
  A('study', true, { study: 'vwap' }), A('ind', true, { a: { ind: 'ema' }, b: { ind: 'price' }, spec: '5m', direction: 'cross', every_min: 15 }),
  A('ind', true, { a: { ind: 'ema' }, b: { ind: 'price' }, spec: '5m', direction: 'cross' }),
  A('line', true, { type: 'hline', direction: 'cross', every_min: 1440 }), A('price', false, { price: 1, direction: 'gt', every_min: 5 }),
  A('level', true, { levels: 'all', rearm_ticks: 1 }),
].map((a) => describe(a)[1])));
"""


@needs_node
def test_the_alerts_tab_says_how_often_and_how_far_it_re_arms():
    out = run_app(["describe", "REARMS", "ticksText", "everyText", "trigText"], LIST_JS)
    assert out == ["Only once · open-ended", "Every time · open-ended", "Once every 5 min · open-ended",
                   "Once an hour · re-arms 40 ticks away · open-ended", "Once a minute · re-arms 8 ticks away · open-ended",
                   "Only once · open-ended", "Every time · re-arms 8 ticks away · open-ended",
                   "Once a bar, at most once every 15 min · open-ended", "Once a bar · open-ended",
                   "Once a day · open-ended", "Only once · open-ended", "Every time · re-arms 1 tick away · open-ended"]


DIALOG_JS = """
const els = {}, toasts = [], calls = [];
const $ = (q) => (els[q] ||= { value: '', hidden: false, textContent: '', checked: false, disabled: false, focus() {}, select() {},
  querySelector: () => null });
const S = { rth: false, instruments: { ES: {} } }, CHANNEL = new Set(['enter', 'exit']);
const toast = (m) => toasts.push(m), fail = (e) => toasts.push(`fail ${e.message}`), num = Number, snap = (v) => v;
const ax = { input: { toMs: () => 0 }, short: 'CT' }, describe = (a) => [`made ${a.id}`], closeAlertDialog = () => {};
const ifDlgRows = () => 'all', indDlgBody = (b) => b;
const api = async (method, path, body) => { calls.push({ method, path, body }); return { id: 7 }; };
/*APP*/
/*HANDLERS*/
/*FORM*/
const look = () => ({ trig: $('#al-trig').value, box: !$('#al-every-box').hidden, every: $('#al-every').value,
  rearm: !$('#al-rearm-row').hidden, hint: $('#al-trig-hint').textContent, state: [dlg.repeat, dlg.every] });
(async () => {
  const out = [];
  $('#al-src').value = 'price'; $('#al-rearm').value = '8';
  setTrig(false); out.push(look());
  setTrig(true, 0); out.push(look());
  $('#al-trig').value = '5'; $('#al-trig').onchange(); out.push(look());
  $('#al-src').value = 'level'; $('#al-rearm').value = '12';
  $('#al-trig').value = 'custom'; $('#al-every').value = ''; $('#al-trig').onchange(); out.push(look());
  out.push(dlgBody(), toasts.splice(0));                      // not typed yet: not saved
  for (const v of ['0', '2.5', '2000', '45']) { $('#al-every').value = v; $('#al-every').oninput(); out.push(dlg.every); }
  out.push(look());
  $('#al-rearm').value = '20'; $('#al-rearm').oninput(); out.push(look().hint);
  $('#al-src').value = 'ifthen'; setTrig(true, 60); out.push(look());
  $('#al-src').value = 'i:ema'; setTrig(true, 15); out.push(look());
  $('#al-src').value = 'study'; setTrig(true, 0); out.push(look());

  // Save: a new Daily levels alert, once every 45 minutes, re-armed 12 ticks away
  $('#al-src').value = 'level'; $('#al-tol').value = '1'; $('#al-rearm').value = '12'; $('#al-msg').value = '';
  Object.assign(dlg, { edit: null, sym: 'NQ' }); setTrig(true, 45);
  await $('#al-form').onsubmit({ preventDefault() {} });
  // ...an edit of one that waited 5 minutes, to Every time, then to Only once; one with no wait, kept so
  const was = { id: 3, kind: 'level', symbol: 'NQ', repeat: true, params: { levels: 'all', tolerance_ticks: 1, rearm_ticks: 8, every_min: 5 } };
  for (const [repeat, every, a] of [[true, 0, was], [false, 0, was], [true, 0, { ...was, params: { levels: 'all', rearm_ticks: 8 } }]]) {
    Object.assign(dlg, { edit: a }); setTrig(repeat, every);
    await $('#al-form').onsubmit({ preventDefault() {} });
  }
  out.push(calls);
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_the_dialogs_trigger_picks_once_every_time_or_a_wait():
    """Only once, Every time, the list's waits, or minutes typed (1 to 1440, whole: anything else is not saved). The
    re-arm field shows for levels, studies and If/Then when they repeat, and the hint says the alert's own distance
    (it said 8 ticks whatever the alert's was). Save sends every_min and rearm_ticks; an edit that drops the wait sends
    every_min 0, one that never had one sends none."""
    src = DIALOG_JS.replace("/*HANDLERS*/", app_lines("$('#al-trig').onchange", "$('#al-rearm').oninput")).replace(
        "/*FORM*/", app_lines("$('#al-form').onsubmit", "};"))
    out = run_app(["dlg", "EVERY_PRESETS", "perText", "ticksText", "setTrig", "trigOne", "trigList", "dlgTicks", "dlgBody",
                   "sameAlert"], src)
    once, every_time, five, custom, body, toasts, *typed = out[:10]
    assert once == {"trig": "once", "box": False, "every": "", "rearm": False, "hint": "Fires once, then stops.",
                    "state": [False, 0]}
    assert every_time["trig"] == "repeat" and every_time["hint"].endswith("at most once a minute.")
    assert five["trig"] == "5" and five["state"] == [True, 5] and five["hint"].endswith("at most once every 5 minutes.")
    assert custom["trig"] == "custom" and custom["box"] and custom["rearm"] and custom["state"] == [True, None]   # NaN
    assert custom["hint"].startswith("Type the minutes") and body is None and toasts and "1 to 1440" in toasts[0]
    assert typed[:4] == [None, None, None, 45]
    after, hint20, ifthen, ind, study, calls = out[10:]
    assert after["trig"] == "custom" and after["box"] and after["state"] == [True, 45]
    assert "once every 45 minutes" in after["hint"] and "12 ticks" in after["hint"] and "other levels" in after["hint"]
    assert "20 ticks" in hint20
    assert ifthen["trig"] == "60" and "once an hour" in ifthen["hint"] and "other rows" in ifthen["hint"]
    assert ind["trig"] == "15" and not ind["rearm"] and ind["hint"].endswith("at most once every 15 minutes.")
    assert study["trig"] == "repeat" and study["rearm"] and study["hint"] == "Fires on each touch, again after price has moved 20 ticks away."
    new, to_every_time, to_once, kept = calls
    assert new == {"method": "POST", "path": "/api/alerts", "body": {"symbol": "NQ", "repeat": True, "note": "", "every_min": 45,
                                                                    "tolerance_ticks": 1, "rearm_ticks": 12, "kind": "level"}}
    base = {"enabled": True, "note": "", "expires": 0, "tolerance_ticks": 1, "rearm_ticks": 12}
    assert to_every_time == {"method": "PATCH", "path": "/api/alerts/3", "body": {**base, "repeat": True, "every_min": 0}}
    assert to_once == {"method": "PATCH", "path": "/api/alerts/3", "body": {**base, "repeat": False, "every_min": 0}}
    assert kept == {"method": "PATCH", "path": "/api/alerts/3", "body": {**base, "repeat": True}}


@needs_node
def test_an_indicator_alerts_wait_is_its_trigger_not_another_alert():
    """Save on an indicator alert whose condition is as it was PATCHes it (it keeps what it saw): its wait changed
    too, it is still the same alert."""
    out = run_app(["same", "indLineNew", "sameAlert"], """
/*APP*/
const p = { a: { ind: 'ema', line: 'v', len: 9 }, b: { ind: 'price' }, spec: '5m', on: 'close', direction: 'cross' };
const b = { kind: 'ind', ...p, every_min: 15 };
console.log(JSON.stringify([sameAlert({ kind: 'ind', params: { ...p, every_min: 5, expires: 1 } }, b),
  sameAlert({ kind: 'ind', params: { ...p, spec: '1m', every_min: 5 } }, b)]));""")
    assert out == [True, False]


HIDDEN_JS = """
const els = {}, toasts = [];
const blank = () => ({ value: '', hidden: false, textContent: '', checked: false, disabled: false, focus() {}, select() {},
  querySelector: () => null });
// the Trigger list, as a select: its options can be taken out and put back (trigList)
const kids = [], sel = { ...blank(), kids, querySelector: (q) => kids.find((o) => o.value === q.split('"')[1]) || null,
  insertBefore(o, ref) { kids.splice(kids.indexOf(ref), 0, o); o.parentNode = sel; } };
for (const v of ['once', 'repeat', '1', '5', '15', '30', '60', 'custom']) {
  kids.push({ value: v, parentNode: sel, remove() { const i = kids.indexOf(this); if (i >= 0) kids.splice(i, 1); this.parentNode = null; } });
}
els['#al-trig'] = sel;
const $ = (q) => (els[q] ||= blank());
const S = { rth: false, instruments: { ES: {} } }, CHANNEL = new Set(['enter', 'exit']);
const toast = (m) => toasts.push(m), num = Number, snap = (v) => v;
const ax = { input: { toMs: () => 0 }, short: 'CT' }, ifDlgRows = () => 'all', indDlgBody = (b) => b;
/*APP*/
/*HANDLERS*/
const body = () => { const b = dlgBody(); return [b && [b.every_min ?? null, b.rearm_ticks, b.tolerance_ticks], toasts.splice(0)]; };
const out = { minutes: [], rearm: [], hints: [], list: [] };
Object.assign(dlg, { edit: null, sym: 'ES' });
$('#al-src').value = 'level'; $('#al-tol').value = '0'; $('#al-rearm').value = '8';
// 0 (or 1441, 2.5) typed in the minutes, then another Trigger picked: the box is hidden, and its number is no longer
// the alert's, so Save goes through (the browser blocked it, with nowhere to say why: the form is novalidate now)
for (const [typed, pick] of [['0', 'repeat'], ['1441', '15'], ['2.5', 'once']]) {
  $('#al-trig').value = 'custom'; $('#al-trig').onchange(); $('#al-every').value = typed; $('#al-every').oninput();
  out.minutes.push([body()[0], $('#al-trig').value]);
  $('#al-trig').value = pick; $('#al-trig').onchange();
  out.minutes.push([$('#al-every-box').hidden, ...body()]);
}
// Re-arm after 0: shown (it repeats), the hint and Save say so; hidden (Only once), Save sends the alert's own
$('#al-trig').value = 'repeat'; $('#al-trig').onchange(); $('#al-rearm').value = '0'; $('#al-rearm').oninput();
out.rearm.push([$('#al-rearm-row').hidden, $('#al-trig-hint').textContent, ...body()]);
$('#al-trig').value = 'once'; $('#al-trig').onchange();
out.rearm.push([$('#al-rearm-row').hidden, ...body()]);
dlg.edit = { id: 3, kind: 'level', params: { levels: 'all', rearm_ticks: 12 } };
out.rearm.push(body());
dlg.edit = null;
$('#al-trig').value = 'repeat'; $('#al-trig').onchange(); $('#al-rearm').value = '8';
for (const t of ['500', '2.5', '-1', '']) { $('#al-tol').value = t; out.rearm.push(body()); }
$('#al-tol').value = '0';
// the hint says what Save would send
for (const v of ['1', '-5', '2001', '3.5', '0', '', '12']) { $('#al-rearm').value = v; $('#al-rearm').oninput(); out.hints.push($('#al-trig-hint').textContent); }
$('#al-rearm').value = '8';
// a price alert's list has no Once a minute (its Every time is that); a level's has
const list = () => kids.map((o) => o.value).join(' ');
$('#al-src').value = 'level'; setTrig(true, 1); out.list.push([list(), sel.value, dlg.every]);
$('#al-src').value = 'price'; setTrig(true, 1); out.list.push([list(), sel.value, dlg.every, $('#al-trig-hint').textContent]);
setTrig(true, 1, true); out.list.push([sel.value, dlg.every, $('#al-every-box').hidden]);   // typed 1: as typed
$('#al-src').value = 'study'; setTrig(true, 5); out.list.push([list(), sel.value]);
console.log(JSON.stringify(out));
"""


@needs_node
def test_a_number_left_in_a_field_the_dialog_hides_never_blocks_save():
    """Pick Daily levels, Once every ... min, type 0, then Every time: every field shown was right, and Create did
    nothing, with no word (the browser checked the hidden box's min="1" and had nowhere to say so). The same with 0 in
    Re-arm after, then Only once. The form is novalidate now and Save checks the fields it shows, with a toast: Re-arm
    after and Touch within too (their wrong numbers were cut to the range without a word). The hint says the distance
    Save would send ("1 tick", not "-5 ticks", "3 ticks" for 3.5, or 8 for 0)."""
    form = next(line for line in (WEB / "index.html").read_text().split("\n") if 'id="al-form"' in line)
    assert "novalidate" in form
    src = HIDDEN_JS.replace("/*HANDLERS*/", app_lines("$('#al-trig').onchange", "$('#al-rearm').oninput"))
    out = run_app(["dlg", "EVERY_PRESETS", "perText", "ticksText", "setTrig", "trigOne", "trigList", "dlgTicks", "dlgBody"],
                  src)
    assert out["minutes"] == [
        [None, "custom"], [True, [None, 8, 0], []],
        [None, "custom"], [True, [15, 8, 0], []],
        [None, "custom"], [True, [None, 8, 0], []]]
    shown, hidden, own, *tols = out["rearm"]
    assert shown[0] is False and shown[1].startswith("Type how far price must move away") and shown[2] is None
    assert shown[3] == ["Re-arm after: a whole number of ticks from 1 to 2000"]
    assert hidden == [True, [None, 8, 0], []] and own == [[None, 12, 0], []]
    assert tols[:3] == [[None, ["Touch within: a whole number of ticks from 0 to 200"]]] * 3 and tols[3] == [[None, 8, 0], []]
    one, *bad, empty, twelve = out["hints"]
    assert one == "Fires on each touch, again after price has moved 1 tick away."
    assert all(h.startswith("Type how far price must move away") for h in bad) and len(bad) == 4
    assert empty.endswith("moved 8 ticks away.") and twelve.endswith("moved 12 ticks away.")
    level, price, typed, study = out["list"]
    assert level == ["once repeat 1 5 15 30 60 custom", "1", 1]
    assert price == ["once repeat 5 15 30 60 custom", "repeat", 0, "Fires each time the condition is met, at most once a minute."]
    assert typed == ["custom", 1, False]
    assert study == ["once repeat 1 5 15 30 60 custom", "5"]


def test_a_drawn_lines_list_has_no_once_a_minute():
    """A line alerts as a price alert does: every time is at most once a minute already."""
    js = (WEB / "drawings.js").read_text()
    freq = js[js.index("const FREQ = "):js.index("];", js.index("const FREQ = "))]
    assert "'repeat', 'every time'" in freq and "once a minute" not in freq and "'5', 'every 5 min'" in freq
