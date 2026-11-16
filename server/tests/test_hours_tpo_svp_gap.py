"""TPO Profile, Gap Analysis and Session Volume Profile 1.1.0: Use Instrument Hours (UseInstrumentHours), as the
NinjaTrader originals have it since their v1.1.0, with the AMT Toolkit's name: on by default, their session times move
from ES's 08:30-15:00 CT to each instrument's own regular hours; off, every time runs as typed on every instrument (before,
this app always moved them). Also the parity audit's other gaps closed here: the AMT Toolkit's IB Stop Time and Delete OVN
Lines At at NinjaTrader's defaults (its OVN alerts end at the RTH close), SVP keeping a holiday halt's own window and the
one opening on its evening, SVP's label showing the window it runs, and the Level Lines 1.1.1 and PVP 1.0.1 labels.
SVP's window moves in every time zone it offers, each date's at that date's offset from CT (NinjaTrader's ZoneWindows).

With TATANKA_INDICATORS set to a tatanka-indicators checkout, the schema is checked against its sources (the repo's own
generators), its three hours tables against config/futures.json, and, with mono, the TPO, Gap and SVP parity harnesses
are built from it and run with the setting on and off."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from chartsrv import amt, amt_hub, gaps, sessions, tpo
from chartsrv.config import Instrument, futures_catalog, load_holidays
from chartsrv.gaps_service import GapWatch, check_hours, own_hours as gap_hours
from chartsrv.indicators import SCHEMA, by_name, clean
from chartsrv.models import Bar
from chartsrv.store import Store
from chartsrv.tpo_service import own_hours as tpo_hours
from chartsrv.vp_service import VpService, ZoneWindows, svp_hours, svp_zone_windows

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent
ROOT = SERVER.parent
CONFIG = ROOT / "config"
WEB = ROOT / "web"
CL = Instrument("CL", "Crude", 0.01, 1000, "CL", rth=("08:00", "13:30"), calendar="energy")
GC = Instrument("GC", "Gold", 0.10, 100, "GC", rth=("07:20", "12:30"), calendar="metals")
SI = ("07:25", "12:25")
ES = Instrument("ES", "E-mini", 0.25, 50, "ES", rth=("08:30", "15:00"), calendar="equity")
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_indicators = pytest.mark.skipif(not INDICATORS, reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS)")
needs_mono = pytest.mark.skipif(not (INDICATORS and shutil.which("mcs") and shutil.which("mono")),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS) and mono")
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")
SETTING = {"tpo": ("Session", 3), "gap": ("01. Gap Settings", 3), "prof": ("01. Session", 3)}
FILES = {"tpo": "TatankaTpoProfile.cs", "gap": "TatankaGapAnalysis.cs", "prof": "TatankaSessionVolumeProfile.cs"}


# ---- the setting ----------------------------------------------------------------------------------------------------

def test_use_instrument_hours_is_a_setting_of_each_on_by_default():
    """NinjaTrader's name, group and order, right after the session times, on by default; only off is stored."""
    for key, (group, order) in SETTING.items():
        spec = by_name(key)["UseInstrumentHours"]
        assert (spec["group"], spec["order"], spec["label"], spec["type"], spec["default"]) == \
            (group, order, "Use Instrument Hours", "bool", True), key
        assert spec["description"].endswith("Off: as typed on every market."), key
        assert clean(key, {"UseInstrumentHours": False}) == {"UseInstrumentHours": False} and clean(key, {"UseInstrumentHours": True}) == {}
        with pytest.raises(ValueError):
            clean(key, {"UseInstrumentHours": 1})
    assert tpo.DEFAULTS["UseInstrumentHours"] is True and gaps.DEFAULTS["UseInstrumentHours"] is True
    assert tpo.settings({"UseInstrumentHours": False})["UseInstrumentHours"] is False
    for bad in (1, "false", None):
        with pytest.raises(ValueError):
            tpo.settings({"UseInstrumentHours": bad})
        with pytest.raises(ValueError):
            gaps.settings({"UseInstrumentHours": bad})


def test_the_versions_are_the_ninjatrader_ones():
    """TPO, Gap and SVP 1.1.0 (the setting), Level Lines 1.1.1 and PVP 1.0.1 (their behaviour was ported already; the
    labels said 1.1.0 and 1.0.0)."""
    got = {k: SCHEMA[k]["version"] for k in ("tpo", "gap", "prof", "lines", "pvp", "aavp")}
    assert got == {"tpo": "1.1.0", "gap": "1.1.0", "prof": "1.1.0", "lines": "1.1.1", "pvp": "1.0.1", "aavp": "1.1.0"}
    assert tpo.VERSION == gaps.VERSION == "1.1.0"


# ---- TPO --------------------------------------------------------------------------------------------------------------

def test_tpo_session_time_moves_only_with_it_on():
    s = tpo.settings({})
    assert [tpo_hours(s, r)["SessionTime"] for r in (GC.rth, CL.rth, SI, ES.rth, None)] == \
        ["0720-1230", "0800-1330", "0725-1225", "0830-1500", "0830-1500"]
    assert tpo_hours({**s, "SessionTime": "1100-1200"}, CL.rth)["SessionTime"] == "1030-1130"   # as a whole
    off = tpo.settings({"UseInstrumentHours": False})
    assert all(tpo_hours(off, r)["SessionTime"] == "0830-1500" for r in (GC.rth, CL.rth, SI))
    assert tpo_hours({**off, "SessionTime": "0720-1230"}, GC.rth)["SessionTime"] == "0720-1230"   # gold's, typed


def test_a_tpo_session_time_the_profile_cant_read_doesnt_move():
    """One the profile reads no session from filters nothing on ES; it stays so on GC (it was moved into a real session:
    0860-1500 ran 07:50-12:30, 0899-1500 08:29-12:30), as NinjaTrader's RunSessionTime moves only what ParseSession
    reads."""
    s = tpo.settings({})
    for typed in ("0860-1500", "0899-1500", "2400-1500", "0830-1500:1"):
        assert not tpo.TpoProfile.ParseSession(typed)[0]
        assert tpo_hours({**s, "SessionTime": typed}, GC.rth)["SessionTime"] == typed
    assert tpo_hours({**s, "SessionTime": " 830 - 1500 "}, GC.rth)["SessionTime"] == "0720-1230"   # it reads this one


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from chartsrv.app import app
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_FEED", "none")
    monkeypatch.setenv("CHART_QUOTES_FEED", "none")
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    return TestClient(app)


