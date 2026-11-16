"""Round 21's bug hunt, settlements and the NinjaTrader add-on: a settlement heard live after a day that settled
unchanged is today's, one NinjaTrader held (marked so by add-on 22) may be a day missed's; NinjaTrader's replay of the
new contract's last trade at a roll stays out of the old contract's session, the old contract's last prints queued
while the server was away stay in; a micro only quoted takes its own settlement, not its charted E-mini's of maybe
another month. The add-on's own (add-on 22) are in test_addon_replay.py."""
import asyncio
import time
from datetime import datetime

from chartsrv import sessions
from chartsrv.feeds.ninjatrader import NinjaTraderFeed
from chartsrv.models import Tick
from test_review_fixes import build_market

CT = sessions.CT


def _ct(*a):
    return int(datetime(*a, tzinfo=CT).timestamp() * 1000)


def _line(feed, m, monkeypatch, at, line):
    """`line` from the add-on at `at` (ms), as the server reads it."""
    monkeypatch.setattr(time, "time", lambda: at / 1000)
    feed._line(m, (line + "\n").encode())


def _ref(st, root, at):
    st.cache.clear()
    r = st.reference(root, at)
    return r and (r["t"], r["price"])


# ---- r21-c02: a settlement heard while connected, after a day that settled unchanged, is today's -----------------------

def test_a_change_heard_while_connected_after_a_day_settled_unchanged_is_todays(tmp_path, monkeypatch):
    """ZQ (only quoted) settled unchanged on Tuesday, so the add-on sent nothing; Wednesday's, heard at 14:10 CT, was
    stamped Tuesday's (the saved one two windows back looked like a day missed): from then to the 17:00 open the change
    was from Wednesday's own settlement, and every later day was a day early the same way."""
    m, _ = build_market(tmp_path, monkeypatch)
    st, feed = m.settlements, NinjaTraderFeed("127.0.0.1", 0, "")
    assert "ZQ" not in m.instruments
    _line(feed, m, monkeypatch, _ct(2026, 11, 9, 16, 0), "S|ZQ|96.36")     # Monday's, the one it held on connecting
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 10), "S|ZQ|96.37")   # Wednesday's (Tuesday's was unchanged)
    assert st.saved["ZQ"]["t"] == _ct(2026, 11, 11, 14, 0)
    assert _ref(st, "ZQ", _ct(2026, 11, 11, 15, 30)) == (_ct(2026, 11, 9, 14, 0), 96.36)    # the day's change: from Tuesday's
    assert _ref(st, "ZQ", _ct(2026, 11, 11, 17, 30)) == (_ct(2026, 11, 11, 14, 0), 96.37)
    _line(feed, m, monkeypatch, _ct(2026, 11, 12, 14, 10), "S|ZQ|96.375")  # Thursday's
    assert _ref(st, "ZQ", _ct(2026, 11, 12, 15, 30)) == (_ct(2026, 11, 11, 14, 0), 96.37)
    assert _ref(st, "ZQ", _ct(2026, 11, 12, 17, 30)) == (_ct(2026, 11, 12, 14, 0), 96.375)


def test_the_settlement_held_on_connecting_after_a_day_missed_is_still_that_days(tmp_path, monkeypatch):
    """Round 20's case, through the feed: Monday's saved, the PC off Tuesday; NinjaTrader connects Wednesday 14:10 CT
    with Tuesday's (its first on the connection), then CME publishes Wednesday's."""
    m, _ = build_market(tmp_path, monkeypatch)
    st = m.settlements
    _line(NinjaTraderFeed("127.0.0.1", 0, ""), m, monkeypatch, _ct(2026, 11, 9, 16, 0), "S|ZN|112.0")
    feed = NinjaTraderFeed("127.0.0.1", 0, "")   # Wednesday's connection
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 10), "S|ZN|112.5")
    assert st.saved["ZN"]["t"] == _ct(2026, 11, 10, 14, 0)
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 20), "S|ZN|113.0")
    assert _ref(st, "ZN", _ct(2026, 11, 11, 15, 30)) == (_ct(2026, 11, 10, 14, 0), 112.5)
    assert _ref(st, "ZN", _ct(2026, 11, 11, 17, 30)) == (_ct(2026, 11, 11, 14, 0), 113.0)


