"""Older history from NinjaTrader (gapfill's older requests, add-on 20): from before anything stored, a month at a time,
in the background back to a year once a symbol's gap is filled, and at once for a chart scrolled back past its stored
minutes; one request at a time; an empty month is where NinjaTrader's history ends; charts aren't reloaded for it, the
studies once the year is in. The add-on asks each part of it from its contract by NinjaTrader's roll dates."""
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from chartsrv import sessions
from chartsrv.feeds.ninjatrader import NinjaTraderFeed
from chartsrv.gapfill import DAY_MS, OLDER_LIMIT, OLDER_SPAN_MS, OLDER_TRIES
from chartsrv.models import Bar
from test_app import client  # noqa: F401  (the app on a database of the test's own)
from test_hunt10_hold import Writer
from test_review_fixes import build_market

MIN = 60_000
HARNESS = Path(__file__).resolve().parent / "addon_csharp"
ROOT = Path(__file__).resolve().parents[2]


def open_minutes(start: int, n: int) -> list[int]:
    """The first n minutes from `start` the market is open."""
    out, t = [], start // MIN * MIN
    while len(out) < n:
        if sessions.is_open(t, "equity"):
            out.append(t)
        t += MIN
    return out


def store(m, sym, ts):
    m.store.write([], [(sym, Bar(t=t, o=6000.0, h=6001.0, l=5999.0, c=6000.25, v=10)) for t in ts])


def connect(m, version=20):
    """The add-on says hello (what NinjaTraderFeed._handle does): every symbol's gap asked for, then answered empty."""
    nt = NinjaTraderFeed("127.0.0.1", 0, "t")
    nt.writer, nt.stats["version"], nt.stats["connected"] = Writer(), version, True
    m.feed_objs.append(nt)
    gaps = m.gapfill.requests(int(time.time() * 1000), live=True, minutes=True, contracts=True, older=nt.older_ok())
    nt.writer.lines.extend(gaps)
    for line in gaps:   # nothing new from NinjaTrader: each gap done
        nt._line(m, f"Z|{line.split('|')[1]}|minutes|0|ok\n".encode())
    return nt


def older_lines(nt):
    return [ln for ln in nt.writer.lines if ln.endswith("|older")]


def answer(m, nt, line, minutes):
    """The add-on's answer to an older request: those minutes (Y lines), then Z."""
    sym = line.split("|")[1]
    for t in minutes:
        nt._line(m, f"Y|{sym}|{t}|6100|6101|6099|6100.5|7\n".encode())
    nt._line(m, f"Z|{sym}|minutes|{len(minutes)}|ok\n".encode())


