"""Round 22, hours and calendar: a holiday half-day's last bar ends at the halt or early close for TPO and Gap Analysis,
as NinjaTrader ends it; the micros' Christmas Eve settlement windows are their E-minis'; the TPO line in the Studies
panel shows the Session Time the server ran."""
import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

from test_amt import client  # noqa: F401  (the app on the simulator, as a fixture)
from test_hours_tpo_svp_gap import INDICATORS, _build, _parity, needs_mono
from test_hunt_web_chart import needs_node, run_app
from test_review_fixes import build_market

from chartsrv import gaps, sessions, tpo
from chartsrv.amt_runner import ct
from chartsrv.bars import parse_spec
from chartsrv.config import ROOT, Instrument, load_holidays
from chartsrv.gaps_service import GapWatch, SecondsRun, own_hours as gap_hours, run as gap_run
from chartsrv.models import Bar
from chartsrv.settlement import Settlements
from chartsrv.tpo_service import BarBuilder, Daily, chart_bars, own_hours as tpo_hours, run as tpo_run

CT = sessions.CT
CL = Instrument("CL", "Crude Oil", 0.01, 1000, rth=("08:00", "13:30"), calendar="energy")
ES = Instrument("ES", "E-mini S&P 500", 0.25, 50, rth=("08:30", "15:00"), calendar="equity")


def at(mo, d, h, mi=0, s=0, tz=CT):
    return int(datetime(2026, mo, d, h, mi, s, tzinfo=tz).timestamp() * 1000)


def holidays():
    sessions.set_calendar(load_holidays(ROOT / "config"))


def minutes(inst, base, start=(11, 24, 17), end=(11, 27, 16)):
    """1-minute bars where the instrument trades (its holidays' halts and early closes out), each one price that rises
    through the day: day * 1500 + the minute of the day, in ticks over base (CL's Thanksgiving 13:29 is 458.09)."""
    out, t, stop = [], at(*start), at(*end)
    while t < stop:
        d = sessions.to_ct(t)
        if sessions.is_open(t, inst.calendar) and not sessions._no_session(t):
            p = round(base + (d.day * 1500 + d.hour * 60 + d.minute) * inst.tick_size, 6)
            out.append(Bar(t, p, p, p, p, 10))
        t += 60_000
    return out


def close_of(bars, opens_at):
    return next(b[1] for b in bars if b[0] == opens_at)


def test_a_bar_a_halt_or_early_close_cuts_short_ends_there():
    """NinjaTrader ends a partial holiday's session at its halt or early close, its last bar with it: CL's 13:00 60-minute
    bar on Thanksgiving (13:30 energy halt) at 13:30, ES's 12:00 bar the day after (12:15 close) at 12:15, and on an RTH
    chart its 11:30 bar there too. Other days' bars are as before, and with no calendar given nothing is cut."""
    holidays()
    cl, es = minutes(CL, 60.0), minutes(ES, 5000.0)
    bars = chart_bars(cl, 60, calendar="energy")
    assert close_of(bars, at(11, 26, 13)) == at(11, 26, 13, 30)
    assert close_of(bars, at(11, 27, 13)) == at(11, 27, 13, 45)          # the day after: 13:45 close
    assert close_of(chart_bars(cl, 60), at(11, 26, 13)) == at(11, 26, 14)   # no calendar: as before
    normal = [b for b in bars if b[0] < at(11, 25, 17)]
    assert normal == [b for b in chart_bars(cl, 60) if b[0] < at(11, 25, 17)]   # a normal day: as before
    assert close_of(chart_bars(cl, 120, calendar="energy"), at(11, 26, 13)) == at(11, 26, 13, 30)
    assert close_of(chart_bars(es, 60, calendar="equity"), at(11, 27, 12)) == at(11, 27, 12, 15)
    assert close_of(chart_bars(es, 60, calendar="equity"), at(11, 26, 11)) == at(11, 26, 12)   # ends on the 12:00 halt
    rth = chart_bars(es, 60, ES.rth, "equity")
    assert close_of(rth, at(11, 27, 11, 30)) == at(11, 27, 12, 15) and close_of(rth, at(11, 25, 14, 30)) == at(11, 25, 15)
    # GapWatch's bars as they come: the same close
    for args, mins, k in [((60, None, "energy"), cl, at(11, 26, 13)), ((60, ES.rth, "equity"), es, at(11, 27, 11, 30))]:
        bb = BarBuilder(*args)
        closes = {got[1]: got[2] for got in map(bb.add, mins) if got}
        assert closes[k] == close_of(chart_bars(mins, *args), k)