def test_the_settlements_ninjatrader_held_are_the_ones_marked_so():
    """Add-on 22 marks the settlements NinjaTrader held (sent again on a connect, its first after subscribing or after
    its data connection was down): those may be from before a day missed; the others are changes it heard live, also
    the first on a connection. From an older add-on, the first for a root on a connection is taken as held, the next
    ones as heard live, and a new connection starts over."""
    from test_ninjatrader_feed import send, start

    class Settled:
        def __init__(self):
            self.got = []

        def on_feed(self, root, price, first=True):
            self.got.append((root, price, first))

    async def go():
        _, m, server, port = await start()
        m.settlements = Settled()
        got = []
        for lines in [("HELLO||22", "S|ZQ|96.36|held", "S|ZN|112.5|held", "S|ZQ|96.37", "S|ZQ|96.375|held"),
                      ("HELLO||22", "S|ZQ|96.38"),
                      ("HELLO||21", "S|ZQ|96.36", "S|ZN|112.5", "S|ZQ|96.37"),
                      ("HELLO||21", "S|ZQ|96.37")]:
            _, w = await send(port, *lines)
            w.close()
            await asyncio.sleep(0.2)
            got.append(m.settlements.got)
            m.settlements.got = []
        server.close()
        return got
    assert asyncio.run(go()) == [[("ZQ", 96.36, True), ("ZN", 112.5, True), ("ZQ", 96.37, False), ("ZQ", 96.375, True)],
                                 [("ZQ", 96.38, False)],
                                 [("ZQ", 96.36, True), ("ZN", 112.5, True), ("ZQ", 96.37, False)],
                                 [("ZQ", 96.37, True)]]


def test_the_settlement_held_as_ninjatraders_data_came_back_after_a_day_missed_is_still_that_days(tmp_path, monkeypatch):
    """r21 review of c02: ZN only quoted, one add-on connection Monday to Wednesday. NinjaTrader's data connection (not
    the add-on's to the server) is down Tuesday 13:00 to Wednesday 14:10 CT; back, NinjaTrader raises the settlement it
    holds, Tuesday's (Wednesday's not published yet). It was not the first on the connection, so it was taken as heard
    live, today's: Wednesday's change until the 17:00 open was from Monday's, a day stale. The add-on marks it held."""
    m, _ = build_market(tmp_path, monkeypatch)
    st = m.settlements
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    feed.stats["version"] = 22
    _line(feed, m, monkeypatch, _ct(2026, 11, 9, 16, 0), "S|ZN|112.0")         # Monday's, heard live
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 10), "S|ZN|112.5|held")  # Tuesday's, held as its data came back
    assert st.saved["ZN"]["t"] == _ct(2026, 11, 10, 14, 0)
    assert _ref(st, "ZN", _ct(2026, 11, 11, 15, 30)) == (_ct(2026, 11, 10, 14, 0), 112.5)
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 25), "S|ZN|113.0")       # Wednesday's, heard live
    assert _ref(st, "ZN", _ct(2026, 11, 11, 15, 30)) == (_ct(2026, 11, 10, 14, 0), 112.5)
    assert _ref(st, "ZN", _ct(2026, 11, 11, 17, 30)) == (_ct(2026, 11, 11, 14, 0), 113.0)


def test_add_on_22s_change_heard_live_after_a_day_settled_unchanged_is_todays(tmp_path, monkeypatch):
    """c02 with add-on 22: the first S line on its connection is held (Monday's), Tuesday settles unchanged (nothing
    sent), Wednesday's is heard live: today's, also as the first unmarked one on a new connection."""
    m, _ = build_market(tmp_path, monkeypatch)
    st = m.settlements
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    feed.stats["version"] = 22
    _line(feed, m, monkeypatch, _ct(2026, 11, 9, 16, 0), "S|ZQ|96.36|held")
    _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 10), "S|ZQ|96.37")
    assert _ref(st, "ZQ", _ct(2026, 11, 11, 15, 30)) == (_ct(2026, 11, 9, 14, 0), 96.36)
    feed = NinjaTraderFeed("127.0.0.1", 0, "")   # a new connection
    feed.stats["version"] = 22
    _line(feed, m, monkeypatch, _ct(2026, 11, 12, 14, 10), "S|ZQ|96.375")
    assert _ref(st, "ZQ", _ct(2026, 11, 12, 15, 30)) == (_ct(2026, 11, 11, 14, 0), 96.37)


