"""r23, the add-on's side of the Nov 16 history trouble (HistoryHarness.cs drives the real add-on, compiled with mono
against stand-ins for NinjaTrader, Stubs.cs):
- NinjaTrader answered every history request with ErrorCode Panic until it was restarted; the add-on asked each 22
  times (10 retries as trades, 10 more as 1-minute bars, 30 s apart). Add-on 23 retries an error NinjaTrader gives
  twice; no answer (or an error while its data connection is down) still ten times; and it skips the 1-minute bars
  when no whole minute fits in the stretch (none could be stored), answering as the trades did.
- NinjaTrader started mid-session raises a one-time catch-up of recent trades as live ones; its history, asked up to
  the first one's second, brought the others again and they were stored twice. Through NinjaTraderFeed and GapFill
  into a real store: each trade once, whatever order the catch-up comes in; but not when the history comes as
  1-minute bars (a stretch over 3 days, no tick history, trades failing): nothing to pair, counted twice in the bars.
The add-on compiles as C# 5 and 6 too (NinjaTrader 8's NinjaScript Editor)."""
import asyncio
import os
import random
import shutil
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from chartsrv import sessions
from chartsrv.feeds import ninjatrader
from chartsrv.feeds.ninjatrader import NinjaTraderFeed
from chartsrv.models import Bar, Tick

from test_review_fixes import build_market

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "addon_csharp"
MIN = 60_000
CASES = ["panic-short", "panic-long", "panic-older", "empty-short", "empty-long", "silent", "offline", "mixed"]
pytestmark = pytest.mark.skipif(not (shutil.which("mcs") and shutil.which("mono")), reason="needs mono")


