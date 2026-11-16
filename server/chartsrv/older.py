"""Older history: the bars before a chart's first one, for a chart scrolled or zoomed out past it (the page asks for
them as its left edge comes into view, as TradingView does). They are built the way the chart's own bars are:

- minute and longer bars (D, W and M too) from the stored 1-minute bars, exactly;
- seconds and tick bars from the stored trades, stopping where those do;
- volume, range and Renko bars from the stored trades, and for a session without all of its trades stored, from its
  1-minute bars instead: each minute's open, high and low (in the order its close suggests) and close as prints of a
  quarter of its volume, buys and sells as its delta has them. Close to what its trades would have made, not the
  same: `approx` says so.

Runs in a worker (workers.build_older)."""
import threading
import time

from . import sessions
from .bars import BarSpec, Series, make_series
from .models import Tick
from .store import bar_1m_start, connect, iter_bars_1m, iter_ticks_range

DAY_MS = 86_400_000
MAX_COUNT = 5000              # bars one request can ask for
MAX_SESSIONS = 40             # sessions one request reads (tick, volume, range and Renko bars)
BUDGET_S = 6.0                # and for about this long: the page asks again for more
JUMPS = 4                     # stretches with nothing stored stepped over in one request
MAX_MINUTES = 400_000         # stored minutes one request reads at most (minute and longer bars; the page asks again)
MAX_TRADE_MINUTES = 2_800     # ...and minutes of trades (seconds bars: two sessions' worth)
CAL_WINDOW_DAYS = 800         # ...and calendar days (D, W and M bars)
SESSION_KEEP_S = 300          # the last session built, kept for the pages before it until unused this long (per worker)
_last_session: dict = {}      # {"key": ..., "end": ms, "bars": [...], "approx": bool, "at": monotonic}, replaced whole
_keep_lock = threading.Lock()            # (without worker processes, builds run in threads)
_expiry: threading.Timer | None = None   # lets _last_session go once unused for SESSION_KEEP_S (_expire)
FALLBACK = {"volume", "range", "renko"}           # can be built from 1-minute bars where no trades are stored
CAL_DAYS = {"D": 1.5, "W": 7.3, "M": 31}           # calendar days a D, W or M bar spans, weekends and holidays in
COMPLETE = 0.99               # a session's trades are all there when they add up to this much of its minutes' volume


def build_older(db_path, symbol: str, spec: BarSpec, tick_size: float, decimals: int, rth, rth_only: bool,
                group: str | None, before: int, count: int, max_rowid: int, have: int = 0, built: int | None = None) -> dict:
    """Up to `count` bars before `before` (the chart's first bar's open, epoch ms), oldest first. more: older ones are
    stored still; approx: some were built from 1-minute bars; next: with no bars but more, the `before` to ask with
    next. have: how many of the chart's first bars open at `before` (tick, volume, range and Renko bars: several can
    open on one millisecond, and those before the chart's are sent too). built: when the chart's own history was built
    (its sessions from the one before then on are its trades'); None: now."""
    count = max(1, min(MAX_COUNT, int(count)))
    if spec.cal:   # (a D, W or M bar reads a day's to a month's minutes: not years of them in one request)
        count = max(1, min(count, int(CAL_WINDOW_DAYS / (CAL_DAYS[spec.cal] * spec.mult))))

    def make(n: int) -> Series:
        return make_series(spec, tick_size, decimals, n, rth, rth_only=rth_only, group=group)

    db = connect(db_path, readonly=True)
    try:
        if spec.from_minutes:
            out = _minutes(db_path, db, symbol, spec, make, before, count, rth_only, group)
        elif spec.kind == "time":
            out = _seconds(db_path, db, symbol, spec, make, before, count, rth_only, max_rowid)
        else:
            out = _sessions(db_path, db, symbol, spec, make, before, count, max_rowid, have, built,
                            (str(db_path), symbol, spec.text, tick_size, rth_only, group))
    finally:
        db.close()
    bars, more, approx, nxt = out
    tied = not spec.from_minutes and spec.kind != "time"   # (those opening at `before` are sorted out by _sessions)
    # next: where to ask from when no bars came (a long stretch with nothing stored, past what one request steps over)
    return {"bars": [b.to_dict() for b in bars if b.t < before or tied and b.t == before], "more": more, "approx": approx,
            "next": nxt if more and not bars else None}


