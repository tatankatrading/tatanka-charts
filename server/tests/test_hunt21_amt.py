"""Round 21's bug hunt, the AMT Toolkit: what its reviews found, each fixed."""
import functools
import math
from datetime import datetime, timedelta
from types import SimpleNamespace

import amt_parity
from amt_parity import dump, replay_python, synth, write_record
from test_hunt13_amt import session
from test_hunt_web_chart import needs_node, run_app

from chartsrv import amt, amt_hub, sessions
from chartsrv.amt_runner import AmtRunner, epoch_ms
from chartsrv.config import ROOT, Instrument, load_holidays
from chartsrv.models import Bar as MB

CL = Instrument("CL", "Crude", 0.01, 1000, "CL", rth=("08:00", "13:30"), calendar="energy")
GC = Instrument("GC", "Gold", 0.10, 100, "GC", rth=("07:20", "12:30"), calendar="metals")
ES = Instrument("ES", "E-mini", 0.25, 50, "ES", rth=("08:30", "15:00"), calendar="equity")


def _hub(*insts):
    hub = amt_hub.AmtHub.__new__(amt_hub.AmtHub)
    hub.m = SimpleNamespace(instruments={i.symbol: i for i in insts})
    hub._set({}, {})   # every chart's settings and the symbols' alert settings at the defaults
    return hub


def _minutes(start, end, fn, volume=lambda t: 100, cal=None):
    """1-minute bars from start to end where Globex trades; fn(t) -> price or (open, high, low, close)."""
    out, t = [], start
    while t < end:
        ms = epoch_ms(t)
        if sessions.is_open(ms, cal):
            p = fn(t)
            o, h, l, c = p if isinstance(p, tuple) else (p, p, p, p)
            out.append(MB(t=ms, o=o, h=h, l=l, c=c, v=volume(t)))
        t += timedelta(minutes=1)
    return out


# ---- r21-42: the parity check replays committed bars on each engine itself, so state carried between bars counts ---

def test_the_parity_record_replays_committed_bars_on_the_engine_and_previews_on_a_clone(tmp_path):
    """tests/amt_parity.py draws every committed frame (commit_visuals) and the B record's last field said so, but both
    replays read it as "run on a clone": every frame was a first bar on an engine that never moved, so the IB, the
    settle, the prior day, the 80% Rule and the profiles were never compared. The field now says whether the runner
    ran the bar on a clone, and the record replayed in Python gives the runner's own frames."""
    rec, clones, own = [], [], []   # each frame dumped as it is made (its profile keeps growing after)
    r = AmtRunner("ES", 0.25, 5, amt.Settings({"ib_stop_time": "0830-1515"}), record=rec)
    r.commit_visuals = True
    process = r._process

    def keep(engine, *a):
        f = process(engine, *a)
        clones.append(engine is not r.engine)
        dump(own, f)
        return f
    r._process = keep
    bars = synth(4, 7, datetime(2026, 10, 16, 15, 30))
    for i, m in enumerate(bars):
        r.add_minute(m)
        if i % 37 == 0:
            r.commit_ready()
        if i % 997 == 0 and r.engine:
            r.preview()
    r.commit_ready()
    r.preview()
    flags = [x[3] for x in rec if x[0] == "B"]
    assert flags == clones
    assert flags.count(False) > 900 and 0 < flags.count(True) < 10    # committed bars on the engine, previews on clones
    path = tmp_path / "record.txt"
    write_record(rec, path)
    replayed = replay_python(path, {"ib_stop_time": "0830-1515"})
    assert replayed == own
    assert any(x.startswith("LN|IB High|") for x in replayed) and any(x.startswith("LN|SETTLE|") for x in replayed)


def test_the_parity_check_refuses_a_record_without_committed_bars(tmp_path, monkeypatch):
    """A record whose bars all ran on clones compares nothing carried from bar to bar: amt_parity.main says so and fails
    instead of printing PARITY OK."""
    monkeypatch.setattr(amt_parity, "AmtRunner", type("Clones", (AmtRunner,), {
        "_process": lambda self, engine, key, index, visuals: AmtRunner._process(self, engine.Clone(), key, index, visuals)}))
    monkeypatch.setattr(amt_parity.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("not run")))
    monkeypatch.setattr(amt_parity.sys, "argv", ["amt_parity.py", str(tmp_path), "3", "30"])
    assert amt_parity.main() == 1


