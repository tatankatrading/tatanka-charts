"""Round 21's bug hunt, data and calendar: the older-history session cache, the dayvol check tool and the watchlist
details panel's day around holiday halts."""
import asyncio
import json
import re
import shutil
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from test_older_history import RTH, ticks
from test_review_fixes import build_market

import chartsrv.older as older_mod
from chartsrv import dayvol, details, sessions
from chartsrv.bars import make_series, parse_spec
from chartsrv.config import load_holidays
from chartsrv.models import Bar
from chartsrv.sessions import CT
from chartsrv.store import SCHEMA, Store

CONFIG = Path(__file__).resolve().parents[2] / "config"
THANKSGIVING = {"days": [{"date": "2026-11-26", "name": "Thanksgiving", "halt": {"default": "12:00", "energy": "13:30"}},
                         {"date": "2026-11-27", "name": "Day after Thanksgiving", "close": {"default": "12:15"}}]}


def ms(*a) -> int:
    return int(datetime(*a, tzinfo=CT).timestamp() * 1000)


def _wait(cond, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def _open_minutes(start, end, group="equity"):
    """Every minute the market trades in [start, end), with the price the test gives it."""
    t = start
    while t < end:
        if sessions.is_open(t, group):
            yield t
        t += 60_000


# ---- c37: the session older history keeps for the pages before it is let go once unused, not held for good ----------

def test_the_session_kept_for_older_pages_is_let_go_once_unused_for_its_keep_time(tmp_path, monkeypatch):
    """Paging a tick chart back keeps the session it built (every bar from its open) for the pages before it. That
    was only no longer reused after SESSION_KEEP_S: nothing let it go, so an idle worker held a busy session's 1t
    bars (a few hundred MB) until another session replaced them or the server stopped. Now a timer lets it go once
    it has gone unused that long, and a page served from it in the meantime keeps it on from then."""
    monkeypatch.setattr(older_mod, "SESSION_KEEP_S", 0.3)
    store = Store(tmp_path / "t.db")
    store.write(ticks(ms(2026, 10, 22, 17), ms(2026, 10, 23, 16), every=20_000), [])
    older_mod._last_session = {}
    rowid = store.max_tick_rowid()
    out = older_mod.build_older(store.path, "ES", parse_spec("10t"), 0.25, 2, RTH, False, "equity",
                                ms(2026, 10, 23, 15), 100, rowid)
    assert out["bars"] and older_mod._last_session.get("bars")
    kept = older_mod._last_session
    kept["at"] = time.monotonic() + 2   # used again (a page served from it sets this) two seconds on
    time.sleep(0.8)   # past the keep time from its build: kept, as it was used since
    assert older_mod._last_session is kept
    assert _wait(lambda: older_mod._last_session == {}), "the kept session is never let go"


# ---- c40: dayvol counts a holiday halt's session toward the next trade date, as the daily bars do -------------------

def _minutes_db(start, end, group="equity"):
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    bars = [Bar(t=t, o=100.0 + i % 7, h=101.0 + i % 7, l=99.0 + i % 7, c=100.0, v=10)
            for i, t in enumerate(_open_minutes(start, end, group))]
    db.executemany("INSERT INTO bars_1m (symbol, t, o, h, l, c, v) VALUES ('ES', ?, ?, ?, ?, ?, ?)",
                   [(b.t, b.o, b.h, b.l, b.c, b.v) for b in bars])
    return db, bars


def test_dayvol_counts_a_holiday_halts_session_toward_the_next_trade_date_as_the_1d_bar():
    """Thanksgiving's halt session (Wed 17:00 to Thu 12:00 CT) has no settlement: CME's, NinjaTrader's and this app's
    1D bar put it in Friday's. The check tool printed it as a Thursday row of its own (1140 minutes, which reads as
    missing data), so neither row matched a daily bar's volume."""
    sessions.set_calendar(THANKSGIVING)
    db, bars = _minutes_db(ms(2026, 11, 24, 17), ms(2026, 11, 30, 16))
    got = dayvol.daily(db, "ES", 10, ms(2026, 11, 30, 16), "equity")
    assert [(d, v, n) for d, v, n, _, _ in got] == [
        (date(2026, 11, 25), 13800, 1380), (date(2026, 11, 27), 22950, 2295), (date(2026, 11, 30), 13800, 1380)]
    s = make_series(parse_spec("1D"), 0.25, 2, group="equity")
    for b in bars:
        s.on_bar(b)
    assert [b.v for b in s.bars] == [v for _, v, _, _, _ in got]   # the app's own 1D bars, row for row
    fri = next(r for r in got if r[0] == date(2026, 11, 27))
    assert (fri[3], fri[4]) == (max(b.h for b in bars if b.t >= ms(2026, 11, 25, 17)),
                                min(b.l for b in bars if b.t >= ms(2026, 11, 25, 17)))
    # without a group (or a calendar), the sessions as they are, as before
    assert [d for d, *_ in dayvol.daily(db, "ES", 10, ms(2026, 11, 30, 16))] == [
        date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 30)]


