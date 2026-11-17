"""Price alerts on a symbol with no live chart here: a stock, an ETF, an index, an economic series or a formula of
symbols (params "free": True). No trades come for them, so each is checked on its quotes, as the watchlist shows them:
a formula worked out from its parts' last quotes (a live future's part from its last trade), through the same
AlertEngine as the futures' alerts, apart from their trades (AlertEngine.on_tick's free). Their symbols are quoted while
such an alert is on (Market._watch_set). Each is checked at the time its value arrives (a series' quote carries its
observation's date, days or weeks old)."""
import math
import re

from . import watchlists as wl
from .formula import MAX_SYMBOLS, canonical, compile_formula, is_formula, live_root, symbols
from .models import Tick


def free_symbol(symbol: str, instruments: dict, only) -> str:
    """The alert's symbol as its chart has it ("VIXM/VIXY", "(GC1!/SI1!)*100", "AAPL"), or ValueError. only: the roots
    a bare one is written as the future of (the symbol database's only_futures)."""
    from .ext import chart_symbol
    if not is_formula(symbol):
        return chart_symbol(symbol, instruments, only)
    # as its chart writes it (chartSym): no exchange prefixes, no spaces ("nasdaq:aapl / msft" is AAPL/MSFT's)
    s = canonical(re.sub(r"\s+", "", wl.bare(wl._clean_item(symbol, only))), instruments)
    compile_formula(s)
    n = len(symbols(s))
    if not 1 <= n <= MAX_SYMBOLS:
        raise ValueError(f"a formula of 1 to {MAX_SYMBOLS} symbols")
    return s


def free_round(v: float) -> float:
    return float(f"{v:.10g}")


def free_fmt(v: float) -> str:
    """A price with no instrument's decimals: six significant digits, at least two decimals (230.12, 0.62341)."""
    d = min(10, max(2, 5 - math.floor(math.log10(abs(v))))) if v else 2
    s = f"{v:.{d}f}"
    whole, _, frac = s.partition(".")
    return f"{whole}.{frac.rstrip('0').ljust(2, '0')}"


FREE_LAST = "free_alert_last"   # setting: each alert symbol's value last checked, kept over a restart


