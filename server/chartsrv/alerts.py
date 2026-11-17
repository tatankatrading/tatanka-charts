"""Server-side alert evaluation, run on every live tick.

price: TradingView's conditions on `price`: cross, above (crossing up), below (crossing down), gt and lt (greater
       or less than: true while price is past it), enter and exit (a channel `price`..`price2`). One-shot, or with
       repeat at most once a minute (Once every N minutes: N, below). An alert created while the last print sits
       exactly on its price fires on the first move off it. Any alert with params.expires (epoch ms) switches itself
       off then (expire: with no trade too). params.free: a stock's, a series' or a formula's (free_alerts.py),
       checked on its own values alone.
line:  the same for a drawn line, ray or trend line (drawings.line_price), whose level can move with market time (or
       as the chart its alert was switched on from lays the line out: drawings.LAYOUT).
ind:   an indicator series crossing, or above or below, a value or another series (ind_alerts), on bar close or
       intrabar once a bar.
study: fires when price trades within `tolerance_ticks` of a study level (session or RTH VWAP and its sigma
       bands, a session profile's POC, VAH or VAL), re-armed like level alerts. The level comes from `studies`.
level: fires when price trades within `tolerance_ticks` of one of the symbol's daily levels (levels.py: the Daily
       levels study's). Each level then stays quiet until price moves `rearm_ticks` beyond that band, so chop around a
       level fires once, not on every print. New levels (reset_levels) keep the state of each one still at its price.
ifthen: the symbol's If/Then plan (the block pasted on the Plan tab, ifthen.py), each row by itself: a line (one price,
       or a band open at one end) fires on a touch within `tolerance_ticks`, as a level; a zone (a bottom and a top)
       fires when price comes into it from outside. Each re-arms once price is `rearm_ticks` outside it. params.rows:
       "all", or the rows chosen, each named by its [LO, HI] as written, so the choice holds through a new paste. The
       block is read from the store once and kept (ifthen_changed: a new paste). A row a new paste keeps (its LO and
       HI as written) keeps its state, so a resave never re-arms one that just fired; a new row takes a first look at
       price, as at a start, so levels moved under price never fire.

How often, any kind: Only once (repeat False: it switches itself off when it fires), Every time (repeat), or Once every
N minutes (repeat, and params.every_min N, 1 to 1440). Price and line alerts take N in place of Every time's minute,
counted from the alert's last fire (stored: a restart keeps it); a cross meanwhile is dropped, not sent later. Indicator
alerts take it as a gap on top of once a bar, from the same. Moved, its condition changed, or turned back on, a price,
line or indicator alert starts afresh, wait included: its last fire before that is no wait (AFRESH). Level, study and
If/Then alerts wait per level or row (`waits`): one that fired stays quiet for N minutes AND until it has re-armed as
without it, while the alert's other levels or rows fire as usual. A touch during the wait sends nothing and is held
(HELD): once the wait is over a line still touched fires on its next trade there, a zone only on price coming into it
again, and one price has left meanwhile on its next touch. A level is known by its price (so a renamed one, or one gone
for a poll and back, still waits; two names at one price fire as one), a row by its LO and HI as written (through a new
paste), and an edit of the alert keeps their waits: none of them moved. Turned back on (a pause, a fired Only once
restarted), the alert has none. The waits are kept in the store's settings (WAITS), so a restart keeps them too. A
contract roll starts the symbol's alerts afresh, their waits of N minutes included: the new contract at the same number
is another place (Every time's minute holds as it did).
Changing how often an alert fires, or how far it re-arms (STEADY_PARAMS), is no new condition: the alert carries on as
it was, a level that just fired still quiet. A fire stamped after a print holds it when no more than AHEAD_MS ahead."""
import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Callable

from . import drawings, ifthen, study_alerts
from .config import Instrument
from .free_alerts import free_fmt
from .levels import Levels, LevelSet
from .models import Tick
from .store import Store

