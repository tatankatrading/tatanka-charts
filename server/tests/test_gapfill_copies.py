"""r23: NinjaTrader started mid-session raises a one-time catch-up of recent trades as live ones; the add-on sent them,
and the gap fill's history (asked up to the first live trade's second) brought them again: stored twice (Nov 16: ES
13,527 contracts sent where NinjaTrader counted 5,010). Add-on 23 marks a trade older than that second with its own time
(a T line's 7th field, once the server's V line says it takes one); the server pairs those with the history's trades in
runs (gapfill.copies) and keeps each once, at the history's time, with the live trade's aggressor side, and builds every
minute either touched again. The history's trades of the minute live trades start in now go into that minute's bar.

Each scenario here is the tape (every trade once, by its exchange time) as NinjaTrader's catch-up and its history
deliver it, through NinjaTraderFeed's lines into GapFill and a real store: every trade stored once, every 1-minute bar
(stored and the live feed's in memory) equal to the trades that belong to it, at their exchange time, or, for a trade
only ever sent live, at the time the add-on stamped it (all it has)."""
import random
from datetime import datetime

import pytest

from chartsrv.feeds.ninjatrader import NinjaTraderFeed
from chartsrv.gapfill import COPY_KINDS, COPY_RUN, EARLY_MS, copies
from chartsrv.models import Bar, Tick
from chartsrv.sessions import CT

from test_review_fixes import build_market

MIN = 60_000


def ms(h, mi, s=0, milli=0):
    return int(datetime(2026, 10, 14, h, mi, s, tzinfo=CT).timestamp() * 1000) + milli


STORED = (ms(10, 0, 30), 7000.0, 2, 1)   # where the stored data stops: one trade, and its bar
START = ms(10, 20, 5)                     # NinjaTrader starts: its catch-up comes from here, a trade every 5 ms