# ---- r21-43: the alert runner turns on the lines' Show switches an Alert switch on needs ------------------------------

def test_the_alert_runner_has_every_line_an_alert_switch_on_watches():
    """Prior Day, Prior Week, Weekly Open, pdPOC/VAH/VAL, the prior month and the Developing lines hidden on a chart (or
    in every chart's starting settings), their Alert switches on (the defaults, and Alert VA Touch): the alert runner
    shows them all, so their alerts are armed. Before, only the groups' Enable switches were turned back on."""
    hidden = {"pd_enable": False, "pd_showH": False, "pw_enable": False, "pw_showL": False, "wk_showOpen": False,
              "cc_show_poc": False, "cc_show_vah": False, "cc_show_val": False, "macro_show_poc": False,
              "macro_show_month": False, "macro_show_hl": False, "mvp_master": False, "mvp_show_poc": False}
    ov = amt_hub.alert_overrides({**hidden, "cc_alert_va": True})
    assert not set(hidden) & set(ov), ov    # every one back at its default (on)
    # an Alert switch off leaves its lines as the chart has them (nothing to alert on)
    off = amt_hub.alert_overrides({**hidden, "pd_alert_hl": False, "wk_alert_open": False, "mvp_use_cur_alerts": False})
    assert (off["pd_enable"], off["wk_showOpen"], off["mvp_master"]) == (False, False, False)
    assert "pw_enable" not in off and "macro_show_poc" not in off
    assert off["cc_show_poc"] is False    # Alert VA Touch is off by default: pdPOC stays hidden


def test_alerts_at_hidden_lines_are_sent(tmp_path, monkeypatch):
    """A synthetic ES session with those lines hidden in the starting settings sends the PDH and Developing alerts it
    sends with them shown (before: none of them)."""
    hidden = {"pd_enable": False, "pw_enable": False, "wk_showOpen": False, "cc_show_poc": False, "cc_show_vah": False,
              "cc_show_val": False, "macro_show_poc": False, "macro_show_month": False, "macro_show_hl": False,
              "mvp_master": False}
    names = lambda sent: {t.split("] ")[1].split(" Cross")[0] for _, t in sent if " Cross " in t}   # noqa: E731
    shown, _ = session(tmp_path, monkeypatch, {"cc_alert_va": True})
    got, h = session(tmp_path, monkeypatch, {"cc_alert_va": True, **hidden})
    assert {"PDH", "Developing POC"} <= names(shown)
    assert names(got) == names(shown), (names(shown), names(got))
    (e,) = h.entries.values()
    assert all(getattr(e.runner.S, k) is True for k in hidden) and all(getattr(h.settings(), k) is False for k in hidden)


# ---- r21-44: a short window either side of 11:45 keeps its order on another instrument's hours -------------------------

def test_a_window_either_side_of_the_middle_of_the_session_moves_as_a_whole():
    """Each end moved on its own put 11:00-12:00 at 10:30-10:30 on CL (all day) and 09:50-09:30 on GC (through the
    night): both ends now move as the window's middle does, keeping its order and length. Windows that kept their order
    move as before."""
    cl, gc = sessions.rth_mover(CL.rth), sessions.rth_mover(GC.rth)
    cases = {"1100-1200": ("1030-1130", "0950-1050"), "1130-1200": ("1100-1130", "1020-1050"),
             "0645-0715": ("0615-0645", "0535-0605"), "0830-0930": ("0800-0900", "0720-0820"),
             "0830-1500:23456": ("0800-1330:23456", "0720-1230:23456"), "1700-0830:123456": ("1700-0800:123456", "1700-0720:123456"),
             "1430-1500": ("1300-1330", "1200-1230"), "1700-1600": ("1700-1600", "1700-1600")}
    for v, (on_cl, on_gc) in cases.items():
        assert (sessions.move_session(v, cl), sessions.move_session(v, gc)) == (on_cl, on_gc), v
    assert sessions.move_clocks("11:00", "12:00", cl) == ("10:30", "11:30")   # Session VP's start and end
    assert sessions.move_clocks("08:30", "15:00", gc) == ("07:20", "12:30")
    # every window keeps its order and, where the ends moved apart, its length
    for move in (cl, gc):
        for a in range(0, 1440, 5):
            for b in range(0, 1440, 5):
                x, y = sessions.move_window(a, b, move)
                assert (a == b) == (x == y) and (a < b) == (x < y), (a, b, x, y)


