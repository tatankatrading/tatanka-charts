"""Runs the Tatanka AMT Toolkit on the server: one AmtRunner per (symbol, minutes, settings), loaded from stored 1m
history, fed live minutes, previewed once a second for the charts that show it. Each chart can have its own settings
(a runner per distinct set; charts on the starting settings share one); the starting ones are every chart's, with the
symbol's alert settings on them, and the symbol's run its alerts (with every group an alert switch that is on needs).
AMT alerts are opt-in per symbol and come from one dedicated runner per symbol, so they never fire twice."""
import asyncio
import hashlib
import json
import logging
import math
import re
import sqlite3
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from . import amt, sessions, workers
from .amt_runner import AmtRunner, TickDays, ct
from .gapfill import filling
from .models import Bar, Tick
from .store import connect, iter_bars_1m

log = logging.getLogger(__name__)
HISTORY_DAYS = 90            # the Toolkit's recommended 90 days loaded...
YEARLY_DAYS = 400            # ...and 400 for the Yearly profile (NinjaTrader's guide: Days to load)
ALERT_MINUTES = 5            # the runner that alerts, per symbol
ROLL_HOLD_MS = 15 * 60_000   # after a contract roll, no AMT alert for this long: its bars before it are the old contract's
STEP_S = 1.0
PROFILE_EVERY_S = 5.0
IDLE_S = 6 * 3600            # an AMT run no chart shows stays loaded this long...
IDLE_MAX = 12                # ...unless more are idle than this: the longest idle go first
PD_RETRY_S = 600             # a prior-day value job that failed waits this long before the next try
PD_KEY = "amtpd2"            # vp_cache key prefix: PD_KEY|SYM|start|end
PD_NT_DAYS = 7               # sessions this recent come from NinjaTrader's tick history when the add-on can send it
PD_NT_WAIT_S = 300           # ...waited for this long each
LIVE_BAR_AGE_MS = 5 * 60_000  # notices from older bars (buffered data catching up) never alert
REPLAY_PER_PAGE = 8          # replaying charts' runners one page (connection) holds: the oldest goes past this
# Every setting as NinjaTrader's property grid shows it, with the checks of its ValidateSettings (tools/gen_amt_schema.py).
SCHEMA = json.loads(Path(__file__).with_name("amt_schema.json").read_text(encoding="utf-8"))
# NinjaTrader properties that live outside the engine's Settings: where the daily levels come from (daily_levels.py)
# and the session-change warning. "Paste Levels Here" is per symbol here (the Plan tab), as each NinjaTrader chart
# has its own.
EXTRA_DEFAULTS = {"LevelsFile": "", "tzguard_enable": True, "PdTickSessions": 5, "ProfileTicks": True}
_EXTRA = [
    {"name": "LevelsFile", "group": "2. Daily Levels", "order": 8, "label": "Levels File or Link (supports {root})", "type": "str",
     "description": "A levels link (http or https) or a levels file. {root} becomes the symbol's root; "
                    "micros use the parent (MES reads ES). A Windows path under Documents reads this server's Documents folder "
                    "(any path when the server runs on Windows without Docker). "
                    "Blank: Documents\\Tatanka\\levels\\{root}.txt if it exists, else the symbol's pasted line (Plan tab)"
                    "."},
    {"name": "tzguard_enable", "group": "1. Global Settings", "order": 7, "label": "Warn if Session Times Were Changed", "type": "bool",
     "description": "All session fields are Central exchange time, written for ES/NQ's 08:30-15:00: on another instrument "
                    "they move with that one's regular hours (CL's IB 08:30-09:30 is 08:00-09:00) while Use Instrument Hours "
                    "is on. Shows a warning on the chart when any differs from its default."},
    # NinjaTrader downloads the ticks these name as the chart loads; here they are the trades stored (NinjaTrader's tick
    # history for the last PD_NT_DAYS days when the add-on can send it), worked out once and kept
    {"name": "PdTickSessions", "group": "4. Previous Day Value & 80% Rule", "order": 53, "label": "Value From Ticks (Sessions)",
     "type": "int", "min": 0, "max": 20,
     "description": "pdPOC, pdVAH and pdVAL from every trade at one-tick rows (as a tick volume profile) for this many "
                    "recent RTH sessions, counted back from when the chart loads, and each one that closes after; older "
                    "days from 1-minute bars. 0: 1-minute bars only (before v1.2.0)."},
    {"name": "ProfileTicks", "group": "11. Volume Profile", "order": 158, "label": "Profile From Ticks", "type": "bool",
     "description": "The current Session or Weekly profile and its Developing POC, VAH and VAL from every trade: the "
                    "period's stored trades as the chart loads, then each trade live. Monthly and Yearly profiles, older "
                    "periods and any minute whose trades are missing use 1-minute bars. Off: 1-minute bars only (before v1.2.2)."},
]
# by NinjaTrader's group and order; Profile From Ticks shares Calc Precision's order and comes before it, as declared
SCHEMA = sorted([x for x in SCHEMA if x["name"] != "lvl_text"] + _EXTRA,
                key=lambda x: (int(x["group"].split(".")[0]), x["order"], x["name"] != "ProfileTicks"))
SCHEMA_BY_NAME = {x["name"]: x for x in SCHEMA}
DEFAULTS = {**amt.DEFAULTS, **EXTRA_DEFAULTS}
# Never a chart's own, whatever a chart has: where the daily levels come from, and the alerts (one runner per symbol
# fires them): the Alerts group, each group's own alert switches (Alert 80% Rule Confirmed, Enable Level Alerts, Alert
# Window...), and the level zones' width, which Zone Entry alerts use (so a chart's zones are the ones that alert).
ZONE_WIDTH = {"lvl_zone_mode", "lvl_zone_atr_pct", "lvl_zoneWidthTicks"}
SHARED = frozenset({"LevelsFile"} | ZONE_WIDTH
                   | {x["name"] for x in SCHEMA if x["group"].startswith("18.") or "alert" in x["name"].lower()})
# Of those, all but the levels file are the symbol's: every ES chart has ES's and ES's alerts run on them, so turning one
# off on an NQ chart turns it off on every NQ chart and leaves ES's as they are (a zone width in ticks is an instrument's
# too). The levels file is one setting for every chart and symbol: {root} makes it each symbol's file or link.
PER_SYMBOL = SHARED - {"LevelsFile"}
SYMBOL_SETTING = "amt_symbol_settings"   # the store's: symbol -> its PER_SYMBOL settings that differ from the defaults
# This app's defaults before it took NinjaTrader's (1.2.4): settings stored then as what differed from the defaults mean
# these where they leave them out (AmtHub._keep_old_defaults).
OLD_DEFAULTS = {"amt_del_sess": "1700-1705", "ib_stop_time": "0830-1500"}
OLD_DEFAULTS_KEPT = "amt_alerts_old_defaults_kept"   # the store's: the alert configs from before were given them (once)
# The alerts follow their alert switches only: what each needs on for the engine to work out what it alerts on (the
# group's Enable, nPOCs' kinds, the lines' Show switches that arm their alerts in Engine.Process). A chart's own Enable
# or Show only shows or hides the group there; the alert runner has every one that an alert switch on needs.
ALERT_NEEDS = {
    "ib_alert_ibh": ("ib_enable",), "ib_alert_ibl": ("ib_enable",), "amt_alert_ovn": ("amt_enable",),
    "vwap_alert": ("vwap_enable",), "cc_alert_80": ("cc_enable",),
    "cc_alert_va": ("cc_enable", "cc_show_poc", "cc_show_vah", "cc_show_val"),
    "inv_alert": ("inv_enable",), "spike_alert": ("showSpike",),
    "npoc_alert": ("npoc_enable", "npoc_d_show", "npoc_w_show", "npoc_m_show"),
    "pd_alert_hl": ("pd_enable", "pd_showH", "pd_showL"), "pw_alert_hl": ("pw_enable", "pw_showH", "pw_showL"),
    "wk_alert_open": ("wk_showOpen",), "macro_alert_lvl": ("macro_show_poc", "macro_show_month", "macro_show_hl"),
    "mvp_use_cur_alerts": ("mvp_master", "mvp_show_poc", "mvp_show_vah", "mvp_show_val"),
}


