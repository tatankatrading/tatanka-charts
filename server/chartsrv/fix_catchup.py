"""Finds the trades a NinjaTrader started mid-session stored twice (add-on 22 and older, or a server before 23) and
keeps one of each, then rebuilds the 1-minute bars of every minute either copy was in.

NinjaTrader started mid-session sends its last trades again as a one-time catch-up of live ones, stamped by the add-on
as it sent them (all within a second or two of its start); the gap fill's history then brought the same trades at the
exchange's time (Nov 16 2026: ES sent 13,527 contracts where NinjaTrader counted 5,010). Add-on 23 with a server of 23
keeps those once as they come (gapfill.copies); this mends the ones stored before.

Stop the server first, so nothing writes while it runs. Docker, from the charting folder:
    docker compose stop chart
    docker compose run --rm chart python -m chartsrv.fix_catchup          # shows what it finds, changes nothing
    docker compose run --rm chart python -m chartsrv.fix_catchup --yes    # backs up, then fixes
    docker compose up -d
Without Docker, from charting/server: python -m chartsrv.fix_catchup [--yes] [--days N]

update.cmd (and update.sh) look by themselves on each update (--update): the days since the last look (the CHECKED
setting; the first time, the last UPDATE_DAYS). What it finds is listed and the update asks before fixing it (--update
--yes, the same days) or not (--decline). Either answer, or nothing found, moves the last look to now.

How it tells: a gap fill's history is written after the live trades that follow it, so where it ends the stored
trades (in time order) step back to a row stored earlier: that row is the first live trade after the add-on connected.
The catch-up is the live trades sent right after it, in one burst (none more than BURST_GAP_MS after the one before,
within EARLY_MS): those are compared with the history's trades before it (its rows with no aggressor side), print by
print (price and size), as the server does now: only runs of COPY_RUN or more alike one for one, in either order,
with COPY_KINDS or more different prints, pair up. Of each pair the history's copy stays (at the exchange's time, with
the live one's aggressor side) and the live one goes. The first live trade itself is never one: the history stops
before its second. Where it can't tell, it leaves a trade twice rather than take one out that was stored once.

Left as they are: a catch-up whose history came as 1-minute bars (no trades to compare with); one whose history was
written before the live trades and that came with no aggressor side (NinjaTrader had no bid or ask yet), where the
two can't be told apart; and, rarely, a real trade sent within BURST_GAP_MS of the catch-up's last whose print
equals the history's trade just before the catch-up, which pairs with it and goes (a trade or two)."""
import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from .amt_runner import ct
from .config import load_settings
from .gapfill import COPY_KINDS, COPY_RUN, EARLY_MAX, EARLY_MS, copies
from .store import UPSERT_BAR, connect

MIN = 60_000
BACK_MS = 4 * 86_400_000   # the history's trades compared: from this far before the first live trade (a weekend)...
HIST_MAX = 50_000          # ...the last this many of them
BURST_GAP_MS = 250         # the catch-up: live trades sent one right after another, none more than this apart
CHECKED = "fix_catchup_checked"   # setting: {"at": ms, "result": "none" | "fixed" | "declined"}: the last look
UPDATE_DAYS = 90
FOUND = 3                  # --update's exit code when it found some (update.cmd asks then)
OVERLAP_MS = 3_600_000     # a look is remembered this much earlier than it ran: a NinjaTrader started just before or
                           # during it (its gap fill's history written after) is looked at again by the next one


class NoRoom(Exception):
    """Not enough free disk for the backup: nothing changed."""


def starts(db: sqlite3.Connection, symbol: str, since: int) -> list[tuple[int, int]]:
    """(ts, rowid) of each stored trade where, in time order, a gap fill's history (trades with no aggressor side) ends
    and live ones start: the trade before it was stored after it, or it has a side after COPY_RUN or more without
    (a history written whole before the live trades were, in chunks as it came)."""
    out, prev_id, flat = [], None, 0   # flat: trades in a row with no side, up to this one
    for rowid, ts, side in db.execute("SELECT rowid, ts, side FROM ticks WHERE symbol=? AND ts>=? ORDER BY ts, rowid",
                                      (symbol, since)):
        if flat and (rowid < prev_id or side != 0 and flat >= COPY_RUN):
            out.append((ts, rowid))
        flat = flat + 1 if side == 0 else 0
        prev_id = rowid
    return out


