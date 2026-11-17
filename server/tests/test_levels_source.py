"""The AMT Toolkit's daily levels by NinjaTrader's order (levels file or link, else the pasted line), when a levels
link is asked, the indicator settings API, and the NinjaTrader settings seed."""
import json
import os

import pytest

from chartsrv import daily_levels as dl
from chartsrv import indicators as ind
from test_review_fixes import build_market

LINE = "2026-01-15, 5000.00, 5020.00, 5040.00, 5060.00, 4980.00, 4960.00, 4940.00"   # invented


def test_paths_and_roots(tmp_path):
    assert dl.levels_root("MES") == "ES" and dl.levels_root("CL") == "CL"
    assert dl.local_path(r"C:\Users\you\Documents\Tatanka\levels\ES.txt", tmp_path) == tmp_path / "Tatanka" / "levels" / "ES.txt"
    assert dl.local_path(r"%USERPROFILE%\Documents\Tatanka\levels\NQ.txt", tmp_path) == tmp_path / "Tatanka" / "levels" / "NQ.txt"
    assert dl.local_path(r"D:\levels\ES.txt", tmp_path) is None


def test_file_beats_paste(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    (docs / "Tatanka" / "levels").mkdir(parents=True)
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    m, _ = build_market(tmp_path, monkeypatch)
    levels, date, src = m.daily.current("ES")
    assert src.origin == "none" and levels == []
    m.daily.set_paste("ES", LINE)
    levels, date, src = m.daily.current("ES")
    assert src.origin == "paste" and [x.Name for x in levels][:3] == ["PIVOT", "RES1", "RES2"]
    assert next(x for x in levels if x.Name == "SUPP1").Price == 4980.0
    with pytest.raises(ValueError):
        m.daily.set_paste("ES", "2026-01-15, 5000, 50,200")   # a thousands separator
    # the file in Documents\Tatanka\levels\{root}.txt wins; micros read their parent's
    (docs / "Tatanka" / "levels" / "ES.txt").write_text("2026-10-16, 7700.00, 7720.00, 7680.00\n")
    import asyncio
    asyncio.run(m.daily.poll())
    levels, date, src = m.daily.current("MES")
    assert src.origin == "file" and levels[0].Price == 7700.0 and date.day == 16
    st = m.daily.status("ES")
    assert st["origin"] == "file" and st["count"] == 3 and st["paste"] == LINE
    # a bad rewrite keeps the last good levels and says why
    p = docs / "Tatanka" / "levels" / "ES.txt"
    p.write_text("garbage")
    os.utime(p, (1, 1))
    asyncio.run(m.daily.poll())
    levels, _, src = m.daily.current("ES")
    assert levels[0].Price == 7700.0 and "Keeping the last good levels" in m.daily.src["ES"].error


def test_indicator_settings_and_symbols(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    assert m.ind.put("tpo", {"BlockMinutes": 15, "StartColor": "#0000FF", "ShowLive": False}) == {"BlockMinutes": 15}
    assert m.ind.value("tpo", "BlockMinutes") == 15 and m.ind.value("tpo", "TargetRows") == 80
    for bad in [{"BlockMinutes": 3}, {"Nope": 1}, {"StartColor": "blue"}, {"SessionTime": "8:30-15"}]:
        with pytest.raises(ValueError):
            m.ind.put("tpo", bad)
    assert m.ind.put("hema", {"PlotHTFEMA": {"color": "#FF0000", "dash": "Dash", "width": 1.5}})["PlotHTFEMA"]["dash"] == "Dash"
    with pytest.raises(ValueError):
        m.ind.put("hema", {"HtfPeriodType": "Tick"})   # this app builds NinjaTrader's time bars: Second to Year
    m.ind.put_symbol("lines", "ES", {"RangeTop": 7800.0, "CustomLevels": "7845.75|VAH"})
    assert m.ind.value("lines", "RangeTop", "ES") == 7800.0 and m.ind.value("lines", "RangeTop", "NQ") == 0.0
    m.ind.put_symbol("ifthen", "ES", {"RowsBlock": "U|1|2|x"}, 5)
    assert m.ind.value("ifthen", "RowsBlock", "ES") == "U|1|2|x" and m.store.get_setting("ifthen")["ES"]["rows"] == "U|1|2|x"
    with pytest.raises(ValueError):
        m.ind.put_symbol("tpo", "ES", {"BlockMinutes": 5})


def test_seed_from_your_ninjatrader_settings(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)   # the repo's config/nt_settings.json
    seed = json.loads((m.s.config_dir / "nt_settings.json").read_text())
    assert m.amt.overrides["lvl_bull_color"] == seed["amt"]["lvl_bull_color"] == 0xFFFF3333
    assert m.ind.value("gap", "TablePosition") == "BottomRight"
    assert m.store.get_setting("nt_seed") == seed["version"]


def test_seed_reset_puts_a_setting_back(tmp_path, monkeypatch):
    m, _ = build_market(tmp_path, monkeypatch)
    m.store.set_setting("amt_settings", {**m.amt.overrides, "mvp_align_right": False, "mvp_offset": 30})
    m.store.set_setting("nt_seed", 1)   # a server seeded before the reset existed
    m2, _ = build_market(tmp_path, monkeypatch)
    assert "mvp_align_right" not in m2.amt.overrides and m2.amt.overrides["mvp_offset"] == 30   # only the reset one
    assert m2.amt._settings.mvp_align_right is True


class _Link:
    """A levels link (or several) answering by the clock; `asked` lists when and which. An answer is (status, text) or
    (status, text, headers); `sent` lists the request headers."""
    def __init__(self, answer):
        self.answer, self.asked, self.now, self.sent = answer, [], 0, []

    async def get(self, url, timeout=None, follow_redirects=None, headers=None):
        from types import SimpleNamespace
        self.asked.append((self.now, url))
        self.sent.append(dict(headers or {}))
        status, text, *more = self.answer(url, self.now)
        return SimpleNamespace(status_code=status, text=text, headers=more[0] if more else {}, raise_for_status=lambda: None)


def _link_levels(setting, http):
    from types import SimpleNamespace
    from chartsrv.config import Instrument
    store = SimpleNamespace(get_setting=lambda k, d=None: d, set_setting=lambda k, v: None)
    m = SimpleNamespace(store=store, http=http, broadcast=lambda msg: None, levels_changed=lambda: None,
                        amt=SimpleNamespace(settings_value=lambda k: setting["v"], levels_changed=lambda s: None),
                        instruments={"ES": Instrument("ES", "E-mini", 0.25, 50)})
    return dl.DailyLevels(m)


async def _poll_until(d, http, start, end, step=5000, each=None):
    t = start
    while t < end:
        http.now = t
        if each:
            each(t)
        await d.poll(t)
        t += step


def _ct(y, mo, day, h, mi, s=0):
    from datetime import datetime
    from chartsrv import sessions
    return int(datetime(y, mo, day, h, mi, s, tzinfo=sessions.CT).timestamp() * 1000)


def test_a_new_levels_link_is_asked_at_once():
    """The setting changed to another link (the old one refused): asked on the next check, not when the old link's
    next ask was due, even while the market is shut."""
    import asyncio
    setting = {"v": "https://levels.example/OLD/{root}.txt"}
    http = _Link(lambda url, now: (403, "") if "OLD" in url else (200, LINE))
    d = _link_levels(setting, http)
    wed = lambda h, mi, s=0: _ct(2026, 10, 21, h, mi, s)   # noqa: E731
    asyncio.run(_poll_until(d, http, wed(10, 0), wed(10, 1)))
    assert d.status("ES")["error"] == "The levels link was refused (HTTP 403): check the link."
    setting["v"] = "https://levels.example/NEW/{root}.txt"
    asyncio.run(_poll_until(d, http, wed(10, 1), wed(10, 1, 10)))
    assert http.asked == [(wed(10, 0), "https://levels.example/OLD/ES.txt"), (wed(10, 1), "https://levels.example/NEW/ES.txt")]
    assert d.status("ES")["origin"] == "link" and d.status("ES")["error"] == "" and d.status("ES")["count"] == 7
    setting["v"] = "https://levels.example/NEWER/{root}.txt"   # on a Saturday, with levels: asked too
    sat = _ct(2026, 10, 24, 11, 0)
    asyncio.run(_poll_until(d, http, sat, sat + 10_000))
    assert http.asked[-1] == (sat, "https://levels.example/NEWER/ES.txt")


def test_a_levels_link_is_asked_on_ninjatraders_schedule_while_the_market_is_open():
    """At once when set (and at a start), then as the NinjaTrader Toolkit's LevelLink.NextAsk has it while the market is
    open (the C# itself is compared in test_levels_link_schedule.py); not in the 16:00-17:00 CT break, a weekend or a
    closed holiday, and at the first check once it opens again. LINE has no Published line, so a first answer is not
    taken for this publish: inside 16:45's hour it is asked every minute until the text changes, then every 2 minutes."""
    import asyncio
    from chartsrv import sessions
    later = "2026-10-21, 5100.00, 5120.00, 5080.00\n"
    http = _Link(lambda url, now: (200, later if now >= _ct(2026, 10, 20, 17, 3) else LINE))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, _ct(2026, 10, 20, 15, 51), _ct(2026, 10, 20, 17, 7)))   # a Tuesday
    tue = lambda h, mi: _ct(2026, 10, 20, h, mi)   # noqa: E731
    # 15:51, then 30 minutes (16:21, in the break: the first check at 17:00), 17:00-17:03 each minute (16:45's levels
    # not in hand), the new text at 17:03, then every 2 minutes
    assert [t for t, _ in http.asked] == [tue(15, 51), tue(17, 0), tue(17, 1), tue(17, 2), tue(17, 3), tue(17, 5)]
    assert d.src["ES"].read_ms == tue(17, 3) and d.current("ES")[0][0].Price == 5100.0
    # set on a Friday in the last minutes: asked then, not over the weekend, again at Sunday's open (16:45's hour)
    http = _Link(lambda url, now: (200, LINE))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, _ct(2026, 10, 23, 15, 57), _ct(2026, 10, 25, 17, 3), step=60_000))
    assert [t for t, _ in http.asked] == [_ct(2026, 10, 23, 15, 57), _ct(2026, 10, 25, 17, 0), _ct(2026, 10, 25, 17, 1),
                                          _ct(2026, 10, 25, 17, 2)]
    # a closed day (here an invented Wednesday): set that day, asked once; none until the next open
    sessions.set_calendar({"days": [{"date": "2026-11-25", "name": "Closed", "closed": True}]})
    try:
        http = _Link(lambda url, now: (200, LINE))
        d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
        asyncio.run(_poll_until(d, http, _ct(2026, 11, 25, 9, 0), _ct(2026, 11, 25, 17, 3), step=60_000))
        assert [t for t, _ in http.asked] == [_ct(2026, 11, 25, 9, 0), _ct(2026, 11, 25, 17, 0), _ct(2026, 11, 25, 17, 1),
                                              _ct(2026, 11, 25, 17, 2)]
    finally:
        sessions.set_calendar({"days": []})