def test_the_amt_runs_a_short_window_as_one_hour_on_cl_and_gc():
    """Developing alerts limited to 11:00-12:00 and an RTH VWAP of 11:00-12:00: an hour on CL and GC too (before: all
    day on CL, 23h40m on GC, and a VWAP never on)."""
    hub = _hub(CL, GC)
    for sym, start in (("CL", 630), ("GC", 590)):
        s = hub.settings_for(sym, {"mvp_alert_time": "1100-1200", "vwap_start_h": 11, "vwap_start_m": 0, "vwap_end_h": 12,
                                   "vwap_end_m": 0})
        inside = [m for m in range(1440) if amt.in_session(datetime(2026, 10, 20, m // 60, m % 60), s.mvp_alert_time)]
        assert inside == list(range(start, start + 60)), (sym, s.mvp_alert_time)
        assert (s.vwap_start_h * 60 + s.vwap_start_m, s.vwap_end_h * 60 + s.vwap_end_m) == (start, start + 60)


@needs_node
def test_the_tpo_footer_shows_the_window_the_server_runs():
    """web/app.js ownHours moves a window as sessions.move_session does."""
    out = run_app(["ownHours"], """
let rth = ['08:00', '13:30'];
const inst = () => ({ rth });
/*APP*/
const a = ['1100-1200', '1130-1200', '0830-1500', '0645-0715', '1700-0830:123456', '1700-1600'].map(ownHours);
rth = ['07:20', '12:30'];
const b = ['1100-1200', '1130-1200', '0830-1500', '0645-0715'].map(ownHours);
console.log(JSON.stringify([a, b]));
""")
    assert out == [["1030-1130", "1100-1130", "0800-1330", "0615-0645", "1700-0800:123456", "1700-1600"],
                      ["0950-1050", "1020-1050", "0720-1230", "0535-0605"]]


# ---- r21-45: GC's TPO periods start at its 07:20 open, as its TPO session does -----------------------------------------

def _tpo_run(sym, fn, start, end, overrides=None):
    inst = {"GC": GC, "ES": ES}[sym]
    r = _hub(inst).new_runner(sym, 5, overrides)   # as the hub makes it: the TPO Session's move with it
    for m in _minutes(start, end, fn):
        r.add_minute(m)
    r.commit_ready()
    return r.engine


def test_gc_tpo_periods_start_at_its_open():
    """GC's TPO session is 07:20-12:30, but its 30-minute periods stayed on the clock: 07:20-07:30 was a period of its
    own. A high made at 07:20-07:29 and retested at 07:30-07:49 counted twice and drew a false Poor High; the same
    shape on ES (70 minutes later) draws none. GC's periods now run 07:20-07:50, 07:50-08:20...; ES's are as before."""
    def gc(t):
        m = t.hour * 60 + t.minute
        if 440 <= m < 450:
            return 3500.0, 3502.0, 3500.0, 3501.0     # 07:20-07:29 makes the high
        if 450 <= m < 470:
            return 3501.0, 3502.0, 3499.0, 3500.0     # 07:30-07:49 retests it
        return (3497.0, 3499.0, 3495.0, 3497.0) if 470 <= m < 750 else (3497.0, 3498.0, 3496.0, 3497.0)

    def es(t):   # the same, 70 minutes later
        return gc(t - timedelta(minutes=70))
    e = _tpo_run("GC", gc, datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 13, 0))
    assert e.S.sp_rth_session == "0720-1230"
    assert [(p.Price, p.High) for p in e.poor] == [(3495.0, False)]
    assert e.tpo[max(e.tpo)].Hits == 1
    e = _tpo_run("ES", es, datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 15, 30))
    assert [p.High for p in e.poor] == [False] and e.tpo[max(e.tpo)].Hits == 1


def test_tpo_periods_move_as_the_tpo_session_does():
    """This round's first change (never released) moved the periods by the open's move (GC -70) whatever the TPO
    Session: one written 1200-1500, nearer the close, runs 09:30-12:30 on GC (the close's move, -150), but its periods
    broke at 09:20/09:50, so 09:30-09:50 was a 20-minute first period (the released code's was 09:30-10:00, on the
    clock). A high made in its first 20 minutes and retested in the next 10 drew a false Poor High that ES, at 12:00,
    does not. They now move by the TPO Session's own move."""
    def shape(start, base=0.0):   # the session's first 20 minutes make the high, the next 10 retest it, then lower
        def fn(t):
            m = t.hour * 60 + t.minute
            if start <= m < start + 20:
                p = 3500.0, 3502.0, 3500.0, 3501.0
            elif start + 20 <= m < start + 30:
                p = 3501.0, 3502.0, 3499.0, 3500.0
            else:
                p = (3497.0, 3499.0, 3495.0, 3497.0) if start + 30 <= m < start + 150 else (3497.0, 3498.0, 3496.0, 3497.0)
            return tuple(x + base for x in p)
        return fn
    late = {"sp_rth_session": "1200-1500"}
    e = _tpo_run("GC", shape(570), datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 13, 0), late)
    assert e.S.sp_rth_session == "0930-1230" and e.TpoShift == -150
    assert [(p.Price, p.High) for p in e.poor] == [(3495.0, False)] and e.tpo[max(e.tpo)].Hits == 1
    e = _tpo_run("ES", shape(720, 3400.0), datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 15, 30), late)
    assert e.TpoShift == 0 and [(p.Price, p.High) for p in e.poor] == [(6895.0, False)] and e.tpo[max(e.tpo)].Hits == 1
    e = _tpo_run("GC", shape(440), datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 13, 0))
    assert e.TpoShift == -70 and [(p.Price, p.High) for p in e.poor] == [(3495.0, False)]


