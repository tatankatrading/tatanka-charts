"""Parity check: the HTF EMA port (chartsrv/htfema.py on the server, htfEmaValues in web/studies.js) against
the original C# indicator code (compiled with mono).

    tests/htfema_csharp/build.sh <tatanka-indicators checkout> <workdir>
    python tests/htfema_parity.py <workdir> [days] [chart: 5m | 1D | t3 | 15s] [seed] [ES|NQ|CL|GC] [settings json] [rth]
    python tests/htfema_parity.py <workdir> [days] [chart: 5m | 1m | 4h] [seed] [ES|NQ|CL|GC] [settings json] live
                                  [bip1first] [tickreplay] [break] [gaps] [barclose]

Generates synthetic 1-minute Globex data and the chart and higher-timeframe bars, records the order
NinjaTrader processes them in (by bar close time; the chart bar first on a tie; the last bar of each series loaded
forming, as the data ends mid-session), runs the C# on that record, and the browser code (node) on the chart bars
with the server's payload. Every plotted value must match.
"t3" is a tick-style chart (3-minute buckets treated as untimed bars, closing when the next one opens); "15s" a seconds
chart built from synthetic trades. rth: an RTH chart's bars and higher-timeframe bars (the instrument's regular hours), as
on an RTH trading-hours template.
The source series (settings json: HtfPeriodType, HtfPeriod) is built here as NinjaTrader builds its bars, on its own
(source_bars): Minute bars from the session open; Second bars from the synthetic trades (each minute's, as in live), a
grid from the session open cut at its close, one only where a trade printed; Day bars one per session, Day N, Week,
Month and Year bars those grouped from fixed starts (trading days Monday-Friday, Monday weeks, months, years), each
closing with its last day's close and the last one, still forming, at its last weekday's. The server's payload for a
Second source is thinned to what the chart reads, as the endpoint thins it.

live: a live chart, as NinjaTrader runs it with Calculate.OnPriceChange (live()). The chart loads at 13:37:30 (break:
16:30, in the daily halt) and takes trades to 20:07, past the 16:00 close and the 17:00 open: every minute's trades
(its open, high, low and close, prices repeated in between) go to the chart series and the source series, each called
on the first trade of a bar and on a new price, the chart series first on each trade (bip1first: the source series
first). The historical pass runs each bar at its close but the last of each series, which runs with the trades up to
the load and then takes the live ones; tickreplay: Tick Replay, every bar from the trades (its historical bars keep
their last call's values; the browser loads history as a reload, so a source closing inside chart bars fails there).
The browser runs as the page does (htfEmaLive): it loads at the load with the server's answer then, and each trade is
an update of its forming bar (a call where it opens the bar or brings a new price) with the answer from the trade
before; the answer with that trade comes before the next. (The page itself gets bar updates in the server's 150 ms
batches, several trades as one update, and asks for a closed source bar on the first update after its close, at most
every 10 seconds: not modeled here; INDICATORS.md says what they change.) Checked: on every call the C#'s value is what
the browser shows once that answer is in (bip1first: or, the source series first on a trade that opens a source bar,
what it shows at the bar's next call: counted, not failed), every bar the C# ends on is the browser's (a realtime bar
keeps what its last call showed on both; counted where that is not what a reload shows), a reload at the end is the same
on both, and Exact holds still while a source bar forms. gaps: a thin market, no trades from :57 to :00 of each hour and
one at :01, so the first trade after an hour opens a 1m chart bar a gap after the one before, and it has no other price
change. barclose: Calculate set to OnBarClose, each bar once at its close, checked against the reload.
A Second source live: its bars from the same trades the chart takes, and the server's answer at each moment from the
minutes and trades by then, thinned as the endpoint thins it."""
import bisect
import json
import random
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from amt_parity import synth                          # noqa: E402
from chartsrv import htfema                           # noqa: E402
from chartsrv.sessions import session_start          # noqa: E402
from chartsrv.tpo_service import chart_bars           # noqa: E402