@pytest.fixture
def market(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    calls = []
    real = m.history_changed
    m.history_changed = lambda sym, charts=True: (calls.append((sym, charts)), real(sym, charts))
    m.reloads = calls
    return m


def test_the_year_fills_in_month_by_month_until_ninjatraders_history_ends(market):
    m = market
    now = int(time.time() * 1000)
    floor = open_minutes(now - 20 * DAY_MS, 1)[0]
    store(m, "ES", [floor, now // MIN * MIN - 10 * MIN])
    nt = connect(m)
    asked = older_lines(nt)
    assert asked == [f"GAP|ES|{floor - OLDER_SPAN_MS}|{floor}|older"]   # ES only (nothing else stored), once its gap is done
    got = open_minutes(floor - 25 * DAY_MS, 3)                          # NinjaTrader's month: 3 minutes of it, say
    answer(m, nt, asked[0], got)
    rows = m.store.db.execute("SELECT t, v FROM bars_1m WHERE symbol='ES' AND t<? ORDER BY t", (floor,)).fetchall()
    assert [tuple(r) for r in rows] == [(t, 7) for t in got]
    assert m.reloads == []   # charts not reloaded
    asked = older_lines(nt)
    assert asked[-1] == f"GAP|ES|{got[0] - OLDER_SPAN_MS}|{got[0]}|older"     # on back from the new first minute
    answer(m, nt, asked[-1], [])                                             # an empty month: NinjaTrader's history ends
    assert m.store.get_setting(OLDER_LIMIT)["ES"][0] == got[0]
    assert m.gapfill.status["ES"]["state"] == "limit" and len(older_lines(nt)) == 2
    assert m.gapfill.want_older("ES", 0) == "limit"
    assert m.reloads == [("ES", False)]   # its studies once, the year's months in; no chart


def test_a_chart_scrolled_back_goes_first_with_the_span_it_wants(market, monkeypatch):
    monkeypatch.setenv("CHART_HISTORY_PREFILL_DAYS", "0")   # nothing in the background
    m = market
    now = int(time.time() * 1000)
    es, nq = open_minutes(now - 5 * DAY_MS, 1)[0], open_minutes(now - 6 * DAY_MS, 1)[0]
    store(m, "ES", [es])
    store(m, "NQ", [nq])
    nt = connect(m)
    assert older_lines(nt) == []
    assert m.gapfill.want_older("NQ", 90 * DAY_MS) == "fetching"
    assert older_lines(nt) == [f"GAP|NQ|{nq - 90 * DAY_MS}|{nq}|older"]   # sent at once
    assert m.gapfill.want_older("ES", 0) == "fetching"                    # waits for NQ's
    assert m.gapfill.want_older("NQ", 10 * DAY_MS) == "fetching" and len(older_lines(nt)) == 1
    answer(m, nt, older_lines(nt)[0], open_minutes(nq - 40 * DAY_MS, 2))
    assert older_lines(nt)[1] == f"GAP|ES|{es - OLDER_SPAN_MS}|{es}|older"
    answer(m, nt, older_lines(nt)[1], open_minutes(es - 10 * DAY_MS, 2))
    first = m.gapfill.floor("NQ")
    assert older_lines(nt)[2] == f"GAP|NQ|{first - OLDER_SPAN_MS}|{first}|older"   # NQ's second ask, after
    assert m.reloads == [("ES", False)]   # ES's studies, nothing more asked for it; not NQ's: it is still being asked
    answer(m, nt, older_lines(nt)[2], open_minutes(first - 10 * DAY_MS, 2))
    assert m.reloads == [("ES", False), ("NQ", False)]
    m.gapfill.want_older("ES", 0)
    answer(m, nt, older_lines(nt)[3], open_minutes(m.gapfill.floor("ES") - 10 * DAY_MS, 2))
    assert m.reloads == [("ES", False), ("NQ", False)]   # not again within 5 minutes...
    m.gapfill.older_reloaded["ES"] -= 301
    m.gapfill.kick_older()
    assert m.reloads[-1] == ("ES", False)                # ...but after (the watchdog's pass)


def test_a_late_answer_counts_and_nothing_else_is_asked_for_the_symbol_meanwhile(market):
    m = market
    now = int(time.time() * 1000)
    first = open_minutes(now - 5 * DAY_MS, 1)[0]
    store(m, "ES", [first])
    store(m, "NQ", [open_minutes(now - 5 * DAY_MS, 1)[0]])
    nt = connect(m)
    gf = m.gapfill
    asked = older_lines(nt)
    assert len(asked) == 1 and asked[0].split("|")[1] == "ES"
    gf.older_busy = ("ES", time.monotonic() - 1000)   # no answer in time (NinjaTrader's data connection down, say)
    gf.kick_older()
    assert gf.windows["ES"].late and gf.status["ES"]["state"] == "late"
    assert gf.want_older("ES", 0) == "wait"   # a chart at its first bar told so, not kept polling: asked again on its Z
    assert [ln.split("|")[1] for ln in older_lines(nt)] == ["ES", "NQ"]   # the others go on; ES not asked again
    gf.older_busy = ("NQ", time.monotonic() - 1000)
    for _ in range(OLDER_TRIES + 1):   # however long: not NinjaTrader's limit, no second request for it (two would mix)
        gf.kick_older()
    assert len(older_lines(nt)) == 2 and m.store.get_setting(OLDER_LIMIT) is None
    got = open_minutes(first - 10 * DAY_MS, 3)
    answer(m, nt, asked[0], got)   # the late answer: kept
    assert m.store.db.execute("SELECT COUNT(*) FROM bars_1m WHERE symbol='ES'").fetchone()[0] == 4
    assert older_lines(nt)[-1] == f"GAP|ES|{got[0] - OLDER_SPAN_MS}|{got[0]}|older"   # on back from it


def test_an_error_is_not_ninjatraders_limit_unless_it_keeps_failing(market):
    m = market
    now = int(time.time() * 1000)
    floor = open_minutes(now - 5 * DAY_MS, 1)[0]
    store(m, "ES", [floor])
    nt = connect(m)
    gf = m.gapfill
    for i in range(OLDER_TRIES):
        # an error after part of it: none of it kept (the first stored minute would hide the rest)
        sym = older_lines(nt)[-1].split("|")[1]
        for t in open_minutes(floor - 20 * DAY_MS, 2):
            nt._line(m, f"Y|{sym}|{t}|6100|6101|6099|6100.5|7\n".encode())
        nt._line(m, b"Z|ES|error|0|no data connection\n")
        assert m.store.db.execute("SELECT COUNT(*) FROM bars_1m WHERE symbol='ES'").fetchone()[0] == 1
        if i < OLDER_TRIES - 1:
            assert m.store.get_setting(OLDER_LIMIT) is None and gf.want_older("ES", 0) == "wait"
            gf.older_wait.clear()
            gf.kick_older()
    assert m.store.get_setting(OLDER_LIMIT)["ES"][0] == floor and gf.want_older("ES", 0) == "limit"
    assert len(older_lines(nt)) == OLDER_TRIES


def test_a_lost_data_connections_hole_waits_for_the_older_request(market):
    m = market
    now = int(time.time() * 1000)
    store(m, "ES", [open_minutes(now - 20 * DAY_MS, 1)[0], now // MIN * MIN - 10 * MIN])
    nt = connect(m)
    gf = m.gapfill
    assert gf.windows["ES"].older
    gf.holes = lambda sym, now: [(now - 30 * MIN, now - 5 * MIN)]   # the hole the lost connection left
    assert gf.again("ES", now) is None and gf.queue["ES"]   # queued, not dropped
    answer(m, nt, older_lines(nt)[0], [])
    assert nt.writer.lines[-1].startswith("GAP|ES|") and not nt.writer.lines[-1].endswith("older")   # sent after it


def test_an_older_add_on_is_never_asked(market):
    m = market
    now = int(time.time() * 1000)
    store(m, "ES", [open_minutes(now - 5 * DAY_MS, 1)[0]])
    nt = connect(m, version=19)
    assert older_lines(nt) == [] and m.gapfill.want_older("ES", 0) == "off"


def test_the_api_says_fetching_and_asks_ninjatrader(client, tmp_path):
    from chartsrv.app import M
    from chartsrv.store import Store
    m = M()
    first = open_minutes(int(time.time() * 1000) - 40 * DAY_MS, 1)[0]
    Store(tmp_path / "chart.db").write([], [("ES", Bar(t=first, o=6000.0, h=6001.0, l=5999.0, c=6000.25, v=10))])
    nt = NinjaTraderFeed("127.0.0.1", 0, "t")
    nt.writer, nt.stats["version"], nt.stats["connected"] = Writer(), 20, True
    m.feed_objs.append(nt)
    m.gapfill.older_ok = True
    d = client.get(f"/api/older?symbol=ES&spec=30m&before={first}&count=100").json()
    assert d["bars"] == [] and d["more"] and d["fetching"]   # 100 30m bars: under a month, so a month
    assert older_lines(nt) == [f"GAP|ES|{first - OLDER_SPAN_MS}|{first}|older"]
    d = client.get(f"/api/older?symbol=ES&spec=500t&before={first}&count=100").json()   # tick bars: trades only
    assert not d["more"] and d["why"] == "stored"
    n = len(older_lines(nt))
    d = client.get(f"/api/older?symbol=ES&spec=1D&before={first - 400 * DAY_MS}&count=100").json()
    assert d["why"] == "stored" and len(older_lines(nt)) == n   # a D chart's free-data days before them: not asked
    m.gapfill.older_ok = False   # no add-on that answers
    d = client.get(f"/api/older?symbol=ES&spec=30m&before={first}&count=100").json()
    assert not d["more"] and d["why"] == "stored"


@pytest.mark.skipif(not (shutil.which("mcs") and shutil.which("mono")), reason="needs mono")
def test_the_add_on_asks_each_older_month_from_its_contract(tmp_path):
    exe = tmp_path / "segs.exe"
    r = subprocess.run(["mcs", "-langversion:Latest", "-r:System.Core.dll", f"-out:{exe}", str(HARNESS / "Stubs.cs"),
                        str(ROOT / "ninjatrader" / "TatankaChartFeed.cs"), str(HARNESS / "SegmentsHarness.cs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {line.split(" ", 1)[0]: line for line in
           subprocess.run(["mono", str(exe)], capture_output=True, text=True, timeout=60).stdout.splitlines()}
    names = lambda s: re.findall(r"(ES \d\d-\d\d)@", s)   # noqa: E731
    assert names(out["gap"]) == ["ES 09-26", "ES 12-26"]                 # a gap: never older than the one charted last
    assert names(out["older"]) == ["ES 06-26", "ES 09-26", "ES 12-26"]   # older: each by the roll dates, none before the first
    assert names(out["before"]) == []                                         # all before the first roll date it knows: none


def test_an_amt_request_that_times_out_keeps_its_window_till_its_answer(market, monkeypatch):
    """The AMT's prior day asked of NinjaTrader times out (its data connection down) while the add-on still waits to
    answer it: nothing else is asked for that symbol until its Z (the add-on's lines name the symbol only: before, the
    hole asked once data came back took the AMT's Z, was marked filled with nothing, and never asked again). The hole
    is asked after it, and filled."""
    import asyncio

    from chartsrv import amt_hub
    from chartsrv.gapfill import ASKED
    m = market
    now = int(time.time() * 1000)
    mins = open_minutes(now - 2 * DAY_MS, 400)
    hole = mins[200:260]
    store(m, "ES", [t for t in mins if t not in hole])
    nt = connect(m)
    gf = m.gapfill
    for _ in range(50):
        if gf.windows.get("ES") is None:
            break
        nt._line(m, b"Z|ES|minutes|0|ok\n")
    m.store.set_setting(ASKED, {})
    gf.queue.clear()
    monkeypatch.setattr(amt_hub, "PD_NT_WAIT_S", 0.05)
    n0 = len(nt.writer.lines)
    b = mins[0]
    assert asyncio.run(m.amt._nt_value("ES", b, b + 6 * 60 * MIN, nt)) is None   # timed out: the stored trades
    assert "ES" not in gf.windows and "ES" in gf.nt_out   # its answer still owed: dropped when it comes
    nt.ticking_again(m, "ES")   # data back: its holes queued behind the AMT's request
    assert len(nt.writer.lines) == n0 + 1 and gf.queue.get("ES")
    for i in range(5):   # the AMT's answer, late: dropped
        nt._line(m, f"X|ES|{b + i * MIN + 1000}|6000.25|1\n".encode())
    nt._line(m, b"Z|ES|ticks|5|ok\n")
    hole_line = nt.writer.lines[-1]
    assert len(nt.writer.lines) == n0 + 2 and hole_line.startswith("GAP|ES|")
    for t in hole:
        nt._line(m, f"X|ES|{t + 5000}|6001.25|2\n".encode())
    nt._line(m, f"Z|ES|ticks|{len(hole)}|ok\n".encode())
    m.flush_for_read()
    got = m.store.db.execute("SELECT COUNT(*) FROM bars_1m WHERE symbol='ES' AND t>=? AND t<=?", (hole[0], hole[-1])).fetchone()[0]
    assert got == len(hole)
