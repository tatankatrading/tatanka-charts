"""Gap fill from NinjaTrader's own history. When the add-on connects, each symbol's stored data ends somewhere
(NinjaTrader closed, the PC off, the server down); the server sends GAP|SYM|from|to and the add-on answers with
NinjaTrader's history for that stretch: trades (X lines) or, when the data provider has no tick history, 1-minute
bars (Y lines), then Z|SYM|kind|count|note.

Only the stretch is filled: trades strictly after the last stored data and before the first live trade the add-on
sends after connecting (its buffer included), so nothing is counted twice. That end is open until the first live
trade comes: NinjaTrader starts the add-on before its data connection is up, so the add-on (6+) waits for the
connection, then asks up to its own now (GAP|...|live). Minutes wholly inside get bars built from those trades; the
minute where stored data ended is merged; the history's trades of the minute where live data starts go into that
minute's bar (the live feed's, or the stored one if it has moved on), whose trades are counted again for it.

NinjaTrader started mid-session raises a one-time catch-up of recent trades as live ones, its last trade (or the newest)
first: the add-on asks the history up to the first one's second (firstLive), so it holds the others too. Add-on 23
marks those with NinjaTrader's own time (a T line's 7th field). Of a gap at the live edge (not a hole, older history
or the AMT's), the marked trades sent in the first EARLY_MS after the first live trade (by the add-on's stamps, not the
stored times: in the 16:00 break on_tick moves them into a session) are kept, and the history's last TAIL trades are
held unwritten until its Z; then copies() pairs the two, print by print (price and size), in runs of COPY_RUN or more
with COPY_KINDS different prints, in either order; each pair is kept once: the history's copy (in its order: a
millisecond's trades as the exchange printed them) with the live trade's aggressor side, the live one taken out; and
every minute either touched is built again from its trades. What isn't in such a run stays as it came. An error answer
compares nothing, nor does a Z that finds the live trades not written yet (the database not writable), nor a history
of 1-minute bars (a stretch over 3 days, no tick history: no trade to pair; the catch-up counts twice in their volume).

Then the holes inside the stored data of the last days (minutes with no bar while the market was open: NinjaTrader
closed and its history not reached then, a fill that failed, its data connection lost while the add-on stayed
connected) are asked for too, and looked for again when a symbol ticks after the watchdog found it stale (again()),
by add-on 6+ (it waits for its data
connection, so an empty answer means NinjaTrader has none), one at a time per symbol, each once, never from before
the symbol's last contract roll (the history would be the other contract's); each is asked from PROBE_MS before it,
and when NinjaTrader's prices there don't match the stored ones (a roll before rolls were recorded), its answer is
another contract's and is dropped. A hole runs from its last stored trade
to the next stored minute's open: that minute is left as stored (the add-on stamps live trades when it sends them,
history with the exchange's time, so a cut inside it could count a trade twice). Charts and studies of the symbol
reload once its holes are done.

Older history (add-on 20+, GAP|...|older): the 1-minute bars from before anything stored, a month at a time, each
contract's by NinjaTrader's roll dates. In the background, once a symbol's gap and holes are done, back to a year
(CHART_HISTORY_PREFILL_DAYS); and at once for a chart scrolled back past its stored minutes (want_older, by
/api/older), a few months at a time. One such request at a time for all symbols, after the others. A month that comes
back empty is where the data provider's history ends: remembered (OLDER_LIMIT), asked again after a week. Charts
don't reload for them (they load older bars as they are scrolled back); a symbol's studies reload once its year is
in, if any of it is from the last STUDY_DAYS. An error answer (or none in time) only puts that symbol off: OLDER_RETRY_S,
doubling up to OLDER_RETRY_MAX_S, until the add-on says hello again; only an empty month sets the limit."""
import logging
import math
import os
import time

from . import sessions
from .models import Bar, Tick

log = logging.getLogger(__name__)
MAX_GAP_MS = 14 * 86_400_000     # older than this comes from backfill
LONG_GAP_MS = 90 * 86_400_000    # add-on 8+: each contract of a stretch across rolls in turn, so this far back
MIN_GAP_MS = 120_000
CHUNK = 20_000
TAIL = 20_000                    # a live-edge gap's last trades, held unwritten till its Z: the catch-up's copies are there
EARLY_MS = 30_000                # marked live trades (add-on 23) kept this long after the first live trade...
EARLY_MAX = 50_000               # ...this many at most
COPY_RUN = 50                    # a live trade is the history's copy only inside a run of this many alike...
COPY_KINDS = 5                   # ...with this many different prints (price, size) in it
HOLE_DAYS = 7                    # holes inside the stored data looked for this far back
HOLE_MIN_MS = 5 * 60_000         # open-market minutes with no bar that make a hole
QUIET_HOLE_MIN_MS = 30 * 60_000  # for a thin market (quiet: no stale alerts) minutes without a trade are normal
MAX_HOLES = 30                   # per symbol and connect, the newest first
ASKED = "gapfill_holes_asked2"   # {symbol: [[from, to], ...]}: holes asked for once already (2: since add-on 7 asks the
                                 # data provider, not only NinjaTrader's own recordings, each is asked once more)
ROLLED = "contract_rolled_at"    # {symbol: ms}: its last contract roll; no hole from before it
PROBE_MS = 3 * 60_000            # a hole is asked from this much earlier: the stored prices there tell the contract apart
DAY_MS = 86_400_000
OLDER_ADDON = 20                 # the add-on version that answers GAP|...|older
OLDER_SPAN_MS = 30 * DAY_MS      # older history asked a month at a time...
OLDER_MAX_SPAN_MS = 180 * DAY_MS # ...or, for a chart scrolled back, as much as it wants, up to this
OLDER_TIMEOUT_S = 360            # unanswered this long (the add-on waits 5 minutes for NinjaTrader): the others go on
OLDER_RETRY_S = 600              # an error answer: the symbol asked again after this, doubling with each in a row...
OLDER_RETRY_MAX_S = 3600         # ...up to this; never taken as where its history ends (NinjaTrader's Panic, a lost
                                 # data connection), and asked again at the add-on's next hello
OLDER_LIMIT = "history_limit"    # {symbol: [ms, when]}: NinjaTrader had nothing before ms (asked again after a week)
OLDER_LIMIT_DAYS = 7
STUDY_DAYS = 400                 # older minutes this recent reload the symbol's studies (AMT, Daily SMA x4...) once
STUDY_RELOAD_S = 300             # ...once its older requests are through, and at most this often (a chart scrolled back)


def prefill_days() -> int:
    """How far back older history is filled in the background: CHART_HISTORY_PREFILL_DAYS (365; 0 turns it off)."""
    v = os.environ.get("CHART_HISTORY_PREFILL_DAYS", "").strip()
    return max(0, min(int(v), 20 * 366)) if v.isdigit() else 365


def filling(market, sym: str) -> bool:
    """Whether the stretch from `sym`'s stored data up to its live trades is still being filled (the add-on just
    connected, the instrument just put back on Instruments) and is MIN_GAP_MS or more. Its alert watches (gap,
    indicator, study, AMT), loaded from history that stops before it, keep learning from the live minutes but send
    nothing meanwhile: they would compare live prices with days-old bars. history_changed loads them again with the
    fill. A shorter stretch (the add-on reconnecting after a server restart or a network blip) holds nothing back:
    the watches miss a minute or two at most, nothing days old. Nor does a hole."""
    w = getattr(getattr(market, "gapfill", None), "windows", {}).get(sym)
    return w is not None and w.stale