def test_a_publish_is_asked_for_every_15_seconds_until_it_comes():
    """Set at 08:00 with the levels published yesterday at 16:45: asked again at 08:14 (woken for 08:15), every 15 s
    until the 08:15 levels come (here at 08:16:30), every 2 minutes for 30 minutes after, then every 30 minutes."""
    import asyncio
    old = "Published: 2026-10-20 16:45 CT\n" + LINE
    new = "Published: 2026-10-21 08:15 CT\n2026-10-21, 5100.00, 5120.00, 5080.00\n"
    http = _Link(lambda url, now: (200, new if now >= _ct(2026, 10, 21, 8, 16, 30) else old))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, _ct(2026, 10, 21, 8, 0), _ct(2026, 10, 21, 9, 30)))
    at = lambda h, mi, s=0: _ct(2026, 10, 21, h, mi, s)   # noqa: E731
    want = [at(8, 0)] + [at(8, 14) + 15_000 * k for k in range(11)] + [at(8, 16, 30) + 120_000 * k for k in range(1, 16)] \
        + [at(9, 16, 30)]
    assert [t for t, _ in http.asked] == want
    assert d.current("ES")[0][0].Price == 5100.0


def test_failures_back_off_and_a_link_the_service_refuses_waits_30_minutes():
    """Failures in a row: 1, 2, 4, 8, 15, 15 minutes (1 at most in a publish's hour). The levels service's own 403 or 404
    (its X-Tatanka-Levels mark): every 30 minutes; one without the mark (a proxy) is a failure. An ETag is sent back and
    a 304 is an answer."""
    import asyncio
    thu = lambda h, mi: _ct(2026, 10, 22, h, mi)   # noqa: E731
    http = _Link(lambda url, now: (500, ""))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, thu(10, 0), thu(11, 0), step=60_000))
    assert [t for t, _ in http.asked] == [thu(10, m) for m in (0, 1, 3, 7, 15, 30, 45)]
    assert "unreachable (HTTP 500)" in d.status("ES")["error"]
    http = _Link(lambda url, now: (500, ""))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, thu(8, 20), thu(8, 30), step=60_000))   # 08:15's hour: each minute
    assert [t for t, _ in http.asked] == [thu(8, m) for m in range(20, 30)]
    for mark, want in (({"X-Tatanka-Levels": "1"}, [thu(10, 0), thu(10, 30)]), ({}, [thu(10, m) for m in (0, 1, 3, 7, 15, 30)])):
        http = _Link(lambda url, now, mark=mark: (403, "", mark))
        d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
        asyncio.run(_poll_until(d, http, thu(10, 0), thu(10, 31), step=60_000))
        assert [t for t, _ in http.asked] == want, mark
        assert d.status("ES")["error"] == "The levels link was refused (HTTP 403): check the link."
    http = _Link(lambda url, now: (200, LINE, {"ETag": '"v1"'}) if now == thu(10, 0) else (304, ""))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, thu(10, 0), thu(11, 1), step=60_000))
    assert [t for t, _ in http.asked] == [thu(10, 0), thu(10, 30), thu(11, 0)]
    assert http.sent == [{}, {"If-None-Match": '"v1"'}, {"If-None-Match": '"v1"'}]
    assert d.status("ES")["error"] == "" and d.current("ES")[0][0].Price == 5000.0