def _hours_long(session: str) -> float | None:
    """A session's length in hours ("0830-1500" -> 6.5; one past midnight counts on), None if it doesn't read."""
    m = re.match(r"^(\d{2})(\d{2})-(\d{2})(\d{2})", str(session or ""))
    if not m:
        return None
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return ((b - a) % 1440) / 60 or None


def instrument_hours(rth, overrides: dict) -> tuple[dict | None, str, int]:
    """Settings (overrides: what differs from the defaults) as an instrument whose regular hours are rth (CT "HH:MM"
    open and close; None: ES's) runs them. They are written for ES's 08:30-15:00, as the NinjaTrader defaults; with
    Use Instrument Hours on (inst_hours, the default) another instrument's session fields move to its own (CL's IB
    08:00-09:00), the RTH VWAP window as one, the end of the overnight count too, and a Session Duration that was the
    Session profile's own length becomes the moved one's. Returns the overrides with those moved, the regular hours
    themselves (the settle, the RTH open and the day's high and low) and the minutes the TPO Session's start moved,
    which its TPO periods move by (GC's 0830-1500 -70, a 1200-1500 one -150). Off, or on ES's hours: (None,
    "0830-1500", 0), every field as written. The NinjaTrader Toolkit's InstrumentHours.Apply (v1.2.4) does the same."""
    eff = {**DEFAULTS, **overrides}
    move = sessions.rth_mover(rth) if eff["inst_hours"] else None
    if not move:
        return None, "0830-1500", 0
    moved = {k: sessions.move_session(eff[k], move) for k, spec in SCHEMA_BY_NAME.items() if spec.get("session")}
    vwap = sessions.move_window(eff["vwap_start_h"] * 60 + eff["vwap_start_m"], eff["vwap_end_h"] * 60 + eff["vwap_end_m"], move)
    for (h, m), t in zip((("vwap_start_h", "vwap_start_m"), ("vwap_end_h", "vwap_end_m"), ("inv_end_h", "inv_end_m")),
                         (*vwap, move(eff["inv_end_h"] * 60 + eff["inv_end_m"]))):
        moved[h], moved[m] = t // 60, t % 60
    if eff["mvp_sess_dur"] == _hours_long(eff["mvp_sess_time"]):   # the session's own length: the moved one's
        moved["mvp_sess_dur"] = _hours_long(moved["mvp_sess_time"]) or eff["mvp_sess_dur"]
    a, b = (amt._session(x)[0] for x in (eff["sp_rth_session"], moved["sp_rth_session"]))
    return {**overrides, **moved}, sessions.move_session("0830-1500", move), (b - a + 720) % 1440 - 720


def alert_overrides(overrides: dict) -> dict:
    """A symbol's alert runner's settings: its starting ones, with what each alert switch that is on needs turned on."""
    on = {k for alert, needs in ALERT_NEEDS.items() if overrides.get(alert, DEFAULTS[alert]) for k in needs}
    out = {**overrides, **{k: True for k in on}}
    return {k: v for k, v in out.items() if v != DEFAULTS[k]}   # what differs from the defaults, as clean_settings keeps


def engine_settings(overrides: dict) -> amt.Settings:
    """The engine's Settings: the hub's own keys left out, and no pasted levels (they arrive per symbol)."""
    return amt.Settings({k: v for k, v in overrides.items() if k not in EXTRA_DEFAULTS and k != "lvl_text"})


def clean_settings(overrides: dict) -> dict:
    """Checks settings as the Toolkit does (types, choices, bounds, finite numbers, CT sessions, then the three
    cross-field rules of ValidateSettings) and keeps only those that differ from the NinjaTrader defaults."""
    out = {}
    for k, v in overrides.items():
        spec = SCHEMA_BY_NAME.get(k)
        if spec is None:
            raise ValueError(f"unknown AMT setting {k}")
        label, t = spec["label"], spec["type"]
        number = isinstance(v, (int, float)) and not isinstance(v, bool)
        if number and not math.isfinite(v):
            raise ValueError(f"{label} must be finite.")
        if not {"bool": isinstance(v, bool), "int": number and v == int(v), "color": number and v == int(v),
                "float": number, "str": isinstance(v, str) and len(v) <= 20_000}[t]:
            raise ValueError(f"{label}: expected {'a whole number' if t == 'int' else t}")
        if t in ("int", "color"):
            v = int(v)
        elif t == "float":
            v = float(v)
            if not math.isfinite(v):
                raise ValueError(f"{label} must be finite.")
        if t == "color" and not 0 <= v <= 0xFFFFFFFF:
            raise ValueError(f"{label}: not a color")
        if spec.get("min") is not None and v < spec["min"]:
            raise ValueError(f"{label} is below its minimum ({spec['min']}).")
        if spec.get("max") is not None and v > spec["max"]:
            raise ValueError(f"{label} exceeds its maximum ({spec['max']}).")
        if "choices" in spec and v not in spec["choices"]:
            raise ValueError(f"Invalid {label}: {v}")
        if spec.get("session"):
            try:
                amt.in_session(datetime(2026, 1, 5), v)
            except ValueError:
                raise ValueError(f"{label}: use HHmm-HHmm in CT, optionally :days (1 = Sunday), e.g. 1700-0830:123456")
        if v != DEFAULTS[k]:
            out[k] = v
    s = engine_settings(out)
    if s.daily_atr_length < 1 or s.sp_tpo_minutes < 1 or s.mvp_sess_dur <= 0 or s.mvp_smooth_len < 1 or s.sp_max_days < 1:
        raise ValueError("Periods and history lengths must be positive.")
    if not 0 < s.mvp_val_pct <= 100:
        raise ValueError("Profile value area must be between 0 and 100 percent.")
    if not re.fullmatch(r"\d+", s.mvp_calc_tf.strip()) or not 1 <= int(s.mvp_calc_tf) <= 30:
        raise ValueError("Calc Precision must be a whole number of minutes from 1 to 30.")
    return out


class AmtEntry:
    def __init__(self, runner: AmtRunner):
        self.runner = runner
        self.ready = asyncio.Event()
        self.pending: list[Bar] = []
        self.pending_trades: list = []   # live trades while it loads (Tick)
        self.subs: set = set()        # (client, sid)
        self.error: str | None = None
        self.dropped = False          # new settings or a gap fill let go of it (so its failure is no chart's)
        self.idle_since = time.monotonic()
        self.sig = None


def history_days(S: amt.Settings) -> int:
    """The days of 1-minute bars a runner loads, as NinjaTrader's guide sets Days to load: 90, or 400 with the Yearly
    profile drawn, so it starts at January 1."""
    return YEARLY_DAYS if S.mvp_master and S.mvp_tf_mode == "Yearly" else HISTORY_DAYS


class ReplayAmt:
    """A replaying chart's Toolkit: its own runner, never alerting, loaded from the history_days before the replay
    clock and fed the stored minutes as the replay plays (built again when it goes back, or its settings change)."""

    def __init__(self, runner: AmtRunner, want: tuple, upto: int, gen: int = 0):
        self.runner = runner
        self.want = want            # (symbol, minutes, its settings): another and it is built again
        self.upto = upto            # the minutes before this are in it
        self.gen = gen              # AmtHub.replay_gen of its symbol when loaded: another (new settings, a gap fill)
                                    # and it is loaded again


def feed_minutes(db_path, runner: AmtRunner, symbol: str, since: int, before: int) -> AmtRunner:
    load_trades(db_path, runner, symbol, before, since)
    for b in iter_bars_1m(db_path, symbol, since, before):
        runner.add_minute(b)
    return runner


def load_trades(db_path, runner: AmtRunner, symbol: str, before: int, since: int = 0):
    """The stored trades of the runner's current Session or Weekly profile before `before` (from `since` on), per
    minute and price: that profile is drawn from them (a minute without all of its trades: from its 1-minute bar)."""
    start = runner.period_start(before)
    if start is None:
        return
    db = connect(db_path, readonly=True)
    try:
        for m, px, v in db.execute("SELECT ts / 60000 * 60000, price, SUM(size) FROM ticks WHERE symbol=? AND ts>=? "
                                   "AND ts<? AND size>0 GROUP BY ts / 60000, price", (symbol, max(start, since), before)):
            runner.add_trade(m, px, v)
    finally:
        db.close()


