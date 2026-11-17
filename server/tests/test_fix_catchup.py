"""chartsrv.fix_catchup: a NinjaTrader started mid-session with add-on 22 stored its catch-up twice (the live copies,
stamped as the add-on sent them, and the gap fill history's, at the exchange's time). The repair keeps each once, as
add-on 23 does now: the database comes out exactly as add-on 23 would have stored it, trades and 1-minute bars. A gap
fill with nothing sent twice, a failed history and a dry run change nothing."""
import random

import pytest

from chartsrv import fix_catchup
from chartsrv.gapfill import COPY_RUN

from test_gapfill_copies import START, bars_of, feed, market, ms, run, stored, tape

DAY = 86_400_000


def stored_bars(m):
    return {r[0]: tuple(r[1:]) for r in m.store.db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol='ES'")}


def repair(m, yes, lines):
    return fix_catchup.fix(m.s.db_path, yes=yes, days=2, out=lines.append, now=START + DAY)


@pytest.mark.parametrize("span", [70_000, 4_000], ids=["over-a-minute", "in-4s"])
@pytest.mark.parametrize("order", ["last", "newest"])
def test_a_catch_up_stored_twice_comes_out_as_add_on_23_stores_it(tmp_path, monkeypatch, order, span):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(f"fix{order}{span}"), START - 1000, 300, span)
    expect, both = run(m, feed(22), trades, catchup, order, marks=False)
    assert len(both) >= COPY_RUN and len(stored(m)) == len(expect) + len(both)   # add-on 22: twice

    lines = []
    assert repair(m, False, lines) is None and len(stored(m)) == len(expect) + len(both)   # a dry run changes nothing
    assert f"{len(both):,} trades" in lines[0] and "Nothing changed" in lines[-1]
    lines = []
    backup = repair(m, True, lines)
    assert backup is not None and backup.exists()
    assert stored(m) == sorted(expect)   # each once: the history's copy, at its time, with the live side
    want = bars_of(expect)
    assert stored_bars(m) == want        # every minute either copy was in built again, none left empty
    assert any(f"trades {len(expect) + len(both):,} -> {len(expect):,}" in s for s in lines)
    lines = []
    assert repair(m, True, lines) is None and lines == ["No catch-up stored twice found."]   # nothing left to do


@pytest.mark.parametrize("answered,order", [(True, "oldest"), (False, "newest")], ids=["oldest-first", "failing"])
def test_nothing_sent_twice_is_left_as_it_is(tmp_path, monkeypatch, answered, order):
    """Oldest first, the history stops before the catch-up; a failed history brings nothing: no trade twice."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(f"none{order}"), START - 1000, 300, 70_000)
    expect, both = run(m, feed(22), trades, catchup, order, answered=answered, marks=False)
    assert not both and stored(m) == sorted(expect)
    before, bars = stored(m), stored_bars(m)
    lines = []
    assert repair(m, True, lines) is None and lines == ["No catch-up stored twice found."]
    assert stored(m) == before and stored_bars(m) == bars


def test_add_on_23_left_nothing_to_do(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("r23"), START - 1000, 300, 70_000)
    expect, both = run(m, feed(23), trades, catchup, "newest")
    assert both and stored(m) == sorted(expect)
    lines = []
    assert repair(m, True, lines) is None and stored(m) == sorted(expect)


def test_a_live_tape_like_the_history_but_not_its_copy_is_kept(tmp_path, monkeypatch):
    """Live trades after a gap fill that only resemble its history (the same prices, other sizes and order) pair
    nothing: only runs of COPY_RUN alike one for one count."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("alike"), START - 1000, 300, 70_000)
    rng = random.Random(5)
    other = [(t, px, rng.choice((1, 2, 3, 4, 5, 8)), s) for t, px, _, s in catchup]
    rng.shuffle(other)
    run(m, feed(22), trades, sorted(other), "oldest", marks=False)
    before = stored(m)
    lines = []
    assert repair(m, True, lines) is None and stored(m) == before


def test_only_the_days_asked_are_looked_at(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("days"), START - 1000, 300, 4_000)
    expect, both = run(m, feed(22), trades, catchup, "newest", marks=False)
    lines = []
    assert fix_catchup.fix(m.s.db_path, yes=True, days=2, out=lines.append, now=START + 5 * DAY) is None
    assert len(stored(m)) == len(expect) + len(both) and ms(10, 0) < START