def test_gap_and_tpo_on_a_halt_day_take_its_last_bar_as_ninjatrader():
    """CL 60m, RTH 08:00-13:30 (Use Instrument Hours): the 13:00 bar of Thanksgiving ends at the 13:30 halt, inside the
    regular hours, so Friday's gap is measured from its 13:29 close (458.09), not the 12:59 one (457.79), in the chart's
    gap table and in the alerts; and the TPO's 0800-1330 session has that bar. ES's 12:00 bar on the day after
    Thanksgiving gets letter H (index 7), as its 12:15 close puts it, not I."""
    holidays()
    cl = minutes(CL, 60.0)
    s = gap_hours(gaps.settings({}), CL.rth)
    eng, opens = gap_run(s, 60, cl, calendar="energy")
    fri = next(g for g in eng.History if ct(opens[g.StartBar]) == datetime(2026, 11, 27, 8))
    assert (fri.OpenPrice, fri.SettlePrice) == (469.8, 458.09)
    old, old_opens = gap_run(s, 60, cl)   # without the calendar: the bug's 12:59 close
    assert next(g for g in old.History if ct(old_opens[g.StartBar]).day == 27).SettlePrice == 457.79
    w = GapWatch({"on": True, "minutes": 60, **gaps.DEFAULTS}, CL.rth, CL.calendar)
    for b in cl:
        w.add(b)
    assert [(g.OpenPrice, g.SettlePrice) for g in w.engine.History] == [(g.OpenPrice, g.SettlePrice) for g in eng.History]
    # TPO: the 13:00 bar's close inside the 0800-1330 session
    run_s = tpo_hours(tpo.settings({}), CL.rth)
    assert run_s["SessionTime"] == "0800-1330"
    rec = []
    tpo_run(run_s, CL.tick_size, 60, cl, Daily("energy"), at(11, 30, 12), rec, calendar="energy")
    thu = [r[1] for r in rec if r[0] == "B" and datetime(2026, 11, 26, 8) < r[1] <= datetime(2026, 11, 26, 16)]
    assert thu[-1] == datetime(2026, 11, 26, 13, 30)
    ok, a, b = tpo.TpoProfile.ParseSession(run_s["SessionTime"])
    assert ok and a < thu[-1].hour * 60 + thu[-1].minute <= b
    p = tpo.TpoProfile(tpo.settings({}), ES.tick_size)
    p.sessionStartTime = datetime(2026, 11, 27, 8, 30)
    last = close_of(chart_bars(minutes(ES, 5000.0), 60, calendar="equity"), at(11, 27, 12))
    assert p.GetLetterIndex(ct(last)) == 7


def test_a_seconds_charts_bar_ends_at_a_halt():
    holidays()
    run = SecondsRun(gaps.settings({}), parse_spec("45s"), CL, False, at(11, 26, 13))
    assert run._end(at(11, 26, 13, 29, 45)) == at(11, 26, 13, 30)
    assert run._end(at(11, 25, 13, 29, 45)) == at(11, 25, 13, 30, 30)    # a normal day: a whole bar
    assert SecondsRun(gaps.settings({}), parse_spec("45s"), CL, True, at(11, 26, 13))._end(at(11, 26, 13, 29, 45)) \
        == at(11, 26, 13, 30)


@needs_mono
def test_tpo_and_gap_parity_over_thanksgiving(tmp_path):
    """The C# TPO Profile and Gap Analysis against this port over Thanksgiving and the day after, their halts and early
    closes from config/holidays.json: CL's 13:00 bar ends at the 13:30 halt, ES's at 12:00 and 12:15, all hours and RTH."""
    _build("tpo_csharp/build.sh", INDICATORS, tmp_path / "tpo")
    _build("gap_csharp/build.sh", INDICATORS, tmp_path / "gap")
    ends = {"CL": "2026-11-26T13:30:00", "ES": "2026-11-27T12:15:00"}   # CL's 13:00 bar, ES's 12:00 (RTH: 11:30) bar
    for root in ("CL", "ES"):
        for rth in ((), ("rth",)):
            assert "holidays" in _parity("tpo_parity.py", tmp_path / "tpo", 12, 60, 3, root, *rth, "holidays")
            assert "holidays" in _parity("gap_parity.py", tmp_path / "gap", 12, 60, 3, root, *rth, "holidays")
            assert f"B|{ends[root]}|" in (tmp_path / "tpo" / "tpo_record.txt").read_text(), (root, rth)
            assert f"|{ends[root]}|" in (tmp_path / "gap" / "gap_record.txt").read_text(), (root, rth)