def test_a_changed_answer_without_a_published_line_is_the_publish_due_next():
    """LevelLink: a text that changed while asking, with no Published line, counts as published when it came (stampCt =
    nowCt); only a first answer does not (it may be yesterday's). Changed at 08:00: 08:15's levels are in hand, so it is
    asked every 2 minutes for 30 minutes (08:14 is not a publish to wait for), then every 30 minutes."""
    import asyncio
    from datetime import datetime
    other = "2026-10-21, 5100.00, 5120.00, 5080.00\n"
    at = lambda h, mi, s=0: _ct(2026, 10, 21, h, mi, s)   # noqa: E731
    http = _Link(lambda url, now: (200, other if now >= at(7, 40) else LINE))
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, at(7, 30), at(9, 1)))
    assert [t for t, _ in http.asked] == [at(7, 30)] + [at(8, m) for m in range(0, 31, 2)] + [at(9, 0)]
    assert d.src["ES"].link.stamp == datetime(2026, 10, 21, 8, 0) and d.current("ES")[0][0].Price == 5100.0


def test_a_304_keeps_saying_the_answer_held_gave_no_levels():
    """Good levels (ETag "a"), then an answer with none (ETag "b"): the last good levels stay and it says so. The asks
    after it get 304 for that same answer: still said, as NinjaTrader's chart keeps the error of the text it parsed until
    another comes. A good answer clears it."""
    import asyncio
    wed = lambda h, mi: _ct(2026, 10, 21, h, mi)   # noqa: E731

    def answer(url, now):
        tag = http.sent[-1].get("If-None-Match")
        etag, text = ('"a"', LINE) if now < wed(10, 30) or now >= wed(11, 30) else ('"b"', "<html>Session expired</html>")
        return (304, "") if tag == etag else (200, text, {"ETag": etag})
    http = _Link(answer)
    d = _link_levels({"v": "https://levels.example/key/{root}.txt"}, http)
    asyncio.run(_poll_until(d, http, wed(10, 0), wed(10, 31), step=60_000))
    bad = "Levels link unreachable (ValueError): keeping the last good levels."
    assert d.status("ES")["error"] == bad and d.status("ES")["count"] == 7
    asyncio.run(_poll_until(d, http, wed(10, 31), wed(11, 30), step=60_000))
    assert [h.get("If-None-Match") for h in http.sent[-3:]] == ['"b"'] * 3     # 304s for it
    assert d.status("ES")["error"] == bad and d.status("ES")["count"] == 7
    asyncio.run(_poll_until(d, http, wed(11, 30), wed(12, 31), step=60_000))
    assert d.status("ES")["error"] == "" and d.status("ES")["count"] == 7


