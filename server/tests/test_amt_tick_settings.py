"""AMT Toolkit: Value From Ticks (Sessions) and Profile From Ticks, as NinjaTrader has them. The prior day's value comes
from trades for the 5 most recent RTH sessions when the chart loads (0 to 20; 0: 1-minute bars only) and each one that
closes after; older days from 1-minute bars, as NinjaTrader's RequestTickDays and RequestClosedSession pick them. The
current Session or Weekly profile comes from trades unless Profile From Ticks is off. With a tatanka-indicators checkout
(TATANKA_INDICATORS) and mono, the C# parity harness runs the indicator's own tick code against this port with the
settings on and off."""
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from chartsrv import amt, amt_hub, amt_runner
from chartsrv.amt_runner import AmtRunner, TickDays, epoch_ms
from chartsrv.config import Instrument

HERE = Path(__file__).resolve().parent
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_indicators = pytest.mark.skipif(not INDICATORS, reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS)")
needs_mono = pytest.mark.skipif(not (INDICATORS and shutil.which("mcs") and shutil.which("mono")),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS) and mono")
ES = Instrument("ES", "E-mini", 0.25, 50, "ES", rth=("08:30", "15:00"), calendar="equity")
RTH = "0830-1500:23456"


def _hub(*insts):
    hub = amt_hub.AmtHub.__new__(amt_hub.AmtHub)
    hub.m = SimpleNamespace(instruments={i.symbol: i for i in insts})
    hub._set({}, {})
    return hub


def test_both_settings_are_in_the_dialog_as_in_ninjatrader():
    pd, prof = amt_hub.SCHEMA_BY_NAME["PdTickSessions"], amt_hub.SCHEMA_BY_NAME["ProfileTicks"]
    assert (pd["group"], pd["order"], pd["label"], pd["type"], pd["min"], pd["max"]) == \
        ("4. Previous Day Value & 80% Rule", 53, "Value From Ticks (Sessions)", "int", 0, 20)
    assert (prof["group"], prof["order"], prof["label"], prof["type"]) == ("11. Volume Profile", 158, "Profile From Ticks", "bool")
    assert amt_hub.DEFAULTS["PdTickSessions"] == 5 and amt_hub.DEFAULTS["ProfileTicks"] is True
    names = [x["name"] for x in amt_hub.SCHEMA]
    assert names.index("cc_show_poc") + 1 == names.index("PdTickSessions")   # declared after Show pdPOC (both 53)
    assert names.index("ProfileTicks") + 1 == names.index("mvp_calc_tf")     # and before Calc Precision (both 158)
    assert amt_hub.clean_settings({"PdTickSessions": 0, "ProfileTicks": False}) == {"PdTickSessions": 0, "ProfileTicks": False}
    assert amt_hub.clean_settings({"PdTickSessions": 5, "ProfileTicks": True}) == {}
    for bad in ({"PdTickSessions": 21}, {"PdTickSessions": -1}, {"PdTickSessions": 2.5}, {"ProfileTicks": 1}):
        with pytest.raises(ValueError):
            amt_hub.clean_settings(bad)
    # the indicator's, not the engine's: they never reach amt.Settings, and they are a chart's own (not shared)
    assert "PdTickSessions" not in amt.DEFAULTS and not {"PdTickSessions", "ProfileTicks"} & amt_hub.SHARED
    assert amt_hub.engine_settings({"PdTickSessions": 2, "ProfileTicks": False}).cc_enable is True


@needs_indicators
def test_names_ranges_and_defaults_are_the_csharps():
    src = (Path(INDICATORS) / "ninjatrader" / "TatankaAmtToolkit.cs").read_text(encoding="utf-8-sig")
    assert re.search(r'\[Range\(0, 20\)\]\s*\[Display\(Name="Value From Ticks \(Sessions\)".*GroupName="4\. Previous Day '
                     r'Value & 80% Rule", Order=53\)\]\s*public int PdTickSessions', src)
    assert re.search(r'\[Display\(Name="Profile From Ticks".*GroupName="11\. Volume Profile", Order=158\)\]\s*public bool '
                     r'ProfileTicks', src)
    assert re.search(r"\nPdTickSessions = 5;\nProfileTicks = true;", src.replace("\r", ""))