def test_the_gc_tpo_periods_indicators_md_gives_are_the_ones_drawn(monkeypatch):
    """r21m-08: INDICATORS.md said a TPO Session of 1200-1500 on GC used to start with a 09:30-09:50 period. That one
    was only in this round's first change: before it, as now, the periods were 09:30-10:00 and so on (its move, -150,
    is whole periods). The GC periods the page gives, the default TPO Session's and a 1200-1500 one's, are drawn so."""
    starts = []
    real = amt.Engine.UpdateTpo

    def update(self, b, active):
        before = self.tpoPeriod
        real(self, b, active)
        if active and self.tpoPeriod != before:
            starts.append(b.Start)
    monkeypatch.setattr(amt.Engine, "UpdateTpo", update)

    def periods(overrides):   # the first two on Tuesday
        starts.clear()
        _tpo_run("GC", lambda t: 3500.0 + t.minute % 7 * 0.1, datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 13, 0),
                 overrides)
        s = [x.strftime("%H:%M") for x in starts if x.date() == datetime(2026, 10, 20).date()]
        return f"{s[0]}-{s[1]}, {s[1]}-{s[2]}"
    doc = " ".join((ROOT / "docs" / "INDICATORS.md").read_text(encoding="utf-8").split())
    assert f"On GC the default TPO Session runs 07:20-12:30 and its periods {periods(None)} and so on" in doc
    assert (f"A TPO Session of 1200-1500 runs 09:30-12:30 on GC. Its periods are {periods({'sp_rth_session': '1200-1500'})} "
            "and so on, as before.") in doc
    assert "09:30-09:50" not in doc