@pytest.mark.parametrize("order", ["last", "newest"])
@pytest.mark.parametrize("how", ["chunks", "no-side"])
def test_older_servers_writes_and_a_catch_up_with_no_side(tmp_path, monkeypatch, order, how):
    """chunks: a server before 23 wrote the history as it came, CHUNK at a time (TAIL nothing held), some before the
    live trades were written. no-side: NinjaTrader has no bid or ask yet as it starts, so the catch-up comes with none."""
    from chartsrv import gapfill
    if how == "chunks":
        monkeypatch.setattr(gapfill, "TAIL", 0)
        monkeypatch.setattr(gapfill, "CHUNK", 50)
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random(f"{how}{order}"), START - 1000, 400, 70_000)
    if how == "no-side":
        trades = trades[:150] + [(t, px, size, 0) for t, px, size, _ in trades[150:]]
        catchup = trades[150:]
    expect, both = run(m, feed(22), trades, catchup, order, marks=False)
    assert len(both) >= COPY_RUN and len(stored(m)) == len(expect) + len(both)
    lines = []
    assert repair(m, True, lines) is not None
    assert stored(m) == sorted(expect) and stored_bars(m) == bars_of(expect)


def _old_server_db(path, order, force, gap):
    """A chart.db as a server before 23 wrote it, row by row: the stored trade, the catch-up as sent (a trade every
    5 ms, from START), live trades while the history downloads (the first `gap` ms after the catch-up's last), the
    history at its Z (no side), more live trades. force: a trade stored once has the print of the history's trade just
    before the catch-up (c0): newest first, the first new live trade; last first, the first one sent. Returns what
    must be left: every trade once."""
    import sqlite3

    from chartsrv.store import SCHEMA, UPSERT_BAR
    rng = random.Random(f"{order}{force}{gap}")
    px, tp = 7000.0, []
    for t in sorted(rng.sample(range(ms(10, 0, 31), START - 1000), 3000)):
        px = max(100.0, px + rng.choice((-2, -1, 0, 0, 0, 1, 2)) * 0.25)
        tp.append((t, px, rng.choice((1, 1, 1, 1, 2, 2, 3, 4, 5, 8, 10))))
    n = 300
    c0 = tp[-n - 1]
    if force and order == "last":
        tp[-1] = (tp[-1][0], c0[1], c0[2])
    cu = tp[-n:]
    sent = cu[::-1] if order == "newest" else [cu[-1]] + cu[:-1]
    first_live = sent[0][0] // 1000 * 1000
    hist = [(t, p, s, 0) for t, p, s in tp if t < first_live]
    live = [(START + 5 * k, p, s, rng.choice((-1, 1))) for k, (t, p, s) in enumerate(sent)]
    end = live[-1][0]
    xs = [(end + gap + 500 * k, cu[-1][1] + rng.choice((-1, 0, 1)) * 0.25, rng.choice((1, 2, 3, 5)), rng.choice((-1, 1)))
          for k in range(20)]
    if force and order == "newest":
        xs[0] = (xs[0][0], c0[1], c0[2], xs[0][3])
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for rows in ([(ms(10, 0, 30), 7000.0, 2, 1)], live, xs[:10], hist, xs[10:]):
        db.executemany("INSERT INTO ticks VALUES ('ES',?,?,?,?)", rows)
    side = {(t, p, s): x for (t, p, s), (_, _, _, x) in zip(sent, live)}
    keep = [(ms(10, 0, 30), 7000.0, 2, 1)] + [(t, p, s, side.get((t, p, s), 0)) for t, p, s, _ in hist]
    keep += [r for r, (t, p, s) in zip(live, sent) if t >= first_live] + xs
    db.executemany(UPSERT_BAR, [("ES", k, *v) for k, v in bars_of(keep + [r for r, (t, p, s) in zip(live, sent)
                                                                           if t < first_live]).items()])
    db.commit()
    db.close()
    return keep


