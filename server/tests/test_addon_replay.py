"""NinjaTrader replays the last trade when it subscribes or reconnects, and the add-on stamped it with the PC's
clock: in the 16:00 break it became the next session's first print at the last one's close (over a weekend, a
Saturday bar). Add-on 18 leaves out a trade whose own time is over 20 minutes old, unless several in a row are
(then the PC's clock is off and every trade would look old); add-on 19 counts them in a row only a few seconds apart,
so replays at separate reconnects never add up, and reads NinjaTrader's time zone once a minute, not every trade.
Add-on 21 judges a trade by its market's own clock once a minute of its trades has told it from the PC's, so the replay
after an outage of a few minutes is left out too. Add-on 22: a replay is never newer than the trades before it on its
contract, so one that is only has to be under 20 minutes old, and replays never set the market's clock; nothing from
Market Replay sets the settlement, prior close or day volume. A settlement NinjaTrader held (on subscribing, after its data
connection was down) is marked so, as is one sent over 30 minutes after it was heard, and a roll queued while the
server was away comes in order. A future taken off Instruments while NinjaTrader runs is quoted only, its rolls sent as
C|..|quote."""
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "addon_csharp"
SCENARIOS = {"replay": "ES", "skewed": "NQ", "live": "CL", "reconnects": "GC", "lull": "MNQ", "zone": "MES",
             "outage": "RTY", "delayed": "YM", "ratchet": "ZN", "thin": "ZC", "stall": "ZF", "playback": "ZB",
             "settleheld": "ZT", "rollqueued": "6E", "leftover": "SI", "settletoday": "ZQ", "settledayago": "SR3",
             "settlelost": "UB", "settlelostold": "TN"}
pytestmark = pytest.mark.skipif(not (shutil.which("mcs") and shutil.which("mono")), reason="needs mono")


