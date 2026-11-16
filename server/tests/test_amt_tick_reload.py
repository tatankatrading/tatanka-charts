"""Value From Ticks (Sessions) across the hub's reloads. NinjaTrader picks the sessions once as the chart loads
(RequestTickDays) and adds each one that closes while it runs (RequestClosedSession); the chart runs on. Here the hub
loads a runner again when a session's trades are worked out (AmtHub._fill_pd, at every close) or history changes (a gap
fill): the runner that replaces it keeps the sessions the charts still on it have (from the load of the one open
longest), so the older days' pd lines stay from trades as on the running NinjaTrader chart; one no chart shows counts
them afresh, as a chart opened then does. New settings count them again, as NinjaTrader's reload does. Prior days
worked out for a replay of a past date load the replays again, never the live runners and their charts. While the alert
runner loads again, the one it replaces watches the live prices (NinjaTrader's chart alerts on through its patch)."""
import asyncio
import random
from datetime import datetime

from test_replay_studies import FakeClient, clock  # noqa: F401  (clock: the fixture)
from test_review_fixes import build_market

from chartsrv import amt_hub, sessions
from chartsrv.amt_runner import AmtRunner, ct
from chartsrv.models import Bar, Tick
from chartsrv.sessions import CT
from chartsrv.store import iter_bars_1m

MIN = 60_000


def ms(d: datetime) -> int:
    return int(d.replace(tzinfo=CT).timestamp() * 1000)


def minutes(a: datetime, b: datetime, seed: int = 3, px: float = 6800.0):
    """Globex minutes from a to b, each with four trades (its open, high, low and close, random sizes), so a day's value
    from its trades differs from the one from its minutes (spread over each minute's range)."""
    rnd = random.Random(seed)
    ticks, bars = [], []
    t = ms(a)
    while t < ms(b):
        if sessions.is_open(t):
            o = px
            c = round((px + rnd.choice((-2.5, -1.5, -0.75, -0.25, 0, 0.25, 0.75, 1.5, 2.5))) * 4) / 4
            h, lo = max(o, c) + rnd.choice((0, 0.25, 0.5, 1.0)), min(o, c) - rnd.choice((0, 0.25, 0.5, 1.0))
            sizes = [rnd.randint(1, 60) for _ in range(4)]
            bars.append(("ES", Bar(t=t, o=o, h=h, l=lo, c=c, v=sum(sizes))))
            ticks += [Tick("ES", t + 5_000 + k * 15_000, p, sz, 1) for k, (p, sz) in enumerate(zip((o, h, lo, c), sizes))]
            px = c
        t += MIN
    return ticks, bars


async def settle(m):
    """The prior-day job running, if any, done."""
    for _ in range(600):
        await asyncio.sleep(0.02)
        if not m.amt.pd_busy:
            return
    raise AssertionError("the prior-day job never finished")


def days(runner) -> list[str]:
    return sorted(s.strftime("%a %m-%d") for s, _ in runner.tick_windows())


def pd_lines(runner) -> list:
    return [(x.Name, x.Price, x.Start, x.End) for x in runner.history if x.Name in ("pdPOC", "pdVAH", "pdVAL")]


def run(m, loaded: datetime, upto: datetime, kept=(), minutes: int = 5) -> AmtRunner:
    """A runner as the hub makes one, loaded at `loaded` with the prior days' values worked out so far, then run on to
    `upto` over the stored minutes (those after `loaded` live)."""
    r = m.amt.new_runner("ES", minutes)
    r.pd_values, r.loaded_at, r.tick_kept = m.amt._pd_cached("ES"), ms(loaded), list(kept)
    for i, b in enumerate(iter_bars_1m(m.store.path, "ES", ms(upto) - amt_hub.HISTORY_DAYS * 86_400_000, ms(upto))):
        r.add_minute(b)
        if i % 37 == 0:
            r.commit_ready()
    r.commit_ready()
    r.preview()
    return r


async def show(m, c, minutes: int = 5, sid: int = 1) -> AmtRunner:
    """A chart showing the Toolkit (asked again while a reload tells it to: amt_reset): the runner it stays on."""
    c.subs[sid] = object()
    while True:
        await m.amt.subscribe(c, sid, "ES", minutes)
        await settle(m)
        if c.sent[-1]["type"] != "amt_reset":
            return m.amt.entries[("ES", minutes)].runner