def _latest(db, symbol: str, end: int, minutes: bool, ticks: bool) -> int | None:
    """The newest stored minute (minutes) or trade (ticks) before end."""
    got = []
    if minutes:
        got.append(db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=? AND t<?", (symbol, end)).fetchone()[0])
    if ticks:
        got.append(db.execute("SELECT MAX(ts) FROM ticks WHERE symbol=? AND ts<?", (symbol, end)).fetchone()[0])
    got = [g for g in got if g is not None]
    return max(got) if got else None


def _vwap_from(db_path, series: Series, symbol: str, since: int):
    """As market._vwap_from_open: the session's minutes before `since` into the VWAPs only."""
    if series.studies and since:
        for b in iter_bars_1m(db_path, symbol, sessions.session_start(since), since):
            series.vwap_only(b)


def _minutes(db_path, db, symbol, spec, make, before, count, rth_only, group=None):
    """Minute and longer bars from stored 1-minute bars; a window with none stored (a long gap) is stepped over."""
    for _ in range(JUMPS):
        s = make(10 ** 6)
        s.bulk = True
        if spec.cal:   # from a session's open: one that a holiday halt folds into the next day's bar is that bar's
            since = sessions.session_start(max(DAY_MS, before - int(CAL_DAYS[spec.cal] * spec.mult * count * DAY_MS)))
            for _ in range(3):   # (and the window starting on the open of a bar that one folds into: from that one's)
                prev = sessions.previous_session_start(since)
                if sessions.counts_toward(prev, group) < since:
                    break
                since = prev
        else:
            need = int(spec.size // 60 * count * 1.1) + 1   # margin for short session-edge bars
            if rth_only:
                need *= 4   # regular hours are under a third of the Globex day
            since = bar_1m_start(db_path, symbol, before, min(need, MAX_MINUTES))   # 0: fewer stored, all of them
            _vwap_from(db_path, s, symbol, since)
        for b in iter_bars_1m(db_path, symbol, since, before):
            s.on_bar(b)
        latest = _latest(db, symbol, since, True, False) if since > 0 else None
        while latest is not None and s.bars and s.bars[0].t < since:
            s.bars.popleft()   # the first bar began before the window: only part of it (unless nothing is stored before)
        s.end_bulk()
        if s.bars or latest is None:   # more: stored before the window, or built past `count` (asked for next)
            return list(s.bars)[-count:], latest is not None or len(s.bars) > count, False, since
        before = latest // 60_000 * 60_000 + 60_000   # nothing in the window: on from the newest minute before it
    return [], True, False, before


def _seconds(db_path, db, symbol, spec, make, before, count, rth_only, max_rowid):
    """Seconds bars from stored trades, as far back as those go."""
    for _ in range(JUMPS):
        need = int(spec.size * count / 60 * 1.1) + 1   # in stored minutes, so a weekend doesn't empty it
        if rth_only:
            need *= 4
        since = bar_1m_start(db_path, symbol, before, min(need, MAX_TRADE_MINUTES))
        s = make(10 ** 6)
        s.bulk = True
        _vwap_from(db_path, s, symbol, since)
        for tk in iter_ticks_range(db_path, symbol, since, before, max_rowid):
            s.on_tick(tk)
        while s.bars and s.bars[0].t < since:
            s.bars.popleft()
        s.end_bulk()
        latest = _latest(db, symbol, since, False, True) if since > 0 else None
        if s.bars or latest is None:
            return list(s.bars)[-count:], latest is not None or len(s.bars) > count, False, since
        before = latest + 1
    return [], True, False, before


def _sessions(db_path, db, symbol, spec, make, before, count, max_rowid, have=0, built=None, key=None):
    """Tick, volume, range and Renko bars restart at each session's open, so each session is built on its own, newest
    first, until there are enough. A stretch with nothing stored is stepped over."""
    fb = spec.kind in FALLBACK
    t0 = time.monotonic()
    chunks, total, approx, end = [], 0, False, before
    # the chart's own sessions (the one before the current one on) are built from their trades (market.build_history)
    recent = sessions.previous_session_start(sessions.session_start(built or int(time.time() * 1000)))
    for _ in range(MAX_SESSIONS):
        latest = _latest(db, symbol, end, fb, True)
        if latest is None:
            break
        start = sessions.session_start(latest)
        bars, apx = _session_kept(key, db_path, db, symbol, make, start, end, fb, max_rowid, start >= recent,
                                  have if end == before else 0)
        if bars:
            chunks.append(bars)
            total += len(bars)
            approx |= apx
        end = start
        if total >= count or time.monotonic() - t0 > BUDGET_S:
            break
    bars = [b for c in reversed(chunks) for b in c]
    # the oldest session's first bars past `count` are asked for next (from the first bar sent, its session again)
    more = len(bars) > count or _latest(db, symbol, end, fb, True) is not None
    return bars[-count:], more, approx, end


def _session_kept(key, db_path, db, symbol, make, start, end, fb, max_rowid, recent, have):
    """_session, from the last one built when it is the same session built to `end` or later (the pages of one long
    session scrolled back through: not each one built again from its open). Its bars before `end` are the same: `end`
    is the first bar of the page after, a bar's open in that build; and of those opening on `end`'s millisecond the
    chart has the last (have), never one still open."""
    k = None if key is None else (*key, start, recent, fb)
    last = _last_session   # one read: another market's build (another thread) replaces it whole, never changes it
    if (k is not None and last.get("key") == k and last["end"] >= end and time.monotonic() - last["at"] < SESSION_KEEP_S
            and not _stored_since(db, symbol, start, last["end"], last["rowid"], max_rowid)):
        last["at"] = time.monotonic()
        tied = [b for b in last["bars"] if b.t == end]
        return [b for b in last["bars"] if b.t < end] + (tied[:max(0, len(tied) - have)] if have else []), last["approx"]
    out, approx, full = _session(db_path, db, symbol, make, start, end, fb, max_rowid, recent, have, whole=True)
    if k is not None:
        _keep({"key": k, "end": end, "bars": full, "approx": approx, "at": time.monotonic(), "rowid": max_rowid})
    return out, approx


def _keep(entry: dict):
    """Keeps the session just built for the pages before it, and lets it go once unused for SESSION_KEEP_S: not only
    stops using it, or an idle worker would hold all of its bars (a busy session's 1t: a few hundred MB) for good."""
    global _last_session
    with _keep_lock:
        _last_session = entry
        _arm(SESSION_KEEP_S)


def _arm(after: float):
    global _expiry
    if _expiry is not None:
        _expiry.cancel()
    _expiry = threading.Timer(after, _expire)
    _expiry.daemon = True   # (never keeps a worker from stopping)
    _expiry.start()


def _expire():
    global _last_session, _expiry
    with _keep_lock:
        if threading.current_thread() is not _expiry:   # a timer armed since has it
            return
        left = SESSION_KEEP_S - (time.monotonic() - _last_session["at"]) if _last_session else 0
        if left > 0:
            _arm(left)   # used since: looks again when it would go
        else:
            _last_session, _expiry = {}, None


def _stored_since(db, symbol, start, end, rowid, max_rowid) -> bool:
    """A trade of [start, end] stored since the build (a hole filled): live trades come later, so this reads only
    the trades written since (by rowid, not the session's: + keeps SQLite off the symbol/time index)."""
    if max_rowid <= rowid:
        return False
    return db.execute("SELECT 1 FROM ticks WHERE rowid>? AND rowid<=? AND +symbol=? AND +ts>=? AND +ts<=? LIMIT 1",
                      (rowid, max_rowid, symbol, start, end)).fetchone() is not None


def _session(db_path, db, symbol, make, start, end, fb, max_rowid, recent=False, have=0, whole=False):
    """The bars of the session opening at `start` before `end` (its next one's open, or the chart's first bar), from
    its trades, or for volume, range and Renko bars missing some of them, from its minutes: an older session's as a
    whole, whatever part of it is asked for; one of the chart's own (recent), from its trades where it has them.
    have: how many bars opening at `end` the chart has; the ones before those (on the same millisecond) are sent."""
    s = make(1_000_000)
    s.bulk = True
    approx = False
    if recent:
        # one of the chart's own sessions (built from its trades): its trades as they are, and each minute with none
        # stored (before them, an outage filled in as 1-minute bars) from its minute; not the chart's first trade's
        # minute, nor that trade when it is the chart's very first (all of it is its first bar's)
        traded = {r[0] for r in db.execute("SELECT DISTINCT ts / 60000 * 60000 FROM ticks WHERE symbol=? AND ts>=? AND ts<=?",
                                          (symbol, start, end))}
        first = db.execute("SELECT MIN(ts) FROM ticks WHERE symbol=? AND ts>=? AND ts<=?", (symbol, start, end)).fetchone()[0]
        minutes = iter(m for m in (iter_bars_1m(db_path, symbol, start, end // 60_000 * 60_000, db) if fb else ())
                       if m.t not in traded)
        nxt_m = next(minutes, None)
        if first is not None and first < end:
            seen = False   # a trade of the chart's hours before `end`: else the one at `end` is all its first bar's
            for tk in iter_ticks_range(db_path, symbol, first, end + 1, max_rowid):
                if tk.ts >= end and not seen:
                    break
                seen = seen or s.in_hours(tk.ts)
                while nxt_m is not None and nxt_m.t < tk.ts // 60_000 * 60_000:
                    approx = True
                    for p in minute_prints(symbol, nxt_m):
                        s.on_tick(p)
                    nxt_m = next(minutes, None)
                s.on_tick(tk)
        while nxt_m is not None:
            approx = True
            for p in minute_prints(symbol, nxt_m):
                s.on_tick(p)
            nxt_m = next(minutes, None)
    else:   # an older session: from its trades if they are all there, else its minutes, as a whole
        nxt = sessions.next_session_start(start)
        tv = db.execute("SELECT COALESCE(SUM(size), 0) FROM ticks WHERE symbol=? AND ts>=? AND ts<?",
                        (symbol, start, nxt)).fetchone()[0]
        mv = db.execute("SELECT COALESCE(SUM(v), 0) FROM bars_1m WHERE symbol=? AND t>=? AND t<?",
                        (symbol, start, nxt)).fetchone()[0] if fb else 0
        if tv > 0 and (not fb or tv >= COMPLETE * mv):
            for tk in iter_ticks_range(db_path, symbol, start, end + 1, max_rowid):
                s.on_tick(tk)
        elif fb and mv > 0:
            approx = True
            for m in iter_bars_1m(db_path, symbol, start, end + 1, db):
                for tk in minute_prints(symbol, m):
                    if tk.ts <= end:
                        s.on_tick(tk)
    s.end_bulk()
    tied = [b for b in s.bars if b.t == end]
    out = [b for b in s.bars if b.t < end] + (tied[:max(0, len(tied) - have)] if have else [])
    return (out, approx, list(s.bars)) if whole else (out, approx)


def minute_prints(symbol: str, m) -> list[Tick]:
    """A 1-minute bar as prints: open, then its low and high (the high first on a down minute), then close, 15 seconds
    apart, each a quarter of its volume, split into buys, sells and unknown as its own volume is."""
    up = m.c >= m.o
    path = (m.o, m.l, m.h, m.c) if up else (m.o, m.h, m.l, m.c)
    v = max(int(m.v), 0)
    out = []
    for i, px in enumerate(path):
        size = v // 4 + (1 if i < v % 4 else 0)
        if size <= 0 and i:
            continue
        buy = size * m.bv // v if v else 0
        sell = size * m.sv // v if v else 0
        t = m.t + i * 15_000
        for side, sz in ((1, buy), (-1, sell), (0, size - buy - sell)):
            if sz > 0:
                out.append(Tick(symbol, t, px, sz, side))
        if size <= 0:   # an empty minute: its open still moves range and Renko bars
            out.append(Tick(symbol, t, px, 0, 0))
    return out