def test_the_dayvol_tool_loads_the_servers_holidays_and_the_symbols_group(tmp_path, monkeypatch, capsys):
    """Run as `python -m chartsrv.dayvol ES`, it loads config/holidays.json and the instrument's Holidays group the
    way the server does (the list saved in the app before config/instruments.json)."""
    sessions.set_calendar(THANKSGIVING)   # (the config's hours for these days, to store the minutes the market trades)
    cfg = tmp_path / "config"
    cfg.mkdir()
    for f in ("holidays.json", "instruments.json"):
        shutil.copy(CONFIG / f, cfg / f)
    store = Store(tmp_path / "chart.db")
    store.write([], [("ES", b) for b in (Bar(t=t, o=100.0, h=101.0, l=99.0, c=100.0, v=10)
                                         for t in _open_minutes(ms(2026, 11, 24, 17), ms(2026, 11, 30, 16)))])
    store.close()
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_CONFIG_DIR", str(cfg))
    dayvol.main(["ES", "10000"])
    out = capsys.readouterr().out
    assert "Thu 2026-11-26" not in out and "Fri 2026-11-27        22,950     2295" in out
    db = sqlite3.connect(tmp_path / "chart.db")
    assert dayvol._group(db, cfg, "CL") == "energy" and dayvol._group(db, cfg, "ES") == "equity"
    assert dayvol._group(db, cfg, "ZZ") is None
    saved = {k: v for k, v in json.loads((cfg / "instruments.json").read_text()).items() if not k.startswith("_")}
    saved["ES"]["calendar"] = "metals"   # Connections > Instruments' Holidays, saved in the app: that one
    s = Store(tmp_path / "chart.db")
    s.set_setting("instruments", saved)
    s.close()
    assert dayvol._group(db, cfg, "ES") == "metals"


def test_the_dayvol_docs_give_the_holiday_rows_the_minutes_the_tool_prints():
    """With the halt's session folded in, the day after Thanksgiving (an early close) holds the most minutes of the
    year, not fewer than a full day as the docs said. The docs' minutes for a full day, that day and an early close on
    its own (Christmas Eve) are what the tool prints for ES with config/holidays.json."""
    sessions.set_calendar(load_holidays(CONFIG, date(2026, 11, 12)))
    db, _ = _minutes_db(ms(2026, 11, 22, 17), ms(2026, 12, 29, 16))
    rows = {d: n for d, _, n, _, _ in dayvol.daily(db, "ES", 100, ms(2026, 12, 29, 16), "equity")}
    assert rows[date(2026, 11, 27)] == 2295 and rows[date(2026, 12, 24)] == 1155 and rows[date(2026, 12, 23)] == 1380
    data_md = (CONFIG.parent / "docs" / "DATA.md").read_text()
    data_md = data_md[data_md.index("chartsrv.dayvol NQ 15"):data_md.index("## Databento")]
    for doc in (dayvol.__doc__, data_md):
        text = " ".join(doc.split())
        assert not re.search(r"Thanksgiving[^.;]*fewer|fewer[^.;]*Thanksgiving", text), text
        said = {k: int(re.search(k + r"[^.;]*?about (\d+)", text).group(1))
                for k in ("full Globex day", "day after Thanksgiving", "Christmas Eve")}
        assert said == {"full Globex day": 1380, "day after Thanksgiving": rows[date(2026, 11, 27)],
                        "Christmas Eve": rows[date(2026, 12, 24)]}, text


# ---- c41: the details panel's day after a holiday halt has the halt's session, as its 1D bar ------------------------

def test_the_details_panels_day_after_a_holiday_halt_has_the_halts_session_as_the_1d_bar(tmp_path, monkeypatch):
    """On the Friday after Thanksgiving the 1D bar (and the change, from Wednesday's settlement) has Thursday's halt
    session in it; the details panel's day began at Thursday's 17:00 open, so its open, high and low left that out
    and could sit all on one side of the price the change is measured from."""
    sessions.set_calendar(THANKSGIVING)
    m, _ = build_market(tmp_path, monkeypatch)

    async def yahoo(ticker, spec):
        raise ValueError("offline")
    monkeypatch.setattr(m.ext, "_yahoo", yahoo)
    bars = []

    def store(start, end, base):   # the market's minutes, stored as it trades them
        new = [Bar(t=t, o=base + 1, h=base + 2, l=base, c=base + 1, v=10) for t in _open_minutes(start, end)]
        m.store.write([], [("ES", b) for b in new])
        bars.extend(new)
    store(ms(2026, 11, 24, 17), ms(2026, 11, 25, 16), 8700)
    assert details._day_live(m, "ES", ms(2026, 11, 25, 12)) == {"o": 8701, "h": 8702, "l": 8700}   # a plain day
    store(ms(2026, 11, 25, 17), ms(2026, 11, 26, 16), 8760)
    # Thanksgiving afternoon, after the halt: its own session, as before
    assert details._day_live(m, "ES", ms(2026, 11, 26, 13)) == {"o": 8761, "h": 8762, "l": 8760}
    store(ms(2026, 11, 26, 17), ms(2026, 11, 27, 11), 9045)
    s = make_series(parse_spec("1D"), 0.25, 2, group=m.instruments["ES"].calendar)
    for b in bars:
        s.on_bar(b)
    d1 = s.bars[-1]
    for now in (ms(2026, 11, 27, 11), ms(2026, 11, 28, 10)):   # Friday, and the Saturday after
        day = asyncio.run(details.details(m, "ES", now))["day"]
        assert day == {"o": d1.o, "h": d1.h, "l": d1.l} == {"o": 8761, "h": 9047, "l": 8760}