def pd_job(db_path, symbol: str, windows: list, tick: float) -> dict:
    """Worker job: each RTH window's volume at price, the way the SVP reads a session: its trades, and for a minute
    with none stored (gap-filled history is bars only) its 1-minute bar's volume spread evenly over its ticks.
    {(start, end): [(price, volume)]}, [] when nothing is stored."""
    db = connect(db_path, readonly=True)
    out = {}
    try:
        for b, e in windows:
            vol: dict[float, float] = {}
            for px, v in db.execute("SELECT price, SUM(size) FROM ticks WHERE symbol=? AND ts>=? AND ts<? AND size>0 "
                                    "GROUP BY price", (symbol, b, e)):
                vol[px] = vol.get(px, 0.0) + v
            traded = {m for (m,) in db.execute("SELECT DISTINCT ts / 60000 FROM ticks WHERE symbol=? AND ts>=? AND ts<? "
                                               "AND size>0", (symbol, b, e))}
            for t, h, low, v in db.execute("SELECT t, h, l, v FROM bars_1m WHERE symbol=? AND t>=? AND t<? AND v>0",
                                           (symbol, b, e)):
                if t // 60_000 in traded:
                    continue
                lo, hi = round(low / tick), round(h / tick)
                n = max(1, hi - lo + 1)
                for k in range(lo, lo + n):
                    px = round(k * tick, 10)
                    vol[px] = vol.get(px, 0.0) + v / n
            out[(b, e)] = sorted(vol.items())
    finally:
        db.close()
    return out


def _load_history(db_path, runner: AmtRunner, symbol: str, split: int):
    runner.loaded_at = split   # the sessions whose prior-day value comes from trades are counted back from here
    load_trades(db_path, runner, symbol, split)
    for b in iter_bars_1m(db_path, symbol, split - history_days(runner.S) * 86_400_000, split):
        runner.add_minute(b)
    runner.commit_ready()