def test_the_tpo_endpoint_runs_the_session_the_setting_says(tmp_path, monkeypatch):
    """/api/tpo on GC: 0720-1230 by default, 0830-1500 with UseInstrumentHours=false (before: always 0720-1230)."""
    with _app(tmp_path, monkeypatch) as c:
        m = c.app.state.market
        ran = []

        async def get(sym, minutes, s, rth=False, until=None):
            ran.append(s["SessionTime"])
            return {"profiles": []}
        monkeypatch.setattr(m.tpo, "get", get)
        for q in ({}, {"UseInstrumentHours": "false"}, {"UseInstrumentHours": "true", "SessionTime": "0830-1000"}):
            assert c.get("/api/tpo", params={"symbol": "GC", "spec": "5m", **q}).status_code == 200
        assert c.get("/api/tpo", params={"symbol": "ES", "spec": "5m"}).status_code == 200
    assert ran == ["0720-1230", "0830-1500", "0720-0850", "0830-1500"]


# ---- Gap Analysis -----------------------------------------------------------------------------------------------------

MIDDAY = {"RthStart": 110000, "RthEnd": 120000, "MinimumGapPoints": 0.0}   # 10:30-10:30 on CL with it on


def test_gap_times_move_only_with_it_on():
    s = gaps.settings({})
    assert [(x["RthStart"], x["RthEnd"]) for x in (gap_hours(s, GC.rth), gap_hours(s, CL.rth), gap_hours(s, SI))] == \
        [(72000, 123000), (80000, 133000), (72500, 122500)]
    off = gaps.settings({"UseInstrumentHours": False})
    assert (gap_hours(off, CL.rth)["RthStart"], gap_hours(off, CL.rth)["RthEnd"]) == (83000, 150000)
    # times its hours make no session of are refused with it on only, and the message says what to do
    with pytest.raises(ValueError, match=r"no session on CL: .* same time\. Enter other times, or turn Use Instrument Hours off\."):
        check_hours(gaps.settings(MIDDAY), {"CL": CL})
    check_hours(gaps.settings({**MIDDAY, "UseInstrumentHours": False}), {"CL": CL, "GC": GC})
    # an alert config: off runs CL's alerts on 08:30-15:00; one stored before the setting has it on
    off_w = GapWatch({"on": True, "RthStart": 83000, "RthEnd": 150000, "MinimumGapPoints": 0.0, "UseInstrumentHours": False}, CL.rth)
    old_w = GapWatch({"on": True, "RthStart": 83000, "RthEnd": 150000, "MinimumGapPoints": 0.0}, CL.rth)
    assert (off_w.engine.rthStart, off_w.engine.rthEnd) == (timedelta(hours=8, minutes=30), timedelta(hours=15))
    assert (old_w.engine.rthStart, old_w.engine.rthEnd) == (timedelta(hours=8), timedelta(hours=13, minutes=30))


def test_the_gap_endpoints_take_the_setting(tmp_path, monkeypatch):
    """/api/gaps runs the times as typed with UseInstrumentHours=false (its hours say so), and refuses CL's 11:00-12:00
    with it on only; the alerts take it too, from switching them on and from a chart's saved settings."""
    with _app(tmp_path, monkeypatch) as c:
        q = {"symbol": "CL", "spec": "5m"}
        assert c.get("/api/gaps", params=q).json()["hours"] == "0800-1330"
        assert c.get("/api/gaps", params={**q, "UseInstrumentHours": "false"}).json()["hours"] == "0830-1500"
        r = c.get("/api/gaps", params={**q, **MIDDAY})
        assert r.status_code == 400 and "turn Use Instrument Hours off" in r.json()["detail"], r.text
        assert c.get("/api/gaps", params={**q, **MIDDAY, "UseInstrumentHours": "false"}).json()["hours"] == "1100-1200"
        r = c.put("/api/gaps/alerts/CL", json={"on": True, **MIDDAY})
        assert r.status_code == 400, r.text
        r = c.put("/api/gaps/alerts/CL", json={"on": True, **MIDDAY, "UseInstrumentHours": False})
        assert r.status_code == 200 and r.json()["UseInstrumentHours"] is False, r.text
        m = c.app.state.market
        assert (m.gaps.watches["CL"].engine.rthStart, m.gaps.watches["CL"].engine.rthEnd) == (timedelta(hours=11), timedelta(hours=12))
        r = c.put("/api/gaps/alerts", params={"symbol": "CL"}, json={"UseInstrumentHours": True, "RthStart": 83000, "RthEnd": 150000})
        assert r.status_code == 200, r.text
        cfg = c.get("/api/gaps/alerts", params={"symbol": "CL"}).json()["alerts"]["CL"]
        assert cfg["UseInstrumentHours"] is True and cfg["on"] is True
        assert m.gaps.watches["CL"].engine.rthStart == timedelta(hours=8)


def test_use_instrument_hours_alone_cant_make_a_symbols_alert_times_no_session(tmp_path, monkeypatch):
    """CL's alerts on 11:00-12:00 with the setting off: turning it on alone (the API, for CL or every symbol) is refused,
    as those times are no session on CL with it on, and nothing is stored (it was taken, and the alerts went off with an
    error). A body that leaves the hours alone (an alert switch) still goes through."""
    with _app(tmp_path, monkeypatch) as c:
        r = c.put("/api/gaps/alerts/CL", json={"on": True, **MIDDAY, "UseInstrumentHours": False})
        assert r.status_code == 200, r.text
        before = c.get("/api/gaps/alerts", params={"symbol": "CL"}).json()["alerts"]["CL"]
        for params in ({"symbol": "CL"}, {}):
            r = c.put("/api/gaps/alerts", params=params, json={"UseInstrumentHours": True})
            assert r.status_code == 400 and "no session on CL" in r.json()["detail"], r.text
            assert c.get("/api/gaps/alerts", params={"symbol": "CL"}).json()["alerts"]["CL"] == before
        assert c.put("/api/gaps/alerts", params={"symbol": "CL"}, json={"new": False}).status_code == 200
        r = c.put("/api/gaps/alerts", params={"symbol": "CL"}, json={"UseInstrumentHours": True, "RthStart": 83000, "RthEnd": 150000})
        assert r.status_code == 200, r.text
        cfg = c.get("/api/gaps/alerts", params={"symbol": "CL"}).json()["alerts"]["CL"]
        assert (cfg["on"], cfg["UseInstrumentHours"], cfg["RthStart"]) == (True, True, 83000)


# ---- Session Volume Profile -------------------------------------------------------------------------------------------