# ---- r21m-00: on a reconnect, a settlement is marked by when the add-on heard it -------------------------------------

def test_a_settlement_sent_on_a_reconnect_is_placed_by_when_the_addon_heard_it(tmp_path, monkeypatch):
    """r21 review of c02: the server back at Wednesday 14:12 CT, Monday's 96.36 saved, Tuesday settled unchanged. The
    add-on sent each settlement known again held on connecting, before its queue: Wednesday's 96.37, heard live at
    14:05 while the server was away, went as S|ZQ|96.37|held and was stamped Tuesday's (the change from Wednesday's own
    until 17:00). Add-on 22 now sends one heard live in the last 30 minutes unmarked (today's), one heard longer ago held
    (Tuesday's 96.37 heard on Tuesday stays Tuesday's), and a queued one only once, in its place in the queue. The
    add-on's side, and its lines through this server, are in test_addon_replay.py."""
    def placed(sub, root, lines):
        (tmp_path / sub).mkdir()
        m, _ = build_market(tmp_path / sub, monkeypatch)
        feed = NinjaTraderFeed("127.0.0.1", 0, "")
        feed.stats["version"] = 22
        _line(feed, m, monkeypatch, _ct(2026, 11, 9, 16, 0), f"S|{root}|96.36|held")   # Monday's
        feed = NinjaTraderFeed("127.0.0.1", 0, "")   # Wednesday's connection
        feed.stats["version"] = 22
        for ln in lines:
            _line(feed, m, monkeypatch, _ct(2026, 11, 11, 14, 12), ln)
        st = m.settlements
        return st.saved[root]["t"], _ref(st, root, _ct(2026, 11, 11, 15, 30))
    wed, tue, mon = _ct(2026, 11, 11, 14, 0), _ct(2026, 11, 10, 14, 0), _ct(2026, 11, 9, 14, 0)
    # heard live on Wednesday: as add-on 22 sends it now (queued, or sent again), and as it was sent before
    assert placed("now", "ZQ", ["S|ZQ|96.36|held", "S|ZQ|96.37"]) == (wed, (mon, 96.36))
    assert placed("again", "ZQ", ["S|ZQ|96.37"]) == (wed, (mon, 96.36))
    assert placed("before", "ZQ", ["S|ZQ|96.37|held", "S|ZQ|96.36|held", "S|ZQ|96.37"]) == (wed, (tue, 96.37))
    # heard live on Tuesday, the server away since before: held, Tuesday's (unmarked, it would be Wednesday's)
    assert placed("dayago", "ZQ", ["S|ZQ|96.36|held", "S|ZQ|96.37|held"]) == (tue, (tue, 96.37))
    assert placed("lastmark", "ZQ", ["S|ZQ|96.37"]) == (wed, (mon, 96.36))   # (the reviewer's fix: wrong here)


# ---- r21-c14: at a roll, NinjaTrader's replay of the new contract's last trade stays out of the old one's session ------