def chart_minutes(spec_kind: str, spec_seconds: int) -> int:
    """The Toolkit runs on 1-30 minute bars: minute charts use their own size, longer time charts 30,
    tick/volume/range charts 5."""
    if spec_kind != "time":
        return 5
    return max(1, min(30, spec_seconds // 60))


class AmtHub:
    def __init__(self, market):
        self.m = market
        self.entries: dict[tuple[str, int], AmtEntry] = {}
        self.alert_cfg: dict = market.store.get_setting("amt_alerts", {})   # symbol -> {"on"}
        every = {k: v for k, v in (market.store.get_setting("amt_settings", {}) or {}).items()
                 if k in SCHEMA_BY_NAME}   # a pasted line from before is per symbol now
        per = market.store.get_setting(SYMBOL_SETTING, None)
        if not isinstance(per, dict):
            per = self._migrate(every)
        self._keep_old_defaults(every, per)
        # a setting at its default is left out, as clean_settings leaves it out: one stored when the default was another
        # (1500-1505 typed when 1700-1705 was the default) would give the symbol's alerts a runner of their own (_of)
        mine = lambda ov, alerts: {k: v for k, v in ov.items() if (k in PER_SYMBOL) == alerts and v != DEFAULTS[k]}   # noqa: E731
        self._set(mine(every, False), {sym: mine(ov, True) for sym, ov in per.items() if isinstance(ov, dict)})
        self._clean_alert_cfg()
        self.alert_times: dict[str, deque] = {}
        self.roll_hold: dict[str, int] = {}   # symbol -> no AMT alert before this (ms): just rolled to another contract
        self.roll_pending: set[str] = set()   # rolled, the new contract not traded yet: the hold starts with its first trade
        self.cap_warned: dict[str, bool] = {}
        self.pd_gen: dict[str, int] = {}       # bumps when a symbol's history changes: a job from before keeps nothing
        self.pd_busy: set[str] = set()
        self.pd_failed: dict[str, float] = {}
        self.pd_mem: dict[str, dict] = {}      # recent sessions from the trades stored here (NinjaTrader's history
                                               # couldn't be had): kept in memory only, asked of NinjaTrader again later
        self.replays: dict[tuple, ReplayAmt] = {}   # (client, sid) -> a replaying chart's Toolkit
        self.replay_locks: dict[tuple, asyncio.Lock] = {}
        self.replay_gen: dict[str, int] = {}        # symbol -> its settings or history changed (replays load again)
        # Value From Ticks (Sessions): the sessions a runner had when it was dropped for a reload NinjaTrader has none of
        # (a prior-day job, a gap fill), given to the one that replaces it as far as the charts on it have them: a
        # running NinjaTrader chart keeps the ones it asked for at its load and each close since. A runner no chart
        # shows (the alerts', an idle one), new settings or a new load count them again.
        self.tick_kept: dict[tuple, tuple] = {}     # entry key -> (when dropped, TickDays' params, sessions)
        self.chart_since: dict[tuple, tuple] = {}   # (client, sid) -> (entry key, when the chart loaded it: ms), kept
                                                    # through those reloads while it shows that Toolkit
        self.replay_ticks: dict[tuple, tuple] = {}  # (client, sid) -> (want, upto, params, sessions) of its last runner
        # symbol -> its alert runner dropped by such a reload: it watches the live prices until the one replacing it is
        # ready (a Yearly one takes seconds to load), as a running NinjaTrader chart alerts on through its patch
        self.standby: dict[str, AmtEntry] = {}
        db = getattr(getattr(market, "store", None), "db", None)
        try:   # the first version's, from the trades stored here only: worked out again
            if db is not None:
                with db:
                    db.execute("DELETE FROM vp_cache WHERE key LIKE 'amtpd|%'")
        except sqlite3.Error:
            pass

    def _migrate(self, every: dict) -> dict:
        """The first start since the alert settings are each symbol's: the ones every chart had (the shared settings'
        alert settings, stored with the rest) become every symbol's, each instrument's and each one's whose AMT alerts
        are kept for when it is back on Instruments, so nothing set is lost; then they leave the every-chart settings.
        Once: from then on the symbols' are stored (SYMBOL_SETTING), even with none changed."""
        alerts = {k: v for k, v in every.items() if k in PER_SYMBOL}
        per = {sym: dict(alerts) for sym in sorted({*self.m.instruments, *self.alert_cfg})}
        self.m.store.set_setting(SYMBOL_SETTING, per)
        if alerts:
            self.m.store.set_setting("amt_settings", {k: v for k, v in every.items() if k not in PER_SYMBOL})
            log.info("AMT alert settings are each symbol's now: %s copied to %s", ", ".join(sorted(alerts)), ", ".join(per))
        return per

    def _keep_old_defaults(self, every: dict, per: dict):
        """Once, the first start with NinjaTrader's IB Stop Time and Delete OVN Lines At defaults (OLD_DEFAULTS were this
        app's before): an alert config's own settings ("set": what differed from the defaults then) without one of them
        meant the old default. Where the symbol's starting settings (every, per, as stored) have another value, the
        chart the alerts were switched on from had the old default as its own (the page sends its starting settings
        with its own on top) and still draws it, so the set gets it written out: the alerts keep ending where that chart
        draws. Where the starting settings had the old default too, that chart follows them to the new one, and so do
        the alerts."""
        if self.m.store.get_setting(OLD_DEFAULTS_KEPT, False):
            return
        cfg = self.alert_cfg if isinstance(self.alert_cfg, dict) else {}
        kept = []
        for sym, c in cfg.items():
            own = c.get("set") if isinstance(c, dict) else None
            if not isinstance(own, dict):
                continue
            base = {**every, **(per[sym] if isinstance(per.get(sym), dict) else {})}
            for k, old in OLD_DEFAULTS.items():
                if k not in own and base.get(k, old) != old:
                    own[k] = old
                    kept.append(f"{sym} {k}")
        if kept:
            self.m.store.set_setting("amt_alerts", cfg)
            log.info("AMT alerts keep the old defaults their charts draw: %s", ", ".join(kept))
        self.m.store.set_setting(OLD_DEFAULTS_KEPT, True)

    # --- settings ---------------------------------------------------------------------------------
    def _set(self, overrides: dict, per_symbol: dict | None = None):
        """overrides: every chart's starting settings, without the alert settings; per_symbol: symbol -> its alert
        settings (PER_SYMBOL; a symbol with none has NinjaTrader's defaults)."""
        self.overrides, self._settings = overrides, engine_settings(overrides)
        if per_symbol is not None:
            self.per_symbol = per_symbol
        self._sym: dict = {}   # symbol -> what _of works out, until the settings change

    def _of(self, sym: str | None) -> tuple:
        """The symbol's starting settings (every chart's with its alert settings on them), as the engine has them, and
        its alert runner's (alert_overrides) with that runner's sig: "" when it is the starting settings' runner, else
        a runner of its own. None: no symbol's (the alert settings at their defaults)."""
        got = self._sym.get(sym)
        if got is None:
            base = {**self.overrides, **self.per_symbol.get(sym, {})} if sym is not None else self.overrides
            alert = alert_overrides(base)
            got = self._sym[sym] = (base, engine_settings(base), alert,
                                    "" if alert == base else json.dumps(alert, sort_keys=True, separators=(",", ":")))
        return got

    def base(self, sym: str | None) -> dict:
        """What the symbol's charts start from: every chart's starting settings with the symbol's alert settings."""
        return self._of(sym)[0]

    def settings(self, sym: str | None = None) -> amt.Settings:
        """base(sym) as the engine takes them (sessions as written; settings_for moves them to the symbol's hours)."""
        return self._of(sym)[1]

    def symbols(self) -> list[str]:
        """Every symbol with alert settings: the instruments, and those kept for one taken off Instruments."""
        return sorted({*self.m.instruments, *self.per_symbol, *self.alert_cfg})

    def alert_run(self, sym: str) -> tuple[int, str, dict]:
        """The runner that alerts for `sym`: the minutes and own settings of the chart its alerts were switched on from
        (alert_cfg: "minutes", "set"), so what it alerts on is what that chart draws (a Value Area % or IB session of
        its own, its bars), with what each alert switch on needs (alert_overrides). A symbol's alerts switched on before
        they were kept: ALERT_MINUTES on its starting settings. (minutes, sig, overrides): sig "" for the starting
        settings' runner."""
        cfg = self.alert_cfg.get(sym) or {}
        chart = cfg.get("minutes") or ALERT_MINUTES
        own = cfg.get("set")
        if own is None:
            _base, _s, alert, sig = self._of(sym)
            return self.engine_minutes(sym, chart, alert), sig, alert
        try:
            _sig, mine = self.chart_settings(own, sym)
        except (ValueError, KeyError, TypeError, OverflowError):   # settings no longer valid (a newer version's checks): the symbol's
            _base, _s, alert, sig = self._of(sym)
            return self.engine_minutes(sym, chart, alert), sig, alert
        alert = alert_overrides(mine)
        return (self.engine_minutes(sym, chart, alert),
                ("" if alert == self.base(sym) else json.dumps(alert, sort_keys=True, separators=(",", ":"))), alert)

    def engine_minutes(self, sym: str, minutes, overrides: dict) -> int:
        """The Toolkit's bars for a chart's minutes (1-30) on its settings (overrides, as a runner has them): ones the
        regular hours they run on open and close on, so the IB, the RTH open and the sessions start where they should
        (GC's 07:20 on a 15- or 30-minute chart: 5- and 10-minute bars; ES's 08:30 on a 20-minute chart: 10). With Use
        Instrument Hours off those are ES's on every instrument, as NinjaTrader v1.2.3 (a 30-minute GC chart: 30). Bars
        are whole minutes of the hour (a 7-minute chart's: 1)."""
        m = max(1, min(30, int(minutes)))
        inst = self.m.instruments.get(sym)
        on = overrides.get("inst_hours", DEFAULTS["inst_hours"])
        for t in (inst.rth if on and inst and inst.rth else ("08:30", "15:00")):
            m = math.gcd(m, int(t[:2]) * 60 + int(t[3:5]))
        return math.gcd(m, 60)

    def alert_key(self, sym: str) -> tuple:
        """The key of the runner that alerts for `sym` (alert_run)."""
        minutes, sig, _ = self.alert_run(sym)
        return (sym, minutes, sig) if sig else (sym, minutes)

    def chart_settings(self, overrides: dict | None, sym: str | None = None) -> tuple[str, dict]:
        """A chart's own settings, cleaned (ValueError if bad), and its runners' key: "" when they are the symbol's
        starting ones. The alerts are always the symbol's (sym; None: at their defaults) and the levels file every
        chart's."""
        base = self.base(sym)
        if overrides is None:
            return "", base
        if not isinstance(overrides, dict):
            raise ValueError("AMT settings must be an object")
        mine = clean_settings({**{k: v for k, v in overrides.items() if k not in SHARED},
                               **{k: v for k, v in base.items() if k in SHARED}})
        return ("" if mine == base else json.dumps(mine, sort_keys=True, separators=(",", ":"))), mine

    def _hours(self, sym: str, overrides: dict | None) -> tuple[dict | None, str, int]:
        """instrument_hours for sym's regular hours and its starting settings (None) or a chart's own."""
        return instrument_hours(self.m.instruments[sym].rth, self.base(sym) if overrides is None else overrides)

    def settings_for(self, sym: str, overrides: dict | None = None) -> amt.Settings:
        """The settings as one symbol runs them (its starting ones, or a chart's own). They are written for ES's regular
        hours (08:30-15:00 CT, as the NinjaTrader defaults): with Use Instrument Hours on, another instrument's session
        fields move to its own (CL's IB 08:00-09:00); off, every field as written (instrument_hours)."""
        moved = self._hours(sym, overrides)[0]
        if moved is None:
            return self._rows(self.settings(sym) if overrides is None else engine_settings(overrides), sym)
        return self._rows(engine_settings(moved), sym)

    def tpo_shift(self, sym: str, overrides: dict | None = None) -> int:
        """The minutes settings_for moves the TPO Session's start to sym's own hours, which its TPO periods move by, so
        they fall in it as they do on ES's: GC's 0830-1500 runs 07:20-12:30 (-70), its periods 07:20-07:50...; a
        1200-1500 one, nearer the close, runs 09:30-12:30 (-150), its periods 09:30-10:00... 0 on ES's hours, and with
        Use Instrument Hours off."""
        return self._hours(sym, overrides)[2]

    def new_runner(self, sym: str, minutes: int, overrides: dict | None = None) -> AmtRunner:
        """A runner for sym at its settings as it runs them (settings_for: its starting ones, or a chart's own), on its
        calendar, regular hours and TPO periods (ES's 0830-1500 and 0 with Use Instrument Hours off)."""
        inst = self.m.instruments[sym]
        _moved, rth_session, shift = self._hours(sym, overrides)
        r = AmtRunner(sym, inst.tick_size, minutes, self.settings_for(sym, overrides), calendar=inst.calendar,
                      rth_session=rth_session, tpo_shift=shift)
        eff = {**DEFAULTS, **(self.base(sym) if overrides is None else overrides)}
        r.pd_sessions, r.profile_ticks = int(eff["PdTickSessions"]), bool(eff["ProfileTicks"])
        return r

    def _rows(self, S: amt.Settings, sym: str) -> amt.Settings:
        """A profile's rows no finer than the instrument's tick (the settings allow 0.0001: a 90-day load of minutes
        then, not seconds; prices trade on ticks, so the profile is the same)."""
        tick = self.m.instruments[sym].tick_size
        for k in ("amt_res_val", "tick_manual_val"):
            if getattr(S, k, tick) < tick:
                setattr(S, k, tick)
        return S

    def settings_value(self, name: str):
        """A setting every chart and symbol has the same: the levels file."""
        return self.overrides.get(name, DEFAULTS[name])

    def set_overrides(self, overrides: dict, sym: str | None = None):
        """All the settings (PUT /api/amt/settings): what is left out goes back to NinjaTrader's default. The alert
        settings are sym's (every chart of it), or with none given every symbol's; the rest every chart's."""
        overrides = clean_settings(overrides)        # raises ValueError on bad input
        alerts = {k: v for k, v in overrides.items() if k in PER_SYMBOL}
        self._put({k: v for k, v in overrides.items() if k not in PER_SYMBOL},
                  {s: alerts for s in ([sym] if sym else self.symbols())})

    def merge_overrides(self, changes: dict, sym: str | None = None):
        """Only what changed (PATCH; a setting back at its default goes back to it), over the settings as they are now:
        the alert settings sym's (every chart of it), or with none given every symbol's; the rest every chart's."""
        every = clean_settings({**self.overrides, **changes})   # raises ValueError on bad input, the alert settings too
        alerts = {k: v for k, v in changes.items() if k in PER_SYMBOL}
        self._put({k: v for k, v in every.items() if k not in PER_SYMBOL},
                  {s: clean_settings({**self.per_symbol.get(s, {}), **alerts}) for s in ([sym] if sym else self.symbols())})

    def seed(self, values: dict, reset: set):
        """config/nt_settings.json's starting settings (values) under what the app has, so a setting changed in the app
        is kept; those in reset back to NinjaTrader's default whatever the app had. The alert settings among them go
        to every symbol. At start, before any runner (ValueError, KeyError or TypeError on bad ones: nothing changes)."""
        def under(ov, alerts):
            return clean_settings({k: v for k, v in {**{k: v for k, v in values.items() if (k in PER_SYMBOL) == alerts},
                                                     **ov}.items() if k not in reset})
        every, per = under(self.overrides, False), {s: under(self.per_symbol.get(s, {}), True) for s in self.symbols()}
        self.m.store.set_setting("amt_settings", every)
        self.m.store.set_setting(SYMBOL_SETTING, {**self.per_symbol, **per})
        self._set(every, {**self.per_symbol, **per})

    def _put(self, overrides: dict, per: dict):
        """New settings: every chart's (overrides, without the alert settings) and some symbols' alert settings (per:
        symbol -> them). The runners of each symbol whose settings changed (every symbol's when every chart's did)
        start again on the new ones, and the charts on them are told to ask anew; the other symbols' go on."""
        every = overrides != self.overrides
        changed = {s for s, ov in per.items() if ov != self.per_symbol.get(s, {})}
        if every:
            self.m.store.set_setting("amt_settings", overrides)
        if changed:
            self.m.store.set_setting(SYMBOL_SETTING, {**self.per_symbol, **per})
        self._set(overrides, {**self.per_symbol, **per})
        for key in [k for k in self.tick_kept if every or k[0] in changed]:   # new settings: counted again
            del self.tick_kept[key]
        for k in [k for k, v in self.chart_since.items() if every or v[0][0] in changed]:   # from now (a new load)
            del self.chart_since[k]
        for sym in [x for x in self.standby if every or x in changed]:       # and the old settings alert no more
            del self.standby[sym]
        for key, e in list(self.entries.items()):   # rebuilt with the new settings
            if every or key[0] in changed:
                e.dropped = True
                for client, sid in list(e.subs):
                    client.amt_entries.pop(sid, None)
                    client.put({"type": "amt_reset", "id": sid})
                del self.entries[key]
        self._reset_replays(lambda sym: every or sym in changed)
        self.ensure_alert_runners()

    def _reset_replays(self, hit, keep: bool = False):
        """Replaying charts whose symbol `hit` takes: their runners go, and they are told to ask again (one loading
        meanwhile is not kept: replay_gen). keep: their Value From Ticks sessions go on to the runners they load next
        (a prior-day job, a gap fill); else (new settings) counted again."""
        for sym in {*self.m.instruments, *(rp.want[0] for rp in self.replays.values())}:
            if hit(sym):
                self.replay_gen[sym] = self.replay_gen.get(sym, 0) + 1
        if not keep:
            for k in [k for k, v in self.replay_ticks.items() if hit(v[0][0])]:
                del self.replay_ticks[k]
        for (client, sid), rp in list(self.replays.items()):
            if hit(rp.want[0]):
                del self.replays[(client, sid)]
                client.amt_state.pop(sid, None)
                client.put({"type": "amt_reset", "id": sid})

    def reload(self, sym: str):
        """History of `sym` changed: its engines load again (charts are told to ask anew), the prior days' values
        from trades worked out again."""
        self.pd_gen[sym] = self.pd_gen.get(sym, 0) + 1
        self.pd_failed.pop(sym, None)
        self.pd_mem.pop(sym, None)
        try:
            with self.m.store.db:
                self.m.store.db.execute("DELETE FROM vp_cache WHERE key LIKE ?", (f"amtpd%|{sym}|%",))
        except sqlite3.Error:
            log.exception("AMT %s: prior-day values not cleared", sym)
        self._rebuild(sym)

    def _rebuild(self, sym: str, replays: bool = True):
        """Every runner of `sym` loads again (history or prior-day values changed), each keeping its Value From Ticks
        sessions for the charts on it (tick_kept, replay_ticks): NinjaTrader's chart would have run on with them."""
        shown = self._shown()
        for key, e in list(self.entries.items()):
            if key[0] != sym:
                continue
            if key in shown and not e.error and (w := e.runner.tick_windows()):
                self.tick_kept[key] = (time.monotonic(), e.runner.tick_params(), w)
            if key == self.alert_key(sym) and e.ready.is_set() and not e.error and self.alerting(sym):
                self.standby[sym] = e
            e.dropped = True
            for client, sid in list(e.subs):
                client.amt_entries.pop(sid, None)
                client.put({"type": "amt_reset", "id": sid})
            del self.entries[key]
        if replays:
            self._reset_replays(lambda s: s == sym, keep=True)
        self.ensure_alert_runners()

    def _clean_alert_cfg(self):
        """Each symbol's stored alert settings checked as set_alerts checks them now: one stored before that check
        (Infinity, from a version that let it in) broke /api/amt and every page's AMT message; its own settings go
        (the symbol's starting ones), the on/off stays."""
        cfg = self.alert_cfg if isinstance(self.alert_cfg, dict) else {}
        out = {}
        for sym, c in cfg.items():
            if not isinstance(c, dict):
                continue
            c = dict(c)
            if "set" in c:
                try:
                    c["set"] = self.chart_settings(c["set"], sym)[1]
                except (ValueError, KeyError, TypeError, OverflowError):
                    del c["set"]
            if not isinstance(c.get("minutes", 1), int) or isinstance(c.get("minutes"), bool):
                c.pop("minutes", None)
            out[sym] = c
        if out != cfg:
            self.m.store.set_setting("amt_alerts", out)
        self.alert_cfg = out

    def set_alerts(self, symbol: str, on: bool, minutes: int | None = None, own: dict | None = None):
        """Per symbol on/off; how levels alert (Cross, Zone Entry, Both) is the Level alert mode setting. Switched on
        from a chart: its minutes and own settings (None: the symbol's starting ones), which the alerts run on
        (alert_run). ValueError for settings that don't check, switching on; switching off, they are left out (a
        chart's stale ones never keep the alerts on)."""
        if own is not None:   # checked and stored as the check leaves them
            try:
                own = self.chart_settings(own, symbol)[1]
            except (ValueError, KeyError, TypeError, OverflowError):
                if on:
                    raise
                own = None
        was = self.alerting(symbol)
        self.standby.pop(symbol, None)
        self.alert_cfg[symbol] = {"on": bool(on), **({"minutes": int(minutes)} if minutes else {}),
                                  **({"set": own} if own is not None else {})}
        self.m.store.set_setting("amt_alerts", self.alert_cfg)
        e = self.entries.get(self.alert_key(symbol))
        if on and not was and e and e.ready.is_set() and not e.error:
            # turned back on, they start afresh, as a load does: nothing from while they were off is sent (the bars
            # it took meanwhile and the forming one count as sent, and the levels are watched from the next price on)
            r = e.runner
            r.fresh(r.commit_ready() + r.preview()[1])
            r.tracker.Reset()
        self.ensure_alert_runners()

    def alerting(self, symbol: str) -> dict | None:
        cfg = self.alert_cfg.get(symbol)
        return cfg if cfg and cfg.get("on") else None

    def ensure_alert_runners(self):
        for sym in self.m.instruments:
            if self.alerting(sym) and self.alert_key(sym) not in self.entries:
                asyncio.get_running_loop().create_task(self._safe_entry(sym))

    async def _safe_entry(self, sym):
        minutes, sig, alert = self.alert_run(sym)
        try:
            await self.entry(sym, minutes, sig, alert)
        except Exception:
            log.exception("AMT runner %s %sm failed", sym, minutes)

    # --- runners --------------------------------------------------------------------------------------
    def _levels(self, runner: AmtRunner, sym: str):
        """The symbol's daily levels by NinjaTrader's order: levels file or link, else the pasted line (daily_levels.py)."""
        try:
            levels, date, _src = self.m.daily.current(sym)
        except ValueError as e:
            runner.levels_error = f"Pasted levels rejected; no daily levels loaded. {e}"
            runner.set_level_list([], None, None)
            return
        tk = self.m.last_tick.get(sym)
        runner.set_level_list(levels, date, tk.price if tk else None)

    async def entry(self, sym: str, minutes: int, sig: str = "", overrides: dict | None = None,
                    raise_error: bool = True) -> AmtEntry:
        """The runner for a symbol, its minutes and a chart's own settings (sig and overrides from chart_settings;
        none: the symbol's starting settings). A load that failed raises its error, or comes back with it (raise_error
        False)."""
        key = (sym, minutes, sig) if sig else (sym, minutes)
        e = self.entries.get(key)
        if e is None:
            runner = self.new_runner(sym, minutes, overrides if sig else None)
            runner.pd_values = self._pd_cached(sym)
            runner.tick_kept = self._kept_ticks(key, runner)
            self._levels(runner, sym)
            e = self.entries[key] = AmtEntry(runner)
            # same cutoff scheme as chart history: stored minutes before the current one, live after
            self.m.flush_for_read()
            split = int(time.time() * 1000) // 60_000 * 60_000
            if runner.wants_trades():   # the forming minute's trades from before it existed (the load reads to split)
                e.pending_trades = [Tick(sym, ts, px, sz, 0) for ts, px, sz in self.m.store.db.execute(
                    "SELECT ts, price, size FROM ticks WHERE symbol=? AND ts>=? AND size>0 ORDER BY ts", (sym, split))]
            try:
                t0 = time.perf_counter()
                e.runner = runner = await workers.run("study", workers.load_amt, self.m.store.path, runner, sym, split)
                self._levels(runner, sym)   # levels that changed while it loaded
                for b in list(self.m.m1[sym].bars):
                    if b.t >= split:
                        runner.add_minute(b)
                for b in e.pending:
                    runner.add_minute(b)
                for tk in e.pending_trades:
                    if tk.ts >= split:
                        runner.add_trade(tk.ts, tk.price, tk.size)
                e.pending = []
                e.pending_trades = []
                runner.commit_ready()
                # the forming bar's notices count as sent: history never alerts, and the runner this one replaces (a
                # restart, a gap fill, new settings) may have sent them already
                runner.fresh(runner.preview()[1])
                log.info("AMT %s %sm ready in %.1fs", sym, minutes, time.perf_counter() - t0)
                self._want_pd(sym, runner)
            except Exception as ex:
                log.exception("AMT history failed for %s %sm", sym, minutes)
                e.error = str(ex)
                if self.entries.get(key) is e:   # not a runner that replaced it while this one loaded
                    del self.entries[key]
            finally:
                e.ready.set()
                if key == self.alert_key(sym) and (e.error or self.entries.get(key) is e):
                    self.standby.pop(sym, None)   # it alerts from now on (or none will: the load failed)
        await e.ready.wait()
        if e.error and raise_error:
            raise RuntimeError(e.error)
        return e

    def _shown(self) -> set:
        """The runners' keys that charts show (or ask again for after a reload)."""
        return {v[0] for (client, _), v in self.chart_since.items() if not client.closed}

    def _kept_ticks(self, key: tuple, runner: AmtRunner) -> list:
        """The Value From Ticks sessions the runner of `key` takes from the one it replaces (tick_kept): those the
        oldest chart still on it has, counted from that chart's load and each close since (TickDays.seen), within the
        days the runner loads. None for a runner no chart shows: it counts them afresh, as a new load."""
        kept = self.tick_kept.pop(key, None)
        since = [v[1] for (client, _), v in self.chart_since.items() if v[0] == key and not client.closed]
        if not kept or not since or kept[1] != runner.tick_params():
            return []
        oldest = ct(int(time.time() * 1000) - history_days(runner.S) * 86_400_000)
        return [w for w in TickDays.seen(kept[2], kept[1], ct(min(since))) if w[1] > oldest]

    # --- hooks from Market ---------------------------------------------------------------------------
    def on_minute(self, sym: str, b: Bar):
        for key, e in self.entries.items():
            if key[0] == sym:
                if e.ready.is_set():
                    e.runner.add_minute(b)
                else:
                    e.pending.append(b)

    def on_trade(self, tk: Tick):
        """Every trade (live or a gap fill's, in order): the runners drawing their current profile from trades."""
        for key, e in self.entries.items():
            if key[0] == tk.symbol and e.runner.wants_trades():
                if e.ready.is_set():
                    e.runner.add_trade(tk.ts, tk.price, tk.size)
                else:
                    e.pending_trades.append(tk)

    def on_live_tick(self, tk: Tick):
        if tk.symbol in self.roll_pending:   # the new contract's first trade: its alerts held from now
            self.roll_pending.discard(tk.symbol)
            self.roll_hold[tk.symbol] = int(time.time() * 1000) + ROLL_HOLD_MS
        cfg = self.alerting(tk.symbol)
        if not cfg:
            return
        e = self.entries.get(self.alert_key(tk.symbol))
        if not (e and e.ready.is_set() and not e.error):
            e = self.standby.get(tk.symbol)   # loading again: the runner it replaces watches meanwhile
        if e and e.ready.is_set() and not e.error:
            self._notify(tk.symbol, e.runner.fresh(e.runner.observe(tk.price, self.settings(tk.symbol).LevelAlertMode)), tk.price)

    def levels_changed(self, sym: str):
        for key, e in self.entries.items():
            if key[0] == sym and e.ready.is_set():
                self._levels(e.runner, sym)
                e.sig = None

    # --- chart subscriptions --------------------------------------------------------------------------
    async def subscribe(self, client, sid: int, sym: str, minutes: int, overrides: dict | None = None,
                        until: int | None = None):
        """A chart showing the Toolkit, on its own settings (overrides: all that differ from NinjaTrader's defaults;
        None: the symbol's starting ones). until: bar replay's clock, the Toolkit as it stood then (replay())."""
        if sym not in self.m.instruments:
            raise ValueError(f"unknown symbol {sym}")
        if until is not None:   # a replaying chart (one replaying up to the present: as of now, its forming bar's end ahead)
            minutes = self.engine_minutes(sym, minutes, self.chart_settings(overrides, sym)[1])
            return await self.replay(client, sid, sym, minutes, overrides, max(0, min(int(until), int(time.time() * 1000))))
        asked = client.amt_asked[sid] = object()   # a newer request, or amt_off, while this one loads: this one is dropped
        at = int(time.time() * 1000) // 60_000 * 60_000   # its load, as a runner's (the minute it asks in)
        sig, mine = self.chart_settings(overrides, sym)
        minutes = self.engine_minutes(sym, minutes, mine)
        e = await self.entry(sym, minutes, sig, mine, raise_error=False)
        if e.error and not e.dropped:
            raise RuntimeError(e.error)
        if client.closed or client.amt_asked.get(sid) is not asked or not (sid in client.subs or sid in client.loading):
            return
        # New settings for the symbol or a gap fill dropped the runner while it loaded (loaded or failed), before this
        # chart was on it to be told: told now, as the charts on it were, it asks again with the settings as they are now
        # (its own ones came on top of the old starting ones).
        if self.entries.get((sym, minutes, sig) if sig else (sym, minutes)) is not e:
            client.put({"type": "amt_reset", "id": sid})
            return
        self.m.note_recent("amt", [sym, minutes])
        key = (sym, minutes, sig) if sig else (sym, minutes)
        had = self.chart_since.get((client, sid))
        self.unsubscribe(client, sid)
        e.subs.add((client, sid))
        client.amt_entries[sid] = e
        # asked again after a reload (amt_reset), the chart runs on from its own load; else it loads it now
        self.chart_since[(client, sid)] = had if had and had[0] == key else (key, at)
        client.put(self._message(e, client, sid, full=True))

    async def replay(self, client, sid: int, sym: str, minutes: int, overrides: dict | None, until: int):
        """A replaying chart's Toolkit at the replay clock (to its minute): its runner carried forward to it, or loaded
        from the stored minutes before it (the first ask, a jump back, other settings). One message per ask: the
        chart asks again as the replay steps. Its daily levels are those of the replay's day (DailyLevels.as_of)."""
        sig, mine = self.chart_settings(overrides, sym)
        want, cut = (sym, minutes, json.dumps(mine, sort_keys=True)), until // 60_000 * 60_000
        asked = client.amt_asked[sid] = object()
        lock = self.replay_locks.setdefault((client, sid), asyncio.Lock())
        async with lock:
            if client.closed or client.amt_asked.get(sid) is not asked:   # a newer ask came while this one waited
                return
            gen = self.replay_gen.get(sym, 0)
            rp = self.replays.get((client, sid))
            full = rp is None or rp.want != want or cut < rp.upto or rp.gen != gen
            self.m.flush_for_read()
            if full:
                runner = self.new_runner(sym, minutes, mine if sig else None)
                runner.pd_values = self._pd_cached(sym)
                kept = self.replay_ticks.get((client, sid))
                if kept and kept[0] == want and cut >= kept[1] and kept[2] == runner.tick_params():
                    runner.tick_kept = kept[3]   # loaded again, not gone back: the sessions its run had
                self._replay_levels(runner, sym, cut)
                t0 = time.perf_counter()
                runner = await workers.run("study", workers.load_amt, self.m.store.path, runner, sym, cut)
                rp = ReplayAmt(runner, want, cut, gen)
                log.info("AMT %s %sm replay at %s loaded in %.1fs", sym, minutes, datetime.fromtimestamp(cut / 1000, sessions.CT)
                         .strftime("%Y-%m-%d %H:%M"), time.perf_counter() - t0)
            elif cut > rp.upto:
                await asyncio.to_thread(feed_minutes, self.m.store.path, rp.runner, sym, rp.upto, cut)
                rp.upto = cut
                self._replay_levels(rp.runner, sym, cut)
            if rp.runner.pd_missing:   # prior days from minutes until their trades are worked out (then it loads again)
                self._want_pd(sym, rp.runner)
            now_asked = client.amt_asked.get(sid)
            if client.closed or now_asked is None:   # amt_off, or the chart went
                return
            if self.replay_gen.get(sym, 0) != gen:   # new settings or a gap fill meanwhile: from what it read, not kept
                self.replays.pop((client, sid), None)
                if now_asked is asked:
                    client.amt_asked.pop(sid, None)
                    client.amt_state.pop(sid, None)
                    client.put({"type": "amt_reset", "id": sid})
                return
            if (client, sid) not in self.replays:   # a page's runners: the oldest go past REPLAY_PER_PAGE
                mine_ = [k for k in self.replays if k[0] is client]
                for k in mine_[:max(0, len(mine_) - REPLAY_PER_PAGE + 1)]:
                    del self.replays[k]
                    client.amt_state.pop(k[1], None)
            self.replays[(client, sid)] = rp   # kept for the newer ask waiting, if there is one: it carries on from here
            self.replay_ticks[(client, sid)] = (want, rp.upto, rp.runner.tick_params(), rp.runner.tick_windows())
            if now_asked is not asked:
                return
            client.amt_asked.pop(sid, None)
            e = client.amt_entries.pop(sid, None)   # it showed the live Toolkit: no more
            self.chart_since.pop((client, sid), None)
            if e:
                e.subs.discard((client, sid))
            r = rp.runner
            r.commit_ready()
            r.preview()
            if full:
                client.amt_state.pop(sid, None)
            client.put(self._message(rp, client, sid, full=full, profile=True))

    def _replay_levels(self, runner: AmtRunner, sym: str, at: int):
        try:
            levels, date, _src = self.m.daily.as_of(sym, at)
        except ValueError:
            levels, date = [], None
        runner.set_level_list(levels, date, None)

    def unsubscribe(self, client, sid: int):
        self.chart_since.pop((client, sid), None)
        self.replays.pop((client, sid), None)
        self.replay_ticks.pop((client, sid), None)
        self.replay_locks.pop((client, sid), None)
        client.amt_asked.pop(sid, None)
        e = client.amt_entries.pop(sid, None)
        client.amt_state.pop(sid, None)
        if e:
            e.subs.discard((client, sid))
            if not e.subs:
                e.idle_since = time.monotonic()

    def _message(self, e: AmtEntry | ReplayAmt, client, sid: int, full: bool, profile: bool = False) -> dict:
        """Lines and dashboard every update; the developing profile at most every PROFILE_EVERY_S (profile: now);
        past profiles, past days' lines and the 80% fill runs only when they change."""
        r = e.runner
        now = time.monotonic()
        st = client.amt_state.get(sid) or {"past": -1, "hist": -1, "samples": -1, "profile_at": 0.0}
        profile = profile or full or now - st["profile_at"] >= PROFILE_EVERY_S
        data = r.payload(full or st["past"] != r.past_rev, full or st["hist"] != r.history_rev,
                         full or st["samples"] != r.samples_rev, profile, None if full else st.get("vwap"))
        sent = {}
        for k in ("lines", "zones", "dash", "warning"):     # unchanged parts are not resent
            h = hash(json.dumps(data.get(k), separators=(",", ":")))
            sent[k] = h
            if not full and st.get(k) == h:
                data.pop(k, None)
        if data.get("ready"):
            client.amt_state[sid] = {"past": r.past_rev, "hist": r.history_rev, "samples": r.samples_rev, "vwap": r.vwap_total,
                                     "profile_at": now if profile else st["profile_at"], **sent}
        else:   # no bars yet, so nothing of them was sent: the first ready message brings it all (the style too)
            client.amt_state.pop(sid, None)
        return {"type": "amt", "id": sid, "symbol": r.symbol, "minutes": r.minutes, "full": full, "data": data}

    # --- alerts ------------------------------------------------------------------------------------------
    def rolled(self, sym: str):
        """`sym` moved to another contract: its levels are watched from the new contract's prices on, and no notice is
        sent for ROLL_HOLD_MS from the new contract's first trade (counted as sent): its bars before it are the old
        contract's (a roll comes in the break before the open: the hold starts when trading does)."""
        self.roll_pending.add(sym)
        self.roll_hold[sym] = int(time.time() * 1000) + ROLL_HOLD_MS
        self.standby.pop(sym, None)
        e = self.entries.get(self.alert_key(sym))
        if e and e.ready.is_set() and not e.error:
            e.runner.tracker.Reset()

    def _notify(self, sym: str, notices: list, price: float):
        if not notices or filling(self.m, sym) or sym in self.roll_pending \
                or time.time() * 1000 < self.roll_hold.get(sym, 0):   # counted as sent all the same (fresh)
            return
        S = self.settings(sym)   # the symbol's storm cap
        times = self.alert_times.setdefault(sym, deque())
        now = int(time.time() * 1000)
        inst = self.m.instruments[sym]
        for n in notices:
            while times and times[0] < now - S.alertcap_window_min * 60_000:
                times.popleft()
            if len(times) < S.alertcap_max:
                self.cap_warned[sym] = False
            if S.alertcap_enable and len(times) >= S.alertcap_max:
                if not self.cap_warned.get(sym):
                    self.cap_warned[sym] = True
                    self._emit(sym, now, price, f"[{sym}] AMT alert storm cap reached ({S.alertcap_max}/"
                                                f"{S.alertcap_window_min}m). Further alerts suppressed.")
                continue
            if S.alertcap_enable:
                times.append(now)
            self._emit(sym, now, price, f"[{sym}] {n.Text} | close {amt.price_str(price, sym, inst.tick_size)}")

    def _emit(self, sym, now, price, text):
        ev = {"alert_id": None, "symbol": sym, "ts": now, "price": price, "message": text}
        try:
            ev["id"] = self.m.store.add_event(ev)
        except sqlite3.Error as e:   # the database can't be written (a full disk, another program's lock): sent all the same
            log.warning("AMT alert for %s not saved: %s", sym, e)
            ev["id"] = None
        self.m.broadcast({"type": "alert", "event": ev})
        asyncio.get_running_loop().create_task(self.m._deliver(ev))

    # --- the once-a-second loop ---------------------------------------------------------------------------
    async def loop(self):
        while True:
            await asyncio.sleep(STEP_S)
            try:
                self.step()
            except Exception:
                log.exception("AMT step failed")

    def step(self):
        now = int(time.time() * 1000)
        idle = sorted((e.idle_since, key) for key, e in self.entries.items()
                      if e.ready.is_set() and not e.subs and not (key == self.alert_key(key[0]) and self.alerting(key[0])))
        for i, (since, key) in enumerate(idle):
            if time.monotonic() - since > IDLE_S or i < len(idle) - IDLE_MAX:
                del self.entries[key]
        shown = self._shown()
        for key in [k for k, v in self.tick_kept.items() if k not in shown or time.monotonic() - v[0] > IDLE_S]:
            del self.tick_kept[key]   # no chart came back for them: a new load counts them again
        for key, e in list(self.entries.items()):
            if not e.ready.is_set() or e.error:
                continue
            sym = key[0]
            cfg = self.alerting(sym) if key == self.alert_key(sym) else None
            if not e.subs and not cfg:
                continue
            r = e.runner
            last = r.chart.bars[r.chart.keys[-1]][-1] if r.chart.keys else None
            sig = (len(r.chart.keys), r.next_commit, last.t if last else 0, last.c if last else 0,
                   last.v if last else 0, r.levels_error, id(r.levels))
            if sig == e.sig:
                continue
            e.sig = sig
            notices = r.commit_ready()
            frame, pn = r.preview()
            if r.pd_missing:
                self._want_pd(sym)
            tk = self.m.last_tick.get(sym)
            if cfg and frame is not None and tk and now - tk.ts < LIVE_BAR_AGE_MS:   # live data only
                self._notify(sym, r.fresh(notices + pn), tk.price)
            for client, sid in list(e.subs):
                client.put(self._message(e, client, sid, full=False))

    # --- the prior day's value from trades ----------------------------------------------------------------
    def _pd_cached(self, sym: str) -> dict:
        out = {}
        try:
            for key, data in self.m.store.db.execute("SELECT key, data FROM vp_cache WHERE key LIKE ?", (f"{PD_KEY}|{sym}|%",)):
                _, _, b, e = key.split("|")
                out[(int(b), int(e))] = [tuple(x) for x in json.loads(data)]
        except (sqlite3.Error, ValueError):
            log.exception("AMT %s: prior-day values not read", sym)
        out.update(self.pd_mem.get(sym, {}))
        return out

    def _nt_feed(self, sym: str):
        """The NinjaTrader add-on, when it supplies `sym` and can send the data provider's tick history."""
        if sym in getattr(self.m, "trade_owner", {}) or not hasattr(self.m, "gapfill"):
            return None
        return next((f for f in getattr(self.m, "feed_objs", []) if getattr(f, "name", "") == "ninjatrader"
                     and hasattr(f, "history_ok") and f.history_ok()), None)

    async def _nt_value(self, sym: str, b: int, e: int, feed) -> dict | None:
        """The session's trades from NinjaTrader's tick history (as the NinjaTrader AMT Toolkit downloads them): the
        trades recorded here can differ from them by a few hundred contracts, enough to move a value area edge a few
        ticks where the profile is thin. Price -> volume, or None."""
        gf = self.m.gapfill
        fut = asyncio.get_running_loop().create_future()
        line = gf.ask_value(sym, b, e, fut)
        if line is not None and not feed.ask_history(line):
            gf.fail_values(sym)
        try:
            got = await asyncio.wait_for(asyncio.shield(fut), PD_NT_WAIT_S)
        except asyncio.TimeoutError:
            got = None
            w = gf.windows.get(sym)
            if w is not None and w.future is fut:   # its answer, when it comes, is dropped (gave_up: till its Z, nothing
                gf.gave_up(sym)                    # else asked for it: the add-on's lines name the symbol only)
            if not fut.done():
                fut.set_result(None)
        if not got:
            return None
        stored = self.m.store.db.execute("SELECT SUM(v) FROM bars_1m WHERE symbol=? AND t>=? AND t<?", (sym, b, e)).fetchone()[0]
        if stored and sum(got.values()) < 0.9 * stored:
            log.info("AMT %s: NinjaTrader's trades for %s short of the stored minutes; the stored trades used", sym, b)
            return None   # part of the session only
        return got

    def _want_pd(self, sym: str, loaded: AmtRunner | None = None):
        """Recent prior days the runners of `sym` took from minutes as their trades weren't worked out yet: worked out in
        a worker and kept (vp_cache), then the runners load again with them. loaded: one just loaded (not ready yet)."""
        if sym in self.pd_busy or time.monotonic() - self.pd_failed.get(sym, -1e9) < PD_RETRY_S:
            return
        missing = set(loaded.pd_missing) if loaded else set()
        for key, e in self.entries.items():
            if key[0] == sym and e.ready.is_set() and not e.error:
                missing |= e.runner.pd_missing
        if missing:
            self.pd_busy.add(sym)
            asyncio.get_running_loop().create_task(self._fill_pd(sym, sorted(missing)))

    async def _fill_pd(self, sym: str, windows: list):
        gen = self.pd_gen.get(sym, 0)
        try:
            self.m.flush_for_read()
            dv = self.m.store.data_version()
            t0 = time.perf_counter()
            recent = time.time() * 1000 - PD_NT_DAYS * 86_400_000
            from_nt = {}
            for b, e in windows:
                if e > recent and (feed := self._nt_feed(sym)) is not None:
                    if (got := await self._nt_value(sym, b, e, feed)) is not None:
                        from_nt[(b, e)] = sorted(got.items())
            rest = [w for w in windows if w not in from_nt]
            done = await workers.run("study", pd_job, self.m.store.path, sym, rest, self.m.instruments[sym].tick_size) if rest else {}
            if self.pd_gen.get(sym, 0) != gen or self.m.store.data_version() != dv:
                return   # the history changed meanwhile: worked out again when a runner asks
            # kept: NinjaTrader's, and the older ones from the trades here; a recent one from the trades here stays in
            # memory only, so NinjaTrader is asked for it again after a restart
            keep = {**{w: v for w, v in done.items() if w[1] <= recent}, **from_nt}
            self.pd_mem.setdefault(sym, {}).update({w: v for w, v in done.items() if w[1] > recent})
            with self.m.store.db:
                self.m.store.db.executemany("INSERT OR REPLACE INTO vp_cache VALUES (?, ?)",
                                            [(f"{PD_KEY}|{sym}|{b}|{e}", json.dumps(v)) for (b, e), v in keep.items()])
            log.info("AMT %s: prior-day value from trades for %d days (%d from NinjaTrader) in %.1fs", sym,
                     len(windows), len(from_nt), time.perf_counter() - t0)
            asked = set(windows)
            if any(key[0] == sym and asked & e.runner.pd_missing for key, e in self.entries.items()):
                self._rebuild(sym)   # replaying charts too: they load again with the trades
            else:   # only replays asked (a past date's sessions): the live runners and their charts go on as they are
                self._reset_replays(lambda s: s == sym, keep=True)
        except Exception:
            log.exception("AMT %s: prior-day values from trades failed", sym)
            self.pd_failed[sym] = time.monotonic()
        finally:
            self.pd_busy.discard(sym)

    def health(self) -> dict:
        name = lambda k: f"{k[0]} {k[1]}m" + (f" own {hashlib.sha1(k[2].encode()).hexdigest()[:6]}" if len(k) > 2 else "")   # noqa: E731
        return {name(k): {"subs": len(e.subs), "ready": e.ready.is_set(), "error": e.error,
                          "alerts": bool(self.alerting(k[0])) and k == self.alert_key(k[0])}
                for k, e in self.entries.items()}