def test_svp_window_moves_only_with_it_on():
    """In CT (ExchangeTemplate, Central, Chart) the typed window moves as a whole (svp_hours); in Eastern and UTC each
    date's does (svp_zone_windows, below), so svp_hours leaves them as typed."""
    base = {"SessionStart": "08:30", "SessionEnd": "15:00", "SessionTimeZone": "ExchangeTemplate", "UseInstrumentHours": True}
    win = lambda s, rth: f"{svp_hours(s, rth)['SessionStart']}-{svp_hours(s, rth)['SessionEnd']}"   # noqa: E731
    assert [win(base, r) for r in (GC.rth, CL.rth, SI, ES.rth)] == ["07:20-12:30", "08:00-13:30", "07:25-12:25", "08:30-15:00"]
    assert win({**base, "SessionTimeZone": "Central"}, GC.rth) == win({**base, "SessionTimeZone": "Chart"}, GC.rth) == "07:20-12:30"
    assert win({**base, "SessionTimeZone": "Eastern"}, GC.rth) == win({**base, "SessionTimeZone": "UTC"}, GC.rth) == "08:30-15:00"
    assert win({**base, "UseInstrumentHours": False}, GC.rth) == "08:30-15:00"
    for zone in ("ExchangeTemplate", "Central", "Chart"):   # CT: no windows per date
        assert svp_zone_windows({**base, "SessionTimeZone": zone}, GC.rth) is None
    for zone in ("Eastern", "UTC"):   # as typed with it off, and on an instrument with ES's hours
        assert svp_zone_windows({**base, "SessionTimeZone": zone, "UseInstrumentHours": False}, GC.rth) is None
        assert svp_zone_windows({**base, "SessionTimeZone": zone}, ES.rth) is None
    # every time SVP takes moves (before: only "HH:mm"; "8:30" stayed and its end moved alone), seconds kept
    assert win({**base, "SessionStart": "8:30", "SessionEnd": "15:00:30"}, GC.rth) == "07:20-12:30:30"
    assert win({**base, "SessionStart": "11:00", "SessionEnd": "12:00"}, CL.rth) == "10:30-11:30"
    assert win({**base, "SessionStart": "17:00", "SessionEnd": "16:00"}, GC.rth) == "17:00-16:00"


def _label(zw: ZoneWindows, d: date) -> str:
    return zw.label(*zw.window(d))


def test_svp_window_outside_ct_moves_each_date_at_its_offset():
    """Eastern and UTC (and any zone, for the parity harness): each date's typed window is put in CT at that date's offset
    from it, moved there and put back. Eastern is always an hour from CT: ES's hours, 09:30-16:00 there, run 08:20-13:30
    on GC all year. UTC has no daylight saving: 14:30-21:00 is 08:30-15:00 CST (13:20-18:30 on GC) and 09:30-16:00 CDT
    (13:20-21:00), date for date; London's offset is 5 hours in the week its daylight saving has ended and Chicago's
    hasn't. The NinjaTrader Session Volume Profile 1.1.0's ZoneWindows."""
    s = {"SessionStart": "09:30", "SessionEnd": "16:00", "SessionTimeZone": "Eastern", "UseInstrumentHours": True}
    et = svp_zone_windows(s, GC.rth)
    assert [_label(et, d) for d in (date(2026, 7, 7), date(2026, 11, 3), date(2027, 1, 5))] == ["08:20-13:30"] * 3
    assert et.window(date(2026, 11, 3)) == (datetime(2026, 11, 3, 8, 20), datetime(2026, 11, 3, 13, 30))
    assert _label(svp_zone_windows({**s, "SessionStart": "12:00", "SessionEnd": "13:00"}, GC.rth), date(2026, 11, 3)) == "10:50-11:50"
    assert _label(svp_zone_windows({**s, "SessionStart": "9:30", "SessionEnd": "16:00:30"}, CL.rth), date(2026, 11, 3)) == "09:00-14:30:30"
    utc = svp_zone_windows({**s, "SessionTimeZone": "UTC", "SessionStart": "14:30", "SessionEnd": "21:00"}, GC.rth)
    assert [_label(utc, date(2026, 10, d)) for d in (29, 30)] + [_label(utc, date(2026, 11, d)) for d in (2, 3)] == \
        ["13:20-21:00"] * 2 + ["13:20-18:30"] * 2
    london = ZoneWindows(timedelta(hours=14, minutes=30), timedelta(hours=21), sessions.rth_mover(GC.rth), ZoneInfo("Europe/London"))
    assert [_label(london, date(2026, 10, d)) for d in (23, 26, 30)] + [_label(london, date(2026, 11, 2))] == \
        ["13:20-18:30", "13:20-21:00", "13:20-21:00", "13:20-18:30"]
    # a start moved back past midnight opens the window the evening before: Tokyo's 00:30-06:00 (09:30-15:00 CST) runs
    # 23:20-03:30 on GC; a time in it finds the next date's window
    tokyo = ZoneWindows(timedelta(minutes=30), timedelta(hours=6), sessions.rth_mover(GC.rth), ZoneInfo("Asia/Tokyo"))
    assert tokyo.window(date(2026, 11, 4)) == (datetime(2026, 11, 3, 23, 20), datetime(2026, 11, 4, 3, 30))
    assert tokyo.try_window(datetime(2026, 11, 3, 23, 25)) == tokyo.try_window(datetime(2026, 11, 4, 3, 29)) == tokyo.window(date(2026, 11, 4))
    assert tokyo.try_window(datetime(2026, 11, 4, 3, 30)) is None and tokyo.try_window(datetime(2026, 11, 3, 23, 19)) is None


def ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=sessions.CT).timestamp() * 1000)


def test_svp_sessions_in_utc_follow_chicagos_daylight_saving(tmp_path):
    """The UTC window each date runs, as /api/vp/prof reads trades and bars: 13:20-21:00 UTC while Chicago is on CDT,
    13:20-18:30 from the Sunday it ends (November 1, 2026), for 14:30-21:00 typed. Before, every date ran as typed."""
    from chartsrv.indicators import defaults
    sessions.set_calendar(load_holidays(CONFIG))
    store = Store(tmp_path / "t.db")
    store.insert_bars_if_missing("GC", [Bar(t=t, o=3500, h=3501, l=3499, c=3500, v=10)
                                        for t in range(ms(2026, 10, 25, 17), ms(2026, 11, 4, 16), 10 * 60_000)
                                        if sessions.is_open(t, "metals")])
    svc = VpService.__new__(VpService)
    svc.m = SimpleNamespace(store=store, instruments={"GC": SimpleNamespace(calendar="metals", rth=GC.rth)})
    s = {**defaults("prof"), "SessionTimeZone": "UTC", "SessionStart": "14:30", "SessionEnd": "21:00", "SessionsToKeep": 8}
    utc = ZoneInfo("UTC")
    run = lambda zw: [f"{datetime.fromtimestamp(b / 1000, utc):%a %d %H:%M}-{datetime.fromtimestamp(e / 1000, utc):%H:%M}"   # noqa: E731
                      for b, e in svc._windows("GC", s, ms(2026, 11, 4, 15), zw=zw)]
    assert run(svp_zone_windows(s, GC.rth)) == ["Mon 26 13:20-21:00", "Tue 27 13:20-21:00", "Wed 28 13:20-21:00", "Thu 29 13:20-21:00",
                                               "Fri 30 13:20-21:00", "Mon 02 13:20-18:30", "Tue 03 13:20-18:30", "Wed 04 13:20-18:30"]
    assert run(None) == [f"{d} 14:30-21:00" for d in ("Mon 26", "Tue 27", "Wed 28", "Thu 29", "Fri 30", "Mon 02", "Tue 03", "Wed 04")]