@pytest.mark.parametrize("force", [True, False], ids=["c0s-print", "any"])
@pytest.mark.parametrize("order", ["last", "newest"])
def test_a_trade_stored_once_next_to_the_catch_up_stays(tmp_path, order, force):
    """A run grows as far as the prints stay alike: past the catch-up's oldest trade it meets the history's trade before
    it (c0). The first live trade (sent first: the history stops before its second) and NinjaTrader's new trades
    after the burst are never compared, so one with c0's print stays."""
    import sqlite3
    path = tmp_path / "chart.db"
    keep = _old_server_db(path, order, force, gap=500)
    lines = []
    assert fix_catchup.fix(path, yes=True, days=2, out=lines.append, now=START + DAY) is not None
    db = sqlite3.connect(path)
    assert sorted(tuple(r) for r in db.execute("SELECT ts, price, size, side FROM ticks")) == sorted(keep)
    assert {r[0]: tuple(r[1:]) for r in db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m")} == bars_of(keep)


# --- update.cmd's check: once per database ---------------------------------------------------------------------

def test_update_lists_them_asks_once_and_a_declined_fix_is_not_asked_again(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("upd"), START - 1000, 300, 70_000)
    expect, both = run(m, feed(22), trades, catchup, "newest", marks=False)
    path, lines = m.s.db_path, []
    assert fix_catchup.update(path, out=lines.append, now=START + DAY) == fix_catchup.FOUND
    assert any(f"{len(both):,} trades" in s for s in lines) and "backs up your database first" in lines[-1]
    assert len(stored(m)) == len(expect) + len(both) and fix_catchup.checked(path) is None   # nothing changed or kept
    assert fix_catchup.main(["--db", str(path), "--decline"]) == 0
    assert fix_catchup.checked(path)["result"] == "declined"
    lines = []
    assert fix_catchup.update(path, out=lines.append, now=START + DAY) == 0 and lines == []   # never asked again
    assert len(stored(m)) == len(expect) + len(both)


def test_each_update_looks_since_the_last_look(tmp_path, monkeypatch):
    """A look before the catch-up (none found then) and the next update finds it; a look after it, nothing to ask."""
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("upd-since"), START - 1000, 300, 70_000)
    expect, both = run(m, feed(22), trades, catchup, "newest", marks=False)
    fix_catchup.remember(m.s.db_path, "none", now=START + DAY)
    lines = []
    assert fix_catchup.update(m.s.db_path, out=lines.append, now=START + 2 * DAY) == 0 and lines == []
    fix_catchup.remember(m.s.db_path, "none", now=START - DAY)
    assert fix_catchup.update(m.s.db_path, out=lines.append, now=START + 2 * DAY) == fix_catchup.FOUND
    assert any(f"{len(both):,} trades" in s for s in lines)
    assert fix_catchup.main(["--db", str(m.s.db_path), "--update", "--yes"]) == 0   # the same days as the look
    assert stored(m) == sorted(expect) and fix_catchup.checked(m.s.db_path)["result"] == "fixed"


def test_update_with_none_found_remembers_it(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("upd-none"), START - 1000, 300, 70_000)
    run(m, feed(22), trades, catchup, "oldest", marks=False)
    lines = []
    assert fix_catchup.update(m.s.db_path, out=lines.append, now=START + DAY) == 0 and lines[-1].strip() == "None found."
    assert fix_catchup.checked(m.s.db_path)["result"] == "none"
    lines = []
    assert fix_catchup.update(m.s.db_path, out=lines.append, now=START + DAY) == 0 and lines == []


def test_yes_fixes_and_remembers_and_no_room_changes_nothing(tmp_path, monkeypatch):
    m = market(tmp_path, monkeypatch)
    trades, catchup = tape(random.Random("upd-yes"), START - 1000, 300, 70_000)
    expect, both = run(m, feed(22), trades, catchup, "last", marks=False)
    path = str(m.s.db_path)
    import shutil
    from collections import namedtuple
    real = shutil.disk_usage
    monkeypatch.setattr(fix_catchup.shutil, "disk_usage", lambda p: namedtuple("U", "total used free")(1, 1, 1000))
    assert fix_catchup.main(["--db", path, "--yes", "--days", "36500"]) == 1   # no room for the backup
    assert len(stored(m)) == len(expect) + len(both) and fix_catchup.checked(m.s.db_path) is None
    monkeypatch.setattr(fix_catchup.shutil, "disk_usage", real)
    assert fix_catchup.main(["--db", path, "--yes", "--days", "36500"]) == 0
    assert stored(m) == sorted(expect) and fix_catchup.checked(m.s.db_path)["result"] == "fixed"


def test_update_on_a_new_install_has_nothing_to_check(tmp_path):
    assert fix_catchup.main(["--update", "--db", str(tmp_path / "chart.db")]) == 0