def tape(rng, end, n, span, before=150, edge=0):
    """`before` trades from after the stored one up to 1.5 s before the catch-up (`edge` of them in the minute it
    starts in), then the catch-up's `n` trades over the `span` ms before `end`: (exchange ms, price, size, side), each
    its own millisecond, in time order."""
    px = 7000.0
    out = []
    first = end - span
    early = sorted(rng.sample(range(STORED[0] + 1000, first // MIN * MIN), before - edge)
                   + rng.sample(range(first // MIN * MIN, first - 1500), edge))
    late = sorted(rng.sample(range(first, end), n))
    for t in early + late:
        px = max(6900.0, px + rng.choice((-0.5, -0.25, 0, 0.25, 0.5)))
        out.append((t, px, rng.choice((1, 1, 1, 2, 2, 3, 4, 5, 8, 10)), rng.choice((-1, 0, 1))))
    return out, out[before:]


def market(tmp_path, monkeypatch, last=STORED):
    m, _ = build_market(tmp_path, monkeypatch, feed="ninjatrader")
    m.reloads = []
    monkeypatch.setattr(m, "history_changed", lambda sym, charts=True: m.reloads.append(sym))
    t, px, size, side = last
    m.store.write([Tick("ES", t, px, size, side)], [("ES", Bar(t=t // MIN * MIN, o=px, h=px, l=px, c=px, v=size, bv=size, n=1))])
    return m


def feed(version=23):
    f = NinjaTraderFeed("127.0.0.1", 0, "")
    f.stats["version"] = version
    return f


def deliver(catchup, order):
    """NinjaTrader's catch-up in the order it raises it: oldest first; its last trade first, then the rest oldest first;
    newest first."""
    if order == "oldest":
        return list(catchup)
    if order == "last":
        return [catchup[-1]] + catchup[:-1]
    return catchup[::-1]


def run(m, f, trades, catchup, order, answered=True, live_after=20, moved=0, marks=True):
    """The add-on's lines for this tape: hello, the catch-up (T lines stamped as they come, START on; the add-on marks
    those older than the first one's second), live trades as the history downloads, the history (X lines up to that
    second, then Z; or an error), live trades after. `moved`: minutes the live feed moves on before the Z. Returns
    what should be stored, (ms, price, size, side) each, and the trades sent both ways."""
    m.gapfill.requests(START - 1000, live=True, minutes=True, contracts=True)
    sent = deliver(catchup, order)
    first_live = sent[0][0] // 1000 * 1000   # the add-on's firstLive: history stops at its second
    expect = [STORED]
    stamped = {}
    for k, (t, px, size, side) in enumerate(sent):
        rx = START + k * 5
        mark = marks and t < first_live
        f._line(m, f"T|ES|{rx}|{px}|{size}|{side}{f'|{t}' if mark else ''}\n".encode())
        stamped[(t, px, size)] = rx   # (by print too: a sweep's prints share one ms)
    half = live_after // 2   # (ms, ...) as the exchange has them; the add-on stamps each 20 ms later
    live = [(START + 2000 + k * 1000, 7010.0 + (k % 7) * 0.25, 1 + k % 3, (-1, 1)[k % 2]) for k in range(half)]
    live += [(START + k * MIN + 10_000, 7012.0, 1, 1) for k in range(1, moved + 1)]   # a trade a minute: moved on
    live += [(START + moved * MIN + 12_000 + k * 1000, 7011.0 + (k % 5) * 0.25, 1 + k % 4, (-1, 1)[k % 2])
             for k in range(live_after - half)]
    before = half + moved
    for t, px, size, side in live[:before]:   # while the history downloads
        f._line(m, f"T|ES|{t + 20}|{px}|{size}|{side}\n".encode())
    history = [x for x in trades if STORED[0] < x[0] < first_live]
    if answered:
        for t, px, size, _ in history:
            f._line(m, f"X|ES|{t}|{px}|{size}\n".encode())
        f._line(m, f"Z|ES|ticks|{len(history)}|ok\n".encode())
    else:
        f._line(m, b"Z|ES|error|0|Panic \n")
    for t, px, size, side in live[before:]:
        f._line(m, f"T|ES|{t + 20}|{px}|{size}|{side}\n".encode())
    m.flush_for_read()
    both = [x for x in sent if x[0] < first_live] if answered else []
    hist_ts = {x[0] for x in history} if answered else set()
    for t, px, size, side in trades:
        if t in hist_ts and (t, px, size) not in stamped:
            expect.append((t, px, size, 0))                          # the history's only: no side
        elif t in hist_ts:
            expect.append((t, px, size, side))                       # both: kept once, at its time, the live side
        elif (t, px, size) in stamped:
            expect.append((stamped[(t, px, size)], px, size, side))  # sent live only: as the add-on stamped it
    expect += [(t + 20, px, size, side) for t, px, size, side in live]
    return expect, both


def stored(m):
    return sorted(tuple(r) for r in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES'"))


def bars_of(trades):
    """1-minute bars of these trades (o, h, l, c, v, bv, sv, n) by minute, in time order."""
    out = {}
    for t, px, size, side in sorted(trades):
        b = out.get(k := t // MIN * MIN)
        if b is None:
            b = out[k] = Bar(t=k, o=px, h=px, l=px, c=px)
        b.add(px, size, side)
    return {k: (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) for k, b in out.items()}


def check_bars(m, expect):
    rows = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    want = bars_of(expect)
    assert {k: rows.get(k) for k in want} == want and set(rows) == set(want)
    for b in m.m1["ES"].bars:   # the live feed's own copies: as stored
        assert (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) == want[b.t]


@pytest.mark.parametrize("answered", [True, False], ids=["answered", "failing"])
@pytest.mark.parametrize("span", [70_000, 4_000], ids=["over-a-minute", "in-4s"])
@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_every_trade_once_and_every_bar_its_trades(tmp_path, monkeypatch, order, span, answered):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(f"{order}{span}"), START - 1000, 120, span)
    expect, both = run(m, feed(), trades, catchup, order, answered)
    assert stored(m) == sorted(expect)
    check_bars(m, expect)
    st = m.gapfill.health()["ES"]
    # oldest first: the history stops before them all, none sent both ways; else the ones older than the first's second
    assert bool(both) == (answered and order != "oldest") and st.get("copies", 0) == len(both)
    if both:
        assert len(both) >= COPY_RUN and m.reloads == ["ES"]
        side = {(t, px, size): s for t, px, size, s in catchup}
        at = {x[0] for x in both}
        for t, px, size, s in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES'"):
            if t in at:
                assert s == side[(t, px, size)]   # kept with the live trade's aggressor side


def test_the_burst_spread_over_a_minute_moves_back_into_its_minutes(tmp_path, monkeypatch, caplog):
    """Sent live at 10:20:05, they were 10:18:54 to 10:20:04 by the exchange: their bars there again, 10:20's without
    them (before, 10:20's bar held all of them, and the history's copies were in 10:18 and 10:19 too). Logged."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(7), START - 1000, 150, 70_000)
    expect, both = run(m, feed(), trades, catchup, "newest")
    assert f"gap fill ES: {len(both)} trades sent live came again in NinjaTrader's history" in caplog.text
    bars = {r[0]: r[1] for r in m.store.db.execute("SELECT t, v FROM bars_1m WHERE symbol='ES'")}
    for k in (ms(10, 18), ms(10, 19)):
        assert bars[k] == sum(x[2] for x in trades if x[0] // MIN * MIN == k)
    assert {x[0] // MIN * MIN for x in both} == {ms(10, 18), ms(10, 19), ms(10, 20)}
    assert stored(m) == sorted(expect)


@pytest.mark.parametrize("moved", [0, 2, 7])
def test_the_live_minute_holds_the_historys_trades_in_memory_and_after_the_feed_moved_on(tmp_path, monkeypatch, moved):
    """The history's trades of 10:20 (before the first live trade at 10:20:05) go into 10:20's bar: the live feed's own,
    in place, while it still holds it (it keeps the last 5 minutes), else the stored one. Before, that bar was short of
    them at every reconnect."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(moved), START - 1000, 60, 1_000, edge=5)   # 10:20:00 to 10:20:01.5: the history's
    expect, _ = run(m, feed(), trades, catchup, "oldest", moved=moved)
    assert any(x[0] // MIN * MIN == ms(10, 20) and x[0] < START for x in expect)   # the history has some of 10:20
    assert stored(m) == sorted(expect)
    check_bars(m, expect)
    held = [b.t for b in m.m1["ES"].bars]
    assert (ms(10, 20) in held) == (moved < 5) and held[-1] == ms(10, 20 + moved)
    hist = [x for x in trades if ms(10, 20) <= x[0] < catchup[0][0]]   # 10:20's from the history (the catch-up's sent live)
    bar = m.store.db.execute("SELECT o, v FROM bars_1m WHERE symbol='ES' AND t=?", (ms(10, 20),)).fetchone()
    assert len(hist) == 5 and tuple(bar) == (hist[0][1], sum(x[2] for x in expect if x[0] // MIN * MIN == ms(10, 20)))


def test_add_on_22_compares_nothing(tmp_path, monkeypatch):
    """No 7th field: nothing compared, as before r23: the catch-up's trades are stored twice."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(22), START - 1000, 120, 70_000)
    expect, both = run(m, feed(22), trades, catchup, "newest", marks=False)
    assert len(stored(m)) == len(expect) + len(both) and m.gapfill.health()["ES"].get("copies", 0) == 0


def test_a_run_shorter_than_copy_run_is_stored_twice(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(3), START - 1000, COPY_RUN - 1, 4_000)   # the first's second: some left out
    expect, both = run(m, feed(), trades, catchup, "newest")
    assert both and len(both) < COPY_RUN and len(stored(m)) == len(expect) + len(both)


@pytest.mark.parametrize("order,span,edge,moved",
                         [("newest", 4_000, 0, 0), ("oldest", 1_000, 5, 0), ("oldest", 1_000, 5, 7)],
                         ids=["copies", "live-minute", "moved-on"])
def test_a_live_trade_not_written_yet_keeps_its_historys_copy(tmp_path, monkeypatch, order, span, edge, moved):
    """The database not writable when the history's Z comes (the live trades still waiting for it, flush_for_read
    returns them), then writable again: nothing compared, the history's copies are kept too (stored twice, as before
    r23), nothing lost; and the live minute's bar, which takes the history's trades of that minute, holds the trades
    still waiting too (before, it was built from the stored ones only: 10:20 came out with 6 contracts of 254), also
    when the live feed has let it go (it keeps 5 minutes) and only the bars waiting to be written hold it."""
    m = market(tmp_path, monkeypatch)
    real = m.flush

    def fails():
        raise OSError("disk full")
    monkeypatch.setattr(m, "flush", fails)
    trades, catchup = tape(random.Random(0 if edge else 11), START - 1000, 60 if edge else 120, span, edge=edge)
    expect, both = run(m, feed(), trades, catchup, order, live_after=0, moved=moved)
    assert m.tick_buf   # the live trades: still waiting
    assert (ms(10, 20) in {b.t for b in m.m1["ES"].bars}) == (moved < 5)
    monkeypatch.setattr(m, "flush", real)
    m.flush_for_read()
    assert m.gapfill.health()["ES"]["copies"] == 0 and len(stored(m)) == len(expect) + len(both)
    assert bool(both) == (order == "newest")
    rows = stored(m)
    got = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    assert got == bars_of(rows)
    if edge:   # the history has some of 10:20, the live minute
        assert sum(x[0] // MIN * MIN == ms(10, 20) and x[0] < START for x in rows) == edge
    for b in m.m1["ES"].bars:
        assert (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) == got[b.t]


def sweeps(rng, n, span):
    """tape(), each minute of its catch-up opening with a sweep (3 prints in one ms, falling), closing with one
    (rising), and one every 10 trades between (rising), as a fast market prints them: (exchange ms, price, size, side)
    in the exchange's order, ms ties too."""
    trades, _ = tape(rng, START - 1000, n, span)
    out = [list(x) for x in trades]
    first = out[-n][0]
    for minute in sorted({x[0] // MIN * MIN for x in out[-n:]}):
        idx = [k for k, x in enumerate(out) if x[0] // MIN * MIN == minute and x[0] >= first]
        if len(idx) < 8:
            continue
        for q, k in enumerate(idx[:3]):   # its first three at its first one's ms
            out[k][0], out[k][1] = out[idx[0]][0], out[idx[0]][1] - 0.25 * q
        for q, k in enumerate(idx[-3:]):   # its last three at its last one's
            out[k][0], out[k][1] = out[idx[-1]][0], out[idx[-3]][1] + 0.25 * q
        for a in range(8, len(idx) - 6, 10):   # and between
            for q, k in enumerate(idx[a:a + 3]):
                out[k][0], out[k][1] = out[idx[a]][0], out[idx[a]][1] + 0.25 * q
    return [tuple(x) for x in out], [tuple(x) for x in out[-n:]]


def in_order(trades):
    """1-minute bars of these trades as they are listed (no sort: trades of one ms in their order)."""
    out = {}
    for t, px, size, side in trades:
        b = out.get(k := t // MIN * MIN)
        if b is None:
            b = out[k] = Bar(t=k, o=px, h=px, l=px, c=px)
        b.add(px, size, side)
    return {k: (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) for k, b in out.items()}


@pytest.mark.parametrize("answered", [True, False], ids=["answered", "failing"])
@pytest.mark.parametrize("span", [70_000, 4_000], ids=["over-a-minute", "in-4s"])
@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_trades_of_one_ms_keep_the_exchanges_order(tmp_path, monkeypatch, order, span, answered):
    """Sweeps (several prints in one ms) open and close the catch-up's minutes. The tape as every reader takes it (ORDER
    BY ts, rowid: bars, replay, volume profiles) is the exchange's, ms ties too, for every delivery order; every bar's
    open and close are its first and last print. Before, a copy was kept by moving the live trade to the history's time:
    it kept its own rowid, so a catch-up sent newest first came out backwards within each ms (10:19 closed at a sweep's
    first print, not its last)."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = sweeps(random.Random(f"{order}{span}"), 120, span)
    assert len(trades) - len({x[0] for x in trades}) >= 2 * 8   # 8 sweeps at least
    expect, both = run(m, feed(), trades, catchup, order, answered)
    assert bool(both) == (answered and order != "oldest") and m.gapfill.health()["ES"].get("copies", 0) == len(both)
    assert stored(m) == sorted(expect)
    # the order to read them in: by stored time, a ms's trades as the exchange printed them (a trade stored at the
    # exchange's time: the history's, a copy kept once; one sent live only has its own stamp)
    seq = {x[:3]: k for k, x in enumerate(trades)}
    want = sorted(expect, key=lambda x: (x[0], seq.get(x[:3], -1)))
    tape_ = [tuple(r) for r in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES' "
                                                   "ORDER BY ts, rowid")]
    assert tape_ == want
    bars = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    assert bars == in_order(want)
    for b in m.m1["ES"].bars:
        assert (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) == bars[b.t]


def test_unmarked_trades_from_add_on_23_are_stored_twice(tmp_path, monkeypatch):
    """Add-on 23 sends a trade unmarked while its connection's server hasn't said its version (a line queued before
    the V line, then sent as the queue drains): nothing to compare, stored twice, as from add-on 22."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(23), START - 1000, 120, 70_000)
    expect, both = run(m, feed(23), trades, catchup, "newest", marks=False)
    assert both and len(stored(m)) == len(expect) + len(both) and m.gapfill.health()["ES"].get("copies", 0) == 0


@pytest.mark.parametrize("order", ["last", "newest"])
def test_a_history_answered_as_1_minute_bars_counts_the_catch_up_twice(tmp_path, monkeypatch, order):
    """The add-on answers with 1-minute bars (Y lines) for a stretch over 3 days, or when the trades come back empty (no
    tick history) or fail: there is no trade to pair, so nothing is compared (a Z of kind minutes). The catch-up's trades
    stay as the add-on stamped them, in the live minute's bar (10:20), and the history's bars of their own minutes
    (10:18, 10:19) count them again: those minutes' volume twice. NINJATRADER.md's 'Still stored twice' says so; the
    real add-on's side of it is test_addon_history's."""
    m = market(tmp_path, monkeypatch)
    f = feed()
    trades, catchup = tape(random.Random(f"minutes{order}"), START - 1000, 150, 70_000)
    m.gapfill.requests(START - 1000, live=True, minutes=True, contracts=True)
    sent = deliver(catchup, order)
    first_live = sent[0][0] // 1000 * 1000
    for k, (t, px, size, side) in enumerate(sent):
        f._line(m, f"T|ES|{START + k * 5}|{px}|{size}|{side}{f'|{t}' if t < first_live else ''}\n".encode())
    marked = [x for x in sent if x[0] < first_live]
    assert len(marked) >= COPY_RUN and len(m.gapfill.windows["ES"].early) == len(marked)   # kept to compare...
    whole = {}   # the history's 1-minute bars: the whole minutes after the stored data's, up to the first's second
    for t, px, size, side in trades:
        if t < first_live and (k := t // MIN * MIN) > STORED[0] and k + MIN <= first_live:
            b = whole.setdefault(k, Bar(t=k, o=px, h=px, l=px, c=px))
            b.add(px, size, side)
    for k, b in sorted(whole.items()):
        f._line(m, f"Y|ES|{k}|{b.o}|{b.h}|{b.l}|{b.c}|{b.v}\n".encode())
    f._line(m, f"Z|ES|minutes|{len(whole)}|ok\n".encode())
    m.flush_for_read()
    assert m.gapfill.health()["ES"]["kind"] == "minutes" and m.gapfill.health()["ES"].get("copies", 0) == 0   # ...not
    rows = stored(m)   # the trades: the catch-up's once, as the add-on stamped them; the history brought none
    assert rows == sorted([STORED] + [(START + k * 5, px, size, side) for k, (_, px, size, side) in enumerate(sent)])
    bars = {r[0]: r[1] for r in m.store.db.execute("SELECT t, v FROM bars_1m WHERE symbol='ES'")}
    assert {ms(10, 18), ms(10, 19)} <= set(whole)
    for k, b in whole.items():   # the history's bars: their minutes' trades, the catch-up's among them
        assert bars[k] == b.v
    assert bars[ms(10, 20)] == sum(x[2] for x in catchup)   # the catch-up again, at the add-on's stamps
    twice = [x for x in catchup if x[0] // MIN * MIN in whole]
    assert twice and sum(v for k, v in bars.items() if k >= ms(10, 18)) \
        == sum(x[2] for x in trades if x[0] >= ms(10, 18)) + sum(x[2] for x in twice)


def test_a_catch_up_longer_than_tail_keeps_its_older_copies_twice(tmp_path, monkeypatch):
    """Only the history's last TAIL trades are held for its Z (the older ones are written in chunks as they come): of
    a longer catch-up, only those are kept once; the rest of it is stored twice. (TAIL 60 and CHUNK 50 here for
    20,000 each.)"""
    from chartsrv import gapfill as gapfill_mod
    monkeypatch.setattr(gapfill_mod, "TAIL", 60)
    monkeypatch.setattr(gapfill_mod, "CHUNK", 50)
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(60), START - 1000, 150, 70_000)
    expect, both = run(m, feed(), trades, catchup, "newest")
    n = m.gapfill.health()["ES"]["copies"]
    assert len(both) > 110 and 60 <= n < 110 and len(stored(m)) == len(expect) + len(both) - n
    rows = stored(m)
    for t, px, size, side in both[:n]:   # the newest n of them (sent first): once, at the history's time, the live side
        assert (t, px, size, side) in rows
    stamps = {(px, size, side) for t, px, size, side in rows if t >= START}
    for t, px, size, side in both[n:]:   # the older ones: the history's (no side) and the live one
        assert (t, px, size, 0) in rows and (px, size, side) in stamps


def test_catch_up_trades_from_before_the_stored_data_ends_are_stored_twice(tmp_path, monkeypatch):
    """The gap's history starts where the stored data ends: catch-up trades from before then (stored already, sent
    live again) are never in it, so never compared: stored twice. The ones after are kept once."""
    m = market(tmp_path, monkeypatch)
    f = feed()
    _, cat = tape(random.Random(4), START - 1000, 140, 70_000)
    old = cat[:20]   # stored before (the add-on sent them until NinjaTrader closed)
    m.store.write([Tick("ES", *x) for x in old], [])
    m.gapfill.requests(START - 1000, live=True, minutes=True, contracts=True)
    assert m.gapfill.windows["ES"].start == old[-1][0]
    sent = cat[::-1]
    first_live = sent[0][0] // 1000 * 1000
    for k, (t, px, size, side) in enumerate(sent):
        f._line(m, f"T|ES|{START + k * 5}|{px}|{size}|{side}{f'|{t}' if t < first_live else ''}\n".encode())
    hist = [x for x in cat if old[-1][0] < x[0] < first_live]
    for t, px, size, _ in hist:
        f._line(m, f"X|ES|{t}|{px}|{size}\n".encode())
    f._line(m, f"Z|ES|ticks|{len(hist)}|ok\n".encode())
    m.flush_for_read()
    assert m.gapfill.health()["ES"]["copies"] == len(hist) > 100
    rows = stored(m)
    assert len(rows) == 1 + len(cat) + len(old)
    for t, px, size, side in old:   # at its time, and again as the add-on stamped it
        assert (t, px, size, side) in rows and sum(r[1:] == (px, size, side) and r[0] >= START for r in rows) >= 1


def test_a_reconnect_before_the_historys_answer_compares_nothing(tmp_path, monkeypatch):
    """The add-on reconnects (a new hello) before the history's Z: the window and its marked trades are gone; the
    stretch before the catch-up's live stamps is a hole now, asked as one, and a hole is not compared: the catch-up's
    trades it brings again are stored twice."""
    m = market(tmp_path, monkeypatch)
    f = feed()
    trades, catchup = tape(random.Random(8), START - 1000, 120, 70_000)
    m.gapfill.requests(START - 1000, live=True, minutes=True, contracts=True)
    sent = catchup[::-1]
    first_live = sent[0][0] // 1000 * 1000
    for k, (t, px, size, side) in enumerate(sent):
        f._line(m, f"T|ES|{START + k * 5}|{px}|{size}|{side}{f'|{t}' if t < first_live else ''}\n".encode())
    assert len(m.gapfill.windows["ES"].early) > 100
    m.gapfill.requests(START + 2000, live=True, minutes=True, contracts=True)   # the hello again
    assert not m.gapfill.windows["ES"].early and m.gapfill.windows["ES"].start >= START
    f._line(m, b"Z|ES|ticks|0|ok\n")   # the new gap at the end: nothing in it
    gap = m.gapfill.next("ES")   # then the hole
    assert gap and gap.startswith("GAP|ES|") and m.gapfill.windows["ES"].hole
    w = m.gapfill.windows["ES"]
    hist = [x for x in trades if w.start < x[0] < w.end]
    for t, px, size, _ in hist:
        f._line(m, f"X|ES|{t}|{px}|{size}\n".encode())
    f._line(m, f"Z|ES|ticks|{len(hist)}|ok\n".encode())
    m.flush_for_read()
    again = [x for x in hist if x in catchup]
    assert len(again) > 50 and m.gapfill.health()["ES"].get("copies", 0) == 0
    assert len(stored(m)) == 1 + len(hist) + len(catchup)   # the catch-up's in the hole: twice


def test_a_reconnect_before_the_historys_answer_with_a_short_stretch_asks_nothing_again(tmp_path, monkeypatch):
    """As above, the stretch before the catch-up shorter than HOLE_MIN_MS (3 minutes): it makes no hole, so it is not
    asked again: nothing in it is stored twice, but its trades from before the catch-up never come (main the same; round
    3). NINJATRADER.md's 'Still stored twice' says so."""
    from chartsrv.gapfill import HOLE_MIN_MS
    last = (START - 3 * MIN, 7000.0, 2, 1)   # where the stored data stops: 3 minutes before NinjaTrader started again
    assert START - last[0] < HOLE_MIN_MS
    m = market(tmp_path, monkeypatch, last)
    f = feed()
    rng = random.Random(5)
    trades, px = [], 7000.0
    for t in sorted(rng.sample(range(last[0] + 1, START - 1000), 300)):
        px += rng.choice((-0.25, 0, 0.25))
        trades.append((t, px, rng.choice((1, 2, 3, 5)), rng.choice((-1, 1))))
    catchup = [x for x in trades if x[0] >= START - 31_000]
    m.gapfill.requests(START - 1000, live=True, minutes=True, contracts=True)
    sent = catchup[::-1]
    first_live = sent[0][0] // 1000 * 1000
    for k, (t, px, size, side) in enumerate(sent):
        f._line(m, f"T|ES|{START + k * 5}|{px}|{size}|{side}{f'|{t}' if t < first_live else ''}\n".encode())
    asked = m.gapfill.requests(START + 3000, live=True, minutes=True, contracts=True)   # the hello again, before the Z
    es = [x for x in asked if x.startswith("GAP|ES|")]   # (the other futures: nothing stored, their 14 days)
    assert es == [f"GAP|ES|{START + (len(sent) - 1) * 5}|{START + 3000}|live"] and not m.gapfill.queue.get("ES")
    f._line(m, b"Z|ES|ticks|0|ok\n")   # the new gap at the end: nothing in it
    assert m.gapfill.next("ES") is None   # no hole
    m.flush_for_read()
    missing = [x for x in trades if x[0] < START - 31_000]
    assert len(missing) > 200 and len(stored(m)) == 1 + len(catchup)   # the catch-up once; the stretch's others never


def test_marked_trades_after_early_ms_or_after_the_answer_are_not_compared(tmp_path, monkeypatch):
    """Only those of the first EARLY_MS after the first live trade are kept to compare; one after the history's Z finds
    no gap left to compare with: both stored twice."""
    m = market(tmp_path, monkeypatch)
    f = feed()
    m.gapfill.requests(START - 1000, live=True)
    f._line(m, f"T|ES|{START}|7001.0|1|1\n".encode())
    for k in range(60):   # 30 s on: not kept
        f._line(m, f"T|ES|{START + EARLY_MS + k}|{7000 + k % 9 * 0.25}|{1 + k % 4}|1|{START - 3000 + k}\n".encode())
    assert m.gapfill.windows["ES"].early == []
    f._line(m, f"T|ES|{START + 10}|7000.5|1|1|{START - 3000}\n".encode())
    assert len(m.gapfill.windows["ES"].early) == 1
    f._line(m, b"Z|ES|ticks|0|ok\n")
    f._line(m, f"T|ES|{START + 20}|7000.5|1|1|{START - 3000}\n".encode())   # after the answer: no window
    assert "ES" not in m.gapfill.windows and f.stats["bad_lines"] == 0


@pytest.mark.parametrize("start", [(16, 5), (16, 45)], ids=["before-16:30", "from-16:30"])
@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_a_catch_up_raised_in_the_break(tmp_path, monkeypatch, order, start):
    """NinjaTrader started in the 16:00 CT break: its catch-up of the session's last trades comes with the add-on's
    stamps in the break, which on_tick moves into a session (before 16:30 to the closing session's last ms, from 16:30
    to the next 17:00 open). Whether a marked one came within EARLY_MS is by the stamp it was sent at, as the first live
    trade's: before, from 16:30 the stored one (17:00) was taken, every one looked 15 minutes late and none was compared:
    stored twice, and the closing session's prints made the next one's first bar. Every trade once, every bar its trades
    (the live feed's too); the copies at the exchange's time with the live side. The unmarked ones (all of an
    oldest-first catch-up, those of the first one's second) stay where on_tick put them, as before r23."""
    from chartsrv.sessions import snap
    stored0 = (ms(15, 0, 30), 7000.0, 2, 1)
    m = market(tmp_path, monkeypatch, stored0)
    f = feed()
    rng = random.Random(f"{order}{start}")
    times = sorted(rng.sample(range(ms(15, 0, 31), ms(15, 57)), 200) + rng.sample(range(ms(15, 57), ms(16, 0)), 400))
    trades, px = [], 7000.0
    for t in times:
        px += rng.choice((-0.25, 0, 0.25))
        trades.append((t, px, rng.choice((1, 1, 2, 3, 5)), rng.choice((-1, 1))))
    catchup = [x for x in trades if x[0] >= ms(15, 58, 54)]   # its last 66 s
    rx0 = ms(*start)
    m.gapfill.requests(rx0 - 1000, live=True, minutes=True, contracts=True)
    sent = deliver(catchup, order)
    first_live = sent[0][0] // 1000 * 1000
    stamped = {}
    for k, (t, px, size, side) in enumerate(sent):
        f._line(m, f"T|ES|{rx0 + k * 5}|{px}|{size}|{side}{f'|{t}' if t < first_live else ''}\n".encode())
        stamped[t] = snap(rx0 + k * 5)
    history = [x for x in trades if stored0[0] < x[0] < first_live]
    for t, px, size, _ in history:
        f._line(m, f"X|ES|{t}|{px}|{size}\n".encode())
    f._line(m, f"Z|ES|ticks|{len(history)}|ok\n".encode())
    m.flush_for_read()
    both = [x for x in sent if x[0] < first_live]
    assert bool(both) == (order != "oldest") and m.gapfill.health()["ES"].get("copies", 0) == len(both)
    hist_ts = {x[0] for x in history}
    expect = [stored0] + [(t, px, size, side if t in stamped else 0) if t in hist_ts else (stamped[t], px, size, side)
                          for t, px, size, side in trades]
    rows = [tuple(r) for r in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES' "
                                                  "ORDER BY ts, rowid")]
    assert sorted(rows) == sorted(expect)
    moved = snap(rx0)
    assert (moved < ms(16, 0)) == (start < (16, 30))   # back into the closing session, or on to the next open
    later = [r for r in rows if r[0] >= ms(16, 0)]   # in the next session: only those sent live alone
    assert len(later) == (0 if moved < ms(16, 0) else len(sent) - len(both))
    bars = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    assert bars == in_order(rows)
    for b in m.m1["ES"].bars:
        assert (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) == bars[b.t]


@pytest.mark.parametrize("version", [23, 22])
@pytest.mark.parametrize("order", ["oldest", "last", "newest"])
def test_a_catch_up_in_the_break_too_short_to_pair_keeps_the_closing_minute_whole(tmp_path, monkeypatch, order,
                                                                                    version):
    """As above, a quiet close: the catch-up's handful of trades (fewer than COPY_RUN, or unmarked from add-on 22) is
    not paired; on_tick moved them back to 15:59:59.999, below the cutoff, and the history's trades replaced that
    minute's bar (the live feed's own flushed before): stored, it held the history's trades only (v short of its stored
    trades), and the live feed's 15:59 bar the catch-up's only (round 3; main the same). Now that minute is built again
    from all its stored trades, both copies of each trade there (stored twice, as before r23)."""
    stored0 = (ms(15, 0, 30), 7000.0, 2, 1)
    m = market(tmp_path, monkeypatch, stored0)
    f = feed(version)
    rng = random.Random(f"{order}{version}")
    trades, px = [], 7000.0
    times = sorted(rng.sample(range(ms(15, 0, 31), ms(15, 58, 54)), 400) + rng.sample(range(ms(15, 58, 54), ms(16, 0)), 12))
    for t in times:   # the catch-up: its last 66 s, a dozen trades
        px += rng.choice((-0.25, 0, 0.25))
        trades.append((t, px, rng.choice((1, 1, 2, 3, 5)), rng.choice((-1, 1))))
    catchup = [x for x in trades if x[0] >= ms(15, 58, 54)]
    assert len(catchup) == 12 < COPY_RUN
    rx0 = ms(16, 5)
    m.gapfill.requests(rx0 - 1000, live=True, minutes=True, contracts=True)
    sent = deliver(catchup, order)
    first_live = sent[0][0] // 1000 * 1000
    for k, (t, px, size, side) in enumerate(sent):
        mark = version >= 23 and t < first_live
        f._line(m, f"T|ES|{rx0 + k * 5}|{px}|{size}|{side}{f'|{t}' if mark else ''}\n".encode())
    history = [x for x in trades if stored0[0] < x[0] < first_live]
    for t, px, size, _ in history:
        f._line(m, f"X|ES|{t}|{px}|{size}\n".encode())
    f._line(m, f"Z|ES|ticks|{len(history)}|ok\n".encode())
    m.flush_for_read()
    rows = [tuple(r) for r in m.store.db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol='ES' "
                                                  "ORDER BY ts, rowid")]
    both = [x for x in sent if x[0] < first_live]
    assert m.gapfill.health()["ES"].get("copies", 0) == 0 and len(rows) == 1 + len(trades) + len(both)
    assert bool(both) == (order != "oldest")
    closing = ms(15, 59)
    assert sum(r[0] == closing + MIN - 1 for r in rows) == len(sent)   # every one sent: at the session's last ms
    bars = {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}
    assert bars == in_order(rows)
    assert bars[closing][4] == sum(r[2] for r in rows if closing <= r[0] < closing + MIN)
    assert any(b.t == closing for b in m.m1["ES"].bars)
    for b in m.m1["ES"].bars:   # the live feed's own: as stored
        assert (b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n) == bars[b.t]


def test_plugin_trades_are_never_collected(tmp_path, monkeypatch):
    """A feed plugin supplies ES: NinjaTrader's lines for it are dropped, marked or not, and its window keeps no live
    trade to compare (a plugin marks none)."""
    from chartsrv.feeds.sdk import FeedPlugin
    m = market(tmp_path, monkeypatch)
    m.trade_owner = {"ES": "p"}
    m.gapfill.open("ES", START)   # the plugin's window
    f = feed()
    f._line(m, f"T|ES|{START}|7001.0|1|1|{START - 3000}\n".encode())
    plugin = FeedPlugin("p", {}, ["ES"])
    monkeypatch.setattr("time.time", lambda: START / 1000 + 1)
    assert plugin.trade(m, "ES", START + 100, 7001.25, 2, 1)
    tk = m.last_tick["ES"]
    m.gapfill.early("ES", tk, START - 2000)   # even if asked
    assert m.gapfill.windows["ES"].early == [] and m.gapfill.windows["ES"].cutoff == START + 100


def test_holes_older_history_and_the_amts_windows_compare_nothing(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    tk = Tick("ES", START, 7000.0, 1, 1)
    m.gapfill.open_hole("ES", ms(9, 0), ms(9, 30))
    m.gapfill.live("ES", START)
    for older, collect in ((False, None), (True, None), (False, {})):   # a hole, older history, the AMT's prior day
        w = m.gapfill.windows["ES"]
        w.older, w.collect = older, collect
        m.gapfill.early("ES", tk, START - 1)
        assert w.early == [] and not w.edge
    del m.gapfill.windows["ES"]
    m.gapfill.open("ES", START - 1000)
    m.gapfill.live("ES", START)
    assert m.gapfill.windows["ES"].edge
    m.gapfill.early("ES", tk, START - 1)
    assert len(m.gapfill.windows["ES"].early) == 1


def test_quiet_and_unrelated_tapes_pair_nothing(tmp_path, monkeypatch):
    """A quiet market (one print, again and again) and two tapes of different trades never pair by chance: the
    live trades stay as they came, the history's too."""
    m = market(tmp_path, monkeypatch)
    f = feed()
    m.gapfill.requests(START - 1000, live=True)
    for k in range(200):
        f._line(m, f"T|ES|{START + k}|7000.0|1|1|{START - 5000 + k}\n".encode())
    for k in range(300):
        f._line(m, f"X|ES|{START - 30_000 + k * 50}|7000.0|1\n".encode())
    f._line(m, b"Z|ES|ticks|300|ok\n")
    assert m.gapfill.health()["ES"]["copies"] == 0 and len(stored(m)) == 1 + 200 + 300
    for seed in range(20):
        rng = random.Random(seed)
        hist = [(7000 + rng.randint(-8, 8) * 0.25, rng.choice((1, 1, 2, 3, 5))) for _ in range(2000)]
        live = [(7000 + rng.randint(-8, 8) * 0.25, rng.choice((1, 1, 2, 3, 5))) for _ in range(2000)]
        assert copies(hist, live) == []


# --- copies() --------------------------------------------------------------------------------------------------------
def prints(rng, n):
    return [(7000 + rng.randint(-20, 20) * 0.25, rng.randint(1, 9)) for _ in range(n)]


def test_copies_forward_and_reverse_runs():
    rng = random.Random(1)
    hist = prints(rng, 300)
    assert copies(hist, hist[100:180]) == [(100 + k, k) for k in range(80)]                 # the same order
    assert copies(hist, hist[180:100:-1]) == [(180 - k, k) for k in range(80)]              # newest first
    assert copies(hist, [hist[299]] + hist[200:299]) == [(200 + k, 1 + k) for k in range(99)]   # its last, then oldest first


def test_copies_needs_a_run_of_copy_run_with_copy_kinds_prints():
    rng = random.Random(2)
    hist = prints(rng, 300)
    assert copies(hist, hist[10:10 + COPY_RUN - 1]) == []
    assert len(copies(hist, hist[10:10 + COPY_RUN])) == COPY_RUN
    few = [(7000.0 + (k % (COPY_KINDS - 1)) * 0.25, 1) for k in range(200)]   # 4 prints only
    assert copies(few, few) == []
    five = [(7000.0 + (k % COPY_KINDS) * 0.25, 1) for k in range(200)]
    assert len(copies(five, five)) == 200


def test_copies_each_trade_once_and_runs_grow_both_ways():
    rng = random.Random(3)
    hist = prints(rng, 400)
    live = hist[50:150] + prints(rng, 7) + hist[200:260] + hist[50:150]   # the same 100 again: one pairing only
    got = copies(hist, live)
    assert [j for _, j in got] == list(range(100)) + list(range(107, 167))
    assert len({i for i, _ in got}) == len(got)
    # a run whose first prints repeat one value (no window of them has COPY_KINDS) is taken whole from its first
    quiet = [(7000.0, 1)] * 30
    hist2 = prints(rng, 100) + quiet + prints(rng, 100)
    assert copies(hist2, hist2[100:230]) == [(100 + k, k) for k in range(130)]


def test_copies_skips_live_prints_the_history_lacks():
    rng = random.Random(4)
    hist = prints(rng, 300)
    live = hist[0:60] + [(1.0, 1)] + hist[60:120]
    assert [j for _, j in copies(hist, live)] == list(range(60)) + list(range(61, 121))


def test_copies_at_full_size_takes_a_moment_even_on_a_periodic_tape():
    """TAIL history trades against EARLY_MAX live ones, at Z on the event loop: a tape of 5 prints round and round
    (every window alike) once rescanned every candidate for every window (half a minute)."""
    import time as clock
    from chartsrv.gapfill import EARLY_MAX, TAIL
    five = [(7000.0 + (k % COPY_KINDS) * 0.25, 1) for k in range(TAIL)]
    rng = random.Random(5)
    for live in ([(7000.0 + (k % COPY_KINDS) * 0.25, 1) for k in range(EARLY_MAX)], prints(rng, EARLY_MAX)):
        t0 = clock.monotonic()
        copies(five, live)
        assert clock.monotonic() - t0 < 3