def test_svp_eastern_window_moved_back_past_midnight(tmp_path):
    """_windows starts at tomorrow's window, as ZoneWindows.try_window does, for a start moved back past the zone's
    midnight. With config/futures.json's hours no Eastern or UTC start moves before the zone's midnight (none before
    05:00 CT), so there it never finds one. Hours set in the instrument editor can open at 00:00 CT: on 00:00-06:00
    Eastern's 08:00-17:00 opens at 23:30 the evening before, and the session forming at 23:45 is tomorrow's."""
    from chartsrv.indicators import defaults
    for rth in {tuple(x["rth"]) for x in futures_catalog(CONFIG) if x.get("rth")}:
        for zone in ("Eastern", "UTC"):
            for a in range(0, 1440, 20):
                for b in range(0, 1440, 20):
                    s = {"SessionStart": f"{a // 60:02d}:{a % 60:02d}", "SessionEnd": f"{b // 60:02d}:{b % 60:02d}",
                         "SessionTimeZone": zone, "UseInstrumentHours": True}
                    zw = svp_zone_windows(s, rth)
                    for d in (date(2026, 7, 7), date(2027, 1, 5)) if zw else ():
                        assert zw.window(d)[0] >= datetime(d.year, d.month, d.day), (rth, s, d)
    early = ("00:00", "06:00")
    store = Store(tmp_path / "t.db")
    store.insert_bars_if_missing("XX", [Bar(t=t, o=100, h=101, l=99, c=100, v=10)
                                        for t in range(ms(2026, 11, 2), ms(2026, 11, 3, 22, 45), 10 * 60_000)])
    svc = VpService.__new__(VpService)
    svc.m = SimpleNamespace(store=store, instruments={"XX": SimpleNamespace(calendar="metals", rth=early)})
    s = {**defaults("prof"), "SessionTimeZone": "Eastern", "SessionStart": "08:00", "SessionEnd": "17:00", "SessionsToKeep": 3}
    et = ZoneInfo("America/New_York")
    got = [f"{datetime.fromtimestamp(b / 1000, et):%a %d %H:%M}-{datetime.fromtimestamp(e / 1000, et):%a %d %H:%M}"
           for b, e in svc._windows("XX", s, ms(2026, 11, 3, 22, 45), zw=svp_zone_windows(s, early))]   # 23:45 ET
    assert got == ["Sun 01 23:30-Mon 02 17:00", "Mon 02 23:30-Tue 03 17:00", "Tue 03 23:30-Wed 04 17:00"]


@pytest.fixture
def mlk(tmp_path):
    """A VpService on ES minutes (one every 10 minutes the market is open, holidays applied) around MLK Day 2026, a
    holiday halt (12:00 CT); the clock on Wednesday after it."""
    sessions.set_calendar(load_holidays(CONFIG))
    store = Store(tmp_path / "t.db")
    store.insert_bars_if_missing("ES", [Bar(t=t, o=6800, h=6801, l=6799, c=6800, v=10)
                                        for t in range(ms(2026, 1, 12), ms(2026, 1, 21, 11), 10 * 60_000)
                                        if sessions.is_open(t, "equity")])
    s = VpService.__new__(VpService)
    s.m = SimpleNamespace(store=store, instruments={"ES": SimpleNamespace(calendar="equity", rth=ES.rth)})
    return s


def test_svp_keeps_a_holiday_halts_sessions(mlk):
    """MLK Day's own 08:30-15:00 window holds its morning to the 12:00 halt: a partial profile, as NinjaTrader draws it.
    The 17:00-16:00 window that opens on MLK Day's evening is Tuesday's Globex day: kept. Both were left out."""
    from chartsrv.indicators import defaults
    now = ms(2026, 1, 21, 11)
    rth = mlk._windows("ES", {**defaults("prof"), "SessionsToKeep": 4}, now)
    assert [sessions.to_ct(b).strftime("%a %d %H:%M") for b, _ in rth] == ["Fri 16 08:30", "Mon 19 08:30", "Tue 20 08:30", "Wed 21 08:30"]
    night = mlk._windows("ES", {**defaults("prof"), "SessionStart": "17:00", "SessionEnd": "16:00", "SessionsToKeep": 3}, now)
    assert [sessions.to_ct(b).strftime("%a %d %H:%M") for b, _ in night] == ["Sun 18 17:00", "Mon 19 17:00", "Tue 20 17:00"]


def test_the_svp_answer_and_label_say_the_window_it_ran(tmp_path, monkeypatch, in_session):
    """/api/vp/prof gives the window it ran (its hours): GC's own with the setting on, in CT and in Eastern (ES's 08:30-15:00
    typed there is 07:30-14:00 CT: 06:20-11:30 CT on GC, 07:20-12:30 Eastern); as typed with it off."""
    from test_redesign_rth_studies import scenario
    from chartsrv.indicators import defaults
    Store(tmp_path / "chart.db").write([], [("GC", b) for b in scenario(sessions.to_ct(in_session), 3)])
    with _app(tmp_path, monkeypatch) as c:
        def ran(**s):
            r = c.get("/api/vp/prof", params={"symbol": "GC", "s": json.dumps({**defaults("prof"), **s})})
            assert r.status_code == 200, r.text
            d = r.json()
            return d["hours"], {sessions.to_ct(p["start"]).strftime("%H:%M") for p in d["sessions"]}
        assert ran() == ("07:20-12:30", {"07:20"})
        assert ran(UseInstrumentHours=False) == ("08:30-15:00", {"08:30"})
        assert ran(SessionTimeZone="Eastern") == ("07:20-12:30", {"06:20"})
        assert ran(SessionTimeZone="Eastern", UseInstrumentHours=False) == ("08:30-15:00", {"07:30"})