def live(m, clock, bars, a: datetime, b: datetime):
    """The stored minutes from a to b, live (the clock at each one's end)."""
    for _, bar in bars:
        if ms(a) <= bar.t < ms(b):
            clock["t"] = bar.t + MIN
            m.amt.on_minute("ES", bar)
    clock["t"] = ms(b)


MON_9, MON_1531 = datetime(2026, 10, 12, 9, 0), datetime(2026, 10, 12, 15, 31)
MON_RTH = (ms(datetime(2026, 10, 12, 8, 30)), ms(datetime(2026, 10, 12, 15, 0)))
LOADED = ["Fri 10-09", "Mon 10-05", "Thu 10-08", "Tue 10-06", "Wed 10-07"]   # the 5 closed by Monday 09:00


def test_a_reload_after_the_close_keeps_the_sessions_of_the_running_chart(tmp_path, monkeypatch, clock):  # noqa: F811
    """A chart loaded Monday 09:00: Monday 10-05 to Friday 10-09. At Monday's close the runner adds Monday; the hub works
    its trades out and loads the runner again at 15:31. NinjaTrader's chart, still running, has all six; so does the
    reload (before, it counted five back from 15:31 and drew Tuesday 10-06's pd lines from Monday 10-05's minutes)."""
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        ticks, bars = minutes(datetime(2026, 9, 10, 17, 0), datetime(2026, 10, 12, 15, 40))
        m.store.write(ticks, bars)
        clock["t"] = ms(MON_9) + 30_000
        c = FakeClient()
        r = await show(m, c)                               # its five sessions' trades worked out: loaded again
        assert days(r) == LOADED and not r.pd_missing and m.amt.chart_since[(c, 1)] == (("ES", 5), ms(MON_9))
        live(m, clock, bars, MON_9, MON_1531)
        r.commit_ready()
        r.preview()
        assert days(r) == sorted(LOADED + ["Mon 10-12"]) and r.pd_missing == {MON_RTH}   # RequestClosedSession
        m.amt._want_pd("ES")
        await settle(m)
        assert ("ES", 5) not in m.amt.entries and m.amt.tick_kept[("ES", 5)][2] == r.tick_windows()
        assert c.sent[-1]["type"] == "amt_reset"
        again = await show(m, c)                           # the chart asks again, from its own load on
        assert again.loaded_at == ms(MON_1531) and days(again) == days(r) and not m.amt.tick_kept
        assert m.amt.chart_since[(c, 1)] == (("ES", 5), ms(MON_9))
        running = run(m, MON_9, MON_1531)                  # NinjaTrader's chart, run on through the close
        assert days(running) == days(again)
        want = pd_lines(running)
        assert len(want) > 30 and pd_lines(again) == want
        # counted from 15:31 instead (the reload before): Monday 10-05 from its minutes, Tuesday's lines another
        recount = run(m, MON_1531, MON_1531)
        assert "Mon 10-05" not in days(recount)
        tue = [x for x, y in zip(pd_lines(recount), want) if x != y]
        assert tue and {ct(ms(x[2])).date() for x in tue} == {datetime(2026, 10, 5).date()}
    asyncio.run(go())


THU_10, THU_1531 = datetime(2026, 10, 15, 10, 0), datetime(2026, 10, 15, 15, 31)


async def close(m, clock, bars, a: datetime, b: datetime, *runners):
    """Live from a to b (a close and its first minutes), then the close's prior-day job: every runner loads again."""
    live(m, clock, bars, a, b)
    for r in runners:
        r.commit_ready()
        r.preview()
    m.amt._want_pd("ES")
    await settle(m)