def test_the_tpo_shift_is_the_tpo_sessions_own_move():
    """How far the hub moves the TPO Session's start, for any regular hours: its periods then fall in it as they do in
    the written one on ES (its first period as long), even where the open's and close's moves differ by a time that is
    not a whole number of periods (07:20-13:00: 50 minutes)."""
    odd = Instrument("XX", "Odd", 0.1, 10, "XX", rth=("07:20", "13:00"))
    hub = _hub(ES, CL, GC, odd)
    cases = {("ES", "0830-1500"): 0, ("ES", "1200-1500"): 0, ("GC", "0830-1500"): -70, ("GC", "1200-1500"): -150,
             ("CL", "0830-1500"): -30, ("CL", "1200-1500"): -90, ("CL", "1100-1200"): -30, ("XX", "1200-1500"): -120,
             ("XX", "0845-1500"): -70, ("GC", "1700-0830"): 0}
    for (sym, sess), shift in cases.items():
        assert hub.tpo_shift(sym, {"sp_rth_session": sess}) == shift, (sym, sess)
        moved = hub.settings_for(sym, {"sp_rth_session": sess}).sp_rth_session
        a, w = amt._session(moved)[0], amt._session(sess)[0]
        for p in (30, 60):
            assert (a - shift) % 1440 % p == w % p, (sym, sess, p)   # the first period as long as on ES
    assert hub.tpo_shift("GC") == -70 and hub.tpo_shift("ES") == 0   # the symbol's own settings


# ---- r21-46: the day type waits for the IB Calc Period set ---------------------------------------------------------------

@functools.lru_cache
def _all_day_types(ib):
    r = AmtRunner("ES", 0.25, 5, amt.Settings({"ib_session": ib}))
    seen = {}
    process = r._process

    def note(engine, *a):
        f = process(engine, *a)
        if engine is r.engine:
            seen[f.Bar.Start] = engine.dayType
        return f
    r._process = note
    for m in synth(25, 8, datetime(2026, 10, 16, 15, 30)):
        r.add_minute(m)
    r.commit_ready()
    return seen


def _day_types(ib, minutes):
    """The day type of each committed bar starting in `minutes` (minutes of the day), ES 5m, 25 synthetic days."""
    return {t: v for t, v in _all_day_types(ib).items() if t.hour * 60 + t.minute in minutes}


def test_the_day_type_waits_for_the_ib_calc_period_set():
    """With IB Calc Period 0830-1000 the IB is still forming at 09:30: the day type stays GAP UP/GAP DOWN/DEVELOPING (or
    a real type) until it ends, never Non-Trend Day. The NinjaTrader original tested a fixed 08:30-09:30 there; v1.2.3
    reads today's IB (tests/amt_parity.py with ib_session=0830-1000 matches)."""
    seen = set(_day_types("0830-1000", range(570, 600)).values())
    assert {"GAP DOWN", "GAP UP"} & seen and "Non-Trend Day" not in seen, seen


def test_the_day_type_waits_for_an_ib_calc_period_that_starts_after_the_open():
    """IB Calc Period 0900-1000: from 08:30 to 08:59 the IB had not started, but the day type read yesterday's IB as
    today's (ibH is only reset at the IB's first bar), and outside the IB window called the first day Non-Trend Day.
    Before today's IB is complete it reads the overnight range and holds Non-Trend Day, as an IB at the open does:
    those bars' day types are the ones 0830-0930 gives them while its IB forms."""
    late = _day_types("0900-1000", range(510, 540))
    at_open = _day_types("0830-0930", range(510, 540))
    assert len(late) > 100 and late == at_open
    assert "Non-Trend Day" not in late.values(), set(late.values())
    after = _day_types("0900-1000", range(600, 900))   # after it ends, the day can be one again
    assert "Non-Trend Day" in after.values()


# ---- r21-47: a Session profile through midnight is one profile ----------------------------------------------------------

def _session_run(sess, end):
    r = AmtRunner("ES", 0.25, 30, amt.Settings({"mvp_tf_mode": "Session", "mvp_sess_time": sess, "mvp_sess_dur": 23.0}))
    r.commit_visuals = True
    evening = lambda t: t.hour >= 17   # noqa: E731
    for m in _minutes(datetime(2026, 10, 19, 17, 0), end, lambda t: 6900.0 if evening(t) else 6950.0,
                      volume=lambda t: 1000 if evening(t) else 100):
        r.add_minute(m)
    r.commit_ready()
    return r.preview()[0]