@pytest.fixture(scope="module")
def sent(tmp_path_factory):
    """Per scenario of TradeHarness.cs: the prices of the T lines the add-on's send loop wrote to the server, its S lines
    (as "<scenario> S"), all its lines for the scenario's root (as "<scenario> lines") and what the harness printed (as
    "<scenario> out")."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    got = []

    def read(conn):
        with conn, conn.makefile("r", encoding="utf-8") as f:
            for line in f:
                got.append(line.strip())

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=read, args=(conn,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    work = tmp_path_factory.mktemp("trades")
    src = (ROOT / "ninjatrader" / "TatankaChartFeed.cs").read_text(encoding="utf-8").replace("Port     = 8801;", f"Port     = {port};")
    (work / "TatankaChartFeed.cs").write_text(src, encoding="utf-8")
    exe = work / "trades.exe"
    r = subprocess.run(["mcs", "-langversion:Latest", "-r:System.Core.dll", f"-out:{exe}", str(HARNESS / "Stubs.cs"),
                        str(work / "TatankaChartFeed.cs"), str(HARNESS / "TradeHarness.cs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    def run(name):
        r = subprocess.run(["mono", str(exe), name, SCENARIOS[name]], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()
    try:
        with ThreadPoolExecutor(len(SCENARIOS)) as pool:
            out = dict(zip(SCENARIOS, pool.map(run, SCENARIOS)))
        time.sleep(0.3)
    finally:
        srv.close()
    return {**{name: [float(ln.split("|")[3]) for ln in got if ln.startswith(f"T|{root}|")] for name, root in SCENARIOS.items()},
            **{f"{name} S": [float(ln.split("|")[2]) for ln in got if ln.startswith(f"S|{root}|")] for name, root in SCENARIOS.items()},
            **{f"{name} lines": [ln for ln in got if ln.split("|")[1:2] == [root]] for name, root in SCENARIOS.items()},
            **{f"{name} out": text for name, text in out.items()}}


def test_the_last_trade_replayed_on_subscribing_is_left_out(sent):
    assert sent["replay"] == [6910.50, 6910.75]


def test_trades_that_all_look_old_go_once_several_come_in_a_row(sent):
    """A PC clock 30 minutes off: the first three are left out, then every one goes (none lost for good)."""
    assert sent["skewed"] == [6900.75, 6901.00, 6901.25]


def test_live_trades_all_go(sent):
    assert sent["live"] == [6920.0, 6920.25, 6920.5]


def test_replays_at_separate_reconnects_never_add_up_to_a_run(sent):
    """r14-review-6: NinjaTrader's data reconnects 4 times between Friday's close and Sunday 16:40, no live trade between:
    each replay of the last trade is left out, the 4th too (before: the 4th went, stamped Sunday 16:40, and the server
    moved it to the 17:00 open, so the week opened at Friday's close); the next live trade goes."""
    assert sent["reconnects"] == [6930.00, 6931.25]


def test_a_pc_clock_that_is_off_keeps_its_trades_after_a_lull(sent):
    """Once a burst of old-looking trades showed the PC's clock is off, they go, also after a minute with none."""
    assert sent["lull"] == [6940.75, 6941.00, 6941.25]


def test_ninjatraders_time_zone_is_read_once_a_minute_not_every_trade(sent):
    """A thousand trades read Tools > Options' time zone once (a scan of every assembly, by reflection, on NinjaTrader's
    instrument thread); a minute on it is read again, so a change there is taken."""
    assert sent["zone out"] == "1 2"


def test_the_last_trade_replayed_after_an_outage_of_a_few_minutes_is_left_out(sent):
    """r18: NinjaTrader's data down 5 minutes, then its replay of the last trade, under the 20 minutes the PC's clock
    allows: it went as a print at the old price now (a wick, an alert on it). By the market's own clock it is 5 minutes
    old: left out; the live trade after it goes."""
    assert sent["outage"] == [6960.00, 6960.25, 6960.75]


def test_a_market_on_delayed_data_keeps_every_trade(sent):
    """Its trades all 10 minutes behind (an exchange not subscribed to): its own clock, so none is taken for a replay."""
    assert sent["delayed"] == [6970.0, 6970.25, 6970.5, 6971.0, 6971.25]


def test_replays_at_reconnects_never_move_the_markets_clock(sent):
    """r21-c16: NinjaTrader's data reconnects every 100 s and replays the last trade each time: the first, under 2
    minutes old, goes (by design); it no longer sets the market's clock 100 s back, so the next replay, 200 s old, is left
    out, and the ones after it (before, each one moved the clock back by its age and went: 30 minutes and more)."""
    assert sent["ratchet"] == [6900.00, 6900.25, 6901.0]


def test_a_quiet_markets_trades_go_after_the_pcs_clock_is_set_forward(sent):
    """r21-c17: the PC's clock set 3 minutes forward, then trades 10 s apart, each 3 minutes old by the market's clock as
    learnt: newer than any before on the contract, so not replays (before, every one was left out, none a run)."""
    assert sent["thin"] == [6980.00, 6980.25, 6981.00, 6981.25, 6981.50, 6981.75, 6982.00]


def test_trades_delivered_late_all_go(sent):
    """r21-c17: NinjaTrader stalls 150 s, then delivers what it held: each newer than the one before, so none is lost
    (before, the first three were, the run starting at the fourth)."""
    assert sent["stall"] == [6990.00, 6990.25, 6991.00, 6991.25, 6991.50, 6991.75, 6992.00, 6992.50]


def test_market_replays_settlement_prior_close_and_volumes_are_never_taken(sent):
    """r21-c15: Market Replay connected: its settlement isn't sent (it replaced the real one for a watchlist future's
    change, and was sent again on each connect), its prior close, day volume and the next contract's volume aren't kept
    (the Q lines, the roll by volume), nor its settlement to send again on a connect."""
    assert sent["playback S"] and set(sent["playback S"]) == {6900.25}   # (once more on connecting, if it came first)
    assert sent["playback"] == [6905.00]
    assert sent["playback out"] == "6890 1000 none 6900.25"


def test_a_settlement_ninjatrader_held_is_marked_so(sent):
    """r21 review of c02: NinjaTrader's data connection down over a publication while the add-on stayed connected to the
    server; back, NinjaTrader raised the settlement it held, a day old, and the server took it for a change heard live
    (today's, as it was not the first on the connection). Its first after subscribing or after the data connection was
    down (a minute's check) is marked held, as are the ones sent again on a connect; a change heard live is not. One
    back unchanged is not sent, and the next change is live. (The server was away until the end: all of them queued,
    so none is sent again on connecting, r21m-00.)"""
    s = [ln for ln in sent["settleheld lines"] if ln.startswith("S|")]
    assert s == ["S|ZT|112|held", "S|ZT|112.25", "S|ZT|112.5|held", "S|ZT|113", "S|ZT|113.25"]


def test_a_roll_queued_while_the_server_was_away_comes_after_the_old_contracts_trades(sent):
    """r21 review of c14: on connecting the add-on wrote the C line of the contract it is on now before the lines it
    queued, so the server took the roll before the old contract's last trades and left them out of their own session
    (the server leaves out a print it would move back before the roll). With a roll still queued, that C line is not
    written: the server gets the old contract's trades, then the roll, then the new contract's replay, as they came."""
    assert sent["rollqueued lines"][0] == "C|6E|6E 12-26"
    kinds = [(ln.split("|")[0], ln.split("|")[2] if ln.startswith("C|") else float(ln.split("|")[3]))
             for ln in sent["rollqueued lines"]]
    assert kinds == [("C", "6E 12-26"), ("T", 6900.0), ("T", 6900.25), ("T", 6900.5), ("C", "6E 03-27"), ("T", 6962.5)]


def test_a_future_taken_off_instruments_while_ninjatrader_runs_is_quoted_only(sent):
    """r21 review (r21m-01): SI charted (its R line released it) and on a watchlist; the server restarted with SI taken
    off Instruments, NinjaTrader running on. Only a chart future still held (no R line yet) became quote-only, so SI
    stayed a chart feed: its roll went as C|SI|<contract>, which the server drops for a root it does not chart, and
    no C|..|quote ever came. The server never heard of the roll, and a free alert on SI1! fired on the jump between the
    contracts. Now any chart future the I line leaves out is quoted only: its contract named |quote at once, its roll
    too, and on each connect."""
    cs = [ln for ln in sent["leftover lines"] if ln.startswith("C|")]
    assert len(cs) == 3, cs
    charted, quoted, rolled = cs
    assert charted.count("|") == 2   # while charted: its R line's release
    assert quoted == charted + "|quote"   # taken off Instruments: quoted only, on the same contract
    assert rolled.endswith("|quote") and rolled != quoted   # NinjaTrader's roll date
    assert sent["leftover out"] == rolled   # what a connect names it with


def _s_lines(sent, name):
    return [ln for ln in sent[f"{name} lines"] if ln.startswith("S|")]


def test_a_settlement_heard_live_while_the_server_was_away_is_sent_as_heard_live(sent):
    """r21 review of c02 (r21m-00): Wednesday's settlement heard live while the server was away (a restart, an
    Instruments edit, or the line written into a connection that was closing) reached it held first: on connecting the
    add-on sent each one known again, held, before the queue. The server took Wednesday's for Tuesday's (the saved one
    older than the window before), and the queued copy, the same, changed nothing. Now one still queued is not sent
    again (the queued ones come in order), and one sent again goes held only if heard over 30 minutes before (the
    server's publish grace)."""
    assert _s_lines(sent, "settletoday") == ["S|ZQ|96.36|held", "S|ZQ|96.37"]
    assert _s_lines(sent, "settlelost") == ["S|UB|96.37"]


def test_a_settlement_heard_live_a_day_before_the_server_is_back_goes_held(sent):
    """The verifier's counter-case: the server away from before Tuesday's publication to Wednesday; the add-on heard
    Tuesday's live. Sent unmarked (its mark when heard), the server would take it for Wednesday's. Over 30 minutes
    after it was heard, it goes held, queued or sent again on connecting."""
    assert _s_lines(sent, "settledayago") == ["S|SR3|96.36|held", "S|SR3|96.37|held"]
    assert _s_lines(sent, "settlelostold") == ["S|TN|96.37|held"]


def test_the_server_places_the_settlements_the_addon_sends_on_a_reconnect_by_when_they_were_heard(sent, tmp_path, monkeypatch):
    """The add-on's lines above, as the server reads them on Wednesday's connection at 14:12 CT, Monday's 96.36 saved
    and Tuesday settled unchanged (nothing sent). Heard live on Wednesday at 14:05: Wednesday's, and the day's change
    is from 96.36 until the 17:00 open (before, Wednesday's own 96.37, stamped Tuesday's). Heard live on Tuesday: still
    Tuesday's (as the reviewer's fix, the last mark, would have lost)."""
    from test_hunt21_addon import _ct, _line, _ref
    from test_review_fixes import build_market

    from chartsrv.feeds.ninjatrader import NinjaTraderFeed

    def placed(name, root):
        (tmp_path / name).mkdir()
        m, _ = build_market(tmp_path / name, monkeypatch)
        feed = NinjaTraderFeed("127.0.0.1", 0, "")
        feed.stats["version"] = 22
        _line(feed, m, monkeypatch, _ct(2026, 11, 9, 16, 0), f"S|{root}|96.36|held")   # Monday's
        feed = NinjaTraderFeed("127.0.0.1", 0, "")   # Wednesday's connection
        feed.stats["version"] = 22
        for ln in _s_lines(sent, name):
            _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 12), ln)
        st = m.settlements
        day = {d: st.windows(root, _ct(2026, 11, d, 23, 0))[0][1] for d in (9, 10, 11)}
        return day, st.saved[root]["t"], _ref(st, root, _ct(2026, 11, 11, 15, 30))
    for name, root in (("settletoday", "ZQ"), ("settlelost", "UB")):
        day, t, ref = placed(name, root)
        assert (t, ref) == (day[11], (day[9], 96.36)), name
    for name, root in (("settledayago", "SR3"), ("settlelostold", "TN")):
        day, t, ref = placed(name, root)
        assert (t, ref) == (day[10], (day[10], 96.37)), name