def test_paths_under_onedrive_and_without_docker(tmp_path, monkeypatch):
    """Documents moved to OneDrive (Windows 11's default, and NinjaTrader's folder with it) is the folder Docker
    mounts too; the server run on Windows itself, without Docker, opens a Windows path as it is."""
    from pathlib import Path
    want = tmp_path / "Tatanka" / "levels" / "ES.txt"
    assert dl.local_path(r"C:\Users\you\OneDrive\Documents\Tatanka\levels\ES.txt", tmp_path) == want
    assert dl.local_path(r"C:\Users\you\OneDrive - Contoso\Documents\Tatanka\levels\ES.txt", tmp_path) == want
    assert dl.local_path(r"C:\Users\you\OneDrive\Pictures\ES.txt", tmp_path) is None
    assert dl.local_path(r"D:\levels\ES.txt", None) is None   # in Docker, Documents not mounted
    monkeypatch.setattr(dl, "WINDOWS", True)
    monkeypatch.setenv("USERPROFILE", r"C:\Users\you")
    assert dl.local_path(r"D:\levels\ES.txt", None) == Path(r"D:\levels\ES.txt")
    assert dl.local_path(dl.DEFAULT_FILE.replace("{root}", "ES"), None) == Path(r"C:\Users\you\Documents\Tatanka\levels\ES.txt")
    assert dl.local_path(r"C:\Users\you\Documents\ES.txt", tmp_path) == tmp_path / "ES.txt"   # CHART_DOCUMENTS_DIR set