@needs_node
def test_the_svp_label_shows_the_window_it_ran():
    """web/studies.js vpTitle: the server's window (GC 07:20-12:30), the typed one until it answers, as NinjaTrader's
    DisplayName since SVP 1.1.0."""
    src = (WEB / "studies.js").read_text()
    part = lambda name: src[src.index(f"function {name}("):src.index("\n}\n", src.index(f"function {name}(")) + 3]   # noqa: E731
    pct = re.search(r"^const vpPct = .*$", src, re.M)[0]
    js = pct + "\n" + part("vpLatest") + part("vpTitle") + """
const set = { ShowInputsInLabel: true, ShowValuesInLabel: false, SessionStart: '08:30', SessionEnd: '15:00',
  SessionTimeZone: 'ExchangeTemplate', ValueAreaPercent: 68, Alignment: 'SessionRight' };
const st = (d, error) => ({ kind: 'prof', set, d, error, fmt: String });
console.log(JSON.stringify([vpTitle(st({ hours: '07:20-12:30', sessions: [] })), vpTitle(st(null)), vpTitle(st({ hours: '07:20-12:30' }, 'bad'))]));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["Tatanka SVP (07:20-12:30, ExchangeTemplate, VA 68%, SessionRight)",
                                    "Tatanka SVP (08:30-15:00, ExchangeTemplate, VA 68%, SessionRight)",
                                    "Tatanka SVP (08:30-15:00, ExchangeTemplate, VA 68%, SessionRight)"]


@needs_node
def test_the_page_sends_the_setting_and_shows_the_hours_it_runs():
    """The Gap request and both gap alert PUTs carry UseInstrumentHours; the TPO line shows the session as typed with it
    off, GC's own with it on."""
    from test_hunt_web_chart import run_app
    out = run_app(["ownHours", "GAP_SERVER", "renderTpoInfo", "putGapAlerts", "gapAlertsFollow"], """
const el = { textContent: '' }, $ = () => el, px = String, sent = [];
let gapAlertsPut = 0;
const inst = () => ({ rth: ['07:20', '12:30'], tick_size: 0.1 });
const S = { st: { tpo: true }, symbol: 'GC', spec: '5m', tpoError: '', tpo: { minutes: 5, step: 0.8 }, instruments: { GC: { rth: ['07:20', '12:30'] } },
  tpoSet: { SessionTime: '0830-1500', UseInstrumentHours: true }, gapAlerts: {},
  gapSet: { RthStart: 83000, RthEnd: 150000, MinimumGapPoints: 0, UseInstrumentHours: false } };
const tpoSpecOk = () => true, gapRthOk = () => true, GAP_SWITCH = {}, renderGapInfo = () => {};
const api = async (method, path, body) => { sent.push(body); return { alerts: {} }; };
/*APP*/
(async () => {
  renderTpoInfo();
  const on = el.textContent;
  S.tpoSet.UseInstrumentHours = false;
  renderTpoInfo();
  await putGapAlerts(true);
  await gapAlertsFollow();
  console.log(JSON.stringify({ on, off: el.textContent, server: GAP_SERVER, sent: sent.map((b) => b.UseInstrumentHours) }));
})();
""")
    assert out["on"].endswith("0720-1230 CT.") and out["off"].endswith("0830-1500 CT.")
    assert "UseInstrumentHours" in out["server"] and out["sent"] == [False, False]


# ---- AMT Toolkit: IB Stop Time and Delete OVN Lines At at NinjaTrader's defaults --------------------------------------

def test_amt_ib_stop_and_ovn_lines_default_to_ninjatraders():
    """0830-1515 and 1500-1505, as NinjaTrader (they were 0830-1500 and 1700-1705 here); with Use Instrument Hours on they
    move as NinjaTrader 1.2.4 moves them: the OVN lines go at 12:30 on GC, 13:30 on CL."""
    assert (amt.DEFAULTS["ib_stop_time"], amt.DEFAULTS["amt_del_sess"]) == ("0830-1515", "1500-1505")
    hub = amt_hub.AmtHub.__new__(amt_hub.AmtHub)
    hub.m = SimpleNamespace(instruments={i.symbol: i for i in (ES, CL, GC)})
    hub._set({}, {})
    got = {sym: (hub.settings_for(sym).ib_stop_time, hub.settings_for(sym).amt_del_sess) for sym in ("ES", "CL", "GC")}
    assert got == {"ES": ("0830-1515", "1500-1505"), "CL": ("0800-1345", "1330-1335"), "GC": ("0720-1245", "1230-1235")}
    off = {"inst_hours": False}
    assert (hub.settings_for("GC", off).ib_stop_time, hub.settings_for("GC", off).amt_del_sess) == ("0830-1515", "1500-1505")


class _Store:
    def __init__(self, d):
        self.d, self.db = json.loads(json.dumps(d)), None

    def get_setting(self, k, default=None):
        return json.loads(json.dumps(self.d.get(k, default)))

    def set_setting(self, k, v):
        self.d[k] = json.loads(json.dumps(v))


def _hub(store):
    return amt_hub.AmtHub(SimpleNamespace(store=store, instruments={"ES": ES, "GC": GC}))


def test_amt_alerts_switched_on_before_keep_the_old_defaults_their_chart_draws():
    """Stored before the defaults were NinjaTrader's: every chart's Delete OVN Lines At 1500-1505 and IB Stop Time
    0830-1515 (typed), and ES's alerts switched on from a chart with its own 1700-1705 and 0830-1500, the old defaults,
    so its stored set left them out. The alerts keep them, as that chart still draws them (they went to 1500-1505 and
    0830-1515). GC's set, from a chart with every chart's own, keeps those; a set stored after the update is left as it
    is (once)."""
    store = _Store({"amt_settings": {"amt_del_sess": "1500-1505", "ib_stop_time": "0830-1515", "amt_width": 3},
                    "amt_symbol_settings": {"ES": {}, "GC": {}},
                    "amt_alerts": {"ES": {"on": True, "minutes": 5, "set": {"amt_width": 3}},
                                   "GC": {"on": True, "minutes": 5, "set": {"amt_del_sess": "1500-1505", "ib_stop_time": "0830-1515", "amt_width": 3}}}})
    hub = _hub(store)
    ran = lambda sym: hub.settings_for(sym, hub.alert_run(sym)[2])   # noqa: E731
    assert (ran("ES").amt_del_sess, ran("ES").ib_stop_time) == ("1700-1705", "0830-1500")
    chart = hub.chart_settings({"amt_del_sess": "1700-1705", "ib_stop_time": "0830-1500", "amt_width": 3}, "ES")[1]
    assert hub.alert_run("ES")[2] == chart   # the chart's own: they share a runner
    assert (ran("GC").amt_del_sess, ran("GC").ib_stop_time) == ("1230-1235", "0720-1245")   # NinjaTrader's, moved
    assert store.d["amt_alerts_old_defaults_kept"] is True
    # after the update a set without them means the new defaults: not written again
    store.d["amt_alerts"]["ES"]["set"] = {"amt_width": 3}
    hub = _hub(store)
    assert (ran("ES").amt_del_sess, ran("ES").ib_stop_time) == ("1500-1505", "0830-1515")