def test_only_the_charts_that_stay_open_keep_their_sessions(tmp_path, monkeypatch, clock):  # noqa: F811
    """ES's alerts (5 minutes) and a chart on ES 15 minutes, from Monday 09:00 through Monday's, Tuesday's and
    Wednesday's closes, each loaded again by its close's prior-day job. The chart's runner keeps the chart's sessions:
    6, 7, then 8, as its running NinjaTrader chart. The alerts' runner, which a chart showed only Monday morning,
    counts them afresh: five, so a chart opened Thursday 10:00 on it draws what a NinjaTrader chart opened then draws
    (before, every runner kept them all from its first load: that chart drew 8, the pd lines of 10-05 to 10-07 from
    trades). One opened Thursday on the chart's Toolkit shares it (8) while that chart is open; once it is closed,
    Thursday's close counts them from the second one's load."""
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        ticks, bars = minutes(datetime(2026, 9, 10, 17, 0), datetime(2026, 10, 15, 15, 40))
        m.store.write(ticks, bars)
        clock["t"] = ms(MON_9) + 30_000
        m.amt.set_alerts("ES", True)
        key = m.amt.alert_key("ES")
        assert key == ("ES", 5)
        mon, early = FakeClient(), FakeClient()
        chart = await show(m, mon, 15)
        alerts = await show(m, early, 5)                   # a chart on the alerts' runner, Monday morning only
        assert days(chart) == days(alerts) == LOADED
        at = datetime(2026, 10, 12, 12, 0)
        live(m, clock, bars, MON_9, at)
        m.amt.unsubscribe(early, 1)                        # closed before the close
        for n, end in enumerate((MON_1531, datetime(2026, 10, 13, 15, 31), datetime(2026, 10, 14, 15, 31))):
            await close(m, clock, bars, at, end, chart, alerts)
            at = end
            assert ("ES", 15) in m.amt.tick_kept and key not in m.amt.tick_kept   # no chart on the alerts' runner
            chart = await show(m, mon, 15)
            alerts = (await m.amt.entry("ES", 5)).runner
            await settle(m)
            assert m.amt.entries[("ES", 15)].runner is chart and m.amt.entries[key].runner is alerts
            assert len(days(chart)) == 6 + n and pd_lines(chart) == pd_lines(run(m, MON_9, end, minutes=15))
            assert days(alerts) == days(run(m, end, end))  # five, counted at the close
        assert days(chart) == sorted(LOADED + ["Mon 10-12", "Tue 10-13", "Wed 10-14"])
        live(m, clock, bars, at, THU_10)
        thu, thu15 = FakeClient(), FakeClient()
        opened = await show(m, thu, 5)                     # joins the alerts' runner
        fresh = run(m, THU_10, THU_10)                     # NinjaTrader's chart opened then
        opened.commit_ready()
        opened.preview()
        assert opened is alerts and days(opened) == days(fresh) == ["Fri 10-09", "Mon 10-12", "Thu 10-08", "Tue 10-13", "Wed 10-14"]
        assert len(pd_lines(fresh)) > 30 and pd_lines(opened) == pd_lines(fresh)
        assert await show(m, thu15, 15) is chart and len(days(chart)) == 8   # shares the Monday chart's
        m.amt.unsubscribe(mon, 1)                          # the Monday chart closed
        await close(m, clock, bars, THU_10, THU_1531, chart, alerts)
        for c, n in ((thu, 5), (thu15, 15)):               # each now from Thursday 10:00's load, plus Thursday
            r = await show(m, c, n)
            want = run(m, THU_10, THU_1531, minutes=n)
            assert days(r) == days(want) == sorted(days(fresh) + ["Thu 10-15"])
            assert pd_lines(r) == pd_lines(want)
    asyncio.run(go())


def test_new_settings_count_the_sessions_again_and_unclaimed_ones_go(tmp_path, monkeypatch, clock):  # noqa: F811
    """A reload for new settings counts them again (NinjaTrader reloads on any change), from then for the charts on it.
    Ones no chart came back for go: after IDLE_S, or at once once no chart shows that Toolkit (closed, or its page
    gone): a later chart load counts them again."""
    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        hub = m.amt
        w = [(datetime(2026, 10, 5, 8, 30), datetime(2026, 10, 5, 15, 0))]
        r = hub.new_runner("ES", 5)
        c, gone = FakeClient(), FakeClient()
        hub.tick_kept[("ES", 5)] = (amt_hub.time.monotonic(), r.tick_params(), w)
        hub.tick_kept[("NQ", 5)] = (amt_hub.time.monotonic(), r.tick_params(), w)
        hub.chart_since[(c, 1)] = (("ES", 5), 0)
        hub.chart_since[(c, 2)] = (("NQ", 5), 0)
        hub.set_overrides({"cc_va_pct": 0.7}, "ES")       # every chart's: both counted again
        assert not hub.tick_kept and not hub.chart_since
        hub.tick_kept[("ES", 5)] = (amt_hub.time.monotonic(), r.tick_params(), w)
        other = hub.new_runner("ES", 5, {"PdTickSessions": 3})
        assert other.tick_params() != r.tick_params()    # another count: not given them
        hub.chart_since[(c, 1)] = (("ES", 5), 0)
        hub.chart_since[(c, 2)] = (("NQ", 5), 0)
        hub.chart_since[(gone, 1)] = (("ES", 15), 0)
        gone.closed = True
        hub.tick_kept[("ES", 5)] = (amt_hub.time.monotonic() - amt_hub.IDLE_S - 1, r.tick_params(), w)
        hub.tick_kept[("NQ", 5)] = (amt_hub.time.monotonic(), r.tick_params(), w)
        hub.tick_kept[("NQ", 15)] = (amt_hub.time.monotonic(), r.tick_params(), w)   # no chart on it
        hub.tick_kept[("ES", 15)] = (amt_hub.time.monotonic(), r.tick_params(), w)   # its page gone
        hub.step()
        assert list(hub.tick_kept) == [("NQ", 5)]
        hub.unsubscribe(c, 2)                              # the chart closed
        hub.step()
        assert not hub.tick_kept
    asyncio.run(go())