def pairs_at(db: sqlite3.Connection, symbol: str, start: int, start_id: int, taken: set[int]) -> list[tuple]:
    """The history's copies of the catch-up sent after the live trade at `start` (its burst, within EARLY_MS; that
    trade itself never: the history stops before its second): (history row, its ts, live row, its ts, the live side,
    size) each."""
    live, prev = [], start
    for r in db.execute("SELECT rowid, ts, price, size, side FROM ticks WHERE symbol=? AND ts>=? AND ts<? AND rowid>? "
                        "ORDER BY rowid LIMIT ?", (symbol, start, start + EARLY_MS, start_id, EARLY_MAX)):
        if r[1] - prev > BURST_GAP_MS:   # the burst is over: what comes after is NinjaTrader's new trades
            break
        prev = max(prev, r[1])
        if r[0] not in taken:
            live.append(r)
    if len(live) < COPY_RUN:
        return []
    hist = list(db.execute("SELECT rowid, ts, price, size FROM ticks WHERE symbol=? AND ts>=? AND ts<? AND side=0 "
                           "ORDER BY ts DESC, rowid DESC LIMIT ?", (symbol, start - BACK_MS, start, HIST_MAX)))[::-1]
    hist = [r for r in hist if r[0] not in taken]
    got = copies([(r[2], r[3]) for r in hist], [(r[2], r[3]) for r in live], COPY_RUN, COPY_KINDS)
    return [(hist[i][0], hist[i][1], live[j][0], live[j][1], live[j][4], live[j][3]) for i, j in got]


def rebuild_minutes(db: sqlite3.Connection, symbol: str, minutes: set[int]) -> tuple[int, int]:
    """These minutes' 1-minute bars again from their stored trades; one left with no trade goes. (rebuilt, removed)"""
    rows, gone = [], []
    for k in sorted(minutes):
        b = None
        for price, size, side in db.execute("SELECT price, size, side FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                            "ORDER BY ts, rowid", (symbol, k, k + MIN)):
            if b is None:
                b = [price, price, price, price, 0, 0, 0, 0]
            b[1], b[2], b[3] = max(b[1], price), min(b[2], price), price
            b[4] += size
            b[5] += size if side > 0 else 0
            b[6] += size if side < 0 else 0
            b[7] += 1
        if b is None:
            gone.append((symbol, k))
        else:
            rows.append((symbol, k, *b))
    db.executemany(UPSERT_BAR, rows)
    db.executemany("DELETE FROM bars_1m WHERE symbol=? AND t=?", gone)
    return len(rows), len(gone)


def find(db: sqlite3.Connection, symbol: str, since: int) -> list[dict]:
    """Each catch-up stored twice: its first live trade's time and the pairs found."""
    events, taken = [], set()
    for ts, rowid in starts(db, symbol, since):
        got = pairs_at(db, symbol, ts, rowid, taken)
        if got:
            taken.update(x for p in got for x in (p[0], p[2]))
            events.append({"at": ts, "pairs": got})
    return events


def scan(db: sqlite3.Connection, since: int, out=print) -> tuple[dict, bool]:
    """Each symbol's catch-ups stored twice from `since`, each said; and whether there were trades at all."""
    symbols = [r[0] for r in db.execute("SELECT DISTINCT symbol FROM ticks WHERE ts>=? ORDER BY symbol", (since,))]
    plan = {}
    for sym in symbols:
        events = find(db, sym, since)
        if not events:
            continue
        plan[sym] = events
        for e in events:
            n, vol = len(e["pairs"]), sum(p[5] for p in e["pairs"])
            out(f"{sym} {ct(e['at']):%a %m-%d %H:%M:%S} CT: trades stored twice (a NinjaTrader catch-up), "
                f"{n:,} trades, {vol:,} contracts")
    return plan, bool(symbols)


def db_size(path: Path) -> int:
    return path.stat().st_size + sum(p.stat().st_size for p in path.parent.glob(path.name + "-wal"))


def _size(n: int) -> str:
    return f"{n / 1e9:,.1f} GB" if n >= 1e9 else f"{n / 1e6:,.0f} MB"


def remember(path: Path, result: str, now: int | None = None):
    """That this database was looked at (none found, fixed, or the fix declined), as of OVERLAP_MS before now: the next
    update looks from there."""
    db = connect(path)
    try:
        with db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (CHECKED, json.dumps({"at": (int(time.time() * 1000) if now is None else now) - OVERLAP_MS,
                                             "result": result})))
    finally:
        db.close()


def checked(path: Path) -> dict | None:
    db = connect(path)
    try:
        r = db.execute("SELECT value FROM settings WHERE key=?", (CHECKED,)).fetchone()
        return json.loads(r[0]) if r else None
    except sqlite3.OperationalError:   # no settings table: a database the server never opened
        return None
    finally:
        db.close()


def update_since(path: Path, now: int | None = None) -> tuple[int, bool]:
    """Where update.cmd's look starts: the last look (and True), or the last UPDATE_DAYS the first time."""
    prev = checked(path)
    if prev and isinstance(prev.get("at"), int):
        return prev["at"], True
    return (int(time.time() * 1000) if now is None else now) - UPDATE_DAYS * 86_400_000, False