def test_amt_alerts_on_the_old_defaults_everywhere_take_the_new_ones():
    """Every chart on the old defaults too (none stored): the chart the alerts came from follows every chart's to
    NinjaTrader's, and so do the alerts."""
    store = _Store({"amt_symbol_settings": {}, "amt_alerts": {"ES": {"on": True, "minutes": 5, "set": {"amt_width": 3}}}})
    hub = _hub(store)
    assert store.d["amt_alerts"]["ES"]["set"] == {"amt_width": 3}
    s = hub.settings_for("ES", hub.alert_run("ES")[2])
    assert (s.amt_del_sess, s.ib_stop_time) == ("1500-1505", "0830-1515")


def test_amt_settings_stored_at_todays_defaults_share_the_charts_runner():
    """NinjaTrader's 1500-1505 and 0830-1515 typed before they were the defaults: stored, they now equal the defaults
    and are left out as a save leaves them out, so ES's alerts share the runner of ES's charts on the starting settings
    (they had one of their own, loading its history twice)."""
    hub = _hub(_Store({"amt_settings": {"amt_del_sess": "1500-1505", "ib_stop_time": "0830-1515"},
                       "amt_symbol_settings": {"ES": {"amt_alert_ovn": True}}, "amt_alerts": {"ES": {"on": True, "minutes": 5}}}))
    assert hub.overrides == {} and hub.per_symbol["ES"] == {}
    assert hub.alert_run("ES")[1] == "" and hub.alert_key("ES") == ("ES", 5)
    assert hub.chart_settings({}, "ES") == ("", {})


def test_amt_ovn_alerts_end_at_the_rth_close():
    """By default OVN High/Low stop alerting at 15:00 with their lines, as NinjaTrader since 1.2.2 (here they alerted to
    17:00)."""
    from test_amt_close_roll import committed
    fs, _, _ = committed({"amt_alert_ovn": True}, False, days=4)
    armed = {f.Bar.Start.strftime("%H:%M") for f in fs if any(w.Name == "OVN High" and w.Enabled for w in f.Watches)}
    assert armed and "14:55" in armed and not any("15:00" <= t < "17:00" for t in armed), sorted(armed)


# ---- against the NinjaTrader sources (TATANKA_INDICATORS) ---------------------------------------------------------------

def _source(name: str) -> str:
    return (Path(INDICATORS) / "ninjatrader" / name).read_text(encoding="utf-8-sig")


def _module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table(src: str) -> dict:
    body = src[src.index("class InstrumentHours"):]
    body = body[:body.index("static void Add(")]
    out = {}
    for o, c, roots in re.findall(r'Add\("(\d\d:\d\d)", "(\d\d:\d\d)", ([^)]*)\);', body):
        for r in re.findall(r'"([^"]+)"', roots):
            assert r not in out, f"{r} listed twice"
            out[r] = (o, c)
    return out


@needs_indicators
def test_each_files_hours_table_is_the_amt_toolkits():
    """TPO, Gap and SVP each carry the AMT Toolkit's table (config/futures.json's hours, root for root), each in a type of
    its own: TatankaTpoProfile.InstrumentHours, TatankaGapInternal.InstrumentHours, TatankaVpCore.InstrumentHours (the
    Toolkit's is TatankaTrading.AmtToolkit.InstrumentHours), so the files install together."""
    want = {x["symbol"]: tuple(x["rth"]) for x in futures_catalog(CONFIG) if x.get("rth")}
    assert _table(_source("TatankaAmtToolkit.cs")) == want
    for name in FILES.values():
        assert _table(_source(name)) == want, name
    assert re.search(r"public class TatankaTpoProfile : Indicator[\s\S]*private static class InstrumentHours", _source(FILES["tpo"]))
    gap = _source(FILES["gap"])
    assert gap.index("internal static class InstrumentHours") > gap.index("namespace NinjaTrader.NinjaScript.Indicators.TatankaGapInternal")
    svp = _source(FILES["prof"])
    assert svp.index("internal static class InstrumentHours") > svp.index("namespace TatankaVpCore")
    # nothing else of the nine files declares one where another file's code could see it
    for f in (Path(INDICATORS) / "ninjatrader").glob("*.cs"):
        if f.name not in (*FILES.values(), "TatankaAmtToolkit.cs"):
            assert "InstrumentHours" not in f.read_text(encoding="utf-8-sig"), f.name


@needs_indicators
def test_the_schema_is_what_the_generators_make_from_the_sources():
    """tpo, gap, hema, ifthen and lines from the checkout (server/tools/gen_nt_schema.py), the volume profiles from the
    copies in tests/vp_csharp (tools/gen_vp_schema.py), which are the checkout's files with the site left out of the
    title line (the public copy carries no site); each indicator's version, the
    setting's NinjaTrader name, group, order and description, on by default in SetDefaults."""
    nt = _module(SERVER / "tools" / "gen_nt_schema.py")
    vpg = _module(ROOT / "tools" / "gen_vp_schema.py")
    stored = json.loads((SERVER / "chartsrv" / "nt_schema.json").read_text())   # (indicators.SCHEMA has this app's limits)
    for key, (fname, _) in nt.INDICATORS.items():
        src = _source(fname)
        assert stored[key]["settings"] == nt.parse(Path(INDICATORS) / "ninjatrader" / fname, key), key
        assert stored[key]["version"] == re.search(r"v(\d+\.\d+\.\d+)", src)[1], key
    for key, fname in vpg.FILES.items():
        copy = (HERE / "vp_csharp" / fname).read_bytes()
        src = (Path(INDICATORS) / "ninjatrader" / fname).read_bytes()
        assert copy == re.sub(rb"(?m)^(// Tatanka [A-Za-z ]+ v?[\d.]+ for NinjaTrader 8) - [a-z]+\.com(\r?)$", rb"\1\2", src, count=1), fname
        assert stored[key] == vpg.parse(copy.decode(), key), key
    for key, fname in FILES.items():
        src = _source(fname)
        assert re.search(r"\bUseInstrumentHours\s*=\s*true;", src.split("State.SetDefaults")[1].split("State.Configure")[0]), key
        spec = by_name(key)["UseInstrumentHours"]
        desc = re.search(r'Display\(Name = "Use Instrument Hours"[^\]]*Description = "([^"]*)"', src)[1]
        # TPO's Session Time is in NinjaTrader's time zone there, in CT here: this app's wording (the generator's LOCAL)
        want = nt.LOCAL[(key, "UseInstrumentHours")]["description"] if key == "tpo" else desc
        assert spec["description"] == want and f'GroupName = "{spec["group"]}"' in src, key
    assert "chart time" in _source(FILES["tpo"]) and "Eastern" not in by_name("tpo")["UseInstrumentHours"]["description"]


def test_the_tpo_setting_says_session_time_is_in_ct_here():
    """The dialog's description: Session Time is in CT here (NinjaTrader's speaks of its own time zone, which this app
    has no setting for)."""
    d = by_name("tpo")["UseInstrumentHours"]["description"]
    assert "in CT here" in d and "NinjaTrader" not in d and "time zone" not in d and "chart time" not in d