def test_the_new_contracts_replayed_close_at_a_roll_stays_out_of_the_old_contracts_last_minute(tmp_path, monkeypatch):
    """ES rolls Dec to Mar at 16:00:30 CT (the add-on's roll check in the break); NinjaTrader replays the Mar contract's
    last trade on subscribing, 40 s old, so it goes, stamped 16:00:40: the server moved it back to 15:59:59.999, so Dec's
    last bar and the day's close got Mar's price, the roll spread above. A late print of Dec's before the roll still goes
    into its session, and Mar's trades from the 17:00 open are stored."""
    import chartsrv.market as mk
    m, _ = build_market(tmp_path, monkeypatch)

    def at(t, f, *a):
        monkeypatch.setattr(mk, "now_ms", lambda: t)
        f(*a)

    async def go():
        at(_ct(2026, 12, 10, 15, 0), m.on_contract, "ES", "ES 12-26")
        for k in range(120):
            m.on_tick(Tick("ES", _ct(2026, 12, 10, 15, 58) + k * 1000, 6900.00 + (k % 3) * 0.25, 1, 1))
        m.on_tick(Tick("ES", _ct(2026, 12, 10, 16, 0, 5), 6900.25, 1, 0))   # Dec's, reported late: its own session
        at(_ct(2026, 12, 10, 16, 0, 30), m.on_contract, "ES", "ES 03-27")
        m.on_tick(Tick("ES", _ct(2026, 12, 10, 16, 0, 40), 6962.50, 1, 0))   # Mar's 15:59:59 close, replayed
        m.on_tick(Tick("ES", _ct(2026, 12, 10, 17, 0, 1), 6963.00, 2, 1))
        m.flush()
    asyncio.run(go())
    rows = m.store.db.execute("SELECT ts, price FROM ticks WHERE symbol='ES' AND ts >= ? ORDER BY ts",
                              (_ct(2026, 12, 10, 15, 59, 59),)).fetchall()
    assert [tuple(r) for r in rows] == [(_ct(2026, 12, 10, 15, 59, 59), 6900.50), (_ct(2026, 12, 10, 16, 0) - 1, 6900.25),
                                        (_ct(2026, 12, 10, 17, 0, 1), 6963.00)]
    bar = m.store.db.execute("SELECT h, c FROM bars_1m WHERE symbol='ES' AND t=?", (_ct(2026, 12, 10, 15, 59),)).fetchone()
    assert tuple(bar) == (6900.50, 6900.25)


def test_the_old_contracts_last_prints_queued_while_the_server_was_away_at_a_roll_stay_in_its_session(tmp_path, monkeypatch):
    """r21 review of c14: the server away from 15:55 to 16:10 CT on a roll day. The add-on queued Dec's last trades (two
    stamped just after 16:00:00 by the PC), its roll at 16:00:30 and Mar's replayed close. On connecting it wrote the C
    line of the contract it is on now first, so the server took the roll at 16:10 and left out Dec's last prints as
    after it (the 15:59 bar closed at 6900.25, not 6900.75). Add-on 22 sends them in the order queued (its harness
    test is in test_addon_replay.py): Dec's go into their session, Mar's replay stays out."""
    import chartsrv.market as mk
    m, _ = build_market(tmp_path, monkeypatch)
    feed = NinjaTraderFeed("127.0.0.1", 0, "")

    def line(at, ln):
        monkeypatch.setattr(time, "time", lambda: at / 1000)
        monkeypatch.setattr(mk, "now_ms", lambda: at)
        feed._line(m, (ln + "\n").encode())

    async def go():
        line(_ct(2026, 12, 10, 15, 0), "C|ES|ES 12-26")
        for k in range(30):
            line(_ct(2026, 12, 10, 15, 50) + k * 1000, f"T|ES|{_ct(2026, 12, 10, 15, 50) + k * 1000}|6899.00|1|1")
        back = _ct(2026, 12, 10, 16, 10)
        for ts, px in [(_ct(2026, 12, 10, 15, 59, 59) + 900, 6900.25), (_ct(2026, 12, 10, 16, 0) + 30, 6900.50),
                       (_ct(2026, 12, 10, 16, 0) + 60, 6900.75)]:
            line(back, f"T|ES|{ts}|{px}|3|1")                                # Dec's, queued
        line(back, "C|ES|ES 03-27")                                          # the roll, queued at 16:00:30
        line(back, f"T|ES|{_ct(2026, 12, 10, 16, 0, 40)}|6962.50|1|0")       # Mar's replay
        m.flush()
    asyncio.run(go())
    rows = m.store.db.execute("SELECT ts, price FROM ticks WHERE symbol='ES' AND ts >= ? ORDER BY ts",
                              (_ct(2026, 12, 10, 15, 59, 59),)).fetchall()
    last = _ct(2026, 12, 10, 16, 0) - 1
    assert [tuple(r) for r in rows] == [(_ct(2026, 12, 10, 15, 59, 59) + 900, 6900.25), (last, 6900.50), (last, 6900.75)]
    bar = m.store.db.execute("SELECT h, c FROM bars_1m WHERE symbol='ES' AND t=?", (_ct(2026, 12, 10, 15, 59),)).fetchone()
    assert tuple(bar) == (6900.75, 6900.75)
    assert m.contracts["ES"] == "ES 03-27"