def test_the_sessions_counted_back_from_the_load():
    """RequestTickDays: weekdays of the RTH Session for VA closed by the load, newest first; today's only once it closed."""
    wed = datetime(2026, 10, 14, 14, 0)   # Wednesday, before the close
    got = TickDays(5, RTH, wed).windows
    assert [s.strftime("%a %d") for s, _ in got] == ["Tue 13", "Mon 12", "Fri 09", "Thu 08", "Wed 07"]
    assert got[0] == (datetime(2026, 10, 13, 8, 30), datetime(2026, 10, 13, 15, 0))
    assert TickDays(5, RTH, wed.replace(hour=15)).windows[0][0].day == 14   # closed at 15:00: today's counts
    assert TickDays(0, RTH, wed).windows == [] and TickDays(5, RTH, wed, on=False).windows == []
    assert len(TickDays(20, RTH, wed).windows) == 20
    # a weekday holiday counts as NinjaTrader asks for it too (it has no ticks: 1-minute bars): Thanksgiving
    thu = TickDays(2, RTH, datetime(2026, 11, 27, 16, 0)).windows
    assert [s.day for s, _ in thu] == [27, 26]
    # at most 45 days back (RequestTickDays' bound): Mondays only, 20 asked, 7 found (Aug 31 is 44 days back)
    mondays = TickDays(20, "0830-1500:2", datetime(2026, 10, 14, 15, 30)).windows
    assert [s.strftime("%m-%d") for s, _ in mondays] == ["10-12", "10-05", "09-28", "09-21", "09-14", "09-07", "08-31"]
    # a moved session (GC's 0720-1230) is counted on its own hours
    assert TickDays(1, "0720-1230:23456", datetime(2026, 10, 14, 13, 0)).windows == \
        [(datetime(2026, 10, 14, 7, 20), datetime(2026, 10, 14, 12, 30))]


def test_a_session_that_closes_after_the_load_is_asked_for():
    """RequestClosedSession: the first live bar within two hours of the close asks for that session; later bars don't."""
    days = TickDays(5, RTH, datetime(2026, 10, 14, 10, 0))
    days.closed(datetime(2026, 10, 14, 14, 55))   # before the close: nothing yet
    assert not days.has(datetime(2026, 10, 14, 8, 30), datetime(2026, 10, 14, 15, 0))
    days.closed(datetime(2026, 10, 14, 15, 0))
    assert days.has(datetime(2026, 10, 14, 8, 30), datetime(2026, 10, 14, 15, 0))
    late = TickDays(5, RTH, datetime(2026, 10, 14, 10, 0))
    late.closed(datetime(2026, 10, 14, 17, 0))    # two hours after: not asked (that bar is past its window)
    assert not late.has(datetime(2026, 10, 14, 8, 30), datetime(2026, 10, 14, 15, 0))
    off = TickDays(0, RTH, datetime(2026, 10, 14, 10, 0))
    off.closed(datetime(2026, 10, 14, 15, 0))
    assert off.windows == []


def test_the_runner_takes_trades_for_those_sessions_only(monkeypatch):
    """Older sessions come from 1-minute bars and are never asked for; the default 5 as NinjaTrader's, counted back
    from the runner's load (loaded_at), then each close it runs through."""
    r = AmtRunner("ES", 0.25, 5, amt.Settings())
    r.loaded_at = epoch_ms(datetime(2026, 10, 16, 9, 0))   # Friday morning
    key = lambda s: (epoch_ms(s), epoch_ms(s + timedelta(hours=6, minutes=30)))   # noqa: E731
    thu, prior = datetime(2026, 10, 15, 8, 30), datetime(2026, 10, 9, 8, 30)
    r.pd_values = {key(thu): [(7000.0, 5.0)], key(prior): [(7000.0, 5.0)]}
    assert r.trade_value(thu, thu + timedelta(hours=6, minutes=30)) == [(7000.0, 5.0)]
    assert r.trade_value(prior, prior + timedelta(hours=6, minutes=30)) == [(7000.0, 5.0)]   # the 5th back (Fri 9th)
    old = datetime(2026, 10, 8, 8, 30)
    r.pd_values[key(old)] = [(7000.0, 5.0)]
    assert r.trade_value(old, old + timedelta(hours=6, minutes=30)) is None and not r.pd_missing   # the 6th: minutes
    fri = datetime(2026, 10, 16, 8, 30)
    assert r.trade_value(fri, fri + timedelta(hours=6, minutes=30)) is None and not r.pd_missing
    r.tick_days.closed(datetime(2026, 10, 16, 15, 0))           # Friday's close, live
    assert r.trade_value(fri, fri + timedelta(hours=6, minutes=30)) is None and r.pd_missing == {key(fri)}
    none = AmtRunner("ES", 0.25, 5, amt.Settings())
    none.pd_sessions, none.loaded_at, none.pd_values = 0, r.loaded_at, dict(r.pd_values)
    assert none.trade_value(thu, thu + timedelta(hours=6, minutes=30)) is None and not none.pd_missing
    cc_off = AmtRunner("ES", 0.25, 5, amt.Settings({"cc_enable": False}))
    cc_off.loaded_at, cc_off.pd_values = r.loaded_at, dict(r.pd_values)
    assert cc_off.trade_value(thu, thu + timedelta(hours=6, minutes=30)) is None


def test_profile_from_ticks_off_draws_the_profile_from_minutes():
    r = AmtRunner("ES", 0.25, 5, amt.Settings())
    assert r.wants_trades() and r.period_start(epoch_ms(datetime(2026, 10, 14, 10, 0))) is not None
    r.profile_ticks = False
    assert not r.wants_trades() and r.period_start(epoch_ms(datetime(2026, 10, 14, 10, 0))) is None
    m = amt_runner.MinuteBar(t=epoch_ms(datetime(2026, 10, 14, 10, 0)), o=7000, h=7001, l=6999, c=7000, v=10)
    r.add_minute(m)
    r.commit_ready()
    assert r.engine.MinuteTrades is None and r.engine.TradeValue is not None