class _Window:
    def __init__(self, start: int, end: float, hole: bool = False):
        self.start, self.end = start, end          # trades with start < ts < end (math.inf: until live trades come)
        self.cutoff = end                          # lowered to the first live trade after connecting
        self.hole = hole                           # inside the stored data
        self.stale = False                         # the stretch before the live trades, MIN_GAP_MS or more (filling),
                                                   # or its rest asked again as minutes
        self.discard = False                       # its answer is another contract's: nothing kept
        self.mismatch = False                      # known by its prices (not by a roll while it was asked)
        self.probe: dict[int, float] = {}          # a hole: the history's last price per minute just before it
        self.first = self.last = None              # the first and last trade the answer brought inside
        self.checked = not hole                    # those compared with the stored ones
        self.ticks: list[Tick] = []
        self.rows: list[tuple] = []                # 1-minute bars from history, not written yet
        self.minutes: dict[int, list] = {}         # minute open -> [o, h, l, c, v, n]
        self.count = 0
        self.t0 = time.monotonic()
        self.answered = False                      # done() with its history stored: no error, nor "none" (none to give)
        self.collect: dict | None = None           # the AMT's prior day (ask_value): price -> volume, nothing stored
        self.future = None
        self.bars = False                          # ...answered with 1-minute bars: no tick history
        self.older = False                         # older history (GAP|...|older): 1-minute bars before anything stored
        self.late = False                          # ...not answered in time: the others go on; its answer is kept
        self.hold = False                          # only holds its alerts: NinjaTrader's last answer for it still coming
        self.early: list[tuple[Tick, int]] = []    # marked live trades (add-on 23) as stored, with their own time
        self.moved: set[int] = set()               # minutes below the cutoff with live trades sent in the 16:00
                                                   # break, moved back into the closing session (moved())
        self.copies = 0                            # history trades that were live ones again: kept once (copies())

    @property
    def edge(self) -> bool:
        """The stretch up to the live trades: not a hole, older history or the AMT's prior day."""
        return not (self.hole or self.older or self.collect is not None)


def shut(ts: int, group: str | None) -> bool:
    """History stamped while the market is shut (the 16:00 break, a weekend, a holiday) is left out: no trade prints
    then. NinjaTrader's 1-minute history has a bar after the close, the session's whole volume at the settlement price
    (a million contracts on NQ); stored, it was a day's volume in one minute on every chart and volume profile holding
    it (store.drop_settlement_bars takes out the ones stored before)."""
    return not sessions.is_open(ts, group)


def copies(hist: list, live: list, run: int | None = None, kinds: int | None = None) -> list[tuple[int, int]]:
    """Which history trades are live trades sent again: (i, j) pairs, hist[i] being live[j], each of either in one pair
    at most. Both are lists of prints, (price, size): the history's in time order, the live ones as they came. A pair is
    taken only inside a run of `run` (COPY_RUN) or more alike one for one, in the same order or the reverse one (a
    catch-up newest first), with `kinds` (COPY_KINDS) or more different prints in it: a quiet market's tape (one print
    again and again) or two tapes that aren't the same trades never pair by chance. Runs are found from windows of `run`
    live prints with that many kinds (a start that can't reach a run is skipped), by a hash of each, then grown both ways
    as far as the prints stay alike."""
    run = COPY_RUN if run is None else run
    kinds = COPY_KINDS if kinds is None else kinds
    n, m = len(hist), len(live)
    if run < 1 or n < run or m < run:
        return []
    ids: dict = {}
    h = [ids.setdefault(k, len(ids) + 1) for k in hist]
    lv = [ids.get(k, 0) for k in live]   # a print the history hasn't: 0, alike with nothing
    mod, base = (1 << 61) - 1, 1_000_003
    top = pow(base, run - 1, mod)

    def windows(seq):   # the hash of each window of `run`, by its start
        out, x = [], 0
        for k, v in enumerate(seq):
            if k >= run:
                x = (x - seq[k - run] * top) % mod
            x = (x * base + v) % mod
            if k >= run - 1:
                out.append(x)
        return out
    fwd: dict[int, list[int]] = {}
    for i, x in enumerate(windows(h)):
        fwd.setdefault(x, []).append(i)
    rev: dict[int, list[int]] = {}
    for r, x in enumerate(windows(h[::-1])):   # live[j + k] is hist[i - k]
        rev.setdefault(x, []).append(n - 1 - r)
    ok, seen, zeros = [], {}, 0   # whether each live window has `kinds` prints, all of them the history's
    for k, v in enumerate(lv):
        seen[v] = seen.get(v, 0) + 1
        zeros += v == 0
        if k >= run:
            old = lv[k - run]
            seen[old] -= 1
            zeros -= old == 0
            if not seen[old]:
                del seen[old]
        if k >= run - 1:
            ok.append(not zeros and len(seen) >= kinds)
    hashes = windows(lv)
    used_h, used_l = [False] * n, [False] * m
    first: dict[tuple, int] = {}   # (direction, hash) -> its first window not paired yet (a periodic tape: no rescans)
    pairs: list[tuple[int, int]] = []
    j = 0
    while j <= m - run:
        found = None
        if ok[j]:
            for d, table in ((1, fwd), (-1, rev)):
                cands = table.get(hashes[j], ())
                k0 = first.get((d, hashes[j]), 0)
                # a window touching a pair touches one of its ends (each run paired is `run` long at least): gone for good
                while k0 < len(cands) and (used_h[cands[k0]] or used_h[cands[k0] + d * (run - 1)]):
                    k0 += 1
                first[(d, hashes[j])] = k0
                for i in cands[k0:]:
                    if not (used_h[i] or used_h[i + d * (run - 1)]) and all(h[i + d * k] == lv[j + k] for k in range(run)):
                        found = i, d
                        break
                if found:
                    break
        if found is None:
            j += 1
            continue
        i, d = found
        a = 0   # back along it, over live prints passed by (no window of their own matched)
        while j - a > 0 and 0 <= i - d * (a + 1) < n and not used_l[j - a - 1] and not used_h[i - d * (a + 1)] \
                and lv[j - a - 1] == h[i - d * (a + 1)]:
            a += 1
        e = run
        while j + e < m and 0 <= i + d * e < n and not used_h[i + d * e] and lv[j + e] == h[i + d * e]:
            e += 1
        for k in range(-a, e):
            used_l[j + k] = used_h[i + d * k] = True
            pairs.append((i + d * k, j + k))
        j += e
    return pairs