log = logging.getLogger(__name__)
REPEAT_COOLDOWN_MS = 60_000   # Every time, for price and line alerts: at most once a minute
EVERY_MIN_MAX = drawings.EVERY_MIN_MAX   # Once every N minutes: N from 1 to 1440, a day
WAITS = "alert_waits"         # the store's setting: [[wait key, when it fired], ...] (AlertEngine.waits)
# params an edit changes without the alert starting afresh (reload's steady): how often it fires, how far price goes to
# re-arm it. A level that just fired stays quiet, an armed one stays armed
STEADY_PARAMS = ("every_min", "rearm_ticks")
# a wait's key (alert id, AFRESH): the last fire of a price, line or indicator alert that has started afresh since
# (moved, edited, turned back on), which its wait no longer runs from
AFRESH = "afresh"
# a level's, study's or If/Then row's state (AlertEngine.armed) after a touch its wait held back: armed, price on it
HELD = "held"
# a last fire (or a wait's start) stamped after a print still holds it when no more than this far ahead: prints a few ms
# out of order, a formula's quote stamped by the server's clock behind a future's trade time (Every time's minute held
# those before there were waits). Further ahead (a clock stepped back, a bad tick time) it holds nothing: it would hold
# for as long as it is ahead, a day for a print stamped a day on
AHEAD_MS = 60_000


def every_ms(a: "Alert") -> int:
    """The alert's Once every N minutes in ms, or 0 (Only once, Every time)."""
    n = a.params.get("every_min")
    return n * 60_000 if a.repeat and type(n) is int and 0 < n <= EVERY_MIN_MAX else 0


def within(ts: int, t: int, w: int) -> bool:
    """Whether a print at ts is within the wait of w ms from a fire at t (AHEAD_MS: one stamped a little after it)."""
    return -AHEAD_MS < ts - t < w


def core_params(params: dict) -> dict:
    """An alert's params but those an edit changes without it starting afresh (STEADY_PARAMS)."""
    return {k: v for k, v in params.items() if k not in STEADY_PARAMS}


@dataclass
class Alert:
    id: int
    symbol: str
    kind: str
    params: dict
    enabled: bool
    repeat: bool
    note: str
    created: int
    last_fired: int | None

    def to_dict(self):
        return dict(self.__dict__)