# ---- r21-c38: a micro only quoted takes its own settlement while its E-mini is charted --------------------------------

def _mcl(tmp_path, monkeypatch, cl):
    """A Market with CL charted on `cl` (rolled there at 16:30 CT Wednesday Dec 9), MCL only quoted."""
    import chartsrv.market as mk
    m, _ = build_market(tmp_path, monkeypatch)
    assert "CL" in m.instruments and "MCL" not in m.instruments and m.settlements.markets["MCL"].same_as == "CL"
    m.contracts["CL"] = "CL 01-27"
    if cl != "CL 01-27":
        monkeypatch.setattr(mk, "now_ms", lambda: _ct(2026, 12, 9, 16, 30))

        async def roll():
            m.on_contract("CL", cl)
        asyncio.run(roll())
    return m


def _quote(m, monkeypatch, sym, last, prev, at):
    import chartsrv.market as mk
    monkeypatch.setattr(mk, "now_ms", lambda: at)
    monkeypatch.setattr(time, "time", lambda: at / 1000)
    m.on_quote(sym, last, prev)
    return m.quotes[sym]


def test_a_micro_only_quoted_takes_its_own_settlement_while_its_e_mini_charted_is_on_another_month(tmp_path, monkeypatch):
    """CL charted, rolled by volume to Feb; MCL in a watchlist, quoted by NinjaTrader's roll dates, still on Jan: its
    change was from CL Feb's settlement (the calendar spread off). Its month isn't known here, so its own counts:
    NinjaTrader's S line for it. The other way round too (MCL rolled first)."""
    m = _mcl(tmp_path, monkeypatch, "CL 02-27")
    m.store.write([Tick("CL", _ct(2026, 12, 10, 13, 29), 59.80, 10, 1)], [])   # CL Feb's Thursday window
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    _line(feed, m, monkeypatch, _ct(2026, 12, 10, 14, 0), "S|MCL|60.05")   # MCL Jan's Thursday settlement
    q = _quote(m, monkeypatch, "MCL1!", 60.10, 60.05, _ct(2026, 12, 10, 17, 30))
    assert (q["prev"], round(q["chg"], 2)) == (60.05, 0.05)   # not CL Feb's 59.80
    (tmp_path / "b").mkdir()
    m = _mcl(tmp_path / "b", monkeypatch, "CL 01-27")
    m.store.write([Tick("CL", _ct(2026, 12, 10, 13, 29), 60.40, 10, 1)], [])   # CL Jan's
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    _line(feed, m, monkeypatch, _ct(2026, 12, 9, 16, 1), "C|MCL|MCL 02-27|quote")
    _line(feed, m, monkeypatch, _ct(2026, 12, 10, 14, 0), "S|MCL|60.10")   # MCL Feb's
    q = _quote(m, monkeypatch, "MCL1!", 60.20, 60.10, _ct(2026, 12, 10, 17, 30))
    assert (q["prev"], round(q["chg"], 2)) == (60.10, 0.10)   # not CL Jan's 60.40


def test_a_micro_and_its_e_mini_both_only_quoted_share_the_e_minis(tmp_path, monkeypatch):
    """Neither month known (both on NinjaTrader's roll dates): the micro still settles to its E-mini's, as CME does."""
    m, _ = build_market(tmp_path, monkeypatch)
    assert "QG" not in m.instruments and "NG" not in m.instruments and m.settlements.markets["QG"].same_as == "NG"
    feed = NinjaTraderFeed("127.0.0.1", 0, "")
    _line(feed, m, monkeypatch, _ct(2026, 12, 10, 14, 0), "S|NG|4.512")
    q = _quote(m, monkeypatch, "QG1!", 4.550, 4.500, _ct(2026, 12, 10, 17, 30))
    assert q["prev"] == 4.512
