"""The AMT Toolkit's daily levels, per symbol, from where NinjaTrader's Toolkit reads them:

1. "Levels File or Link" (the Toolkit's LevelsFile setting): a file path, or an http(s) link. {root} becomes the
   symbol's root; micros read their parent (MES reads ES). A Windows path under Documents
   (C:\\Users\\<you>\\Documents\\..., its OneDrive\\Documents, %USERPROFILE%\\Documents\\...) reads the Documents folder the
   container mounts; run on Windows without Docker, the server opens any path as it is. With the setting blank,
   Documents\\Tatanka\\levels\\{root}.txt is used if it exists.
2. The symbol's pasted line (the Plan tab; NinjaTrader's "Paste Levels Here"), as TradingView takes it:
   "2026-10-15, pivot, prices..." or the Pivot Level / Resistance Levels / Support Levels block.

Each symbol's levels are kept as they come (levels_kept: their trade date and the time each version was first seen; a
row of none when they go), so bar replay shows the levels the chart had at a past moment (as_of): a levels file, link
or pasted line holds only the current day.

A file is re-read when it changes (checked every 5 s). A link is asked at once when the setting is set or changed
(and when the server starts), then on the NinjaTrader Toolkit's schedule (LevelLink.NextAsk, next_ask) while the
market is open, and not while it is shut: until the levels of the latest 8:15 or 16:45 CT publish are in hand, every
15 s for 10 minutes and then every minute to the end of the hour; for 30 minutes after a new answer, every 2 minutes;
otherwise every 30 minutes. Failures back off from 1 to 15 minutes (1 at most while a publish is due); a link the
levels service says is not active, or has nothing for the symbol yet, is asked every 30 minutes. A link that has not
answered since it was set or the server started is asked again after a failure even while the market is shut. An
answer that has not changed (ETag, 304) counts as an answer. A file or link that fails keeps the last good levels and
says why; at a start, until it is read or answers, those are the ones it gave last (levels_kept), as NinjaTrader's
Toolkit starts from its last-good cache. A file or link newly set shows only its own levels, none until it gives some.
The Daily levels study, level alerts and /api/levels take these levels too (levels.py), made again on every change
(Market.levels_changed)."""
import json
import logging
import ntpath
import sqlite3
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import amt, sessions, stopping

log = logging.getLogger(__name__)
MICRO_PARENT = {"MES": "ES", "MNQ": "NQ", "MCL": "CL", "MGC": "GC"}   # MathEx.LevelsRoot
DEFAULT_FILE = r"%USERPROFILE%\Documents\Tatanka\levels\{root}.txt"
FILE_CHECK_S = 5
# The NinjaTrader Toolkit's LevelLink: the levels are published at 8:15 CT (RTH) and 4:45 PM CT (Globex); the levels
# site marks its own answers (a 403 or 404 without the mark came from something in between: retried as a failure).
PUBLISH_TIMES = (timedelta(hours=8, minutes=15), timedelta(hours=16, minutes=45))
SERVICE_HEADER = "X-Tatanka-Levels"
# 24-hour clock only ("Published: 2026-10-15 16:45 CT"); a 12-hour time is ignored rather than misread
PUBLISHED_LINE = re.compile(r"^[ \t]*Published[ \t]*:[ \t]*(\d{4}-\d{2}-\d{2})[ T]+(\d{1,2}:\d{2})(?::\d{2})?"
                            r"(?![\d:]|[ \t]*[AaPp]\.?[Mm]\b)", re.M | re.I)
NONE = datetime.min
KEPT_DAYS = 4             # bar replay: kept levels for a trade date at most this many days before (a weekend, a holiday)
DOCS_RE = re.compile(r"^(?:%USERPROFILE%|[A-Za-z]:\\Users\\[^\\]+)\\(?:OneDrive[^\\]*\\)?Documents\\(.+)$", re.I)
WINDOWS = os.name == "nt"   # the server runs on Windows itself (no Docker)


def levels_root(sym: str) -> str:
    return MICRO_PARENT.get(sym.upper(), sym.upper())


def local_path(spec: str, docs_dir: Path | None) -> Path | None:
    """A levels file setting as a path this server can open, or None (a Windows path outside Documents)."""
    spec = spec.strip().strip('"')
    if docs_dir is None and WINDOWS:   # not in a container: the path as it is
        return Path(os.path.expanduser(ntpath.expandvars(spec)))
    m = DOCS_RE.match(spec)
    if m:
        return docs_dir / Path(*m.group(1).split("\\")) if docs_dir else None
    if re.match(r"^[A-Za-z]:\\", spec):
        return None
    return Path(os.path.expanduser(spec))