def test_a_replay_loaded_again_keeps_its_sessions_and_one_gone_back_counts_them_again(tmp_path, monkeypatch, clock):  # noqa: F811
    """A replay from Monday 09:00 played through the close: its runner adds Monday; the job works Monday out and only the
    replay loads again (no live runner asked), with Monday 10-05 to Monday 10-12 as NinjaTrader's Playback chart has
    them. Gone back to Monday 12:00, it is a new load: five, counted from there."""
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        ticks, bars = minutes(datetime(2026, 9, 10, 17, 0), datetime(2026, 10, 12, 15, 40))
        m.store.write(ticks, bars)
        clock["t"] = ms(datetime(2026, 10, 14, 12, 0))
        c = FakeClient()
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(MON_9))
        await settle(m)
        assert c.sent[-1]["type"] == "amt_reset"           # its five worked out: asked again
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(MON_9))
        assert days(m.amt.replays[(c, 7)].runner) == LOADED
        for at in range(ms(MON_9) + 30 * MIN, ms(MON_1531) + 1, 30 * MIN):
            await m.amt.subscribe(c, 7, "ES", 5, None, at)
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(MON_1531))
        r = m.amt.replays[(c, 7)].runner
        assert days(r) == sorted(LOADED + ["Mon 10-12"]) and MON_RTH in r.pd_missing
        await settle(m)
        assert c.sent[-1]["type"] == "amt_reset" and (c, 7) not in m.amt.replays
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(MON_1531))
        again = m.amt.replays[(c, 7)].runner
        assert days(again) == days(r) and again.loaded_at == ms(MON_1531)
        assert pd_lines(again) == pd_lines(run(m, MON_9, MON_1531))
        m.amt.reload("ES")                                 # a gap fill: every runner of ES loads again, keeping them
        assert c.sent[-1]["type"] == "amt_reset"
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(MON_1531))
        assert days(m.amt.replays[(c, 7)].runner) == days(r)
        await settle(m)
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(datetime(2026, 10, 12, 12, 0)))   # gone back
        assert days(m.amt.replays[(c, 7)].runner) == LOADED
        m.amt.unsubscribe(c, 7)
        assert not m.amt.replay_ticks
    asyncio.run(go())


def test_a_replay_of_a_past_date_leaves_the_live_runners_alone(tmp_path, monkeypatch, clock):  # noqa: F811
    """A replay in September, with the live runner of the same symbol loaded in November: the replay's prior days are
    worked out and the replay loads again; the live runner, its charts and its alerts go on (before, every runner of the
    symbol loaded again, the alert runner unchecked meanwhile, and again at each replayed close)."""
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        for a, b, seed in ((datetime(2026, 9, 13, 17), datetime(2026, 9, 18, 16), 1),
                           (datetime(2026, 11, 8, 17), datetime(2026, 11, 13, 12), 2)):
            m.store.write(*minutes(a, b, seed))
        clock["t"] = ms(datetime(2026, 11, 13, 12, 0))
        await m.amt.entry("ES", 5)
        await settle(m)
        live = await m.amt.entry("ES", 5)
        assert not live.runner.pd_missing
        watcher = FakeClient()
        watcher.subs[1] = object()
        await m.amt.subscribe(watcher, 1, "ES", 5)
        assert watcher.sent[-1]["type"] == "amt"
        c = FakeClient()
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(datetime(2026, 9, 17, 12, 0)))
        assert m.amt.replays[(c, 7)].runner.pd_missing
        await settle(m)
        assert c.sent[-1]["type"] == "amt_reset"           # the replay loads again with them
        assert m.amt.entries[("ES", 5)] is live and not live.dropped
        assert not [x for x in watcher.sent if x["type"] == "amt_reset"]
        await m.amt.subscribe(c, 7, "ES", 5, None, ms(datetime(2026, 9, 17, 12, 0)))
        assert not m.amt.replays[(c, 7)].runner.pd_missing
        for at in range(ms(datetime(2026, 9, 17, 12, 30)), ms(datetime(2026, 9, 18, 11, 0)) + 1, 30 * MIN):
            await m.amt.subscribe(c, 7, "ES", 5, None, at)   # through Thursday's close: its trades worked out
        await settle(m)
        assert m.amt.entries[("ES", 5)] is live and not [x for x in watcher.sent if x["type"] == "amt_reset"]
        # the live runner's own prior days still load it again
        live.runner.pd_missing.add((ms(datetime(2026, 11, 12, 8, 30)), ms(datetime(2026, 11, 12, 15, 0))))
        m.amt.pd_mem.clear()
        m.amt._want_pd("ES")
        await settle(m)
        assert ("ES", 5) not in m.amt.entries and live.dropped and watcher.sent[-1]["type"] == "amt_reset"
    asyncio.run(go())