def test_the_hub_gives_each_runner_its_charts_settings():
    hub = _hub(ES)
    r = hub.new_runner("ES", 5)
    assert (r.pd_sessions, r.profile_ticks) == (5, True)
    sig, mine = hub.chart_settings({"PdTickSessions": 0, "ProfileTicks": False}, "ES")
    assert sig and mine == {"PdTickSessions": 0, "ProfileTicks": False}   # a runner of the chart's own
    r = hub.new_runner("ES", 5, mine)
    assert (r.pd_sessions, r.profile_ticks) == (0, False) and r.S.cc_enable
    hub._set({"PdTickSessions": 12}, {})   # every chart's starting settings
    assert hub.new_runner("ES", 5).pd_sessions == 12


def test_the_load_counts_from_its_own_time(tmp_path, monkeypatch):
    """_load_history sets loaded_at to its cut (the live load's minute, or a replay's clock)."""
    seen = []
    monkeypatch.setattr(amt_hub, "load_trades", lambda *a, **k: None)
    monkeypatch.setattr(amt_hub, "iter_bars_1m", lambda *a, **k: iter(()))
    r = AmtRunner("ES", 0.25, 5, amt.Settings())
    monkeypatch.setattr(r, "commit_ready", lambda: seen.append(r.loaded_at))
    amt_hub._load_history(tmp_path / "x.db", r, "ES", 1_760_000_000_000)
    assert seen == [1_760_000_000_000]


# ---- the C# parity harness: the indicator's own tick code against TickDays --------------------------------------------

@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    work = tmp_path_factory.mktemp("amt_cs")
    r = subprocess.run(["sh", str(HERE / "amt_csharp" / "build.sh"), INDICATORS, str(work)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return work


def _parity(work, days, minutes, seed, root, settings: str | None):
    args = [sys.executable, str(HERE / "amt_parity.py"), str(work), str(days), str(minutes), str(seed), root]
    if settings is not None:
        (work / "settings.txt").write_text(settings)
        args.append(str(work / "settings.txt"))
    r = subprocess.run(args, capture_output=True, text=True, cwd=HERE.parent)
    assert r.returncode == 0 and "PARITY OK" in r.stdout, r.stdout + r.stderr
    return r.stdout


@needs_mono
@pytest.mark.parametrize("root,minutes,settings,said", [
    ("ES", 5, None, "Value From Ticks 5 sessions, Profile From Ticks True"),
    ("ES", 5, "PdTickSessions=0\n", "Value From Ticks 0 sessions"),
    ("ES", 5, "PdTickSessions=2\n", "Value From Ticks 2 sessions"),
    ("ES", 5, "PdTickSessions=20\nProfileTicks=False\n", "Value From Ticks 20 sessions, Profile From Ticks False"),
    ("ES", 5, "cc_enable=False\n", "Value From Ticks 5 sessions"),
    ("GC", 10, "PdTickSessions=3\n", "RTH 0720-1230"),
])
def test_parity_with_the_tick_settings(harness, root, minutes, settings, said):
    assert said in _parity(harness, 14, minutes, 3, root, settings)


@needs_mono
def test_parity_where_the_45_day_bound_binds(harness):
    """The RTH Session for VA on Mondays only (0830-1500:2) and 20 sessions over 50 days: RequestTickDays stops 45 days
    back with 7 Mondays, and so must the port (with 30 days it took 4, and the frames differ)."""
    assert "Value From Ticks 20 sessions" in _parity(harness, 50, 30, 3, "ES", "cc_sess_def=0830-1500:2\nPdTickSessions=20\n")


@needs_mono
def test_the_parity_check_sees_another_choice_of_sessions(harness, monkeypatch):
    """The harness picks the sessions itself: a port that took one session fewer than NinjaTrader would fail."""
    sys.path.insert(0, str(HERE))
    import amt_parity
    real = amt_parity.replay_python
    monkeypatch.setattr(amt_parity, "replay_python", lambda path, runs, rth, shift, n, pt: real(path, runs, rth, shift, n - 1, pt))
    monkeypatch.setattr(amt_parity.sys, "argv", ["amt_parity.py", str(harness), "14", "5", "3", "ES"])
    assert amt_parity.main() == 1


@needs_mono
def test_the_parity_check_runs_the_runners_own_wiring(harness, monkeypatch, capsys):
    """The recording runner's own frames are compared with the record's: a runner that never asks for a session closing
    while it runs (AmtRunner._process without RequestClosedSession) fails, though the record itself is still NinjaTrader's."""
    sys.path.insert(0, str(HERE))
    import amt_parity

    class Deaf(TickDays):
        def closed(self, bar_start):
            pass
    monkeypatch.setattr(amt_runner, "TickDays", Deaf)
    monkeypatch.setattr(amt_parity.sys, "argv", ["amt_parity.py", str(harness), "14", "5", "3", "ES"])
    assert amt_parity.main() == 1
    assert "RUNNER DIFF" in capsys.readouterr().out