def test_a_file_missing_from_the_mounted_documents_says_which_folder(tmp_path, monkeypatch):
    """A path under Documents (OneDrive's too) is read from the Documents folder the server is given, which may not
    be the one the path names (OneDrive without its Documents moved there): the error says which folder it looked in,
    not only that the file the user can see isn't there."""
    import asyncio
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    m, _ = build_market(tmp_path, monkeypatch)
    spec = r"C:\Users\you\OneDrive\Documents\Tatanka\levels\{root}.txt"
    monkeypatch.setattr(m.daily, "spec", lambda: spec)
    asyncio.run(m.daily.poll())
    assert m.daily.src["ES"].error == (r"No levels file at C:\Users\you\OneDrive\Documents\Tatanka\levels\ES.txt in the "
                                       "Documents folder the server sees (DOCUMENTS_DIR).")
    spec = "/no/such/levels/{root}.txt"   # a path of its own: just not there
    asyncio.run(m.daily.poll())
    assert m.daily.src["ES"].error == "No levels file at /no/such/levels/ES.txt."


def test_back_from_a_link_to_an_unchanged_file_reads_the_file(tmp_path, monkeypatch):
    """The setting goes from a file to a link and back to the same file, not edited meanwhile: the file's levels come
    back, not the link's labelled as the file's (the file's time is unchanged, but its levels are not the ones held).
    The blank setting (Documents\\Tatanka\\levels\\{root}.txt) does the same."""
    import asyncio
    docs = tmp_path / "docs"
    for folder in ("lv", "Tatanka/levels"):
        (docs / folder).mkdir(parents=True)
        (docs / folder / "ES.txt").write_text("2026-10-21, 7700.00, 7720.00, 7680.00\n")
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    for file_spec in (r"C:\Users\you\Documents\lv\{root}.txt", ""):
        setting = {"v": file_spec}
        http = _Link(lambda url, now: (200, LINE))
        d = _link_levels(setting, http)
        wed = _ct(2026, 10, 21, 10, 0)
        seen = []
        for i, spec in enumerate([file_spec, "https://levels.example/key/{root}.txt", file_spec]):
            setting["v"] = spec
            asyncio.run(d.poll(wed + i * 5000))
            levels, _, src = d.current("ES")
            seen.append((src.origin, levels[0].Price))
        assert seen == [("file", 7700.0), ("link", 5000.0), ("file", 7700.0)], file_spec
        assert d.status("ES")["read_ms"] == wed + 10_000