def test_a_session_profile_through_midnight_is_one_profile():
    """Session Time 1700-1600 (or 1700-0830): the profile was keyed on the bar's calendar date, so it closed at midnight
    and a new one began (the evening's POC 6900 in one, 6950 after midnight in the other, the Developing POC from
    00:00). It is now keyed on the day its window began: one profile from 17:00, POC 6900."""
    for sess, end in (("1700-1600", datetime(2026, 10, 20, 15, 0)), ("1700-0830", datetime(2026, 10, 20, 8, 0))):
        f = _session_run(sess, end)
        assert [(p.Start, p.Stats.Poc) for p in f.Profiles] == [(datetime(2026, 10, 19, 17, 0), 6900.0)], sess
        dev = next(x for x in f.Lines if x.Name == "Developing POC")
        assert (dev.Price, dev.Start) == (6900.0, datetime(2026, 10, 19, 17, 0))
    # the next window starts the next profile at 17:00
    f = _session_run("1700-1600", datetime(2026, 10, 20, 18, 0))
    assert [p.Start for p in f.Profiles] == [datetime(2026, 10, 19, 17, 0), datetime(2026, 10, 20, 17, 0)]


# ---- r21-48: history from a weekday's 17:00 is no whole week, calendar or not ---------------------------------------------

def _week_run(start, end, fn, cal="equity"):
    r = AmtRunner("ES", 0.25, 30, amt.Settings(), calendar=cal)
    for m in _minutes(start, end, fn, cal=cal):
        r.add_minute(m)
    r.commit_ready()
    return r.engine


def test_history_from_a_wednesday_open_is_no_whole_week():
    """With a holiday calendar (every instrument has one), any 17:00 bar that began a week counted it as whole: history
    cut at a Wednesday's open left a Weekly nPOC at that partial week's POC, its high and low as the next week's prior
    week and the Wednesday's open as its Weekly Open. Now only a week's first open does, as without a calendar and in
    NinjaTrader."""
    price = lambda t: 7000.0 if t < datetime(2026, 10, 9, 17) else 6800.0   # noqa: E731
    for cal in (None, "equity"):
        e = _week_run(datetime(2026, 10, 7, 17, 0), datetime(2026, 10, 13, 10, 0), price, cal)
        assert not [n for n in e.naked if n.Kind == "Weekly"] and math.isnan(e.pwH) and math.isnan(e.pwL), cal
        e = _week_run(datetime(2026, 10, 7, 17, 0), datetime(2026, 10, 8, 10, 0), price, cal)
        assert math.isnan(e.weekOpen), cal


def test_a_week_that_opens_after_a_holiday_still_counts_whole():
    """The calendar's own cases stay: before a Friday holiday halt (Juneteenth 2026) the week opens at Thursday's 17:00,
    and after a Monday closure (Christmas 2023) at Monday's: history starting there is a whole week."""
    sessions.set_calendar(load_holidays(ROOT / "config"))
    e = _week_run(datetime(2026, 6, 18, 17, 0), datetime(2026, 6, 30, 10, 0),
                  lambda t: 6500.25 if t < datetime(2026, 6, 26, 17) else 6400.0)
    assert (e.pwH, e.pwL) == (6500.25, 6500.25) and [n.Price for n in e.naked if n.Kind == "Weekly"] == [6500.25]
    e = _week_run(datetime(2026, 6, 18, 17, 0), datetime(2026, 6, 19, 10, 0), lambda t: 6500.25)
    assert e.weekOpen == 6500.25
    e = _week_run(datetime(2023, 12, 25, 17, 0), datetime(2024, 1, 3, 10, 0),
                  lambda t: 4800.25 if t < datetime(2023, 12, 29, 17) else 4700.0)
    assert [n.Price for n in e.naked if n.Kind == "Weekly"] == [4800.25] and e.pwH == 4800.25


def test_week_first_open():
    e = amt.Engine(amt.Settings(), "ES", 0.25, 6800.0, 30)
    e.Calendar = "equity"
    assert e.WeekFirstOpen(datetime(2026, 10, 11, 17, 0))                  # Sunday
    assert not e.WeekFirstOpen(datetime(2026, 10, 7, 17, 0))               # an ordinary Wednesday
    assert not e.WeekFirstOpen(datetime(2026, 10, 12, 17, 0))              # an ordinary Monday
    assert not e.WeekFirstOpen(datetime(2026, 10, 11, 17, 30))
    e.Calendar = None
    assert e.WeekFirstOpen(datetime(2026, 10, 11, 17, 0)) and not e.WeekFirstOpen(datetime(2026, 10, 7, 17, 0))