ROOTS = {"ES": (0.25, 6800.0), "NQ": (0.25, 25000.0), "CL": (0.01, 65.0), "GC": (0.10, 3500.0)}
RTHS = {"ES": ("08:30", "15:00"), "NQ": ("08:30", "15:00"), "CL": ("08:00", "13:30"), "GC": ("07:20", "12:30")}
WEB = Path(__file__).resolve().parents[2] / "web"
LAST_MS = 253_402_300_799_000   # 9999-12-31 23:59:59 UTC, C#'s last DateTime second: a tick chart's last bar's close


def prints_of(m1, seed, tick):
    """Synthetic trades (trades(): each minute's open first, its close last) as the store gives them to a Second source
    and a seconds chart: (second ms, its last price)."""
    rng = random.Random(seed * 104729 + 3)
    out = {}
    for m in m1:
        for t, p in trades(m, rng, tick):
            out[t // 1000 * 1000] = p
    return sorted(out.items())


def grid_bars(prints, seconds, rth=None):
    """Seconds bars of the prints as NinjaTrader's: a grid of `seconds` from the session's 17:00 CT open (an RTH chart:
    the RTH open), the last cut at the session's 16:00 close (the RTH close), stamped with the close; one only where a
    trade printed. [open, close, -, -, -, -, close price], as chart_bars."""
    period, out = seconds * 1000, []
    for t, p in prints:
        if rth:
            day = htfema.ct(t)
            o = htfema.epoch_ms(day.replace(hour=int(rth[0][:2]), minute=int(rth[0][3:]), second=0, microsecond=0))
            e = htfema.epoch_ms(day.replace(hour=int(rth[1][:2]), minute=int(rth[1][3:]), second=0, microsecond=0))
            if not o <= t < e:
                continue
        else:
            o = session_start(t)
            e = htfema.epoch_ms((htfema.ct(o) + timedelta(days=1)).replace(hour=16, minute=0))
        b = o + (t - o) // period * period
        if out and out[-1][0] == b:
            out[-1][6] = p
        else:
            out.append([b, min(b + period, e), None, None, None, None, p])
    return out


def _group(day, kind, n):
    if kind == "Day":
        return ((day.toordinal() - 1) // 7 * 5 + min(day.weekday(), 4)) // n
    if kind == "Week":
        return (day.toordinal() - 1) // 7 // n
    if kind == "Month":
        return (day.year * 12 + day.month - 1) // n
    return day.year // n


def source_bars(m1, s, rth=None, prints=None):
    """The source series as NinjaTrader builds it (see the header), [open, close, -, -, -, -, close price] each."""
    kind, n = s["HtfPeriodType"], s["HtfPeriod"]
    if kind == "Second" and n % 60:
        return grid_bars(prints, n, rth)
    if kind in ("Second", "Minute"):
        return chart_bars(m1, n // 60 if kind == "Second" else n, rth)
    out, g = [], None
    for b in chart_bars(m1, 1440, rth):   # a session a day (no holidays here)
        day = htfema.ct(b[1]).date()       # its close, 16:00 (the RTH close) on its trade date
        k = _group(day, kind, n)
        if out and k == g:
            out[-1][1], out[-1][6] = b[1], b[6]
        else:
            out.append([b[0], b[1], None, None, None, None, b[6]])
            g, last = k, day
        last = day
    if out:   # the last, maybe forming: at its last weekday's close
        d = last
        while _group(d + timedelta(days=1), kind, n) == g:
            d += timedelta(days=1)
            if d.weekday() < 5:
                last = d
        out[-1][1] = htfema.day_close(last, rth)
    return out


def thin_for(chart, rth):
    """What the endpoint thins a Second source's payload to on this chart (htfema.source_job)."""
    if chart == "t3":
        return None
    seconds = {"15s": 15, "1D": 86400}.get(chart) or int(chart[:-1]) * 60
    return htfema.chart_key(seconds * 1000, rth)


def js_values(bars, d, period_ms, cal=False, after=None):
    """htfEmaValues from web/studies.js, run by node on the given bars and server payload (cal: 2D, 3D, W, M bars;
    after: when the bar after the last one opens, where the chart knows it)."""
    js = f"""
const vm = require('vm'), fs = require('fs'), ctx = {{}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(WEB / 'studies.js'))}, 'utf8') + ';this.f=htfEmaValues;', ctx);
const a = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(ctx.f(a.bars, a.d, a.p, a.cal, a.after).map((x) => (Number.isNaN(x) ? null : x))));"""
    data = json.dumps({"bars": bars, "d": d, "p": period_ms, "cal": cal, "after": after})
    return json.loads(subprocess.run(["node", "-e", js], input=data, capture_output=True, text=True, check=True).stdout)


def run_cs(work, lines):
    """The C# harness on a record (Tail.cs has the format): its output lines."""
    rec, out = work / "htfema_record.txt", work / "htfema_cs.txt"
    rec.write_text("\n".join(lines) + "\n")
    subprocess.run(["mono", str(work / "harness.exe"), str(rec), str(out)], check=True)
    return out.read_text().splitlines()


def setup(days, chart, seed, root, s, rth=None):
    tick, price = ROOTS[root]
    m1 = synth(days, seed, datetime(2026, 10, 16, 12, 7), tick, price)
    now = m1[-1].t + 7_200_000
    timed = chart != "t3"
    secs = s["HtfPeriodType"] == "Second" or chart == "15s"
    prints = prints_of(m1, seed, tick) if secs else None
    if chart == "15s":
        kind, size, period = "time", 15, ("Second", 15)
    elif timed:
        kind, size = ("time", 86400) if chart == "1D" else ("time", int(chart[:-1]) * 60)
        period = htfema.chart_period(kind, size)
    else:
        kind, size = "tick", 3
        period = htfema.chart_period(kind, size)
    scaled = htfema.GetScaledLength(s, *period)
    cb = grid_bars(prints, 15, rth) if chart == "15s" else chart_bars(m1, size // 60 if timed else 3, rth)
    bars = [{"t": b[0], "c": b[6]} for b in cb]
    closes = [b[1] for b in cb] if timed else [cb[i + 1][0] for i in range(len(cb) - 1)] + [2**53]
    return m1, now, timed, kind, size, scaled, bars, closes, prints, period


def trades(m, rng, tick, one=False):
    """A minute's trades as (ms, price): its open first, its close last, its high and low on the way, some prices
    again in between (no OnPriceChange call); all inside the minute (a time bar holds the trades up to its close).
    The first is half the time a tick or two from the open: synth's minutes open at the last close, real ones often
    don't, and the trade that opens a source bar inside a chart bar is then a new price for the chart series too.
    one: a single trade, at the minute's close."""
    if one:
        return [(m.t + rng.randrange(100, 59_900), m.c)]
    o = m.o if rng.random() < .5 else round(m.o / tick + rng.choice((-2, -1, 1, 2))) * tick
    path = [o, *((m.h, m.l) if rng.random() < .5 else (m.l, m.h)), m.c]
    lo, hi = round(m.l / tick), round(m.h / tick)
    for _ in range(rng.randint(0, 6)):
        i = rng.randint(1, len(path) - 1)
        path.insert(i, path[i - 1] if rng.random() < .3 else rng.randint(lo, hi) * tick)
    return [(m.t + dt, p) for dt, p in zip(sorted(rng.sample(range(100, 59_900), len(path))), path)]


def live(work, days=10, chart="5m", seed=7, root="ES", s=None, bip1first=False, tickreplay=False, halt=False,
         gaps=False, barclose=False):
    """A live chart: NinjaTrader's calls (Calculate.OnPriceChange) on the C#, the browser on every trade, a reload of
    both at the end. Returns the mismatches: live (the C#'s call against the browser's value once the server's answer
    to that trade is in), final (the bar the C# ends on against the browser's), reload (the C#'s reload against the
    browser's), moved (Exact changing while no source bar the C# has closed) and the counts: waits (calls the browser
    shows only once that answer is in: a source bar closed by the chart bar's open, NinjaTrader's catch-up), ahead
    (bip1first: calls and bars the C# counts a source bar at that the browser counts from the next call), kept (bars
    whose last call's value is not a reload's, the same on both).
    barclose: Calculate set to OnBarClose instead, each bar run once at its close (historical to the last one closed
    at the load, then live with the trade that opens the next, the chart bar first where both close on one trade):
    checked against the reload only (the browser runs as OnPriceChange)."""
    s = s or htfema.settings({})
    tick, price = ROOTS[root]
    m1 = synth(days, seed, datetime(2026, 10, 15, 20, 7), tick, price)
    if gaps:
        m1 = [m for m in m1 if htfema.ct(m.t).minute not in (57, 58, 59, 0)]
    load = htfema.epoch_ms(htfema.ct(m1[-1].t).replace(hour=16 if halt else 13, minute=30 if halt else 37, second=30))
    minutes = int(chart[:-1]) * (60 if chart[-1] == "h" else 1)
    scaled = htfema.GetScaledLength(s, "Minute", minutes)
    rng = random.Random(seed * 7919 + 1)
    starts = [m.t for m in m1]
    first = 0 if tickreplay else bisect.bisect_left(starts, load - 86_400_000)
    # A Second source is built from the trades, every minute's (its history's too): the ones the chart takes live
    secs = htfema.source(s)[0] == "second"
    made = [(m, trades(m, rng, tick, gaps and htfema.ct(m.t).minute == 1)) for m in (m1 if secs else m1[first:])]
    tr = [x for _, ts in made for x in ts]
    prints = sorted({t // 1000 * 1000: p for t, p in tr}.items()) if secs else None
    cb, hb = chart_bars(m1, minutes), source_bars(m1, s, prints=prints)
    copen, hopen, hclose = [b[0] for b in cb], [b[0] for b in hb], [b[1] for b in hb]
    ticks = [(t, p, bisect.bisect_right(copen, m.t) - 1, bisect.bisect_right(hopen, t) - 1)
             for m, ts in made[first if secs else 0:] for t, p in ts]
    before = [x for x in ticks if x[0] <= load]
    lc, lh, lp = before[-1][2], before[-1][3], before[-1][1]   # the bars the load ends in, and its last price
    header = f"S|{s['HtfPeriodType']}|{s['HtfPeriod']}|{s['EmaLength']}|{s['Mode']}|Minute|{minutes}"
    # the reload: every bar at its close (the last ones hold every trade there is)
    ev = sorted([(b[1], 0, b[6]) for b in cb] + [(b[1], 1, b[6]) for b in hb], key=lambda e: (e[0], e[1]))
    reload = run_cs(work, [header, f"C|{len(cb)}|{len(hb)}"] + [f"{'PH'[k]}|{x!r}|{t}" for t, k, x in ev])
    reload = [x[2:] for x in reload[1:]]
    if barclose:
        lines = [header, "K|OnBarClose", f"C|{lc + 1}|{lh + 1}"]
        ev = [(cb[j][1], 0, cb[j][6]) for j in range(lc)] + [(hb[j][1], 1, hb[j][6]) for j in range(lh)]
        lines += [f"{'PH'[k]}|{x!r}|{t}" for t, k, x in sorted(ev, key=lambda e: (e[0], e[1]))] + ["R"]
        cur = [lc, lh]
        for _, _, jc, jh in ticks[len(before):]:
            for k, j in ((0, jc), (1, jh)):
                if j != cur[k]:   # the trade that opens bar j: the one before has closed
                    b = (cb, hb)[k][cur[k]]
                    lines.append(f"t|{k}|1|{b[6]!r}|{b[1]}")
                    cur[k] = j
        cs = run_cs(work, lines)
        assert cs[0] == f"S|{scaled}", (cs[0], scaled)
        rt = [x.split("|") for x in cs[1:] if x[0] == "v"]
        assert [int(j) for _, j, _ in rt] == list(range(lc, cur[0])), "one call a bar, at its close"
        shown = [x[2:] for x in cs[1:] if x[0] == "V"] + [v for _, _, v in rt]
        return {"live": [], "final": [(j, v, reload[j]) for j, v in enumerate(shown) if v != reload[j]], "reload": [],
                "moved": 0, "waits": 0, "ahead": 0, "kept": 0, "calls": len(rt), "bars": len(shown), "turns": cur[1] - lh,
                "rt_bars": len(rt)}
    lines, cur, last = [header, f"C|{lc + 1}|{lh + 1}"], [-1, -1], [None, None]
    if tickreplay:
        lines.append("T|1|1")
    else:   # each bar once at its close, the last of each series with the trades up to the load
        ev = [(cb[j][1], 0, lp if j == lc else cb[j][6]) for j in range(lc + 1)]
        ev += [(hb[j][1], 1, lp if j == lh else hb[j][6]) for j in range(lh + 1)]
        lines += [f"{'PH'[k]}|{x!r}|{t}" for t, k, x in sorted(ev, key=lambda e: (e[0], e[1]))]
        cur, last = [lc, lh], [lp, lp]
    hist_calls, realtime, trades_live = 0, False, []
    for t, p, jc, jh in ticks if tickreplay else ticks[len(before):]:
        if t > load and not realtime:
            lines.append("R")
            realtime = True
        called = False
        for k in ((1, 0) if bip1first else (0, 1)):
            j = (jc, jh)[k]
            if j == cur[k] and p == last[k]:
                continue   # OnPriceChange: no call
            lines.append(f"t|{k}|{int(j != cur[k])}|{p!r}|{(cb, hb)[k][j][1]}")
            cur[k], last[k] = j, p
            if k == 0:
                called = t > load
                hist_calls += t <= load
        if t > load:   # every trade reaches the browser as an update of the forming bar
            trades_live.append((t, p, jc, called))
    cs = run_cs(work, lines)
    assert cs[0] == f"S|{scaled}", (cs[0], scaled)
    shown = {}
    for i, x in enumerate(v for v in cs[1:] if v[0] == "V"):
        shown[i] = x[2:]
    rt = [x.split("|") for x in cs[1:] if x[0] == "v"]
    for _, j, v in rt:
        shown[int(j)] = v
    rt = rt[hist_calls:]
    # The browser, as the page runs it (htfEmaLive in web/studies.js): it loads at the load and asks the server then; each
    # trade after is an update of the forming bar (a call where it opens a bar or brings a new price) with the server's
    # answer from the trade before, and it asks again: the answer with this trade comes before the next one.
    bars = [{"t": b[0], "c": b[6]} for b in cb]
    answers, which = [], {}
    tr_t, pr_t = [t for t, _ in tr], [t for t, _ in prints or ()]

    def server(now):   # what the server has at now: the minutes traded in by then; a Second source, the trades by then
        mins = m1[:bisect.bisect_right(starts, now)]
        if not secs or not mins:
            return mins, {}
        sofar = [p for _, p in tr[bisect.bisect_left(tr_t, mins[-1].t):bisect.bisect_right(tr_t, now)]]
        mins = mins[:-1] + ([replace(mins[-1], h=max(sofar), l=min(sofar), c=sofar[-1])] if sofar else [])
        sec = now // 1000 * 1000   # its second so far: its last trade by now
        at = [p for _, p in tr[bisect.bisect_left(tr_t, sec):bisect.bisect_right(tr_t, now)]]
        done = prints[:bisect.bisect_left(pr_t, sec)] + ([(sec, at[-1])] if at else [])

        def closes(lo, hi):
            return done[bisect.bisect_left(pr_t, lo):bisect.bisect_left(pr_t, hi)] if hi <= sec else \
                [x for x in done[bisect.bisect_left(pr_t, lo):] if x[0] < hi]
        return mins, {"second_closes": closes, "thin": htfema.chart_key(minutes * 60_000, None)}

    def answer(now, key=None):   # the server's answer at now: at a trade's time, one per number of source bars closed
        key = bisect.bisect_right(hclose, now) if key is None else key
        if key not in which:
            which[key] = len(answers)
            mins, kw = server(now)
            answers.append(htfema.compute(s, mins, minutes, scaled, bars[0]["t"], now, **kw))
        return which[key]
    at_load = answer(load, "load")
    events, prev = [], at_load
    for t, p, jc, called in trades_live:
        events.append([jc, p, prev, answer(t), int(called)])
        prev = events[-1][3]
    js = f"""
const vm = require('vm'), fs = require('fs'), ctx = {{}};
vm.runInNewContext(fs.readFileSync({json.dumps(str(WEB / 'studies.js'))}, 'utf8')
  + ';this.f=htfEmaValues;this.live=htfEmaLive;this.keep=htfEmaKeep;', ctx);
const a = JSON.parse(fs.readFileSync(0, 'utf8')), nul = (x) => (Number.isNaN(x) ? null : x);
a.d.forEach((d, i) => {{ d.htf = (i ? a.d[i - 1].htf.slice(0, d.keep) : []).concat(d.htf); }});
const bars = a.bars.slice(0, a.lc + 1).map((b) => ({{ ...b }}));
bars[a.lc].c = a.lp;   // the forming bar at the load: its price then
const k = ctx.keep(bars[a.lc].t, a.stamped), show = (d, update) => ctx.live(k, bars, d, a.p, false, undefined, update);
show(a.d[a.load], false);
const calls = [];
for (const [j, p, before, after, call] of a.ev) {{
  while (bars.length <= j) bars.push({{ ...a.bars[bars.length] }});
  bars[j] = {{ t: a.bars[j].t, c: p }};
  const at = show(a.d[before], true)[j];   // the update: the answer it has
  const now = show(a.d[after], false)[j];  // the answer to it
  if (call) calls.push([nul(at), nul(now), nul(ctx.f(bars, a.d[after], a.p)[j])]);
}}
const end = a.ev.length ? a.ev[a.ev.length - 1][3] : a.load;
console.log(JSON.stringify({{ calls, shown: show(a.d[end], false).map(nul), reload: ctx.f(bars, a.d[end], a.p).map(nul) }}));"""
    sent, prev = [], []
    for d in answers:   # each answer's htf: what it has of the one before's, then its own
        k = next((i for i, (x, y) in enumerate(zip(prev, d["htf"])) if x != y), min(len(prev), len(d["htf"])))
        sent.append({**d, "keep": k, "htf": d["htf"][k:]})
        prev = d["htf"]
    stamped = s["HtfPeriodType"] in ("Second", "Minute")   # barsTimeStamped: the chart is a Minute chart here
    data = json.dumps({"bars": bars, "d": sent, "p": minutes * 60_000, "lc": lc, "lp": lp, "load": at_load, "ev": events,
                       "stamped": stamped})
    web = json.loads(subprocess.run(["node", "-e", js], input=data, capture_output=True, text=True, check=True).stdout)
    same = lambda v, w: v == "-" if w is None else v != "-" and float(v) == w   # noqa: E731  (G17: the exact double)
    # Every call: what the browser shows once the server's answer to it is in (waits: before that, unlike the C#, as at
    # the first trade after a source bar closes on a chart bar's open). bip1first (the source series first on a trade
    # that opens a source bar, which the browser takes as NinjaTrader's default, the chart series first): the C# counts
    # that source bar at once; the browser at the chart bar's next call (ahead, allowed there only).
    live_bad, waits, ahead, moved, prev, prev_n, alt_last = [], 0, 0, 0, None, None, {}
    times = [before[-1][0]] + [x[0] for x in trades_live]   # each trade's, after the last trade by the load's
    for (_, j, v), (i, t), (at, now, alt) in zip(rt, [(i, x[0]) for i, x in enumerate(trades_live) if x[3]], web["calls"]):
        j, prev_t = int(j), times[i]
        if same(v, now):
            waits += not same(v, at)
        elif bip1first and same(v, alt):
            ahead += 1
        else:
            live_bad.append((j, v, now, t))
        alt_last[j] = alt
        # the source bars the C# has by then: those a trade before this one closed (bip1first: this one too), and on a
        # chart bar's first call those closed by its open where barsTimeStamped
        n = bisect.bisect_right(hclose, t) if bip1first else \
            max(bisect.bisect_right(hclose, prev_t), bisect.bisect_right(hclose, cb[j][0]) if stamped else 0)
        if s["Mode"] == "ExactHTF" and prev is not None and v != prev and n == prev_n:
            moved += 1
        prev, prev_n = v, n
    # Every bar: what NinjaTrader's live chart ends on against the browser's (a bar that ran live keeps its last call's
    # value on both), and a reload of both. Source bars closing inside the first chart bar: NinjaTrader drops them (its
    # first bar shows nothing), this app's first bar shows the line as of them, as in the historical check.
    skip = int(bool(hb) and hb[0][1] < cb[0][1] and reload[0] == "-" and web["reload"][0] is not None)
    final = [(j, shown[j], web["shown"][j]) for j in range(skip, len(cb)) if not same(shown[j], web["shown"][j])]
    if bip1first:
        ahead_bars = [j for j, v, _ in final if j in alt_last and same(v, alt_last[j])]
        final = [x for x in final if x[0] not in ahead_bars]
    reloaded = [(j, reload[j], web["reload"][j]) for j in range(skip, len(cb)) if not same(reload[j], web["reload"][j])]
    kept = sum(shown[j] != reload[j] for j in range(skip, len(cb)) if same(shown[j], web["shown"][j]))
    turns = bisect.bisect_right(hclose, trades_live[-1][0]) - bisect.bisect_right(hclose, load) if trades_live else 0
    return {"live": live_bad, "final": final, "reload": reloaded, "moved": moved, "waits": waits, "ahead": ahead,
            "calls": len(rt), "bars": len(cb), "turns": turns, "rt_bars": len({x[2] for x in trades_live if x[3]}),
            "kept": kept}


def main():
    flags = {"rth", "live", "bip1first", "tickreplay", "break", "gaps", "barclose"}
    argv = [a for a in sys.argv if a not in flags]
    work = Path(argv[1])
    days = int(argv[2]) if len(argv) > 2 else 40
    chart = argv[3] if len(argv) > 3 else "5m"
    seed = int(argv[4]) if len(argv) > 4 else 7
    root = argv[5] if len(argv) > 5 else "ES"
    s = htfema.settings(json.loads(argv[6]) if len(argv) > 6 else {})
    if "live" in sys.argv:
        on = [f for f in ("bip1first", "tickreplay", "break", "gaps", "barclose") if f in sys.argv]
        r = live(work, days, chart, seed, root, s, *(f in on for f in ("bip1first", "tickreplay", "break", "gaps", "barclose")))
        how = ", ".join(on) or "chart series first"
        last = "a reload" if "barclose" in on else "the browser's"
        print(f"{root} {chart}, {s['Mode']} {s['HtfPeriodType']} {s['HtfPeriod']} x{s['EmaLength']} live ({how}): "
              f"{r['calls']} live calls on {r['rt_bars']} bars, {r['turns']} source bars closed live; "
              f"live values unlike the browser's {len(r['live'])} (and {r['waits']} like it only once the server's answer "
              f"to that trade came: a source bar closed by the chart bar's open"
              + (f"; {r['ahead']} a call ahead of it: the source series first" if "bip1first" in on else "") + "), "
              f"bars NinjaTrader's live chart ends on unlike {last} {len(r['final'])} ({r['kept']} keeping a last "
              f"call's value unlike a reload's, on both), reloads unlike {len(r['reload'])}, "
              f"Exact moves inside a source bar {r['moved']}")
        for j, v, w, t in r["live"][:5]:
            print(f"  live: bar {j} at {htfema.ct(t):%a %H:%M:%S}: C# {v}  browser {w}")
        for j, a, b in r["final"][:5]:
            print(f"  final: bar {j}: C# live {a}  {'C# reload' if 'barclose' in on else 'browser'} {b}")
        for j, a, b in r["reload"][:5]:
            print(f"  reload: bar {j}: C# {a}  browser {b}")
        if r["live"] or r["final"] or r["reload"] or r["moved"]:
            print("PARITY FAIL")
            sys.exit(1)
        print("PARITY OK")
        return
    rth = RTHS[root] if "rth" in sys.argv else None
    m1, now, timed, kind, size, scaled, bars, closes, prints, (ctype, cval) = setup(days, chart, seed, root, s, rth)
    secs = htfema.source(s)[0] == "second"
    d = htfema.compute(s, m1, size // 60 if timed and chart != "15s" else None, scaled, bars[0]["t"], now, rth=rth,
                       chart_seconds=15 if chart == "15s" else None, thin=thin_for(chart, rth) if secs else None,
                       second_closes=(lambda lo, hi: [p for p in prints if lo <= p[0] < hi]) if prints else None)
    # NinjaTrader's processing order: by close time, the chart bar (BarsInProgress 0) first on a tie. The data ends
    # mid-session, no trade after it: the last bar of each series is the one loaded still forming (BarsArray[n].Count).
    hb = source_bars(m1, s, rth, prints)
    events = [(c, 0, b["c"]) for b, c in zip(bars, closes)] + [(b[1], 1, b[6]) for b in hb]
    events.sort(key=lambda e: (e[0], e[1]))
    lines = [f"S|{s['HtfPeriodType']}|{s['HtfPeriod']}|{s['EmaLength']}|{s['Mode']}|{ctype}|{cval}",
             f"C|{len(bars)}|{len(hb)}"]
    lines += [f"{'P' if k == 0 else 'H'}|{x!r}|{min(t, LAST_MS)}" for t, k, x in events]
    cs = run_cs(work, lines)
    assert cs[0] == f"S|{scaled}", (cs[0], scaled)
    want = [None if x == "V|-" else float(x[2:]) for x in cs[1:]]
    got = js_values(bars, d, size * 1000 if timed else 0)
    # Source bars closing inside the first chart bar: NinjaTrader drops them (OnBarUpdate returns while CurrentBars[0] < 0),
    # its first bar shows nothing; this app's shows the line as of them (as of its settled history on a real chart).
    skip = int(bool(hb) and hb[0][1] < closes[0] and want[:1] == [None] and got[:1] != [None])
    bad = [(i, a, b) for i, (a, b) in enumerate(zip(want, got)) if a != b and i >= skip]
    if len(want) != len(got) or bad:
        print(f"PARITY FAIL: {len(want)} C# values, {len(got)} JS values, {len(bad)} differ")
        for i, a, b in bad[:10]:
            print(f"  bar {i}: C# {a}  JS {b}")
        sys.exit(1)
    plotted = sum(v is not None for v in got)
    sent = f" ({len(d['htf'])} sent)" if secs else ""
    sent += ", the first bar not compared (source bars closed in it before the chart had one)" if skip else ""
    print(f"PARITY OK: {root} {chart}{' RTH' if rth else ''}, {s['Mode']} {s['HtfPeriodType']} {s['HtfPeriod']} x{s['EmaLength']}, scaled {scaled}, "
          f"{len(got)} bars ({plotted} plotted), {sum(1 for e in events if e[1])} HTF bars{sent}; every value equal")


if __name__ == "__main__":
    main()