@needs_indicators
def test_amt_defaults_are_the_csharps():
    init = _source("TatankaAmtToolkit.cs").split("private void InitializeSettings()")[1][:20000]
    assert re.search(r'\bib_stop_time = "0830-1515";', init) and re.search(r'\bamt_del_sess = "1500-1505";', init)


def _build(script: str, *args) -> None:
    r = subprocess.run(["sh", str(HERE / script), *map(str, args)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def _parity(script: str, *args) -> str:
    r = subprocess.run([sys.executable, str(HERE / script), *map(str, args)], capture_output=True, text=True, cwd=SERVER)
    assert r.returncode == 0 and "PARITY OK" in r.stdout, r.stdout + r.stderr
    return r.stdout


@needs_mono
@pytest.mark.parametrize("root", ["GC", "SI"])
def test_tpo_and_gap_parity_with_the_setting_on_and_off(tmp_path, root):
    """The C# TPO Profile and Gap Analysis 1.1.0 built from the checkout against this port, on a market whose hours move:
    every value equal with Use Instrument Hours on (each moving the times itself) and off."""
    _build("tpo_csharp/build.sh", INDICATORS, tmp_path / "tpo")
    _build("gap_csharp/build.sh", INDICATORS, tmp_path / "gap")
    rth = "07:20-12:30" if root == "GC" else "07:25-12:25"
    assert rth.replace(":", "") in _parity("tpo_parity.py", tmp_path / "tpo", 12, 5, 3, root)
    assert "0830-1500" in _parity("tpo_parity.py", tmp_path / "tpo", 12, 5, 3, root, '{"UseInstrumentHours": false}')
    assert "(Use Instrument Hours on)" in _parity("gap_parity.py", tmp_path / "gap", 12, 5, 3, root)
    assert "RTH 83000-150000" in _parity("gap_parity.py", tmp_path / "gap", 12, 5, 3, root, '{"UseInstrumentHours": false}')
    assert "refused by both" in _parity("gap_parity.py", tmp_path / "gap", 5, 5, 3, "CL", '{"RthStart": 110000, "RthEnd": 120000}')


@needs_mono
def test_tpo_moves_session_time_in_any_ninjatrader_time_zone(tmp_path):
    """NinjaTrader's Session Time is in its own time zone (ES's 08:30-15:00 is 0930-1600 on Eastern time). The C# moves
    it in CT, at each day's offset, in any zone (it moved only on Central time): on a New York or Los Angeles chart the
    profiles are this port's CT ones, setting on and off; RunSessionTime (Q) for a few zones; and London's offset from
    CT, 5 hours in the week its daylight saving has ended and Chicago's hasn't, moves the session for that week."""
    _build("tpo_csharp/build.sh", INDICATORS, tmp_path)
    assert "0930-1600 typed, 0820-1330 run there" in _parity("tpo_parity.py", tmp_path, 12, 5, 3, "GC", "zone=America/New_York")
    assert "0930-1600 typed, 0930-1600 run there" in _parity("tpo_parity.py", tmp_path, 12, 5, 3, "GC", '{"UseInstrumentHours": false}',
                                                              "zone=America/New_York")
    assert "0630-1300 typed, 0600-1130 run there" in _parity("tpo_parity.py", tmp_path, 12, 30, 3, "CL", "rth", "zone=America/Los_Angeles")
    q = {("0930-1600", "1", "GC", 60): "0820-1330", ("0630-1300", "1", "GC", -120): "0520-1030",
         ("2330-0600", "1", "GC", 900): "2220-0330", ("1430-2100", "1", "GC", 300): "1320-2100",
         ("0930-1600", "1", "ES", 60): "0930-1600", ("0930-1600", "0", "GC", 60): "0930-1600",
         ("1100-1200", "1", "CL", 0): "1030-1130", ("0830-1500", "1", "SI", 0): "0725-1225",
         ("0860-1500", "1", "GC", 0): "0860-1500", ("0899-1500", "1", "GC", 0): "0899-1500",
         ("0830-1500:1", "1", "GC", 0): "0830-1500:1"}
    bars = ["2026-10-22T02:00", "2026-10-22T13:20", "2026-10-22T13:25", "2026-10-22T18:30", "2026-10-22T18:35",
            "2026-10-27T13:25", "2026-10-27T20:55", "2026-10-27T21:00", "2026-10-27T21:05",
            "2026-11-03T18:30", "2026-11-03T18:35"]
    rec = ["S|0.1|1|1430-2100|30|1|80|8|GC|1|Europe/London", "D|3510|3490|3500", "D|3512|3492|3502"]
    rec += [f"Q|{t}|{on}|{root}|{off}" for t, on, root, off in q]
    rec += [f"B|{t}:00|3501|3499|0|2026-10-21T23:00:00" for t in bars] + ["E"]
    (tmp_path / "rec.txt").write_text("\n".join(rec) + "\n")
    subprocess.run(["mono", str(tmp_path / "harness.exe"), str(tmp_path / "rec.txt"), str(tmp_path / "out.txt")], check=True)
    out = (tmp_path / "out.txt").read_text().splitlines()
    assert [l[2:] for l in out if l.startswith("Q|")] == list(q.values())
    for (t, on, root, off), got in q.items():   # with NinjaTrader on Central time, this port's
        if off == 0:
            assert tpo_hours(tpo.settings({"SessionTime": t, "UseInstrumentHours": on == "1"}),
                             {"GC": GC.rth, "CL": CL.rth, "SI": SI}[root])["SessionTime"] == got
    assert [l[2:] for l in out if l.startswith("W|")] == ["1320-1830", "1320-2100", "1320-1830"]
    live = [l.split("|")[3] for l in out if l.startswith("T|")]
    assert dict(zip(bars, live)) == {
        "2026-10-22T02:00": "0", "2026-10-22T13:20": "0", "2026-10-22T13:25": "1", "2026-10-22T18:30": "1",
        "2026-10-22T18:35": "0", "2026-10-27T13:25": "1", "2026-10-27T20:55": "1", "2026-10-27T21:00": "1",
        "2026-10-27T21:05": "0", "2026-11-03T18:30": "1", "2026-11-03T18:35": "0"}


@needs_mono
def test_svp_parity_with_the_setting_on_and_off(tmp_path):
    """The C# volume-profile cores built from the checkout: every case equal, SVP's window on ES, NQ, CL, GC and SI with
    Use Instrument Hours on and off included, and in other time zones (Eastern, UTC, Berlin, London and Tokyo across the
    weeks their offset from Chicago changes, Eastern and UTC across both US changes, windows across CT's midnight and
    not the zone's and the other way, Chart on Los Angeles, Central with the chart on New York)."""
    _build("vp_csharp/build.sh", tmp_path, INDICATORS)
    vp_parity = _module(HERE / "vp_parity.py")
    assert sum(1 for head, _ in vp_parity.cases(1) if len(head.split("|")) > 10) == 38
    assert vp_parity.run(str(tmp_path / "harness.exe")) == []


@needs_mono
def test_svp_moves_the_window_in_any_ninjatrader_time_zone(tmp_path):
    """The C# Session Volume Profile 1.1.0 moves the window in any zone (it moved only in Central time): each date's at
    that date's offset from Central time. London's 14:30-21:00 (Chart on London) runs 13:20-18:30 on GC, 13:20-21:00 in
    the week London's daylight saving has ended and Chicago's hasn't; Eastern's 09:30-16:00 with NinjaTrader on Central
    runs 08:20-13:30, each session's times in chart time, and 08:20-13:30 on both sides of the US change (Mar 14 2027);
    Tokyo's 00:30-06:00 opens the evening before (23:20). UTC's 14:30-21:00 runs 13:20-21:00 from Chicago's spring
    change. Berlin's 06:00-22:00 (ExchangeTemplate on a Berlin template, NinjaTrader on Central) opens at 23:00 CT the
    evening before and runs 06:00-19:30, and 06:00-22:00, 00:00-16:00 CT, in the week Berlin's daylight saving has
    ended and Chicago's hasn't. The port's the same, line for line."""
    _build("vp_csharp/build.sh", tmp_path, INDICATORS)
    vp_parity = _module(HERE / "vp_parity.py")

    def run(head, times):
        lines = [head] + [f"T|{i + 1}|{3500 + i * 0.1!r}|1.0|{vp_parity.MS(t)}" for i, t in enumerate(times)] + ["E"]
        out = subprocess.run(["mono", str(tmp_path / "harness.exe")], input="\n".join(lines) + "\n", capture_output=True,
                             text=True, check=True).stdout
        assert out == vp_parity.port(lines)
        lines = out.splitlines()
        spans = [(vp_parity.DT(f[1]), vp_parity.DT(f[2]), int(f[4])) for f in (l.split("|") for l in lines if l.startswith("SESSION|"))]
        return [l[2:] for l in lines if l.startswith("W|")], spans

    w, spans = run("V|14:30|21:00|0.1|1|20|68|0|GC|1|Europe/London|Europe/London",
                   [datetime(2026, 10, 22, 13, 25), datetime(2026, 10, 22, 18, 25), datetime(2026, 10, 22, 18, 35),
                    datetime(2026, 10, 27, 13, 25), datetime(2026, 10, 27, 20, 55), datetime(2026, 10, 27, 21, 5),
                    datetime(2026, 11, 3, 13, 25), datetime(2026, 11, 3, 18, 25), datetime(2026, 11, 3, 18, 35)])
    assert w == ["13:20-18:30", "13:20-21:00", "13:20-18:30"]
    assert spans == [(datetime(2026, 10, 22, 13, 20), datetime(2026, 10, 22, 18, 30), 2),
                     (datetime(2026, 10, 27, 13, 20), datetime(2026, 10, 27, 21, 0), 2),
                     (datetime(2026, 11, 3, 13, 20), datetime(2026, 11, 3, 18, 30), 2)]
    w, spans = run("V|09:30|16:00|0.1|1|20|68|0|GC|1|America/New_York|America/Chicago",
                   [datetime(2026, 11, 3, 7, 15), datetime(2026, 11, 3, 7, 25), datetime(2026, 11, 3, 12, 25), datetime(2026, 11, 3, 12, 35)])
    assert (w, spans) == (["08:20-13:30"], [(datetime(2026, 11, 3, 7, 20), datetime(2026, 11, 3, 12, 30), 2)])   # in chart time, CT
    w, spans = run("V|09:30|16:00|0.1|1|20|68|0|GC|0|America/New_York|America/Chicago", [datetime(2026, 11, 3, 7, 25), datetime(2026, 11, 3, 8, 35)])
    assert (w, spans) == ([], [(datetime(2026, 11, 3, 8, 30), datetime(2026, 11, 3, 15, 0), 1)])   # off: as typed
    w, spans = run("V|00:30|06:00|0.1|1|20|68|0|GC|1|Asia/Tokyo|Asia/Tokyo", [datetime(2026, 11, 3, 23, 25), datetime(2026, 11, 4, 3, 35)])
    assert (w, spans) == (["23:20-03:30"], [(datetime(2026, 11, 3, 23, 20), datetime(2026, 11, 4, 3, 30), 1)])
    w, _ = run("V|14:30|21:00|0.1|1|20|68|0|GC|1|UTC|America/Chicago",
               [datetime(2026, 10, 30, 8, 25), datetime(2026, 11, 2, 7, 25)])   # CT times: 13:25 UTC both
    assert w == ["13:20-21:00", "13:20-18:30"]
    w, _ = run("V|14:30|21:00|0.1|1|20|68|0|GC|1|UTC|America/Chicago",
               [datetime(2027, 3, 12, 7, 25), datetime(2027, 3, 15, 8, 25)])    # 13:25 UTC both
    assert w == ["13:20-18:30", "13:20-21:00"]
    w, spans = run("V|09:30|16:00|0.1|1|20|68|0|GC|1|America/New_York|America/Chicago",
                   [datetime(2027, 3, 12, 7, 25), datetime(2027, 3, 15, 7, 25)])
    assert (w, spans) == (["08:20-13:30"], [(datetime(2027, 3, 12, 7, 20), datetime(2027, 3, 12, 12, 30), 1),
                                            (datetime(2027, 3, 15, 7, 20), datetime(2027, 3, 15, 12, 30), 1)])
    w, spans = run("V|06:00|22:00|0.1|1|20|68|0|GC|1|Europe/Berlin|America/Chicago",
                   [datetime(2026, 10, 21, 23, 5), datetime(2026, 10, 22, 12, 25), datetime(2026, 10, 22, 12, 35),
                    datetime(2026, 10, 27, 0, 5), datetime(2026, 10, 27, 15, 55), datetime(2026, 10, 27, 16, 5),
                    datetime(2026, 11, 2, 23, 5), datetime(2026, 11, 3, 12, 25), datetime(2026, 11, 3, 12, 35)])
    assert w == ["06:00-19:30", "06:00-22:00", "06:00-19:30"]
    assert spans == [(datetime(2026, 10, 21, 23, 0), datetime(2026, 10, 22, 12, 30), 2),
                     (datetime(2026, 10, 27, 0, 0), datetime(2026, 10, 27, 16, 0), 2),
                     (datetime(2026, 11, 2, 23, 0), datetime(2026, 11, 3, 12, 30), 2)]