def published(text: str) -> datetime:
    """LevelLink.Published: the levels' "Published: yyyy-MM-dd H:mm" time (CT), or NONE."""
    m = PUBLISHED_LINE.search(text or "")
    if not m or not (m[1] + m[2]).isascii():
        return NONE
    try:
        return datetime.strptime(f"{m[1]} {m[2]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return NONE


def _publishes_around(now: datetime):
    day = datetime(now.year, now.month, now.day)
    return [day + timedelta(days=d) + t for d in (0, 1) for t in PUBLISH_TIMES]


def _in_hand(at: datetime, stamp: datetime, changed: datetime) -> bool:
    """A publish's levels are in hand: published at most 30 minutes before its time, or changed since its window opened."""
    return stamp >= at - timedelta(minutes=30) or changed >= at - timedelta(minutes=1)


def next_ask(now: datetime, stamp: datetime, changed: datetime, failures: int, status: int) -> datetime:
    """LevelLink.NextAsk: when to ask next (CT), from the last answer's publish time (stamp), when its text last changed,
    the failures in a row and the last status."""
    wait = timedelta(minutes=30)
    quiet, due = status in (403, 404), False
    if not quiet:
        for at in _publishes_around(now):
            if at - timedelta(minutes=1) <= now < at + timedelta(minutes=60) and not _in_hand(at, stamp, changed):
                due = True
                wait = min(wait, timedelta(seconds=15) if now < at + timedelta(minutes=10) else timedelta(minutes=1))
    if not quiet and failures > 0:
        backoff = timedelta(minutes=min(15, 1 << min(4, failures - 1)))
        wait = min(backoff, timedelta(minutes=1)) if due else backoff
    elif not quiet and now - changed < timedelta(minutes=30):
        wait = min(wait, timedelta(minutes=2))
    nxt = now + wait
    if not quiet:   # wake for the next publish instead of sleeping through it
        for at in _publishes_around(now):
            if now < at - timedelta(minutes=1) < nxt and not _in_hand(at, stamp, changed):
                nxt = at - timedelta(minutes=1)
    return nxt


@dataclass
class LinkState:
    """A link's schedule (LevelLink's fields), from when it is set or changed."""
    etag: str = ""
    failures: int = 0
    status: int = 0
    stamp: datetime = NONE        # the answer's publish time (its Published line), when the text last changed
    changed: datetime = NONE      # when the text last changed (not the first answer)
    body: str = ""                # the last text, whatever it held
    answered: bool = False        # a first text came
    problem: str = ""             # why that text gave no levels: a 304 (the same text) keeps saying so, as NinjaTrader's
                                  # chart parses each text once and keeps its error until another comes


@dataclass
class Source:
    """What a symbol's levels came from, for the Plan tab."""
    origin: str = "none"          # file, link, paste, none
    where: str = ""               # the file or link (a link without its private part)
    text: str = ""                # the levels document as read
    date: datetime | None = None
    read_ms: int | None = None
    error: str = ""
    mtime: float | None = None
    next_ask_ms: int = 0          # a link's next ask (0: at once)
    url: str = ""                 # the link asked
    link: LinkState | None = None


def _redact(url: str) -> str:
    """For logs and the status line: the host and file name, never a path or query that may carry a key."""
    return re.sub(r"(https?://[^/]+/).*(/[^/]*)$", r"\1…\2", re.split(r"[?#]", url, maxsplit=1)[0])


def _left(s: Source) -> str:
    """What a failure leaves: the last good levels, or none yet."""
    return "keeping the last good levels." if s.text else "no levels from it yet."


def levels_text(levels: list, day: str) -> str:
    """Levels as the Pivot Level / Resistance Levels / Support Levels block parse_levels reads back the same (each
    kind's prices in their order, so the same names), with their date."""
    kind = {k: ", ".join(repr(float(x.Price)) for x in levels if x.Kind == k) for k in (0, -1, 1)}
    return f"Date: {day}\nPivot Level: {kind[0]}\nResistance Levels: {kind[-1]}\nSupport Levels: {kind[1]}\n"


class DailyLevels:
    def __init__(self, market):
        self.m = market
        self.src: dict[str, Source] = {}   # by levels root
        self.paste: dict[str, str] = dict(market.store.get_setting("amt_paste", {}) or {})   # by symbol

    # --- settings ---------------------------------------------------------------------------------------
    def spec(self) -> str:
        return str(self.m.amt.settings_value("LevelsFile") or "").strip()

    def docs_dir(self) -> Path | None:
        d = os.environ.get("CHART_DOCUMENTS_DIR") or "/import/documents"
        return Path(d) if Path(d).is_dir() else None

    def set_paste(self, sym: str, text: str):
        if text.strip():
            amt.parse_levels(text)   # raises ValueError with the Toolkit's message
            self.paste[sym] = text.strip()
        else:
            self.paste.pop(sym, None)
        self.m.store.set_setting("amt_paste", self.paste)

    # --- the levels for a symbol ----------------------------------------------------------------------------
    def current(self, sym: str) -> tuple[list, datetime | None, Source]:
        """(levels, date, source) by NinjaTrader's order: file or link, else the paste."""
        root = levels_root(sym)
        s = self.src.get(root)
        if s and s.text and s.origin in ("file", "link"):
            try:
                levels, date, _ = amt.parse_levels(s.text)
                if levels:
                    return levels, date, s
            except ValueError:
                pass
        if sym in self.paste:
            levels, date, _ = amt.parse_levels(self.paste[sym])
            return levels, date, Source(origin="paste", text=self.paste[sym], date=date, error=s.error if s else "")
        return [], None, Source(origin="none", error=s.error if s else "")

    def as_of(self, sym: str, at_ms: int) -> tuple[list, datetime | None, Source]:
        """The levels a past moment had (bar replay): the version kept last by then (overnight, the day before's until
        the new day's came), if it is for that moment's trade date or at most KEPT_DAYS before; else none. With no
        database (a stand-in market), the current ones if they are for that trade date."""
        day = amt.trading_date(datetime.fromtimestamp(at_ms / 1000, sessions.CT).replace(tzinfo=None))
        if getattr(getattr(self.m, "store", None), "db", None) is not None:
            return self.kept(sym, day, at_ms) or ([], None, Source(origin="none"))
        levels, date, s = self.current(sym)
        if levels and date is not None and date.date() == day.date():
            return levels, date, s
        return [], None, Source(origin="none")

    # --- the levels kept per day, for bar replay ---------------------------------------------------------
    def remember(self, now_ms: int | None = None):
        """Keeps each symbol's levels (a levels file, link or pasted line, with a date) for their trade date, when they
        differ from the last kept; when it has none of those any more (cleared, a file gone), a row of none for the
        trade date then. Called on every change of the levels and once at start."""
        now_ms = now_ms or int(time.time() * 1000)
        db = getattr(getattr(self.m, "store", None), "db", None)
        if db is None:
            return
        today = amt.trading_date(datetime.fromtimestamp(now_ms / 1000, sessions.CT).replace(tzinfo=None))
        for sym in self.m.instruments:
            try:
                levels, date, s = self.current(sym)
            except ValueError:   # a pasted line the Toolkit rejects: no levels
                levels, date, s = [], None, Source()
            if levels and date is not None and s.origin in ("file", "link", "paste"):
                day, data = date.strftime("%Y-%m-%d"), json.dumps([[x.Name, x.Price, x.Kind] for x in levels])
            else:
                day, data = today.strftime("%Y-%m-%d"), "[]"
            try:
                last = db.execute("SELECT date, levels FROM levels_kept WHERE symbol=? ORDER BY seen DESC LIMIT 1",
                                  (sym,)).fetchone()
                if (last is None and data != "[]") or (last is not None and (last[1] != data or (data != "[]" and last[0] != day))):
                    with db:
                        db.execute("INSERT OR REPLACE INTO levels_kept VALUES (?, ?, ?, ?, ?, ?)",
                                   (sym, day, now_ms, s.origin, s.where if s.origin in ("file", "link") else "", data))
            except sqlite3.Error as e:   # a full disk: kept at the next change
                log.warning("levels for %s %s not kept: %s", sym, day, e)

    def kept(self, sym: str, day: datetime, at_ms: int) -> tuple[list, datetime, Source] | None:
        """The version kept last by at_ms, if it is for `day` or at most KEPT_DAYS before (None: none, or a row of none)."""
        db = getattr(getattr(self.m, "store", None), "db", None)
        if db is None:
            return None
        try:
            row = db.execute("SELECT date, origin, source, levels FROM levels_kept WHERE symbol=? AND seen<=? AND date<=? "
                             "AND date>=? ORDER BY seen DESC LIMIT 1",
                             (sym, at_ms, day.strftime("%Y-%m-%d"), (day - timedelta(days=KEPT_DAYS)).strftime("%Y-%m-%d"))).fetchone()
        except sqlite3.Error:
            return None
        if row is None or row[3] == "[]":
            return None
        try:
            levels = [amt.Level(str(n), float(p), int(k)) for n, p, k in json.loads(row[3])]
            date = datetime.strptime(row[0], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None
        return levels, date, Source(origin=row[1], where=row[2], date=date)

    def status(self, sym: str) -> dict:
        try:
            levels, date, s = self.current(sym)
        except ValueError as e:
            return {"origin": "paste", "error": str(e), "paste": self.paste.get(sym, ""), "count": 0}
        return {"origin": s.origin, "where": s.where, "date": date.strftime("%Y-%m-%d") if date else None,
                "read_ms": s.read_ms, "error": s.error, "count": len(levels), "paste": self.paste.get(sym, ""),
                "spec": self.spec() or DEFAULT_FILE + " (if it exists)"}

    # --- reading files and links -------------------------------------------------------------------------
    async def run(self):
        first = True
        while True:
            try:
                await self.poll()
            except Exception:
                log.exception("daily levels poll failed")
            if first:   # the levels as they are at start, once looked at (a pasted line set before levels were kept;
                first = False   # not a row of none before a file's or link's last good levels are back)
                self.remember()
            await stopping.pause(FILE_CHECK_S)   # not after a stop a link's request swallowed (stopping.py)

    async def poll(self, now_ms: int | None = None):
        now_ms = now_ms or int(time.time() * 1000)
        spec = self.spec()
        roots = sorted({levels_root(s) for s in self.m.instruments})
        changed = set()
        for root in roots:
            target = (spec or DEFAULT_FILE).replace("{root}", root)
            link = bool(re.match(r"^https?://", target, re.I))
            s = self.src.get(root)
            before = (s.text, s.error) if s else ("", "")
            if s is None:   # the first look since the start: from the levels this file or link gave last
                s = self.src[root] = self._last_good(root, target, link)
            if link:
                await self._read_link(root, target, s, now_ms)
            else:
                self._read_file(target, s, now_ms, explicit=bool(spec))
            if s.text != before[0]:
                changed.add(root)
            if (s.text, s.error) != before:
                for sym in self.m.instruments:
                    if levels_root(sym) == root:
                        self.m.broadcast({"type": "amt_levels", "symbol": sym, "status": self.status(sym)})
        for sym in self.m.instruments:
            if levels_root(sym) in changed:
                self.m.amt.levels_changed(sym)
        if changed:
            self.m.levels_changed()   # the Daily levels study's and level alerts' too

    def _last_good(self, root: str, target: str, link: bool) -> Source:
        """A file's or link's Source at the start: the levels it gave last (the latest kept for the root with that
        origin and source), as NinjaTrader's LevelFileSource loads its last-good cache before it reads or asks."""
        origin, where = ("link", _redact(target)) if link else ("file", target)
        s = Source(origin=origin, where=where, url=target if link else "")
        db = getattr(getattr(self.m, "store", None), "db", None)
        syms = [x for x in self.m.instruments if levels_root(x) == root]
        if db is None or not syms:
            return s
        try:
            row = db.execute(f"SELECT date, seen, levels FROM levels_kept WHERE symbol IN ({','.join('?' * len(syms))}) "
                             "AND origin=? AND source=? AND levels!='[]' ORDER BY seen DESC LIMIT 1",
                             (*syms, origin, where)).fetchone()
            if row is None:
                return s
            text = levels_text([amt.Level(str(n), float(p), int(k)) for n, p, k in json.loads(row[2])], row[0])
            s.date = amt.parse_levels(text)[1]
        except (sqlite3.Error, ValueError, TypeError) as e:
            log.warning("last levels kept for %s not used: %s", root, e)
            return s
        s.text, s.read_ms = text, row[1]
        return s

    def _read_file(self, target: str, s: Source, now_ms: int, explicit: bool):
        docs = self.docs_dir()
        path = local_path(target, docs)
        held = s.origin == "file" and s.where == target   # the text held is this file's, not a link's or another file's
        if not held:   # only this file's levels from now on, none until it gives some
            s.text, s.date, s.read_ms = "", None, None
        s.origin, s.where = "file", target
        if path is None:
            s.error = (f"{target} is outside Documents: the server only sees the Documents folder (DOCUMENTS_DIR)."
                       if explicit else "")
            if not explicit:   # the default file, with no Documents folder to look in: the pasted line, as when it is
                s.text, s.origin = "", "none"   # not there (not the levels it gave before a restart)
            return
        try:
            st = path.stat()
        except OSError:   # read from the mounted Documents, which may not be the folder the path names (OneDrive's)
            seen = " in the Documents folder the server sees (DOCUMENTS_DIR)" if docs in path.parents else ""
            s.error = f"No levels file at {target}{seen}." if explicit else ""
            if not explicit:
                s.text, s.origin = "", "none"
            return
        if held and s.mtime == st.st_mtime and s.text:
            return
        try:
            text = path.read_text(encoding="utf-8-sig")
            levels, date, _ = amt.parse_levels(text)
            if not levels:
                raise ValueError("The levels file is empty.")
        except (OSError, UnicodeError, ValueError) as e:
            s.error = f"{target}: {e}. {_left(s).capitalize()}"
            s.mtime = st.st_mtime
            return
        s.text, s.date, s.read_ms, s.mtime, s.error = text, date, now_ms, st.st_mtime, ""

    async def _read_link(self, root: str, url: str, s: Source, now_ms: int):
        if s.origin != "link" or s.url != url:   # set or changed: only this link's levels from now on, none until it
            s.text, s.date, s.read_ms, s.link = "", None, None, None   # answers (not the file's or link's before)
        if s.link is None:   # ...or the server just started (from its last good levels): asked at once, its schedule
            s.next_ask_ms, s.link = 0, LinkState()   # from the start
        s.origin, s.where, s.url = "link", _redact(url), url
        if now_ms < s.next_ask_ms:
            return
        k, now = s.link, datetime.fromtimestamp(now_ms / 1000, sessions.CT).replace(tzinfo=None)
        groups = [self.m.instruments[x].calendar for x in self.m.instruments if levels_root(x) == root]
        # shut: the last levels stand, and it is asked at the first check once the market opens; one that has not
        # answered since it was set or the server started (a failed first ask) is asked on the failure back-off meanwhile
        shut = not any(sessions.is_open(now_ms, g) for g in groups)
        if s.next_ask_ms and shut and (k.answered or k.status in (403, 404)):
            return
        try:
            try:
                r = await self.m.http.get(url, timeout=15, follow_redirects=True,
                                          headers={"If-None-Match": k.etag} if k.etag else {})
            except Exception as e:   # network: keep the last good levels
                k.status, k.failures = 0, k.failures + 1
                s.error = f"Levels link unreachable ({type(e).__name__}): {_left(s)}"
                return
            code, headers = r.status_code, getattr(r, "headers", None) or {}
            # a 403 or 404 the levels service did not mark came from something in between: retried as a failure
            k.status = 502 if code in (403, 404) and headers.get(SERVICE_HEADER) != "1" else code
            if code == 304:
                k.failures, s.error = 0, k.problem
                return
            if code != 200:
                k.failures += 1
                s.error = (f"The levels link was refused (HTTP {code}): check the link." if code in (401, 403, 404, 410)
                           else f"Levels link unreachable (HTTP {code}): {_left(s)}")
                return
            k.failures, k.etag, text = 0, headers.get("ETag", "") or "", r.text
            if text != k.body or not k.answered:   # a new text: the publish it is (its Published line), or this answer
                if k.answered:
                    k.changed = now
                k.stamp = published(text)
                if k.stamp == NONE and k.answered:   # (a first answer without one may be yesterday's)
                    k.stamp = now
                k.body, k.answered = text, True
            try:
                levels, date, _ = amt.parse_levels(text)
                if not levels:
                    raise ValueError("no levels in the answer")
            except ValueError as e:   # keep the last good levels
                k.problem = s.error = f"Levels link unreachable ({type(e).__name__}): {_left(s)}"
                return
            k.problem = ""
            if text != s.text:
                s.text, s.date, s.read_ms = text, date, now_ms
            s.error = ""
        finally:
            s.next_ask_ms = now_ms + int((next_ask(now, k.stamp, k.changed, k.failures, k.status) - now).total_seconds() * 1000)