class FreeAlerts:
    def __init__(self, market):
        self.m = market
        self.fns: dict[str, tuple] = {}         # alert symbol -> (its formula or None, its parts)
        self.keys: dict[str, set[str]] = {}     # quote key -> the alert symbols its quotes move
        self.roots: set[str] = set()            # live futures among the parts: moved by their trades
        self.last: dict[str, float] = {}        # each alert symbol's last value checked
        # a rolled future's root -> what it showed then, the old contract's: a live one's last trade, a quote-only
        # one's last price (add-on 21 sends that again until the new contract's comes). No value while it still does.
        self.stale: dict[str, object] = {}
        self.saved: dict = {}                    # ...as last written (FREE_LAST)

    def shadowed(self, a) -> str | None:
        """The live future's root a free alert's stock is written as (MET: set on MetLife before Micro Ether MET was
        added here; alone or in a formula), else None. That symbol is the future's now (wl.quote_key, canonical): its
        stock is quoted here no more, and the alert never runs on the future's prices (Market._pause_shadowed)."""
        return next((p for p in symbols(a.symbol) if p in self.m.instruments), None)

    def index(self, restarted=frozenset()) -> set[str]:
        """From the alerts as they are now (after each change). restarted: the ids of the alerts on that start afresh
        (AlertEngine.reload: new, edited, or back on after a pause, a one-shot's firing or an expiration). Returns the
        alert symbols with one of them (recheck). One whose alerts were all off starts afresh, as at a first start: its
        first value is where its crossings are looked for from, never one from before it went off."""
        fns, keys, fresh = {}, {}, set()
        for a in list(self.m.alerts.alerts.values()):
            if not a.enabled or a.kind != "price" or not a.params.get("free") or self.shadowed(a):
                continue
            if a.id in restarted:
                fresh.add(a.symbol)
            if a.symbol in fns:
                continue
            try:
                fns[a.symbol] = (compile_formula(a.symbol), symbols(a.symbol)) if is_formula(a.symbol) else (None, [a.symbol])
            except ValueError:
                continue
            for p in fns[a.symbol][1]:
                keys.setdefault(wl.quote_key(p, self.m.instruments), set()).add(a.symbol)
        for s in set(self.fns) ^ set(fns):   # its quotes went unchecked meanwhile: no value kept from before
            self.m.alerts.free_px.pop(s, None)
        self.fns, self.keys = fns, keys
        self.roots = {r for k in keys if (r := live_root(k, self.m.instruments))}
        fresh &= set(fns)
        self.last = {s: v for s, v in self.last.items() if s in fns and s not in fresh}
        return fresh

    def recheck(self, fresh: set[str], ts: int):
        """Alerts new, edited or back on (index) are checked on the value shown now, as a future's on its next trade: a
        Greater than already true fires now, not at the next quote that differs (weeks off for a monthly series)."""
        for s in fresh:
            if s in self.fns:
                self._check(s, ts)

    def _part(self, p: str) -> float:
        root = live_root(p, self.m.instruments)
        tk = self.m.last_tick.get(root) if root else None
        if root in self.stale:   # rolled: the old contract's last trade is no value of the new one's
            if tk is self.stale[root]:
                return math.nan
            del self.stale[root]
        if tk is not None:
            return tk.price
        key = wl.quote_key(p, self.m.instruments)
        q = self.m.quotes.get(key)
        v = q.get("last") if q else None
        if not isinstance(v, (int, float)):
            return math.nan
        froot = wl.future_root(key)
        if not root and froot in self.stale:   # a quote-only future rolled: its old contract's price sent again
            if v == self.stale[froot]:
                return math.nan
            del self.stale[froot]
        return float(v)

    def _check(self, s: str, ts: int):
        f, parts = self.fns[s]
        v = self._part(s) if f is None else f({p: self._part(p) for p in parts})
        if not math.isfinite(v):
            return
        v = free_round(v)
        if self.last.get(s) == v:
            return
        self.last[s] = v
        self.m.alerts.on_tick(Tick(s, ts, v, 0, 0), free=True)

    def moved(self, key: str, ts: int):
        """`key`'s quote (or a live future's trade, key "ES1!") changed: the alerts it is part of are checked. ts: when
        (the value's arrival: the expiry, the repeat's minute or N minutes and the time it fired at are the
        clock's)."""
        for s in self.keys.get(key, ()):
            self._check(s, ts)

    def rolled(self, root: str):
        """A future went to its next contract (a live one: market.on_contract; one only quoted: market.quote_contract,
        NinjaTrader's): an alert on it, or a formula with it, jumps by the spread, through nothing. Its part has
        no value until the new contract's first trade (a quote-only one's: first price other than the old one's last),
        so no other part's move works it out from the old contract's meanwhile."""
        key = f"{root}1!"
        if root in self.m.instruments:
            self.stale[root] = self.m.last_tick.get(root)
        else:
            q = self.m.quotes.get(key)
            self.stale[root] = q.get("last") if q else None
        for s in self.keys.get(key, ()):
            self.last.pop(s, None)
            self.m.alerts.rolled(s, free=True)

    def _kept(self, s: str) -> bool:
        """Whether alert symbol `s`'s last value is still good after a restart: not with a future only quoted (on a
        list, not charted). Its contract may have rolled meanwhile, and the first value after can come before anything
        says so (Yahoo or IBKR quoting it before NinjaTrader connects; theirs never say, nor add-on 21 at its start), so
        the jump would cross. A live future's roll is known first: the feed names its contract on connecting, and
        market.on_contract sets it against the one stored, before its first trade."""
        keys = [wl.quote_key(p, self.m.instruments) for p in self.fns[s][1]]
        return not any(wl.future_root(k) and not live_root(k, self.m.instruments) for k in keys)

    def seed(self):
        """At a start: each alert symbol's value last checked before the restart (save) is where its first value now
        is checked from, as a future's alerts from its last stored trade: a cross while the server was down fires.
        Not one with a future only quoted (_kept): its first value now is only where it looks from."""
        got = self.m.store.get_setting(FREE_LAST, {}) or {}
        for s, v in (got.items() if isinstance(got, dict) else ()):
            if s in self.fns and self._kept(s) and isinstance(v, (int, float)) and math.isfinite(v):
                self.m.alerts.free_px.setdefault(s, float(v))
        self.saved = got if isinstance(got, dict) else {}

    def save(self):
        """Each alert symbol's value last checked, written when it changed (market's housekeeping, and its stop)."""
        got = {s: v for s, v in self.m.alerts.free_px.items() if s in self.fns and self._kept(s)}
        if got != self.saved:
            self.m.store.set_setting(FREE_LAST, got)
            self.saved = got