class GapFill:
    def __init__(self, market):
        self.m = market
        self.windows: dict[str, _Window] = {}
        self.status: dict[str, dict] = {}
        self.queue: dict[str, list[tuple[int, int]]] = {}   # holes still to ask for, per symbol
        self.changed: set[str] = set()   # filled, its charts not reloaded yet (once its holes are done)
        self.minutes_ok = False          # the add-on takes GAP|...|minutes (7+): what trades left out comes as minutes
        self.values: dict[str, list] = {}   # per symbol: RTH sessions the AMT wants from NinjaTrader's tick history
        self.contracts_ok = False        # the add-on answers a stretch across rolls from each contract in turn (8+)
        self.older_ok = False            # the add-on answers GAP|...|older (20+)
        self.live_asks = False           # the add-on asks up to its first live trade (6+): GAP lines end in |live
        self.reopen: set[str] = set()    # symbols handed back to it while their last request was still answered
        # symbols whose request NinjaTrader still answers after it was given up on (an AMT timeout, a plugin taking the
        # symbol): its lines are dropped till its Z, and nothing else is asked of NinjaTrader for them meanwhile
        self.nt_out: set[str] = set()
        self.older_busy: tuple[str, float] | None = None   # (symbol, monotonic time asked): one older request at a time
        self.older_want: dict[str, int] = {}     # symbol -> span (ms): charts scrolled back past its stored minutes
        self.older_wait: dict[str, float] = {}   # symbol -> monotonic time: not asked before (after an error or no answer)
        self.older_failed: dict[str, int] = {}   # symbol -> older requests failed in a row
        self.older_filled: set[str] = set()      # older minutes from the last STUDY_DAYS stored: its studies reload once
        self.older_reloaded: dict[str, float] = {}   # symbol -> monotonic time its studies last reloaded for them

    def last_stored(self, sym: str) -> int | None:
        """End of the stored data: the last trade when trades reach the last 1-minute bar, else that bar's end
        (imported bars have no trades). Trades still waiting to be written (a full disk) count: asked for again, they
        would be stored twice."""
        db = self.m.store.db
        t = db.execute("SELECT MAX(ts) FROM ticks WHERE symbol=?", (sym,)).fetchone()[0]
        held = [tk.ts for tk in self.m.tick_buf if tk.symbol == sym]
        if held:
            t = max(t or 0, max(held))
        b = db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=?", (sym,)).fetchone()[0]
        if t is not None and (b is None or t >= b):
            return t
        return b + 60_000 if b is not None else None

    def requests(self, now: int, live: bool = False, minutes: bool = False, contracts: bool = False,
                 older: bool = False) -> list[str]:
        """GAP lines for every symbol whose stored data stops short of now, or else its first hole (called when the
        add-on says hello); not the ones a feed plugin supplies (it fills its own: open()). `live`: the add-on asks
        up to its first live trade once its data connection is up, and answers holes (add-on 6+). `older`: it answers
        older history (20+): that goes after these (next_older)."""
        self.m.flush_for_read()
        self.minutes_ok, self.contracts_ok, self.older_ok = minutes, contracts, older
        self.live_asks, self.reopen, self.nt_out = live, set(), set()
        self.older_busy = None
        self.older_failed, self.older_wait = {}, {}   # a new connection: what failed on the last one is asked again now
        self.fail_values()   # asked on the connection before: no answer comes now
        owned = getattr(self.m, "trade_owner", {})
        out = []
        for sym in self.m.instruments:
            if sym in owned:
                continue
            self.windows.pop(sym, None)
            self.queue[sym] = self.holes(sym, now) if live else []   # older add-ons answer before NinjaTrader connects
            span = self.open(sym, now, flush=False, least=1 if live else MIN_GAP_MS,   # open end: any stretch at all
                             reach=self.reach())
            if span:
                out.append(f"GAP|{sym}|{span[0]}|{span[1]}" + ("|live" if live else ""))
            elif line := self.next(sym):
                out.append(line)
        return out

    def again(self, sym: str, now: int) -> str | None:
        """`sym` ticks again after the watchdog found it stale, the add-on connected all along (NinjaTrader's data
        connection lost and back): no hello asks for the hole that left, so its holes are queued as at a connect and
        the GAP line for the first one returned. Not while it has a window or holes still to ask (those go on), nor
        for another feed's."""
        w = self.windows.get(sym)
        if (w is not None or sym in self.nt_out) and not self.queue.get(sym) and sym not in getattr(self.m, "trade_owner", {}):
            # a request out (an older month's, the AMT's prior day, a hole: likely waiting on the same data connection):
            # the holes go after its answer (next), not the one it is asking
            self.m.flush_for_read()
            self.queue[sym] = [h for h in self.holes(sym, now) if w is None or w.older or w.collect is not None
                               or not (h[0] < w.cutoff and h[1] > w.start)]
            return None
        if w is not None or self.queue.get(sym) or sym in getattr(self.m, "trade_owner", {}):
            return None
        self.m.flush_for_read()   # the minute it ticks again, still in memory, is where the hole ends
        self.queue[sym] = self.holes(sym, now)
        return self.next(sym)

    def reach(self) -> int:
        """How far back a stretch is filled: across contract rolls only when the add-on fills each from its contract."""
        return LONG_GAP_MS if self.contracts_ok else MAX_GAP_MS

    def open(self, sym: str, now: int, flush: bool = True, least: int = MIN_GAP_MS, reach: int = MAX_GAP_MS) -> tuple[int, int] | None:
        """The stretch of `sym` to fill, (start, now), if its stored data stops short of now; its trades then go to
        tick() or minute(), and done() ends it. It stays open past now until the first live trade."""
        if flush:
            self.m.flush_for_read()
        last = self.last_stored(sym)   # nothing stored (an instrument just added): the last 14 days
        start = now - reach if last is None else max(last, now - reach)
        rolled = self.m.store.get_setting(ROLLED, {}) or {}
        if last is not None and isinstance(rolled, dict) and int(rolled.get(sym, 0)) > start \
                and (not self.contracts_ok or sym in getattr(self.m, "trade_owner", {})):
            # stored data that stops before a roll recorded since (nothing stored after it: a restart over the weekend)
            # is the old contract's: as when the roll came (rolled()), the new one's history from the next session open
            start = sessions.next_session_start(sessions.session_start(start)) - 1
        if now - start < least:
            return None
        w = self.windows[sym] = _Window(start, math.inf)
        w.stale = now - start >= MIN_GAP_MS   # (what older add-ons and the plugins are asked for at all)
        self.status[sym] = {"from": start, "to": None, "state": "asked"}
        return start, now

    def holes(self, sym: str, now: int) -> list[tuple[int, int]]:
        """Stretches inside `sym`'s stored data of the last HOLE_DAYS, since its last contract roll, with no 1-minute
        bar while the market was open (start, end): from its last trade before (a bar's edge when that minute has no
        trades) to the next stored minute's open. The newest MAX_HOLES, oldest first; not the ones asked for already."""
        inst = self.m.instruments.get(sym)
        if inst is None:
            return []
        db = self.m.store.db
        least = QUIET_HOLE_MIN_MS if getattr(inst, "quiet", False) else HOLE_MIN_MS
        group = getattr(inst, "calendar", None)
        asked = self._asked().get(sym, [])
        rolled = self.m.store.get_setting(ROLLED, {}) or {}
        since = max(now - HOLE_DAYS * 86_400_000, int(rolled.get(sym, 0)) if isinstance(rolled, dict) else 0)
        out = []
        for t, prev in db.execute("SELECT t, prev FROM (SELECT t, LAG(t) OVER (ORDER BY t) AS prev FROM bars_1m "
                                  "WHERE symbol=? AND t>=?) WHERE t - prev > ?", (sym, since, least)):
            a, b = prev + 60_000, t   # the minutes with no bar
            if self._open_ms(group, a, b) < least:
                continue   # the daily break, a weekend, a holiday
            if any(x <= a and b <= y for x, y in asked):
                continue
            # from its last trade before, or after the trades a fill cut short stored (none twice)
            last = db.execute("SELECT MAX(ts) FROM ticks WHERE symbol=? AND ts>=? AND ts<?", (sym, prev, b)).fetchone()[0]
            out.append((last if last is not None else a - 1, b))
        return out[-MAX_HOLES:]

    @staticmethod
    def _open_ms(group, a: int, b: int) -> int:
        """How long the market was open between a and b, by whole minutes."""
        return sum(sessions.is_open(k, group) for k in range(a // 60_000 * 60_000, b, 60_000)) * 60_000

    def next(self, sym: str) -> str | None:
        """The GAP line for `sym`'s next hole, its window open (one at a time: the add-on's lines name the symbol
        only); None when they are done, and its charts reload if any was filled. A symbol handed back to NinjaTrader
        while its last request was still answered (reopen): its stretch since its stored data, now."""
        if sym in self.windows or sym in self.nt_out:
            return None
        if sym in self.reopen and sym not in getattr(self.m, "trade_owner", {}):
            self.reopen.discard(sym)
            span = self.open(sym, int(time.time() * 1000), reach=self.reach())
            if span:
                return f"GAP|{sym}|{span[0]}|{span[1]}" + ("|live" if self.live_asks else "")
        if not self.queue.get(sym):
            self.settle(sym)
            return self._next_value(sym)
        item = self.queue[sym].pop(0)   # (start, end[, probe[, minutes[, stale]]])
        span = self.open_hole(sym, item[0], item[1], item[2] if len(item) > 2 else True)
        self.windows[sym].stale = len(item) > 4 and item[4]   # the rest of a stretch that holds alerts: they wait for it
        return f"GAP|{sym}|{span[0]}|{span[1]}" + ("|minutes" if len(item) > 3 and item[3] else "")

    # --- the AMT's prior day from NinjaTrader's tick history -----------------------------------------------
    def ask_value(self, sym: str, start: int, end: int, future) -> str | None:
        """The trades of `sym`'s RTH session [start, end) from NinjaTrader's tick history, as the NinjaTrader AMT
        Toolkit downloads them, for the prior day's value: collected (price -> volume, into `future`), not stored. The
        GAP line to send now, or None: it goes after what `sym` is being asked for (next())."""
        self.values.setdefault(sym, []).append((start, end, future))
        if sym in self.windows or sym in self.nt_out or self.queue.get(sym):
            return None
        return self._next_value(sym)

    def _next_value(self, sym: str) -> str | None:
        while self.values.get(sym):
            start, end, fut = self.values[sym].pop(0)
            if fut.done():   # given up on (timed out)
                continue
            w = self.windows[sym] = _Window(start, end)
            w.collect, w.future = {}, fut
            return f"GAP|{sym}|{start - 60_000}|{end + 60_000}"
        return None

    def fail_values(self, sym: str | None = None):
        """Those asked won't be answered (the add-on reconnected or left, or one timed out): None, and the AMT uses the
        trades stored here."""
        for s in ([sym] if sym else list(self.values)):
            for _, _, fut in self.values.pop(s, []):
                if not fut.done():
                    fut.set_result(None)
        for s, w in list(self.windows.items()):
            if w.collect is not None and (sym is None or s == sym):
                del self.windows[s]
                if w.future is not None and not w.future.done():
                    w.future.set_result(None)

    def settle(self, sym: str):
        """`sym`'s charts and studies reload, once, if anything was filled since the last time."""
        if sym in self.changed:
            self.changed.discard(sym)
            self.m.history_changed(sym)

    def rolled(self, sym: str, now: int):
        """`sym` moved to another contract: no hole from before now is asked for; the one being asked is dropped
        unless its answer has begun (asked before the roll: the old contract's, right for it). The gap at the end
        keeps only what comes from the next session open: the new contract's history, not merged into the old one's
        minute or session (the add-on's rolls land in the break)."""
        got = self.m.store.get_setting(ROLLED, {}) or {}
        got = got if isinstance(got, dict) else {}
        self.m.store.set_setting(ROLLED, {**got, sym: now})
        self.queue.pop(sym, None)
        w = self.windows.get(sym)
        if w is not None and w.older:
            pass   # each contract's by NinjaTrader's roll dates, whatever is charted now
        elif w is not None and w.hole and not (w.probe or w.count):
            w.discard = True
        elif w is not None and not w.hole and (not self.contracts_ok or sym in getattr(self.m, "trade_owner", {})):
            # (add-on 8+ fills each contract from its own; a feed plugin's history is the contract it is on now)
            w.start = max(w.start, sessions.next_session_start(sessions.session_start(w.start)) - 1)

    def open_hole(self, sym: str, start: int, end: int, probe: bool = True) -> tuple[int, int]:
        """A hole's window: trades with start < ts < end; done() ends it. The span to ask for starts PROBE_MS
        earlier: those prices are compared with the stored ones, not kept (`probe` False: checked already)."""
        self.windows[sym] = _Window(start, end, hole=True)
        self.windows[sym].checked = not probe
        self.status[sym] = {"from": start, "to": end, "state": "asked", "hole": True}
        return (start - PROBE_MS if probe else start), end

    def _same_contract(self, sym: str, w: _Window) -> bool:
        """The history's prices just before a hole against the stored bars' closes, minute by minute: one contract
        matches to a few ticks somewhere, another is off by the spread between them everywhere."""
        if not w.probe:
            return True   # nothing to compare: taken as it comes
        rows = self.m.store.db.execute("SELECT t, c FROM bars_1m WHERE symbol=? AND t>=? AND t<=?",
                                       (sym, min(w.probe), max(w.probe))).fetchall()
        diffs = [abs(c - w.probe[t]) for t, c in rows if t in w.probe]
        if not diffs:
            return True
        tick = self.m.instruments[sym].tick_size
        return min(diffs) <= max(10 * tick, 0.0005 * max(w.probe.values()))

    def _check(self, sym: str, w: _Window):
        """Once, at the first answer inside the hole (the history comes oldest first, its probe before it)."""
        if not w.checked:
            w.checked = True
            if not self._same_contract(sym, w):
                w.discard = w.mismatch = True
                log.warning("gap fill %s: NinjaTrader's prices before the hole are another contract's; not kept", sym)

    def _asked(self) -> dict[str, list]:
        got = self.m.store.get_setting(ASKED, {}) or {}
        return got if isinstance(got, dict) else {}

    def _remember(self, sym: str, w: _Window):
        """A hole asked for and answered: not asked again (NinjaTrader had what it had). Ones over a week older than it
        are forgotten (holes are only looked for in the last HOLE_DAYS)."""
        keep = w.end - (HOLE_DAYS + 1) * 86_400_000
        asked = {s: [x for x in v if x[1] >= keep] for s, v in self._asked().items()}
        asked.setdefault(sym, []).append([w.start + 1, w.end])   # its minutes: from the one after the last trade
        self.m.store.set_setting(ASKED, {s: v for s, v in asked.items() if v})

    def forget_asked(self, syms):
        """Another feed supplies these now: the holes asked of the one before are asked of it too (what one had none
        for, the other may have: IBKR keeps 1-minute bars for months)."""
        asked = self._asked()
        if any(s in asked for s in syms):
            self.m.store.set_setting(ASKED, {s: v for s, v in asked.items() if s not in syms})

    def until(self, sym: str, now: int) -> tuple[int, int] | None:
        """An open window's span, up to now unless a live trade already marked where live data starts (a feed plugin
        opens its windows before it connects, then asks for the history once connected)."""
        w = self.windows.get(sym)
        if w is None:
            return None
        end = int(min(w.cutoff, now))
        self.status[sym]["to"] = end
        return w.start, end

    def live(self, sym: str, ts: int):
        """A live trade (or one from the add-on's buffer): history stops before it."""
        w = self.windows.get(sym)
        if w and ts < w.cutoff:
            w.cutoff = max(ts, w.start)

    def moved(self, sym: str, ts: int):
        """A live trade sent in the 16:00 CT break that Market.on_tick moved back to the closing session's last ms
        (`ts`, as stored): below the cutoff its stamp set, in a minute whose bar the history's trades replace at the
        Z. That minute is built again then from all its stored trades (the history's and these, marked or not, paired
        or not)."""
        w = self.windows.get(sym)
        if w is not None and w.edge and not w.discard and ts < w.cutoff:
            w.moved.add(ts // 60_000 * 60_000)

    def early(self, sym: str, tk: Tick, at: int, sent: int | None = None):
        """A live trade add-on 23 marked with NinjaTrader's own time for it (`at`): older than the second its history
        request stops at, so that history brings it again (NinjaTrader's catch-up of recent trades as it starts
        mid-session). `tk` as stored; `sent` the add-on's stamp for it, as live() had it (tk.ts if not given: on_tick
        moves one stamped in the 16:00 break into a session, 16:30 on to the next 17:00 open). Kept for its gap's Z
        (copies()), if its gap is the live edge's and it was sent within EARLY_MS of the first live trade (by the
        add-on's stamps, both), EARLY_MAX at most; never a feed plugin's symbol's."""
        w = self.windows.get(sym)
        if w is None or not w.edge or w.discard or w.cutoff == math.inf or sym in getattr(self.m, "trade_owner", {}) \
                or (tk.ts if sent is None else sent) - w.cutoff >= EARLY_MS or len(w.early) >= EARLY_MAX:
            return
        w.early.append((tk, int(at)))

    def tick(self, sym: str, ts: int, price: float, size: int):
        w = self.windows.get(sym)
        if not w or w.discard or size <= 0 or w.older:   # older history comes as 1-minute bars only
            return
        if w.collect is not None:   # the AMT's: a trade on the minute is the minute it ends, as NinjaTrader's bars
            if w.start <= (ts - 1) // 60_000 * 60_000 < w.end:
                price = self.m.instruments[sym].round(price)
                w.collect[price] = w.collect.get(price, 0) + size
            return
        if w.hole and w.start - PROBE_MS < ts <= w.start:   # just before the hole: to compare, not to keep
            w.probe[ts // 60_000 * 60_000] = self.m.instruments[sym].round(price)
            return
        if not (w.start < ts < w.cutoff):
            return
        inst = self.m.instruments[sym]
        if shut(ts, getattr(inst, "calendar", None)):
            return
        self._check(sym, w)
        if w.discard:
            return
        w.first = ts if w.first is None else min(w.first, ts)
        w.last = ts if w.last is None else max(w.last, ts)
        price = inst.round(price)
        w.ticks.append(Tick(sym, ts, price, size, 0))
        k = ts // 60_000 * 60_000
        b = w.minutes.get(k)
        if b is None:
            w.minutes[k] = [price, price, price, price, size, 1]
        else:
            b[1], b[2], b[3], b[4], b[5] = max(b[1], price), min(b[2], price), price, b[4] + size, b[5] + 1
        w.count += 1
        keep = TAIL if w.edge else 0   # the live edge's last ones wait for its Z: live trades sent again are there
        if len(w.ticks) >= keep + CHUNK and (len(w.ticks) - keep) % CHUNK == 0:
            self._write_ticks(w, keep)   # a failed write (the database not writable) keeps them for the next chunk

    def minute(self, sym: str, t: int, o: float, h: float, lo: float, c: float, v: int):
        """A 1-minute bar (by its open) when the provider has no tick history: only minutes wholly in the gap."""
        w = self.windows.get(sym)
        if not w or w.discard:
            return
        if w.collect is not None:
            w.bars = True
            return
        if w.hole and w.start - PROBE_MS - 60_000 <= t < w.start:   # just before the hole: to compare
            w.probe[t] = self.m.instruments[sym].round(c)
            return
        if t < w.start or t + 60_000 > w.cutoff:
            return
        if shut(t, getattr(self.m.instruments[sym], "calendar", None)):
            return
        self._check(sym, w)
        if w.discard:
            return
        r = self.m.instruments[sym].round
        w.rows.append((t, r(o), r(h), r(lo), r(c), v, 0))   # written in batches: months of minutes come at once
        w.count += 1
        # older history all at once, when it is all in: part of it (oldest first) would leave a hole the first stored
        # minute then hides
        if len(w.rows) % CHUNK == 0 and not w.older:
            self._write_rows(sym, w)

    def done(self, sym: str, kind: str, count: int, note: str):
        w = self.windows.pop(sym, None)
        if w is None:
            return
        if w.late or w.discard:   # a request answered after it was given up on: pages told to wait ask again
            self._told(sym)
        if w.older:
            self._older_done(sym, w, kind, count, note)
            return
        if w.collect is not None:
            got = w.collect if kind == "ticks" and w.collect and not w.bars else None
            log.info("AMT %s: NinjaTrader's trades for %s: %s", sym, w.start, f"{sum(w.collect.values()):,.0f} contracts"
                     if got else f"none ({kind}, {note})")
            if w.future is not None and not w.future.done():
                w.future.set_result(got)
            return
        if w.hole and kind == "ticks" and not w.count and w.probe and not w.discard:
            self._check(sym, w)   # only trades before the hole came: the provider keeps no tick history for it
            if not w.discard:     # the same contract: asked again from its start, so the add-on sends 1-minute bars
                self.queue.setdefault(sym, []).insert(0, (w.start, w.end, False))
                self.status[sym] = {"from": w.start, "to": w.end, "state": "asked", "hole": True, "note": "again for minutes"}
                return
        if w.discard:   # another contract's trades: nothing kept
            if w.mismatch:   # NinjaTrader's history is the current contract's: asking again won't help
                self._remember(sym, w)
            self.status[sym] = {"from": w.start, "to": w.end, "state": "dropped", "hole": True,
                                "note": "another contract's prices" if w.mismatch else "contract rolled meanwhile"}
            if not self.queue.get(sym):
                self.settle(sym)
            return
        open_end = w.cutoff == math.inf
        if open_end:   # no live trade yet: up to now, the minute it is now kept going by the live feed
            w.cutoff = int(time.time() * 1000)
        live_min = int(w.cutoff) // 60_000 * 60_000
        touched: set[int] = set()   # minutes built again from their stored trades
        pending: list[Tick] = []   # live trades not written yet (the database not writable just now)
        if w.edge and not open_end:
            # the live trades stored: to compare with, and the live minute's to merge with
            pending = self.m.flush_for_read(sym) or []
            if kind == "ticks" and w.early and w.ticks and not pending:   # (else both kept, as before r23)
                touched |= self._copies(sym, w)
            if live_min in w.minutes:   # the history's trades of the minute live ones start in: into its bar
                touched.add(live_min)
            # live ones moved below the cutoff (sent in the 16:00 break), in a minute the history's trades replace: with
            # no pair in the history (a catch-up too short to compare, or unmarked) that bar would hold its trades only
            touched |= w.moved & set(w.minutes)
        self._write_ticks(w)
        self._write_rows(sym, w)
        merged = self._write_minutes(sym, w, open_end)
        if touched:   # (those before the live minute were written above, from the history: counted once)
            self._rebuild(sym, touched, live_min, pending)
            merged += sum(k >= live_min for k in touched)
        if w.hole and kind != "error":
            self._remember(sym, w)
        w.answered = kind not in ("error", "none")   # (a gap alert held meanwhile is sent: gaps_service)
        if kind == "ticks" and w.first is not None and self.minutes_ok and sym not in getattr(self.m, "trade_owner", {}):
            self._rest_as_minutes(sym, w)
        took = time.monotonic() - w.t0
        self.status[sym] = {"from": w.start, "to": w.cutoff, "state": "done", "kind": kind, "sent": count,
                            "stored": w.count, "minutes": merged, "note": note, "seconds": round(took, 1),
                            **({"hole": True} if w.hole else {}), **({"copies": w.copies} if w.edge else {})}
        log.info("gap fill %s: %s, %d sent, %d stored, %d minutes (%s) in %.1fs", sym, kind, count, w.count, merged, note, took)
        if w.count or merged or w.copies:
            self.changed.add(sym)
        # the gap at the end reloads now, and again once its rest is in (the alerts it held go on then); holes once they
        # are done
        more = self.queue.get(sym)
        if not w.hole or not more or w.stale and not (len(more[0]) > 4 and more[0][4]):
            self.settle(sym)

    def _copies(self, sym: str, w: _Window) -> set[int]:
        """The marked live trades its history brought again (copies() of the trades still held): each kept once, the
        history's (in its place: a millisecond's trades keep the exchange's order, however the catch-up came) with the
        live one's aggressor side, the live one taken out, in one transaction with the history's trades still held,
        which it writes. The minutes either was in, to build again."""
        got = copies([(t.price, t.size) for t in w.ticks], [(tk.price, tk.size) for tk, _ in w.early])
        if not got:
            return set()
        db = self.m.store.db
        n, touched = 0, set()
        with db:
            for i, j in got:
                h, tk = w.ticks[i], w.early[j][0]
                if db.execute("DELETE FROM ticks WHERE rowid=(SELECT rowid FROM ticks WHERE symbol=? AND ts=? AND "
                              "price=? AND size=? AND side=? ORDER BY rowid LIMIT 1)",
                              (sym, tk.ts, tk.price, tk.size, tk.side)).rowcount:
                    w.ticks[i] = Tick(h.symbol, h.ts, h.price, h.size, tk.side)
                    n += 1
                    touched |= {h.ts // 60_000 * 60_000, tk.ts // 60_000 * 60_000}
                # else not stored (not found): both kept, as before r23, nothing lost
            if n:   # (a live trade taken out only with its history's copy written)
                db.executemany("INSERT INTO ticks VALUES (?,?,?,?,?)", [(t.symbol, t.ts, t.price, t.size, t.side)
                                                                         for t in w.ticks])
        w.copies = n
        if n:
            w.ticks = []
            log.warning("gap fill %s: %d trades sent live came again in NinjaTrader's history (its catch-up as it "
                        "started): kept once, at the history's time", sym, n)
        return touched

    def _rebuild(self, sym: str, minutes: set[int], live_min: int, pending: list[Tick] = ()):
        """These minutes' bars built again from their stored trades in time order (any stored before the gap, then the
        history's, then the live ones), and the live trades still `pending` (not written yet, written after the others):
        written, and changed in place in the live feed's memory while it still holds them, also when it has moved on
        (and in the bars waiting to be written); one left with no trade goes. The live minute, if the live feed has no
        bar for it yet, is handed to it (a live trade in it extends it)."""
        db = self.m.store.db
        built: dict[int, Bar | None] = {}
        for k in sorted(minutes):
            trades = list(db.execute("SELECT ts, price, size, side FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                     "ORDER BY ts, rowid", (sym, k, k + 60_000)))
            trades += [(tk.ts, tk.price, tk.size, tk.side) for tk in pending if k <= tk.ts < k + 60_000]
            trades.sort(key=lambda r: r[0])   # (stable: of one ms, as stored, then as they wait)
            b = None
            for _, px, size, side in trades:
                if b is None:
                    b = Bar(t=k, o=px, h=px, l=px, c=px)
                b.add(px, size, side)
            built[k] = b
        self.m.store.write([], [(sym, b) for b in built.values() if b is not None])
        gone = [k for k, b in built.items() if b is None]
        if gone:
            with db:
                db.executemany("DELETE FROM bars_1m WHERE symbol=? AND t=?", [(sym, k) for k in gone])
        dirty = getattr(self.m, "dirty", {})
        for k, b in built.items():   # one still to be written (the live feed's, or one it let go while writes failed)
            x = dirty.get((sym, k))  # would be written over these
            if x is None:
                continue
            if b is None:
                del dirty[(sym, k)]
            else:
                x.o, x.h, x.l, x.c, x.v, x.bv, x.sv, x.n = b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n
        s = getattr(self.m, "m1", {}).get(sym)
        if s is None:
            return
        for x in list(s.bars):
            if x.t not in built:
                continue
            b = built[x.t]
            if b is None:
                s.bars.remove(x)
            else:
                x.o, x.h, x.l, x.c, x.v, x.bv, x.sv, x.n = b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n
        b = built.get(live_min)
        if b is not None and (s.last is None or s.last.t < live_min):
            s._seq += 1   # as after a restart (Market.seed)
            b.seq = s._seq
            s.bars.append(b)

    def _write_rows(self, sym: str, w: _Window):
        if w.rows:
            self.m.store.insert_rows_if_missing(sym, w.rows)
            w.rows = []

    def _write_ticks(self, w: _Window, keep: int = 0):
        """Its trades so far, but the last `keep`."""
        if len(w.ticks) > keep:
            cut = len(w.ticks) - keep
            self.m.store.write(w.ticks[:cut], [])
            w.ticks = w.ticks[cut:]

    def _rest_as_minutes(self, sym: str, w: _Window):
        """The trades came for part of the stretch only (NinjaTrader recorded them itself; the provider keeps no tick
        history that far back): the rest before and after them is asked again as 1-minute bars, first, holding the
        alerts as the stretch did (filling)."""
        group = getattr(self.m.instruments.get(sym), "calendar", None)
        rest = []
        if self._open_ms(group, w.start, w.first) >= HOLE_MIN_MS:
            rest.append((w.start, w.first // 60_000 * 60_000, False, True, w.stale))
        end = int(w.cutoff)
        if self._open_ms(group, w.last, end) >= HOLE_MIN_MS:
            rest.append(((w.last // 60_000 + 1) * 60_000 - 1, end, False, True, w.stale))
        if rest:
            self.queue[sym] = rest + self.queue.get(sym, [])

    def _write_minutes(self, sym: str, w: _Window, open_end: bool = False) -> int:
        """Bars from the gap's trades: whole minutes replace, the minute where stored data ended is merged, the
        minute the live feed is building is left alone. With no live trade yet (open_end), the minute it is now is
        written too and handed to the live feed, which goes on with it. Trades a fill cut short stored before it (no
        bars: those come at the end) get theirs too, the minute it was cut in from both fills' trades."""
        if w.hole:
            return self._hole_minutes(sym, w)
        db = self.m.store.db
        before = db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=? AND t<=?", (sym, w.start)).fetchone()[0]
        cut: dict[int, list] = {}   # minute open -> [o, h, l, c, v, n] of the stored trades after the last bar
        for ts, px, size in db.execute("SELECT ts, price, size FROM ticks WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
                                       (sym, 0 if before is None else before + 60_000, w.start)):
            b = cut.get(k := ts // 60_000 * 60_000)
            if b is None:
                cut[k] = [px, px, px, px, size, 1]
            else:
                b[1], b[2], b[3], b[4], b[5] = max(b[1], px), min(b[2], px), px, b[4] + size, b[5] + 1
        if not w.minutes and not cut:
            return 0
        first = w.start // 60_000 * 60_000
        last_live = int(w.cutoff) // 60_000 * 60_000
        bars = []
        for k in sorted(set(w.minutes) | set(cut)):
            if k > last_live or (k == last_live and not open_end):
                continue
            o, h, lo, c, v, n = w.minutes.get(k) or cut[k]
            bv = sv = 0   # history has no aggressor side: a merged minute keeps the stored split
            if k in cut and k in w.minutes:   # where the fill was cut: its stored trades came first
                so, sh, sl, _, sv_, sn = cut[k]
                o, h, lo, v, n = so, max(sh, h), min(sl, lo), sv_ + v, sn + n
            elif k == first and w.start % 60_000:
                old = db.execute("SELECT o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol=? AND t=?", (sym, k)).fetchone()
                if old:
                    o, h, lo, v, bv, sv, n = old[0], max(old[1], h), min(old[2], lo), old[4] + v, old[5], old[6], old[7] + n
            bars.append((sym, Bar(t=k, o=o, h=h, l=lo, c=c, v=v, bv=bv, sv=sv, n=n)))
        self.m.store.write([], bars)
        s = getattr(self.m, "m1", {}).get(sym)
        if open_end and bars and s is not None and bars[-1][1].t == last_live:   # the live feed goes on with it
            b = bars[-1][1]
            if s.last is not None and s.last.t == last_live:   # its own copy (seeded, or from before): now the merged one
                x = s.last   # in place: no live trade since the flush at hello, so nothing else holds a newer one
                x.o, x.h, x.l, x.c, x.v, x.bv, x.sv, x.n = b.o, b.h, b.l, b.c, b.v, b.bv, b.sv, b.n
            elif s.last is None or s.last.t < last_live:
                s._seq += 1   # a live trade in this minute extends it, as after a restart (Market.seed)
                b.seq = s._seq
                s.bars.append(b)
        return len(bars)

    def _hole_minutes(self, sym: str, w: _Window) -> int:
        """A hole's bars: each minute with no bar from its trades (those a fill cut short stored before, then this
        fill's); the minute before it, stored already, merged with what came after its last trade."""
        db = self.m.store.db
        first = db.execute("SELECT MAX(t) FROM bars_1m WHERE symbol=? AND t<=?", (sym, w.start)).fetchone()[0]   # its bar before
        first = w.start // 60_000 * 60_000 if first is None else first
        last = int(w.cutoff) // 60_000 * 60_000
        stored = {r[0]: r[1:] for r in db.execute("SELECT t,o,h,l,c,v,bv,sv,n FROM bars_1m WHERE symbol=? AND t>=? AND t<=?",
                                                  (sym, first, last))}
        built: dict[int, list] = {}

        def add(k, o, h, lo, c, v, n):
            b = built.get(k)
            if b is None:
                built[k] = [o, h, lo, c, v, n]
            else:
                b[1], b[2], b[3], b[4], b[5] = max(b[1], h), min(b[2], lo), c, b[4] + v, b[5] + n
        for ts, px, size in db.execute("SELECT ts, price, size FROM ticks WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
                                       (sym, first, w.start)):   # stored before this fill, with no bar
            if ts // 60_000 * 60_000 not in stored:
                add(ts // 60_000 * 60_000, px, px, px, px, size, 1)
        for k, (o, h, lo, c, v, n) in sorted(w.minutes.items()):   # this fill's, all after w.start
            if k not in stored and k < last:
                add(k, o, h, lo, c, v, n)
        bars = [Bar(t=k, o=o, h=h, l=lo, c=c, v=v, n=n) for k, (o, h, lo, c, v, n) in built.items()]
        if first in stored and first in w.minutes:   # where stored data ended: it came first
            (so, sh, sl, _, sv_, sbv, ssv, sn), (o, h, lo, c, v, n) = stored[first], w.minutes[first]
            bars.append(Bar(t=first, o=so, h=max(sh, h), l=min(sl, lo), c=c, v=sv_ + v, bv=sbv, sv=ssv, n=sn + n))
        self.m.store.write([], [(sym, b) for b in bars])
        return len(bars)

    # --- older history: from before anything stored ----------------------------------------------------------
    def floor(self, sym: str) -> int | None:
        """The first stored minute of `sym`."""
        return self.m.store.db.execute("SELECT MIN(t) FROM bars_1m WHERE symbol=?", (sym,)).fetchone()[0]

    def limit(self, sym: str) -> int | None:
        """Where NinjaTrader's history of `sym` was found to end (nothing before it), unless that was a week ago."""
        got = self.m.store.get_setting(OLDER_LIMIT, {}) or {}
        v = got.get(sym) if isinstance(got, dict) else None
        if not (isinstance(v, list) and len(v) == 2):
            return None
        return int(v[0]) if time.time() * 1000 - int(v[1]) < OLDER_LIMIT_DAYS * DAY_MS else None

    def _set_limit(self, sym: str, ms: int):
        got = self.m.store.get_setting(OLDER_LIMIT, {}) or {}
        got = got if isinstance(got, dict) else {}
        self.m.store.set_setting(OLDER_LIMIT, {**got, sym: [int(ms), int(time.time() * 1000)]})

    def _ours(self, sym: str) -> bool:
        return sym in self.m.instruments and sym not in getattr(self.m, "trade_owner", {})

    def _prefill(self, sym: str, floor: int | None = None) -> bool:
        """Whether `sym`'s stored minutes still stop short of the year (prefill_days) filled in the background."""
        days = prefill_days()
        floor = self.floor(sym) if floor is None else floor
        if not days or floor is None or floor <= time.time() * 1000 - days * DAY_MS:
            return False
        lim = self.limit(sym)
        return lim is None or floor > lim

    def want_older(self, sym: str, span: int) -> str:
        """A chart scrolled back past `sym`'s stored minutes wants `span` ms more: 'fetching' (asked of NinjaTrader now,
        or after what is being asked), 'limit' (NinjaTrader has none older), 'wait' (it didn't answer: asked again in a
        few minutes) or 'off' (no add-on that answers, another feed's symbol, nothing stored)."""
        if not self.older_ok or not self._ours(sym):
            return "off"
        floor = self.floor(sym)
        if floor is None:
            return "off"   # nothing stored: the gap fill comes first
        lim = self.limit(sym)
        if lim is not None and floor <= lim:
            return "limit"
        self.older_want[sym] = max(self.older_want.get(sym, 0), int(span))   # (asked when it may be: then pages hear)
        w = self.windows.get(sym)
        if time.monotonic() < self.older_wait.get(sym, 0) or sym in self.nt_out or w is not None and (w.late or w.discard):
            return "wait"   # an error a while ago, or a request NinjaTrader hasn't answered: the page asks again on 'older'
        self.kick_older()
        return "fetching"

    def kick_older(self):
        """The next older request, sent now if none is out (also the watchdog's: one that went unanswered is dropped,
        studies due a reload reload)."""
        for sym in list(self.older_filled):
            self._older_studies(sym)
        line = self.next_older()
        if line is not None:
            feed = next((f for f in getattr(self.m, "feed_objs", []) if getattr(f, "name", "") == "ninjatrader"
                         and hasattr(f, "older_ok") and f.older_ok()), None)
            if feed is None or not feed.send(line):
                w = self.windows.get(self.older_busy[0]) if self.older_busy else None
                if w is not None and w.older:
                    del self.windows[self.older_busy[0]]
                self.older_busy = None

    def next_older(self) -> str | None:
        """The GAP line for the next older request, its window opened; None while one is out (until it times out),
        when every symbol is busy with its gap or holes, or none wants any. Charts scrolled back first, then the
        background year, the symbols charted last first."""
        if not self.older_ok:
            return None
        if self.older_busy is not None:
            sym, t0 = self.older_busy
            w = self.windows.get(sym)
            if w is not None and w.older and not w.late and time.monotonic() - t0 < OLDER_TIMEOUT_S:
                return None
            if w is not None and w.older and not w.late:
                # no answer yet (NinjaTrader's data connection down, a slow download): the other symbols go on. Its
                # window stays until its Z (or the add-on reconnects), so nothing else is asked for it meanwhile (the
                # add-on answers each request on a thread of its own: two at once for a symbol would mix), and its answer
                # counts as any: kept, or an error that puts it off. No answer yet isn't NinjaTrader's limit
                w.late = True
                self.status[sym] = {**self.status.get(sym, {}), "state": "late", "note": "no answer from NinjaTrader yet"}
                log.warning("older history %s: no answer from NinjaTrader yet", sym)
            self.older_busy = None
        recent = [c[0] for c in getattr(self.m, "recent", {}).get("charts", []) if c]
        order = list(self.older_want) + [s for s in dict.fromkeys(recent + list(self.m.instruments)) if s not in self.older_want]
        now = time.monotonic()
        for sym in order:
            if not self._ours(sym):
                self.older_want.pop(sym, None)
                continue
            if sym in self.windows or sym in self.nt_out or self.queue.get(sym) or self.values.get(sym) \
                    or now < self.older_wait.get(sym, 0):
                continue
            floor = self.floor(sym)
            if floor is None:
                continue
            lim = self.limit(sym)
            if lim is not None and floor <= lim:
                self.older_want.pop(sym, None)
                continue
            wanted, span = sym in self.older_want, self.older_want.pop(sym, 0)
            if not wanted and not self._prefill(sym, floor):
                continue
            span = max(OLDER_SPAN_MS, min(OLDER_MAX_SPAN_MS, span))
            end = floor // 60_000 * 60_000
            w = self.windows[sym] = _Window(end - span, end)
            w.older = True
            self.status[sym] = {"from": end - span, "to": end, "state": "asked", "older": True}
            self.older_busy = (sym, now)
            return f"GAP|{sym}|{end - span}|{end}|older"
        return None

    def _older_done(self, sym: str, w: _Window, kind: str, count: int, note: str):
        if self.older_busy is not None and self.older_busy[0] == sym:
            self.older_busy = None
        if kind in ("error", "none"):   # cut short: none of it kept (the first stored minute would hide what's missing)
            w.rows, w.count = [], 0
            self._older_failed(sym)
            state = "error"
        elif not w.count:   # nothing before its end: where the data provider's history ends
            self._set_limit(sym, w.end)
            state = "limit"
        else:
            self._write_rows(sym, w)
            state = "done"
            self.older_failed.pop(sym, None)
            if w.end > time.time() * 1000 - STUDY_DAYS * DAY_MS:
                self.older_filled.add(sym)
        took = time.monotonic() - w.t0
        self.status[sym] = {"from": w.start, "to": w.end, "state": state, "kind": kind, "sent": count, "stored": w.count,
                            "note": note, "seconds": round(took, 1), "older": True}
        log.info("older history %s: %s, %d sent, %d stored (%s) in %.1fs", sym, state, count, w.count, note, took)
        self._older_studies(sym)
        self._told(sym)

    def gave_up(self, sym: str):
        """NinjaTrader's request for `sym` given up on while the add-on still answers it (an AMT timeout, a plugin taking
        the symbol): its window goes (a plugin's own can open), its lines are dropped till its Z (nt_out), and nothing
        else is asked of NinjaTrader for it meanwhile: the add-on's lines name the symbol only."""
        w = self.windows.pop(sym, None)
        if w is None:
            return
        self.nt_out.add(sym)
        if w.future is not None and not w.future.done():
            w.future.set_result(None)
        if self.older_busy is not None and self.older_busy[0] == sym:
            self.older_busy = None

    def owed_done(self, sym: str) -> str | None:
        """The Z of a request given up on: what to ask of NinjaTrader for `sym` now (its stretch, if it was handed back
        meanwhile; its holes; the AMT's sessions), if it is NinjaTrader's again."""
        self.nt_out.discard(sym)
        w = self.windows.get(sym)
        ours = sym not in getattr(self.m, "trade_owner", {})
        if w is not None and w.hold:   # holding its alerts till now
            if ours:   # handed back meanwhile: its stretch asked in this window, from where the plugin's data stopped to
                # NinjaTrader's first live trade (worked out again from what is stored, it would start at those live
                # trades: nothing asked, the alerts let go against bars hours old); its alerts held till the answer
                w.hold = w.discard = False
                w.t0 = time.monotonic()
                self.reopen.discard(sym)
                self._told(sym)
                return f"GAP|{sym}|{w.start}|{int(min(w.cutoff, time.time() * 1000))}" + ("|live" if self.live_asks else "")
            del self.windows[sym]
        self._told(sym)
        return self.next(sym) if ours else None

    def _told(self, sym: str):
        if hasattr(self.m, "broadcast"):   # a chart waiting at its first bar (or told to wait) asks again now
            self.m.broadcast({"type": "older", "symbol": sym})

    def _older_failed(self, sym: str):
        """An older request failed (an error answer): asked again in OLDER_RETRY_S, twice that after the next in a row,
        and so on up to OLDER_RETRY_MAX_S, or at the add-on's next hello. Never taken as where NinjaTrader's history
        ends: an error says nothing of that (NinjaTrader answered every request with Panic until it was restarted;
        three errors in a row stopped its older history for a week)."""
        n = self.older_failed[sym] = self.older_failed.get(sym, 0) + 1
        wait = min(OLDER_RETRY_S * 2 ** min(n - 1, 16), OLDER_RETRY_MAX_S)
        self.older_wait[sym] = time.monotonic() + wait
        log.warning("older history %s: %d requests in a row failed; asked again in %d minutes", sym, n, wait // 60)

    def _older_studies(self, sym: str):
        """`sym`'s studies built from its last months (AMT, Daily SMA x4, TPO's days...) load again with the older
        minutes, once none is being asked for it (its year is in, a chart's scrolling done), at most every
        STUDY_RELOAD_S; not its charts."""
        if sym not in self.older_filled or sym in self.older_want or self._prefill(sym) \
                or (self.older_busy is not None and self.older_busy[0] == sym) \
                or time.monotonic() - self.older_reloaded.get(sym, -math.inf) < STUDY_RELOAD_S:
            return
        self.older_filled.discard(sym)
        self.older_reloaded[sym] = time.monotonic()
        self.m.history_changed(sym, charts=False)

    def health(self) -> dict:
        return self.status