@pytest.mark.parametrize("lang", ["5", "6", "Latest"])
def test_the_add_on_compiles_as_ninjatraders_csharp(tmp_path, lang):
    r = subprocess.run(["mcs", f"-langversion:{lang}", "-target:library", "-r:System.Core.dll", f"-out:{tmp_path / 'a.dll'}",
                        str(HARNESS / "Stubs.cs"), str(ROOT / "ninjatrader" / "TatankaChartFeed.cs")],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "error" not in r.stdout, r.stdout + r.stderr


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    """HistoryHarness.cs with the add-on, its port read from TCF_PORT (each test's server its own)."""
    work = tmp_path_factory.mktemp("history")
    src = (ROOT / "ninjatrader" / "TatankaChartFeed.cs").read_text(encoding="utf-8")
    src = src.replace("private const int    Port     = 8801;",
                      'private static readonly int Port = int.Parse(Environment.GetEnvironmentVariable("TCF_PORT") ?? "8801");')
    (work / "TatankaChartFeed.cs").write_text(src, encoding="utf-8")
    out = work / "history.exe"
    r = subprocess.run(["mcs", "-langversion:Latest", "-r:System.Core.dll", f"-out:{out}", str(HARNESS / "Stubs.cs"),
                        str(work / "TatankaChartFeed.cs"), str(HARNESS / "HistoryHarness.cs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return out


@pytest.fixture(scope="module")
def retries(exe):
    """Per case of HistoryHarness.cs: (requests, of them as trades, as 1-minute bars, the Z line sent)."""
    def run(name):
        r = subprocess.run(["mono", str(exe), name], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        parts = r.stdout.strip().split(" ", 3)
        assert len(parts) == 4 and parts[0].isdigit(), r.stdout
        return int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
    with ThreadPoolExecutor(len(CASES)) as pool:
        return dict(zip(CASES, pool.map(run, CASES)))


def test_an_error_ninjatrader_gives_is_retried_twice(retries):
    """Panic on every request: 3 tries as trades, 3 as 1-minute bars (before, 11 and 11, 30 s apart: 11 minutes a
    symbol), then the error."""
    assert retries["panic-long"] == (6, 3, 3, "Z|ES|error|0|Panic (harness)")
    assert retries["panic-older"] == (3, 0, 3, "Z|ES|error|0|Panic (harness)")   # older history: bars from the start


def test_no_whole_minute_no_one_minute_bars(retries):
    """A 40 s stretch (a reconnect's): 1-minute bars could store none of it, so they aren't asked; the answer is the
    trades' own (before: 11 more tries for the error; for no trades, an empty bars request and Z minutes)."""
    assert retries["panic-short"] == (3, 3, 0, "Z|ES|error|0|Panic (harness)")
    assert retries["empty-short"] == (1, 1, 0, "Z|ES|ticks|0|ok")
    assert retries["empty-long"] == (2, 1, 1, "Z|ES|minutes|3|ok")   # a whole minute in it: bars, as before


def test_no_answer_and_errors_while_offline_are_retried_as_before(retries):
    """No answer is retried ten times (NinjaTrader's data connection may be coming up). An error that comes while
    the data connection is down is taken as no answer, not one of the two: the request goes on once it is back."""
    assert retries["silent"] == (11, 11, 0, "Z|ES|error|0|no answer from NinjaTrader")
    assert retries["offline"] == (5, 5, 0, "Z|ES|ticks|5|ok")
    assert retries["mixed"] == (6, 6, 0, "Z|ES|ticks|5|ok")   # two errors with it up, three with it down: still on


def prints(rng, n):
    return [(7000 + rng.randint(-12, 12) * 0.25, rng.choice((1, 1, 1, 2, 3, 4, 5, 10)), rng.choice((-1, 0, 1))) for _ in range(n)]


def catch_up(exe, tmp_path, monkeypatch, order, answered, back=10 * MIN, flags=(), kind=None, daily=False):
    """NinjaTrader starts 10 minutes (`back`) after the stored data stops; its catch-up is the last minute's 150 trades,
    raised oldest first, its last trade first then oldest first, or newest first; its history holds them (or answers
    Panic; `flags`: the harness's tape lines for no tick history, or trades failing); 5 live trades as it downloads, 5
    after. `daily`: NinjaTrader's count of the day's contracts comes too (the add-on's volume check), the catch-up's
    trades 20 times their size and the live ones 510 contracts each (5,100 in all: the check needs 5,000). Returns the
    market, the lines the server got, its gap fill status for ES (its Z of `kind`), the stored trades, the tape's parts
    (history only, catch-up, as sent, live) and the harness's output."""
    monkeypatch.setattr(sessions, "is_open", lambda ts, group=None: True)   # whatever time it is here
    monkeypatch.setenv("CHART_HISTORY_PREFILL_DAYS", "0")
    m, _ = build_market(tmp_path, monkeypatch, feed="ninjatrader")
    monkeypatch.setattr(m, "history_changed", lambda sym, charts=True: None)
    now = int(time.time() * 1000)
    t0 = now - back
    m.store.write([Tick("ES", t0, 7000.0, 1, 1)], [("ES", Bar(t=t0 // MIN * MIN, o=7000.0, h=7000.0, l=7000.0, c=7000.0,
                                                               v=1, bv=1, n=1))])
    rng = random.Random(order)
    hist = [(t, *p) for t, p in zip(sorted(rng.sample(range(t0 + 5000, now - 62_000), 200)), prints(rng, 200))]
    catch = [(t, *p) for t, p in zip(sorted(rng.sample(range(now - 60_000, now - 2000), 150)), prints(rng, 150))]
    sent = catch if order == "oldest" else [catch[-1]] + catch[:-1] if order == "last" else catch[::-1]
    live, after = prints(rng, 5), prints(rng, 5)
    if daily:
        catch = [(t, px, size * 20, side) for t, px, size, side in catch]
        sent = catch if order == "oldest" else [catch[-1]] + catch[:-1] if order == "last" else catch[::-1]
        live, after = [(px, 510, side) for px, _, side in live], [(px, 510, side) for px, _, side in after]
    tape = tmp_path / "tape.txt"
    tape.write_text("".join([f"H {t} {round(px * 100)} {size} {side}\n" for t, px, size, side in hist]
                            + [f"C {t} {round(px * 100)} {size} {side}\n" for t, px, size, side in sent]
                            + [f"L {round(px * 100)} {size} {side}\n" for px, size, side in live]
                            + [f"A {round(px * 100)} {size} {side}\n" for px, size, side in after]
                            + ([] if answered else ["fail\n"]) + [f"{x}\n" for x in flags] + (["daily\n"] if daily else [])))

    async def go():
        feed = NinjaTraderFeed("127.0.0.1", 0, "")
        real, got = feed._line, []
        feed._line = lambda mk, raw: (got.append(raw.decode().rstrip("\n")), real(mk, raw))
        server = await asyncio.start_server(lambda r, w: feed._handle(m, r, w), "127.0.0.1", 0)
        env = {**os.environ, "TCF_PORT": str(server.sockets[0].getsockname()[1])}
        proc = await asyncio.create_subprocess_exec("mono", str(exe), "catchup", str(tape), env=env, stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            ready = (await asyncio.wait_for(proc.stdout.readline(), 60)).decode().strip()
            for _ in range(600):   # its history done (the Z: answered, or the error after the tries)
                if m.gapfill.status.get("ES", {}).get("state") == "done":
                    break
                await asyncio.sleep(0.05)
            proc.stdin.write(b"go\n")
            await proc.stdin.drain()
            out, err = await asyncio.wait_for(proc.communicate(), 60)
        finally:
            if proc.returncode is None:
                proc.kill()
        await asyncio.sleep(0.3)
        server.close()
        return ready, out.decode(), feed, got
    ready, out, feed, got = asyncio.run(go())
    assert ready == "ready", out
    m.flush_for_read()
    st = m.gapfill.status["ES"]
    assert st["state"] == "done" and st["kind"] == (kind or ("ticks" if answered else "error"))
    assert feed.stats["bad_lines"] == 0
    rows = [tuple(r) for r in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES' ORDER BY ts, rowid")]
    return m, got, st, rows, hist, catch, sent, live + after, out


@pytest.mark.parametrize("answered", [True, False], ids=["answered", "failing"])
@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_ninjatraders_catch_up_is_stored_once(exe, tmp_path, monkeypatch, order, answered):
    """Every trade once; the ones sent both ways at the history's (exchange) time with the live side; every 1-minute
    bar its trades'. Before: the catch-up's trades older than the first one's second stored twice."""
    m, got, st, rows, hist, catch, sent, live, out = catch_up(exe, tmp_path, monkeypatch, order, answered)
    first_live = sent[0][0] // 1000 * 1000   # the add-on's firstLive: its history stops at that second
    marked = [x for x in sent if x[0] < first_live]   # older than the first one's second: sent with their own time
    assert sorted(int(x.split("|")[6]) for x in got if x.startswith("T|ES|") and len(x.split("|")) == 7) == sorted(x[0] for x in marked)
    both = marked if answered else []
    want = [(7000.0, 1)] + [(px, size) for _, px, size, _ in (hist if answered else []) + catch] \
        + [(px, size) for px, size, _ in live]
    assert len(rows) == len(want) and Counter((r[1], r[2]) for r in rows) == Counter(want)   # each trade once
    have = set(rows)
    assert all((t, px, size, 0) in have for t, px, size, _ in (hist if answered else []))   # the history's: its time
    assert all((t, px, size, side) in have for t, px, size, side in both)   # sent both ways: the history's time, live side
    assert st.get("copies", 0) == len(both) and (len(both) > 100) == (answered and order != "oldest")
    bars = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    built = {}
    for t, px, size, side in rows:   # every 1-minute bar: its stored trades, in time order
        b = built.setdefault(t // MIN * MIN, Bar(t=t // MIN * MIN, o=px, h=px, l=px, c=px))
        b.add(px, size, side)
    assert bars == {k: (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) for k, b in built.items()}
    assert int(out.split()[-1]) == (1 if answered else 6)   # its history asked once (or tried 3 + 3 times)


@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_after_a_mid_session_start_the_volume_check_still_counts_the_catch_up(exe, tmp_path, monkeypatch, order):
    """NinjaTrader's count of the day's contracts (DailyVolume) as it starts already holds its catch-up, and the add-on's
    volume check counts every trade it sends from there, the catch-up's too. So after a mid-session start NinjaScript
    Output warns that trades are not reaching the server one for one (Nov 16: ES sent 13,527, NinjaTrader counted
    5,010) with add-on 23 as before, while the server keeps each trade once: that warning is then the catch-up, not
    trades stored twice. CONNECTIONS.md says so (round 3)."""
    m, got, st, rows, hist, catch, sent, live, out = catch_up(exe, tmp_path, monkeypatch, order, True, daily=True)
    first_live = sent[0][0] // 1000 * 1000
    assert len(rows) == 1 + len(hist) + len(catch) + len(live)   # each trade once
    assert st.get("copies", 0) == len([x for x in sent if x[0] < first_live]) and (st.get("copies", 0) > 100) == (order != "oldest")
    sent_n = sum(int(x.split("|")[4]) for x in got if x.startswith("T|ES|"))
    counted = sum(size for _, size, _ in live)
    caught = sum(x[2] for x in catch)
    assert counted >= 5000 and sent_n == caught + counted and caught > 0.25 * counted   # the catch-up: well over 25%
    printed = [x[len("printed "):] for x in out.splitlines() if x.startswith("printed ")]
    assert len(printed) == 1 and printed[0].startswith(f"TatankaChartFeed: ES sent {sent_n:,} contracts while "
                                                       f"NinjaTrader counted {counted:,} (")
    assert printed[0].endswith("x): trades are not reaching the server one for one")


def test_to_a_server_before_23_the_add_on_sends_no_mark(exe, tmp_path, monkeypatch):
    """A server whose V line says 22 (one before 23 sends none): it takes a T line of 6 fields only (a 7th makes the
    line a bad one, the trade lost), so the add-on sends none, and the catch-up is stored twice, as before."""
    monkeypatch.setattr(ninjatrader, "ADDON_VERSION", 22)
    m, got, st, rows, hist, catch, sent, live, out = catch_up(exe, tmp_path, monkeypatch, "newest", True)
    assert all(len(x.split("|")) == 6 for x in got if x.startswith("T|")) and len([x for x in got if x.startswith("T|ES|")]) == 160
    both = [x for x in sent if x[0] < sent[0][0] // 1000 * 1000]
    assert len(both) > 100 and len(rows) == 1 + len(hist) + len(catch) + len(live) + len(both) and st.get("copies", 0) == 0


@pytest.mark.parametrize("how", ["over-3-days", "no-tick-history", "trades-fail"])
def test_a_catch_up_whose_history_comes_as_1_minute_bars_counts_twice(exe, tmp_path, monkeypatch, how):
    """The add-on asks a stretch over 3 days as 1-minute bars from the start (TickHistoryMaxMs), and a shorter one as
    1-minute bars when its trades come back empty (no tick history) or fail (3 tries). Bars have no trade to pair: the
    server compares nothing (only a history of trades), the catch-up's trades stay as the add-on stamped them, in the
    live minute's bar, and the history's bars of their own minutes count them again. Not fixed: NINJATRADER.md's
    'Still stored twice' says so."""
    back = 4 * 86_400_000 if how == "over-3-days" else 10 * MIN
    flags = {"over-3-days": (), "no-tick-history": ("no-ticks",), "trades-fail": ("ticks-fail",)}[how]
    m, got, st, rows, hist, catch, sent, live, out = catch_up(exe, tmp_path, monkeypatch, "newest", True, back, flags,
                                                              "minutes")
    assert int(out.split()[-1]) == {"over-3-days": 1, "no-tick-history": 2, "trades-fail": 4}[how]   # requests made
    first_live = sent[0][0] // 1000 * 1000
    marked = [x for x in sent if x[0] < first_live]
    assert len([x for x in got if x.startswith("T|ES|") and len(x.split("|")) == 7]) == len(marked) > 100
    assert st.get("copies", 0) == 0   # marked, and not compared
    # the trades: the catch-up's once, as sent live; none from the history (bars only)
    assert Counter((r[1], r[2]) for r in rows) == Counter([(7000.0, 1)] + [(px, size) for _, px, size, _ in catch]
                                                          + [(px, size) for px, size, _ in live])
    bars = {r[0]: r[1] for r in m.store.db.execute("SELECT t, v FROM bars_1m WHERE symbol='ES'")}
    tape = sorted(hist + catch)
    whole = {t // MIN * MIN for t, *_ in tape if rows[0][0] < t // MIN * MIN and t // MIN * MIN + MIN <= first_live}
    for k in whole:   # the history's bars: their minutes' trades, the catch-up's among them
        assert bars[k] == sum(x[2] for x in tape if x[0] // MIN * MIN == k)
    stamped = min(int(x.split("|")[2]) for x in got if x.startswith("T|ES|")) // MIN * MIN
    assert stamped > max(whole)
    assert sum(v for k, v in bars.items() if k >= stamped) == sum(x[2] for x in catch) + sum(x[1] for x in live)   # again
    assert sum(x[2] for x in catch if x[0] // MIN * MIN in whole) > 0   # in both