def test_the_services_end_the_bar_at_the_instruments_halt(tmp_path, monkeypatch):
    """/api/gaps, /api/tpo and the gap alerts take the instrument's holidays (CL's energy calendar)."""
    monkeypatch.setattr(time, "time", lambda: at(11, 29, 12) / 1000)    # Sunday: the week is over
    monkeypatch.setenv("CHART_WORKERS", "0")

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        holidays()
        m.store.write([], [("CL", b) for b in minutes(CL, 60.0)])
        got = await m.gaps.get("CL", 60, gap_hours(gaps.settings({}), CL.rth), 30)
        fri = next(g for g in got["gaps"] if ct(g["st"]) == datetime(2026, 11, 27, 8))
        assert fri["settle"] == 458.09
        prof = await m.tpo.get("CL", 60, tpo_hours(tpo.settings({}), CL.rth))
        thu = [p for p in prof["profiles"] if all(454 <= r[0] < 459 for r in p["rows"])]
        assert len(thu) == 1 and max(r[0] for r in thu[0]["rows"]) >= 458.0   # the 13:00-13:30 prices are in it
        assert prof["session"] == "0800-1330"
        m.gaps.set_alerts("CL", {"on": True, "minutes": 60, **gaps.DEFAULTS})
        w = m.gaps.watches["CL"]
        await w.ready.wait()
        assert w.bars.calendar == "energy"
        assert [g.SettlePrice for g in w.engine.History if g.OpenPrice == 469.8] == [458.09]
    asyncio.run(go())


def test_the_micros_settle_in_their_e_minis_window_on_early_close_days():
    """MGC and SIL settle at GC's and SI's Christmas Eve times (11:29-11:30 and 11:24-11:25 CT), as MHG at HG's: every
    market that settles to another has its windows, the day after Thanksgiving and Christmas Eve too."""
    holidays()
    st = Settlements(SimpleNamespace(store=SimpleNamespace(get_setting=lambda k, d=None: d)), ROOT / "config")
    assert st.windows("MGC", at(12, 28, 10))[0] == (at(12, 24, 11, 29), at(12, 24, 11, 30))
    assert st.windows("SIL", at(12, 28, 10))[0] == (at(12, 24, 11, 24), at(12, 24, 11, 25))
    micros = {root: w.same_as for root, w in st.markets.items() if w.same_as}
    assert {"MGC", "SIL", "MHG", "MES", "MCL"} <= set(micros)
    for now in (at(11, 30, 10), at(12, 28, 10)):
        for root, parent in micros.items():
            assert st.windows(root, now) == st.windows(parent, now), (root, ct(now))


def test_api_tpo_says_the_session_it_ran(client):  # noqa: F811
    """A Session Time the profile reads moves to GC's hours; one it can't read runs as typed (0860-1500, a day mask)."""
    want = {"0830-1500": "0720-1230", "0860-1500": "0860-1500", "0830-1500:23456": "0830-1500:23456",
            "0830-2460": "0830-2460"}
    for typed, ran in want.items():
        r = client.get("/api/tpo", params={"symbol": "GC", "spec": "5m", "SessionTime": typed})
        assert r.status_code == 200 and r.json()["session"] == ran, (typed, r.text)
    off = client.get("/api/tpo", params={"symbol": "GC", "spec": "5m", "UseInstrumentHours": "false"})
    assert off.json()["session"] == "0830-1500"


@needs_node
def test_the_tpo_line_shows_the_session_the_server_ran():
    out = run_app(["ownHours", "renderTpoInfo"], """
const el = { textContent: '' }, $ = () => el, px = String;
const inst = () => ({ rth: ['07:20', '12:30'], tick_size: 0.1 });
const tpoSpecOk = () => true;
const S = { st: { tpo: true }, tpoError: '', tpoSet: { SessionTime: '0860-1500', UseInstrumentHours: true },
  tpo: { minutes: 5, step: 0.8, session: '0860-1500' } };
/*APP*/
const out = [];
renderTpoInfo();
out.push(el.textContent);
S.tpoSet.SessionTime = '0830-1500'; S.tpo.session = '0720-1230';
renderTpoInfo();
out.push(el.textContent);
console.log(JSON.stringify(out));
""")
    assert out[0].endswith(" 0860-1500 CT.") and out[1].endswith(" 0720-1230 CT."), out


def test_minutes_helper_leaves_out_the_halt():
    holidays()
    cl = minutes(CL, 60.0)
    thu = [b for b in cl if at(11, 26, 13) <= b.t < at(11, 26, 17)]
    assert ct(thu[-1].t) == datetime(2026, 11, 26, 13, 29) and thu[-1].c == 458.09
    assert not [b for b in cl if at(11, 26, 13, 30) <= b.t < at(11, 26, 17)]
