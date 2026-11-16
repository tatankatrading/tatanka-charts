"""Each trading day's volume, high and low from the stored 1-minute bars, to check against another platform's daily
bars (a NinjaTrader Daily chart's Data Box). Read-only. Docker, from the Tatanka Charts folder:
    docker compose exec chart python -m chartsrv.dayvol NQ 15
Without Docker, from its server folder: python -m chartsrv.dayvol NQ [days]

A trading day is CME's: 17:00 CT the evening before to 16:00 CT, and a holiday halt's session (Thanksgiving, MLK
Day, ...) counts toward the next one, as the daily bars (CME's, NinjaTrader's, this app's 1D) have it. Minutes counts
the 1-minute bars stored for it: a full Globex day is about 1380, and a day with a halt's session in it has that
session's too. The day after Thanksgiving is an early close that also holds Thanksgiving's session: about 2295 (ES, NQ).
An early close on its own (Christmas Eve) has fewer, about 1155 (ES, NQ). Far fewer means data is missing there."""
import argparse
import json
import time
from datetime import date, timedelta

from . import sessions
from .amt_runner import ct
from .config import load_holidays, load_instruments, load_settings
from .store import connect

DAY_MS = 86_400_000


def daily(db, symbol: str, days: int, now_ms: int,
          group: str | None = None) -> list[tuple[date, int, int, float, float]]:
    """(trade date, volume, minutes, high, low) for the last `days` trading days with bars. group: the symbol's holiday
    group, whose halts' sessions count toward the next trade date (the calendar sessions has loaded)."""
    out: dict[date, list] = {}
    since = now_ms - int((days * 1.6 + 5) * DAY_MS)
    for t, h, l, v in db.execute("SELECT t, h, l, v FROM bars_1m WHERE symbol=? AND t>=? ORDER BY t", (symbol, since)):
        d = sessions.fold_trade_date((ct(t) + timedelta(hours=7)).date(), group)   # 17:00 CT opens the next trade date
        x = out.setdefault(d, [0, 0, h, l])
        x[0] += v or 0
        x[1] += 1
        x[2], x[3] = max(x[2], h), min(x[3], l)
    return [(d, *x) for d, x in sorted(out.items())][-days:]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbol")
    ap.add_argument("days", nargs="?", type=int, default=15)
    a = ap.parse_args(argv)
    s = load_settings()
    try:   # the holidays the server loads (a file it can't read: CME's usual pattern, as there)
        sessions.set_calendar(load_holidays(s.config_dir))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        sessions.set_calendar(load_holidays(None))
    db = connect(s.db_path, readonly=True)
    sym = a.symbol.upper()
    rows = daily(db, sym, max(1, a.days), int(time.time() * 1000), _group(db, s.config_dir, sym))
    if not rows:
        print(f"no 1-minute bars stored for {sym}")
        return
    print(f"{sym}  trade date   volume        minutes   high         low")
    for d, v, n, h, l in rows:
        print(f"    {d:%a %Y-%m-%d}  {v:>12,}  {n:>7}   {h:>11,.2f}  {l:>11,.2f}")


def _group(db, config_dir, symbol: str) -> str | None:
    """The symbol's holiday group (Connections > Instruments' Holidays) from the instruments the server loads: the list
    saved in the app, else config/instruments.json's. None for one on neither."""
    class Saved:   # load_instruments' store, on this read-only connection
        def get_setting(self, key, default=None):
            r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(r[0]) if r else default
    try:
        inst = load_instruments(config_dir, Saved()).get(symbol)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return inst.calendar if inst else None


if __name__ == "__main__":
    main()