class _Stop(BaseException):
    """Ends DailyLevels.run() at its first pause."""


def _kept_levels(store, setting, http, events=None):
    """_link_levels on a real Store: each change is kept (levels_kept) as Market.levels_changed keeps it, so a new
    DailyLevels on the same store is a server restart. events: the changes heard (levels_changed)."""
    from types import SimpleNamespace
    from chartsrv.config import Instrument
    m = SimpleNamespace(store=store, http=http, broadcast=lambda msg: None,
                        amt=SimpleNamespace(settings_value=lambda k: setting["v"], levels_changed=lambda s: None),
                        instruments={"ES": Instrument("ES", "E-mini", 0.25, 50)})
    d = dl.DailyLevels(m)

    def changed():
        d.remember(http.now)
        if events is not None:
            events.append(http.now)
    m.levels_changed = changed
    return d


def _start(d, http, t, monkeypatch):
    """DailyLevels.run() at t, to its first pause (what a server start does)."""
    import asyncio
    from types import SimpleNamespace

    async def stop(_s):
        raise _Stop
    monkeypatch.setattr(dl, "time", SimpleNamespace(time=lambda: t / 1000))
    monkeypatch.setattr(dl.stopping, "pause", stop)
    http.now = t
    with pytest.raises(_Stop):
        asyncio.run(d.run())