def update(path: Path, out=print, now: int | None = None) -> int:
    """update.cmd's look, the days since its last one: 0 nothing to ask (none found: the last look moves to now; said
    only the first time), FOUND when some are found (listed, with the backup's size; nothing changed or remembered)."""
    since, before = update_since(path, now)
    if not before:
        out(f"     Looking for trades a NinjaTrader started mid-session stored twice (the last {UPDATE_DAYS} days; "
            "this can take a minute)...")
    db = connect(path)
    try:
        plan, _ = scan(db, since, out=lambda s: out("     " + s))
    finally:
        db.close()
    if not plan:
        remember(path, "none", now)
        if not before:
            out("     None found.")
        return 0
    out(f"     Fixing keeps one of each and backs up your database first ({_size(db_size(path))}, so that much free "
        "disk is needed).")
    return FOUND


def fix(path: Path, yes: bool, days: int, out=print, now: int | None = None, since: int | None = None) -> Path | None:
    db = connect(path)
    try:
        if since is None:
            since = (int(time.time() * 1000) if now is None else now) - days * 86_400_000
        plan, any_trades = scan(db, since, out)
        if not plan:
            out("No catch-up stored twice found." if any_trades else "No trades in that stretch.")
            return None
        if not yes:
            out("Nothing changed. Run again with --yes to back up the database and remove the second copies.")
            return None
        size = db_size(path)
        free = shutil.disk_usage(path.parent).free
        if free < size * 1.1 + 100_000_000:
            raise NoRoom(f"Not enough free disk for the backup: {_size(free)} free, the database is {_size(size)}. "
                         "Nothing changed.")
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        dst = sqlite3.connect(backup)
        try:
            with dst:
                db.backup(dst)
        except sqlite3.Error:   # (the disk filled meanwhile): no half a backup left behind, nothing changed
            dst.close()
            for f in (backup, backup.with_name(backup.name + "-journal")):
                f.unlink(missing_ok=True)
            raise
        dst.close()
        out(f"backup: {backup} ({backup.stat().st_size:,} bytes)")
        for sym, events in plan.items():
            before = db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM ticks WHERE symbol=?", (sym,)).fetchone()
            pairs = [p for e in events for p in e["pairs"]]
            minutes = {p[1] // MIN * MIN for p in pairs} | {p[3] // MIN * MIN for p in pairs}
            with db:
                db.executemany("UPDATE ticks SET side=? WHERE rowid=?", [(p[4], p[0]) for p in pairs])
                ids = [p[2] for p in pairs]
                for i in range(0, len(ids), 5000):
                    chunk = ids[i:i + 5000]
                    db.execute(f"DELETE FROM ticks WHERE rowid IN ({','.join('?' * len(chunk))})", chunk)
                built, gone = rebuild_minutes(db, sym, minutes)
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vp_cache'").fetchone():
                    db.execute("DELETE FROM vp_cache WHERE key LIKE ? OR key LIKE ?", (f"svp2|{sym}|%", f"amtpd%|{sym}|%"))   # session profiles, AMT prior days: built again
            after = db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM ticks WHERE symbol=?", (sym,)).fetchone()
            out(f"{sym}: trades {before[0]:,} -> {after[0]:,}, contracts {before[1]:,} -> {after[1]:,}; "
                f"{built:,} minutes rebuilt, {gone:,} left empty and removed")
        return backup
    finally:
        db.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="back up the database, then remove the second copies")
    ap.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--update", action="store_true", help=f"update.cmd's look since its last one (exit {FOUND}: some "
                                                         "found); with --yes, fixes those days")
    ap.add_argument("--decline", action="store_true", help="remember that the fix was declined (update.cmd)")
    a = ap.parse_args(argv)
    path = a.db or load_settings().db_path
    if not path.exists():
        if a.update:   # a new install: nothing stored yet
            return 0
        raise SystemExit(f"no database at {path}")
    if a.update and not a.yes:
        return update(path)
    if a.decline:
        since, _ = update_since(path)
        remember(path, "declined")
        days = max(1, -(-(int(time.time() * 1000) - since) // 86_400_000))
        print("     Not fixed. To fix them later, from the Tatanka Charts folder:")
        print(f"     docker compose stop chart, then docker compose run --rm chart python -m chartsrv.fix_catchup --yes "
              f"--days {days}, then docker compose up -d")
        print("     (docs/CONNECTIONS.md, \"Each trade once\")")
        return 0
    try:
        done = fix(path, a.yes, max(1, a.days), since=update_since(path)[0] if a.update else None)
    except NoRoom as e:
        print(e)
        return 1
    except sqlite3.Error as e:   # (each symbol is fixed in a transaction of its own: those done stay done)
        print(f"Not finished: {e}. What was fixed stays fixed; the next update looks again.")
        return 1
    if a.yes:
        remember(path, "fixed" if done else "none")
        if done:
            print(f"The backup is {done}. When the charts look right it can go (Docker: docker compose run --rm "
                  f"chart rm {done}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
