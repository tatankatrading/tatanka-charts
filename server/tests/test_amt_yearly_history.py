"""AMT Toolkit: the Yearly profile loads 400 days of 1-minute bars, as NinjaTrader's guide sets Days to load (90, or 400
with the yearly profile), so it starts at January 1; every other run loads 90."""
import sys
from datetime import datetime
from pathlib import Path

from chartsrv import amt, amt_hub
from chartsrv.amt_runner import AmtRunner, epoch_ms

sys.path.insert(0, str(Path(__file__).resolve().parent))
from amt_parity import synth   # noqa: E402

DAY = 86_400_000


def _asked(monkeypatch, settings: dict) -> int:
    got = []
    monkeypatch.setattr(amt_hub, "load_trades", lambda *a, **k: None)
    monkeypatch.setattr(amt_hub, "iter_bars_1m", lambda db, sym, since, before: got.append((since, before)) or iter(()))
    amt_hub._load_history("x.db", AmtRunner("ES", 0.25, 5, amt.Settings(settings)), "ES", 1_000 * DAY)
    (since, before), = got
    assert before == 1_000 * DAY
    return (before - since) // DAY


def test_the_yearly_profile_loads_400_days(monkeypatch):
    assert _asked(monkeypatch, {"mvp_tf_mode": "Yearly"}) == 400
    for other in ({}, {"mvp_tf_mode": "Monthly"}, {"mvp_tf_mode": "Weekly"}, {"mvp_tf_mode": "Yearly", "mvp_master": False}):
        assert _asked(monkeypatch, other) == 90, other


def test_with_them_the_yearly_profile_starts_at_january_1():
    """The minutes the load reads (from 400 days back): the year's profile from its first session, where 90 days
    started it in July."""
    end = datetime(2026, 10, 16, 15, 30)
    for days, first in ((amt_hub.history_days(amt.Settings({"mvp_tf_mode": "Yearly"})), datetime(2025, 12, 31, 17, 0)),
                        (amt_hub.HISTORY_DAYS, None)):
        r = AmtRunner("ES", 0.25, 30, amt.Settings({"mvp_tf_mode": "Yearly"}))
        for m in synth(days, 3, end):
            if m.t >= epoch_ms(end) - days * DAY:
                r.add_minute(m)
        r.commit_ready()
        f, _ = r.preview()
        start = f.Profiles[-1].Start
        assert start == first if first else start > datetime(2026, 7, 1), (days, start)