def test_the_alert_runner_loading_again_leaves_the_old_one_watching_until_it_is_ready(tmp_path, monkeypatch, clock):  # noqa: F811
    """A reload (a prior-day job at the close, a gap fill) took the alert runner away for its whole load (12 s with a
    Yearly profile), and no level was watched meanwhile. Now the one it replaces watches the prices until then; new
    settings or the alerts switched again end that at once."""
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        m.store.write(*minutes(datetime(2026, 10, 5, 17, 0), datetime(2026, 10, 12, 15, 40)))
        clock["t"] = ms(MON_1531)
        m.amt.set_alerts("ES", True)
        key = m.amt.alert_key("ES")
        for _ in range(600):   # loaded, its prior days worked out, loaded again
            await asyncio.sleep(0.02)
            e = m.amt.entries.get(key)
            if e and e.ready.is_set() and not m.amt.pd_busy and not e.runner.pd_missing:
                break
        old = m.amt.entries[key]
        assert not m.amt.standby
        watched = []
        monkeypatch.setattr(AmtRunner, "observe", lambda self, price, mode: watched.append(self) or [])
        tick = lambda: m.amt.on_live_tick(Tick("ES", clock["t"], 6800.0, 1, 1))   # noqa: E731
        m.amt._rebuild("ES")
        tick()
        await asyncio.sleep(0)                             # the new one's load has begun
        new = m.amt.entries[key]
        assert new is not old and not new.ready.is_set()
        tick()
        assert watched == [old.runner, old.runner]
        await new.ready.wait()
        tick()
        assert watched[-1] is new.runner and not m.amt.standby
        m.amt._rebuild("ES")
        assert m.amt.standby["ES"] is new
        m.amt.set_overrides({"cc_va_pct": 0.7})            # new settings: the old ones alert no more
        tick()
        assert not m.amt.standby and watched[-1] is new.runner and len(watched) == 3
        await settle(m)
    asyncio.run(go())


def test_the_kept_sessions_go_through_the_load_worker():
    """The runner goes to a worker process to load (pickled) with its kept sessions; TickDays adds them to its own count
    once, and none with the 80% Rule section off."""
    import pickle
    from chartsrv import amt
    from chartsrv.amt_runner import TickDays
    kept = [(datetime(2026, 10, 5, 8, 30), datetime(2026, 10, 5, 15, 0)), (datetime(2026, 10, 12, 8, 30), datetime(2026, 10, 12, 15, 0))]
    r = AmtRunner("ES", 0.25, 5, amt.Settings())
    r.tick_kept, r.loaded_at = kept, ms(MON_1531)
    r = pickle.loads(pickle.dumps(r))
    assert days(r) == ["Mon 10-05", "Mon 10-12"]          # before its TickDays is made: the ones kept
    r._tick_days()
    assert days(r) == ["Fri 10-09", "Mon 10-05", "Mon 10-12", "Thu 10-08", "Tue 10-06", "Wed 10-07"]
    assert TickDays(5, "0830-1500:23456", MON_1531, on=False, kept=kept).windows == []
    assert TickDays(0, "0830-1500:23456", MON_1531, kept=kept).windows == []