class AlertEngine:
    def __init__(self, store: Store, instruments: dict[str, Instrument], levels: Levels | None,
                 on_fire: Callable[[dict], None]):
        self.store = store
        self.instruments = instruments
        self.levels = levels
        self.on_fire = on_fire
        self.alerts: dict[int, Alert] = {}
        self.last_px: dict[str, float] = {}
        self.last_ts: dict[str, int] = {}     # ...and its time: where a sloped line was then
        self.free_px: dict[str, float] = {}   # a free alert symbol's last value (free_alerts): apart from any trade's
        self.armed: dict[tuple[int, str], bool | str] = {}   # True, False, or HELD (armed: a touch held back)
        self.seen: set[int] = set()        # price and line alerts evaluated at least once since (re)enabled
        self.at_price: set[int] = set()    # ...whose first look found the last print exactly on their price
        self.studies: Callable[[str, dict, int], tuple[float, int] | None] | None = None   # study alert levels (market)
        self.study_session: dict[int, int] = {}   # study alerts: the session their armed state belongs to
        self.indicators: Callable[[Alert, Tick], str | None] | None = None    # indicator alerts (ind_alerts.check)
        self.on_change: Callable[[], None] | None = None   # an alert switched itself off (expired)
        self.ifthen_plans: dict[str, list[tuple]] | None = None   # symbol -> its If/Then rows (ifthen.alert_rows)
        self.ifthen_watch: dict[int, tuple] = {}   # alert id -> (plan rows, params, its bands): worked out once per plan
        self.live: set[str] = set()        # symbols whose last_px is a live trade's (not the stored one a start seeds)
        # level, study and If/Then alerts: when each level (alert id, "level", its price on the tick grid), study
        # (alert id, "study") or row (alert id, "ifthen", LO, HI as written, which of those) last fired, for Once every
        # N minutes. Kept through new levels, a new paste, an edit and a restart (WAITS); gone with the alert, when it
        # is turned back on (reload) or its contract rolls (rolled), or a day on. And (alert id, AFRESH): a price, line
        # or indicator alert's last fire that its wait no longer runs from, kept while it is that alert's last fire
        self.waits: dict[tuple, int] = self._load_waits()
        self.hit_now: set[tuple] = set()   # the waits started on the trade being checked (on_tick): _hit
        self.reload()

    def reload(self) -> set[int]:
        """The alerts as stored. Returns the ids of those on that start afresh: new, edited, or back on (after a pause,
        a one-shot's firing or an expiration, each switched off here first)."""
        old = self.alerts
        self.alerts = {r["id"]: Alert(**r) for r in self.store.list_alerts()}
        self.ifthen_watch = {}
        # turned back on or edited, an alert starts afresh with a first look: nothing kept from while it was off (how
        # often it fires and how far it re-arms are no new condition: changed alone, it carries on as it was)
        steady = {i for i, a in self.alerts.items()
                  if a.enabled and i in old and old[i].enabled and core_params(old[i].params) == core_params(a.params)}
        self.armed = {k: v for k, v in self.armed.items() if k[0] in steady}
        self.study_session = {k: v for k, v in self.study_session.items() if k in steady}
        self.seen &= steady
        self.at_price &= steady
        started = {i for i, a in self.alerts.items() if a.enabled and i not in steady}
        # ...its wait too, if it has one of its own: a moved price alert (a drawn line) is a new price, and one turned
        # back on is new as a whole. A level's or row's wait (by its price) holds through an edit, as none moved. A
        # start's alerts are new to it, not edited: their waits come as saved
        afresh = {i for i in started
                  if i in old and (not old[i].enabled or self.alerts[i].kind in ("price", "line", "ind"))}
        self.waits = {k: v for k, v in self.waits.items() if k[0] in self.alerts}
        self._afresh(afresh)
        return started

    def _afresh(self, ids: set[int], marked: set[int] | None = None):
        """The alerts `ids` have no wait from before: their levels', study's or rows' waits go, and the last fire of
        those `marked` (all of them unless given) is no wait (AFRESH), as a price, line or indicator alert waits from
        it. Saved for a restart when that changes anything."""
        waits = {k: v for k, v in self.waits.items() if k[0] not in ids or self._afresh_now(k, v)}
        waits.update({(i, AFRESH): t for i in (ids if marked is None else marked)
                      if (t := self.alerts[i].last_fired) is not None})
        if waits != self.waits:
            self.waits = waits
            self._save_waits()

    def list(self) -> list[dict]:
        return [a.to_dict() for a in self.alerts.values()]

    def reset_levels(self, symbol: str, old: LevelSet | None = None, new: LevelSet | None = None):
        """New daily levels for `symbol` (Market.levels_changed: a paste, the levels file or link), old and new its
        sets. A level an alert watches that is still at a price it watched (by any name: R1 is R2 once a level is put
        in below it) keeps its state, so a new paste never re-arms one that just fired: it stays quiet until price has
        been rearm_ticks beyond it. A new or moved level takes a first look at the next trade, as at a start, so one
        that landed on price never fires for that."""
        mine = [a for a in self.alerts.values() if a.symbol == symbol and a.kind == "level"]
        inst = self.instruments.get(symbol)
        eps = inst.tick_size * 0.01 if inst else 0.0   # CL/GC levels carry float32 noise
        kept = {}
        for a in mine:
            was = [(p, self.armed[(a.id, n)]) for n, p in (old.lines() if old else ()) if (a.id, n) in self.armed]
            if not (was and new):
                continue
            wanted = a.params.get("levels", "all")
            for n, p in new.lines():
                st = next((s for q, s in was if abs(p - q) <= eps), None)
                if st is not None and (wanted == "all" or n in wanted):
                    kept[(a.id, n)] = st
        ids = {a.id for a in mine}
        self.armed = {k: v for k, v in self.armed.items() if k[0] not in ids}
        self.armed.update(kept)

    def rolled(self, symbol: str, free: bool = False):
        """The feed now sends another contract for `symbol` (market.on_contract): prices jump by the spread between the
        two, through no line or level between. Its alerts start afresh, as at a first start: the new contract's first
        trade is only where they look from. Their waits too (Once every N minutes): a level or row is known by its
        price, and the new contract at a number the old one touched within the wait is another place (50 points away
        on ES), as is a price alert's. Every time's minute holds as it did (no wait of N minutes: one alert a minute
        early at most). free: a free alert symbol's (free_alerts.rolled), not a trade's."""
        if free:
            self.free_px.pop(symbol, None)
        else:
            self.last_px.pop(symbol, None)
            self.last_ts.pop(symbol, None)
            self.live.discard(symbol)
        ids = {i for i, a in self.alerts.items() if a.symbol == symbol and bool(a.params.get("free")) == free}
        self.armed = {k: v for k, v in self.armed.items() if k[0] not in ids}
        self.study_session = {k: v for k, v in self.study_session.items() if k not in ids}
        self.seen -= ids
        self.at_price -= ids
        self._afresh(ids, {i for i in ids if every_ms(self.alerts[i])})

    def on_tick(self, tk: Tick, free: bool = False):
        """A live future's trade; free: a free alert symbol's value (free_alerts.moved), which only its free alerts
        see, with a previous value of its own: a stock's alert never sees the trades of a future with its ticker for a
        root (MetLife's MET, Micro Ether's MET), nor that future's alerts the stock's quotes."""
        if free:
            prev, prev_ts = self.free_px.get(tk.symbol), tk.ts
            self.free_px[tk.symbol] = tk.price
        else:
            prev, prev_ts = self.last_px.get(tk.symbol), self.last_ts.get(tk.symbol, tk.ts)
            self.last_px[tk.symbol] = tk.price
            self.last_ts[tk.symbol] = tk.ts
            self.live.add(tk.symbol)
        self.hit_now = set()
        for a in list(self.alerts.values()):
            if a.symbol != tk.symbol or not a.enabled or bool(a.params.get("free")) != free:
                continue
            exp = a.params.get("expires")
            if exp and tk.ts >= exp:
                self._expire(a)
                if self.on_change:
                    self.on_change()
                continue
            # Every time: at most once a minute; Once every N minutes: N. A cross meanwhile is dropped, not sent later
            if a.kind in ("price", "line") and a.repeat and self._cooling(a, tk.ts, every_ms(a) or REPEAT_COOLDOWN_MS):
                continue
            try:
                if a.kind == "price":
                    self._check_price(a, prev, tk)
                elif a.kind == "level":
                    self._check_levels(a, prev, tk)
                elif a.kind == "line":
                    self._check_line(a, prev, prev_ts, tk)
                elif a.kind == "study":
                    self._check_study(a, prev, tk)
                elif a.kind == "ifthen":
                    self._check_ifthen(a, prev, tk)
                elif a.kind == "ind" and self.indicators:
                    msg = self.indicators(a, tk)
                    if msg and not self._ind_waits(a, tk):
                        self._fire(a, tk, msg)
            except Exception:  # one bad alert must not stop the others
                log.exception("alert %s failed", a.id)

    def _cooling(self, a: Alert, ts: int, w: int) -> bool:
        """A price, line or indicator alert within `w` ms of its last fire: one before it started afresh (AFRESH) is
        none, nor one stamped more than AHEAD_MS after ts (a clock stepped back, a bad tick time)."""
        t = a.last_fired
        return bool(w) and t is not None and self.waits.get((a.id, AFRESH)) != t and within(ts, t, w)

    def _ind_waits(self, a: Alert, tk: Tick) -> bool:
        """An indicator alert's Once every N minutes: a bar's crossing within N minutes of its last fire sends nothing
        (its bar counts as fired all the same, so it is not sent later in that bar either)."""
        return self._cooling(a, tk.ts, every_ms(a))

    def _hit(self, a: Alert, key: tuple, wait: tuple, prev: float | None, px: float, lo: float, hi: float, eps: float,
             ts: int, zone: bool = False) -> bool:
        """Whether an armed level, study or If/Then row fires on this trade (`key`: its state's, `wait`: its wait's): the
        stretch between the last two prints reaches lo..hi. Within its wait (Once every N minutes) a touch sends nothing
        and is held (HELD: still armed). Once the wait is over, a line that price is still on fires on its next trade
        there; a zone that price has stayed in fires only when price comes into it again (price did not just enter it);
        one that price has left fires on its next touch. Another name at the price of a level that fired on this same
        trade (P and S1 both at 27425: hit_now) goes quiet with it, to re-arm with it: one alert. A touch on another
        trade is held, even one stamped with the same ms (a sweep's prints: re-armed and back in that ms)."""
        s_lo, s_hi = (px, px) if prev is None else (min(prev, px), max(prev, px))
        if self.armed.get(key) == HELD and prev is not None and lo - eps <= prev <= hi + eps:   # on it since then
            if not lo - eps <= px <= hi + eps:
                self.armed[key] = True   # it has left: the next touch is a new one
                return False
            if zone:
                return False
        elif not (s_lo <= hi + eps and s_hi >= lo - eps):
            return False
        w = every_ms(a)
        t = self.waits.get(wait) if w else None
        if t is not None and within(ts, t, w):   # (one stamped far after ts would hold as long: no wait)
            self.armed[key] = False if wait in self.hit_now else HELD
            return False
        self.armed[key] = False
        self._waited(wait, ts)
        self.hit_now.add(wait)
        return True

    def _waited(self, key: tuple, ts: int):
        """`key` fires at ts: its wait starts (noted whatever the alert's trigger, so one changed to wait right after a
        burst waits from its last fire), saved for a restart. Those older than the longest wait go, but an AFRESH that
        is still its alert's last fire: a print stamped a day or more ahead (a clock set wrong for a moment) would
        otherwise drop it, and the moved alert wait again from its old fire."""
        cut = ts - EVERY_MIN_MAX * 60_000
        self.waits = {k: t for k, t in self.waits.items() if t > cut or self._afresh_now(k, t)}
        self.waits[key] = ts
        self._save_waits()

    def _afresh_now(self, k: tuple, t: int) -> bool:
        """Whether the wait (k, t) is an AFRESH still in force: its alert's last fire is the one it names."""
        a = self.alerts.get(k[0])
        return len(k) == 2 and k[1] == AFRESH and a is not None and a.last_fired == t

    def _save_waits(self):
        try:
            self.store.set_setting(WAITS, [[list(k), t] for k, t in self.waits.items()])
        except sqlite3.Error as e:   # not written (a full disk): kept here all the same
            log.warning("alert waits not saved: %s", e)

    def _load_waits(self) -> dict[tuple, int]:
        """The waits as saved (_waited), each a key (an alert id, then names and numbers) and a time; anything else in
        there is left out (a key holding a list would not even hash: the engine, and the server, would not start)."""
        try:
            got = self.store.get_setting(WAITS, []) or []
        except (sqlite3.Error, ValueError) as e:
            log.warning("alert waits not read: %s", e)
            return {}
        out = {}
        for item in got if isinstance(got, list) else ():
            if (isinstance(item, list) and len(item) == 2 and isinstance(item[0], list) and item[0]
                    and type(item[0][0]) is int and all(type(v) in (int, float, str) for v in item[0][1:])
                    and type(item[1]) is int):
                out[tuple(item[0])] = item[1]
        return out

    def expire(self, now: int):
        """Switches off each alert whose expiration has passed, with no trade or value to check it on (market's
        housekeeping, once a minute): a series' alert (a new value days or weeks apart), a future's over a weekend."""
        gone = [a for a in list(self.alerts.values()) if a.enabled and (exp := a.params.get("expires")) and now >= exp]
        for a in gone:
            self._expire(a)
        if gone and self.on_change:
            self.on_change()

    def _expire(self, a: Alert):
        a.enabled = False
        try:
            self.store.update_alert(a.id, enabled=False)
        except sqlite3.Error as e:   # not written (a full disk): off all the same, and the trades go on
            log.warning("alert %s expired, not saved: %s", a.id, e)

    def on_shut(self, tk: Tick):
        """The market is shut (16:00-17:00 CT, a weekend, a holiday): no trade will close the session's last bar, so
        bar-close indicator alerts check it now. tk: this moment, at the last trade's price."""
        for a in list(self.alerts.values()):
            if a.symbol != tk.symbol or not a.enabled or a.kind != "ind" or a.params.get("on") != "close" or not self.indicators:
                continue
            exp = a.params.get("expires")
            if exp and tk.ts >= exp:   # switched off on the next trade
                continue
            try:
                msg = self.indicators(a, tk)
                if msg and not self._ind_waits(a, tk):
                    self._fire(a, tk, msg)
            except Exception:  # one bad alert must not stop the others
                log.exception("alert %s failed", a.id)

    def _check_price(self, a: Alert, prev: float | None, tk: Tick):
        p, d, px = a.params["price"], a.params.get("direction", "cross"), tk.price
        # gt and lt are true while price is past it: they need no previous price (a stock's first quote, a series'
        # value that stays the same for weeks)
        if d in ("gt", "lt"):
            if px > p if d == "gt" else px < p:
                self._fire(a, tk, f"{a.symbol} {'above' if d == 'gt' else 'below'} {self._fmt(a, p)}")
            return
        if prev is None:
            return
        if d in ("enter", "exit"):
            lo, hi = p, a.params["price2"]
            was, now = lo <= prev <= hi, lo <= px <= hi
            if (d == "enter" and now and not was) or (d == "exit" and was and not now):
                self._fire(a, tk, f"{a.symbol} {'entered' if d == 'enter' else 'left'} {self._fmt(a, lo)}–{self._fmt(a, hi)}")
            return
        if a.id not in self.seen:
            self.seen.add(a.id)
            if prev == p:
                self.at_price.add(a.id)
        if a.id in self.at_price:          # created on the price: the first move off it is the cross
            if px == p:
                return
            self.at_price.discard(a.id)
            up, down = px > p, px < p
        else:
            up, down = prev < p <= px, prev > p >= px
        if (up and d in ("cross", "above")) or (down and d in ("cross", "below")):
            word = "crossed above" if up else "crossed below"
            self._fire(a, tk, f"{a.symbol} {word} {self._fmt(a, p)}")

    def _check_line(self, a: Alert, prev: float | None, prev_ts: int, tk: Tick):
        if prev is None:
            return
        group = self.instruments[a.symbol].calendar   # its holidays: the hours its bars, and so the line, skip
        lvl = drawings.line_price(a.params, tk.ts, group)
        if lvl is None:
            return
        # each print against the line's level at its own time, so a sloped line moving through price crosses too
        then = drawings.line_price(a.params, prev_ts, group)
        was, now = prev - (lvl if then is None else then), tk.price - lvl
        d = a.params.get("direction", "cross")
        if a.id not in self.seen:
            self.seen.add(a.id)
            if was == 0:
                self.at_price.add(a.id)
        if a.id in self.at_price:          # set on the last print: the first move off the line is the cross
            if now == 0:
                return
            self.at_price.discard(a.id)
            up, down = now > 0, now < 0
        else:
            up, down = was < 0 <= now, was > 0 >= now
        if (up and d in ("cross", "above")) or (down and d in ("cross", "below")):
            what = {"hline": "line", "hray": "ray", "trend": "trend line"}.get(a.params.get("type"), "line")
            self._fire(a, tk, f"{a.symbol} crossed {'above' if up else 'below'} {what} {self._fmt(a, lvl)}")

    def _check_study(self, a: Alert, prev: float | None, tk: Tick):
        got = self.studies(a.symbol, a.params, tk.ts) if self.studies else None
        if got is None:
            return
        lvl, session = got
        inst = self.instruments[a.symbol]
        tol = a.params.get("tolerance_ticks", 0) * inst.tick_size
        rearm = a.params.get("rearm_ticks", 8) * inst.tick_size
        eps = inst.tick_size * 0.01
        key, px = (a.id, "study"), tk.price
        armed = self.armed.get(key)
        if armed is None or self.study_session.get(a.id) != session:
            # first look: never fire on startup, nor at a session's open, where the level starts over at the price
            self.study_session[a.id] = session
            self.armed[key] = abs(px - lvl) > tol + eps
            return
        if armed:
            # a touch within its wait (Once every N minutes) sends nothing: it stays armed (_hit)
            if self._hit(a, key, (a.id, "study"), prev, px, lvl - tol, lvl + tol, eps, tk.ts):
                self._fire(a, tk, f"{a.symbol} touched {study_alerts.describe(a.params)} {self._fmt(a, lvl)}")
        elif abs(px - lvl) >= tol + rearm - eps:    # re-arm measured from the band edge
            self.armed[key] = True

    def _check_levels(self, a: Alert, prev: float | None, tk: Tick):
        ls = self.levels.get(a.symbol) if self.levels else None
        if not ls:
            return
        inst = self.instruments[a.symbol]
        tol = a.params.get("tolerance_ticks", 0) * inst.tick_size
        rearm = a.params.get("rearm_ticks", 8) * inst.tick_size
        wanted = a.params.get("levels", "all")
        eps = inst.tick_size * 0.01        # CL/GC levels carry float32 noise (93.83999633789062)
        px = tk.price
        for name, lvl in ls.lines():
            if wanted != "all" and name not in wanted:
                continue
            key = (a.id, name)
            dist = abs(px - lvl)
            armed = self.armed.get(key)
            if armed is None:                       # first look: never fire on startup
                self.armed[key] = dist > tol + eps
                continue
            if armed:
                wait = (a.id, "level", round(lvl / inst.tick_size))   # by its price: renamed or republished, the same
                if self._hit(a, key, wait, prev, px, lvl - tol, lvl + tol, eps, tk.ts):
                    self._fire(a, tk, f"{a.symbol} touched {name} {self._fmt(a, lvl)}")
                    if not a.enabled:
                        return
            elif dist >= tol + rearm - eps:   # re-arm measured from the band edge
                self.armed[key] = True

    # ---- If/Then plan alerts -----------------------------------------------------------------------------------
    def ifthen_rows(self, symbol: str) -> "list[tuple]":   # (quoted: list is the method above here)
        """The symbol's If/Then rows as stored, read once (every symbol's at once) and kept until a new paste."""
        if self.ifthen_plans is None:
            blocks = self.store.get_setting("ifthen", {}) or {}
            self.ifthen_plans = {s: ifthen.alert_rows(b.get("rows", "")) for s, b in blocks.items() if isinstance(b, dict)}
        return self.ifthen_plans.get(symbol, [])

    def ifthen_changed(self, symbol: str, block: str | None = None):
        """A new If/Then block for `symbol` (pasted on the Plan tab or in the indicator's settings; empty: deleted).
        Its alerts follow it. A row still in the plan (its LO and HI as written; its text may change) keeps its state,
        so saving the plan again never re-arms a row that just fired: it stays quiet until price has been rearm_ticks
        away. A new row takes a first look at the current price, so a level that moved under price never fires for
        that: one outside its band is armed, one at it waits until price has moved rearm_ticks away. Before a live
        trade since the start or a roll, the next trade takes that look. block None: read it again."""
        if block is None:
            block = ((self.store.get_setting("ifthen", {}) or {}).get(symbol) or {}).get("rows", "")
        old_rows, new_rows = self.ifthen_rows(symbol), ifthen.alert_rows(block)
        mine = [a for a in self.alerts.values() if a.symbol == symbol and a.kind == "ifthen"]
        ids = {a.id for a in mine}
        kept = {}   # a row still in the plan keeps its state: one that just fired stays quiet
        for a in mine:
            for key, _, _, name in self._ifthen_named(a, old_rows):
                if self.armed.get(key) is not None:
                    kept[name] = self.armed[key]
        if self.ifthen_plans is not None:
            self.ifthen_plans[symbol] = new_rows
        self.armed = {k: v for k, v in self.armed.items() if k[0] not in ids}
        px = self.last_px.get(symbol) if symbol in self.live else None
        rows = self.ifthen_rows(symbol)
        for a in mine:
            if a.enabled:
                for key, (lo, hi), _, name in self._ifthen_named(a, rows):
                    if name in kept:
                        self.armed[key] = kept[name]
                    elif px is not None:
                        self.armed[key] = self._outside(px, lo, hi) > self.instruments[symbol].tick_size * 0.01

    def _ifthen_named(self, a: Alert, rows: "list[tuple]"):
        """_ifthen_bands, each with what names its row through a new paste: its LO and HI as written (as a pick names
        it; U|7450|9999999 and D|0|7450 are both a line at 7450, two rows), and which of the rows written so it is (the
        second of two the same is the second again)."""
        seen: dict[tuple, int] = {}
        for key, band, r in self._ifthen_bands(a, rows):
            n = seen[r[3:5]] = seen.get(r[3:5], -1) + 1
            yield key, band, r, (a.id, r[3], r[4], n)

    def _ifthen_bands(self, a: Alert, rows: "list[tuple]"):
        """(state key, band low, band high, row) for each row the alert watches: a line's band is its price within
        tolerance_ticks, a zone's the zone itself. A row picked is one whose LO and HI are each within a hundredth of
        a tick of the pick's, looked up by that grid (each pick filed once) rather than tried against every pick."""
        inst = self.instruments[a.symbol]
        tol = a.params.get("tolerance_ticks", 0) * inst.tick_size
        wanted = a.params.get("rows", "all")
        eps = inst.tick_size * 0.01

        def cell(v: float):   # v's place on that grid; a huge open end (1e307: no top) past a float's range stays inf
            q = v / eps
            return round(q) if math.isfinite(q) else q

        pick: dict | None = None
        if wanted != "all":
            pick = {}
            for w in wanted:
                pick.setdefault((cell(w[0]), cell(w[1])), []).append(w)
        for i, r in enumerate(rows):
            kind, lo, hi, low, high, _ = r
            if pick is not None:
                c_lo, c_hi = cell(low), cell(high)
                if not any(abs(low - w[0]) <= eps and abs(high - w[1]) <= eps
                           for d_lo in (-1, 0, 1) for d_hi in (-1, 0, 1) for w in pick.get((c_lo + d_lo, c_hi + d_hi), ())):
                    continue
            yield (a.id, f"ifthen:{i}"), (lo - tol, hi + tol) if kind == "line" else (lo, hi), r

    def _ifthen_watched(self, a: Alert, rows: "list[tuple]") -> list:
        """_ifthen_named, worked out once for this plan and this alert as it is (a paste or an edit works it out again),
        not on every trade."""
        got = self.ifthen_watch.get(a.id)
        if got is None or got[0] is not rows or got[1] is not a.params:
            got = self.ifthen_watch[a.id] = (rows, a.params, list(self._ifthen_named(a, rows)))
        return got[2]

    @staticmethod
    def _outside(px: float, lo: float, hi: float) -> float:
        """How far price is outside a band (0 inside it)."""
        return max(lo - px, px - hi, 0.0)

    def _check_ifthen(self, a: Alert, prev: float | None, tk: Tick):
        rows = self.ifthen_rows(a.symbol)
        if not rows:
            return
        inst = self.instruments[a.symbol]
        rearm = a.params.get("rearm_ticks", 8) * inst.tick_size
        eps = inst.tick_size * 0.01
        px = tk.price
        for key, (lo, hi), (kind, p_lo, p_hi, _, _, text), name in self._ifthen_watched(a, rows):
            out = self._outside(px, lo, hi)
            armed = self.armed.get(key)
            if armed is None:                       # first look (a start, a new plan, a roll): never fires
                self.armed[key] = out > eps
                continue
            if armed:
                wait = (a.id, "ifthen", *name[1:])   # the row by its LO and HI as written: through a new paste
                # into the band, or through it between two prints (within the row's wait: it stays armed)
                if self._hit(a, key, wait, prev, px, lo, hi, eps, tk.ts, zone=kind != "line"):
                    what = f"touched {self._fmt(a, p_lo)}" if kind == "line" \
                        else f"entered {self._fmt(a, p_lo)}–{self._fmt(a, p_hi)}"
                    self._fire(a, tk, f"{a.symbol} {what}{f': {text}' if text else ''}")
                    if not a.enabled:
                        return
            elif out >= rearm - eps:                # re-armed once price is rearm_ticks outside the band
                self.armed[key] = True

    def _fmt(self, a: Alert, price):
        inst = None if a.params.get("free") else self.instruments.get(a.symbol)
        return inst.fmt(price) if inst else free_fmt(price)   # a stock's, a series' or a formula's (free_alerts)

    def _fire(self, a: Alert, tk: Tick, msg: str):
        a.last_fired = tk.ts
        # fired anew: it waits from this fire. AFRESH names the old one by its time, which a print stamped the same ms
        # (a backlog, a sweep) would share: kept, it would take this fire for that one, and never wait
        if self.waits.pop((a.id, AFRESH), None) is not None:
            self._save_waits()
        if not a.repeat:
            a.enabled = False
        if a.note:
            msg = f"{msg} · {a.note}"
        ev = {"alert_id": a.id, "symbol": a.symbol, "ts": tk.ts, "price": tk.price,
              "message": f"{msg} (last {self._fmt(a, tk.price)})"}
        try:
            self.store.update_alert(a.id, last_fired=tk.ts, enabled=a.enabled)
            ev["id"] = self.store.add_event(ev)
        except sqlite3.Error as e:   # the database can't be written (a full disk, another program's lock): sent all the same
            log.warning("alert %s fired, not saved: %s", a.id, e)
            ev["id"] = None
        self.on_fire(ev)