def test_a_restart_keeps_the_links_last_good_levels_and_asks_until_it_answers(tmp_path, monkeypatch):
    """Friday the link gave levels. The server restarts on Saturday and its first ask fails (the levels site down): the
    levels are the ones the link gave last (kept), as NinjaTrader's Toolkit starts from its last-good cache, not none
    until Sunday's open, and no row of none is kept at the restart. A link that has not answered since the start is
    asked again on the failure back-off while the market is shut; once it answers, not until the market opens."""
    import asyncio
    from chartsrv.store import Store
    store = Store(tmp_path / "chart.db")
    line = "2026-11-16, 6800.00, 6820.00, 6840.00, 6780.00, 6760.00\n"
    setting = {"v": "https://levels.example/l/KEY/{root}.txt"}
    fri, sat, sun = (lambda h, mi, day=day: _ct(2026, 11, day, h, mi) for day in (13, 14, 15))
    http = _Link(lambda url, now: (200, line))
    _start(_kept_levels(store, setting, http), http, fri(14, 0), monkeypatch)
    kept = [tuple(r) for r in store.db.execute("SELECT date, origin, source, levels FROM levels_kept")]
    assert kept == [("2026-11-16", "link", "https://levels.example/…/ES.txt",
                     "[[\"PIVOT\", 6800.0, 0], [\"RES1\", 6820.0, -1], [\"RES2\", 6840.0, -1], [\"SUPP1\", 6780.0, 1], "
                     "[\"SUPP2\", 6760.0, 1]]")]

    def down_until(back):
        def answer(url, now):
            if now < back:
                raise ConnectionError("levels site down")
            return 200, line
        return answer
    http, events = _Link(down_until(sat(10, 5))), []
    d = _kept_levels(store, setting, http, events)
    _start(d, http, sat(10, 0), monkeypatch)
    st = d.status("ES")
    assert (st["origin"], st["count"], st["date"]) == ("link", 5, "2026-11-16")
    assert st["error"] == "Levels link unreachable (ConnectionError): keeping the last good levels."
    assert [x.Price for x in d.current("ES")[0]] == [6800.0, 6820.0, 6840.0, 6780.0, 6760.0]
    assert [x.Name for x in d.current("ES")[0]] == ["PIVOT", "RES1", "RES2", "SUPP1", "SUPP2"]
    assert events == [sat(10, 0)]   # the Daily levels study, level alerts and the Toolkit heard them
    assert store.db.execute("SELECT COUNT(*) FROM levels_kept").fetchone()[0] == 1   # no row of none at the restart
    asyncio.run(_poll_until(d, http, sat(10, 0) + 60_000, sun(17, 1), step=60_000))
    assert [t for t, _ in http.asked] == [sat(10, 0), sat(10, 1), sat(10, 3), sat(10, 7), sun(17, 0)]
    assert d.status("ES")["error"] == "" and d.status("ES")["count"] == 5
    assert store.db.execute("SELECT COUNT(*) FROM levels_kept").fetchone()[0] == 1

    # first set on a Saturday with the site down, nothing kept: no levels, and it says so; asked until it answers
    http = _Link(down_until(sat(10, 5)))
    d = _kept_levels(Store(tmp_path / "other.db"), setting, http)
    asyncio.run(_poll_until(d, http, sat(10, 0), sat(10, 1), step=60_000))
    st = d.status("ES")
    assert (st["origin"], st["count"]) == ("none", 0)
    assert st["error"] == "Levels link unreachable (ConnectionError): no levels from it yet."
    asyncio.run(_poll_until(d, http, sat(10, 1), sat(10, 30), step=60_000))
    assert [t for t, _ in http.asked] == [sat(10, 0), sat(10, 1), sat(10, 3), sat(10, 7)]
    assert d.status("ES")["count"] == 5 and d.status("ES")["error"] == ""


def test_a_new_file_or_link_never_shows_the_levels_of_the_one_before(tmp_path, monkeypatch):
    """From a levels file to a new link the levels service refuses (403, 404) or that does not answer: the file's
    levels are not shown as the link's; none (or the pasted line) until the link answers, as NinjaTrader's Toolkit
    starts a new source from that link's own cache. The other way, from a link to a file that is missing or bad: none
    of the link's shown as the file's."""
    import asyncio
    docs = tmp_path / "docs"
    (docs / "lv").mkdir(parents=True)
    (docs / "lv" / "ES.txt").write_text("2026-11-13, 7700.00, 7720.00, 7680.00\n")
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    file_spec = r"C:\Users\you\Documents\lv\{root}.txt"
    mon = _ct(2026, 11, 16, 10, 0)
    for name, answer in (("403", lambda url, now: (403, "", {"X-Tatanka-Levels": "1"})),
                         ("404", lambda url, now: (404, "", {"X-Tatanka-Levels": "1"})),
                         ("timeout", lambda url, now: (_ for _ in ()).throw(TimeoutError("timed out")))):
        setting = {"v": file_spec}
        http = _Link(answer)
        d = _link_levels(setting, http)
        asyncio.run(d.poll(mon))
        assert (d.status("ES")["origin"], d.status("ES")["count"]) == ("file", 3), name
        setting["v"] = "https://levels.example/l/NEWKEY/{root}.txt"
        asyncio.run(_poll_until(d, http, mon + 5000, mon + 60_000))
        st = d.status("ES")
        assert (st["origin"], st["count"], st["date"], d.current("ES")[0]) == ("none", 0, None, []), name
        assert st["error"] and "keeping the last good levels" not in st["error"], name
        d.set_paste("ES", LINE)
        assert d.status("ES")["origin"] == "paste" and d.status("ES")["count"] == 7, name
    # a link to a file that is missing, or bad
    for bad in (None, "garbage"):
        setting = {"v": "https://levels.example/l/KEY/{root}.txt"}
        http = _Link(lambda url, now: (200, LINE))
        d = _link_levels(setting, http)
        asyncio.run(d.poll(mon))
        assert (d.status("ES")["origin"], d.status("ES")["count"]) == ("link", 7)
        if bad:
            (docs / "bad").mkdir(exist_ok=True)
            (docs / "bad" / "ES.txt").write_text(bad)
        setting["v"] = r"C:\Users\you\Documents\bad\{root}.txt"
        asyncio.run(d.poll(mon + 5000))
        st = d.status("ES")
        assert (st["origin"], st["count"], d.current("ES")[0]) == ("none", 0, []), bad
        assert st["error"].startswith("No levels file at" if bad is None else r"C:\Users\you\Documents\bad\ES.txt: "), bad
        assert "Keeping the last good levels" not in st["error"], bad


def test_a_restart_with_no_documents_folder_takes_the_pasted_line_not_the_default_files_last_levels(tmp_path, monkeypatch):
    """Levels File or Link blank: the default file (Documents\\Tatanka\\levels\\{root}.txt) is read only if it exists,
    else the pasted line, as NinjaTrader's Toolkit. It gave levels, and the server restarts with no Documents folder
    (not mounted): the levels it gave last are not shown as the file's (nor win over the pasted line), as when the
    file is gone. An explicit file the server cannot see keeps them, with why."""
    from chartsrv.store import Store
    store = Store(tmp_path / "chart.db")
    docs = tmp_path / "docs"
    (docs / "Tatanka" / "levels").mkdir(parents=True)
    (docs / "Tatanka" / "levels" / "ES.txt").write_text("2026-11-16, 7700.00, 7720.00, 7680.00\n")
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    setting = {"v": ""}
    http = _Link(lambda url, now: (200, ""))
    mon = _ct(2026, 11, 16, 9, 0)
    _start(_kept_levels(store, setting, http), http, mon, monkeypatch)
    assert [tuple(r) for r in store.db.execute("SELECT origin, source FROM levels_kept")] == [
        ("file", dl.DEFAULT_FILE.replace("{root}", "ES"))]
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(tmp_path / "not-mounted"))
    d = _kept_levels(store, setting, http)
    _start(d, http, mon + 3_600_000, monkeypatch)
    assert (d.status("ES")["origin"], d.status("ES")["count"], d.current("ES")[0]) == ("none", 0, [])
    d.set_paste("ES", LINE)
    assert (d.status("ES")["origin"], d.status("ES")["count"]) == ("paste", 7)
    # the same file named in the setting: it is the source, so its last good levels stay, with why
    setting["v"] = dl.DEFAULT_FILE
    store2 = Store(tmp_path / "explicit.db")
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(docs))
    _start(_kept_levels(store2, setting, http), http, mon, monkeypatch)
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(tmp_path / "not-mounted"))
    d = _kept_levels(store2, setting, http)
    _start(d, http, mon + 3_600_000, monkeypatch)
    st = d.status("ES")
    assert (st["origin"], st["count"]) == ("file", 3) and "outside Documents" in st["error"]
