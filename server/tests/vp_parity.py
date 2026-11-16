"""Parity check: chartsrv/vp.py against the calculation cores of the three NinjaTrader volume-profile indicators
(tests/vp_csharp, unchanged, or a tatanka-indicators checkout's), compiled with mono.

    tests/vp_csharp/build.sh <workdir> [<tatanka-indicators checkout>]
    python tests/vp_parity.py <workdir>/harness.exe [seed]

Generates synthetic 1-minute bars (across New Year, with a forming minute updated in place) and trades, runs every
case through the C# harness and through vp.py the same way, and compares every number as raw double bits. The PVP
touch cases give the harness each minute as a 1-minute chart's bar too (C lines): its touches against
vp_service.MinuteState's from the minutes, the stored prices (2600.1) against levels of 26001 * 0.1. The Session
cases with a root and Use Instrument Hours on move the window to the root's own hours: the port as vp_service.svp_hours
does (config/futures.json's hours), the C# (Session Volume Profile 1.1.0) with its InstrumentHours.MoveTimes, and both
print the window. A 1.0.0 C# has no such move: those cases differ there, the rest match.

The zone cases run as NinjaTrader set to a time zone (the chart's) with the window in another or the same (Session time
zone Eastern, UTC, ExchangeTemplate on a template in Berlin, Chart on London, Los Angeles or Tokyo): the trades at the
same instants as CT ones, in chart time, put in session time and each session's start and end back in chart time. Use
Instrument Hours on moves each date's window at that date's offset from Central time: the port with
vp_service.ZoneWindows, the C# with its ZoneWindows, and both print each window they run (W lines). The UTC, London,
Berlin and Tokyo cases cross the weeks their offset from Chicago changes, Eastern and UTC both US changes (Nov 1 2026,
Mar 14 2027). Tokyo's 23:30-06:00 crosses its own midnight, and London's 05:00-21:00 and Berlin's 06:00-22:00 cross
CT's (23:00-15:00 CT) but not their own. A C# without ZoneWindows (before its zone fix) runs them as typed: those cases
differ there."""
import math
import random
import struct
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chartsrv import vp   # noqa: E402
from chartsrv.sessions import rth_mover   # noqa: E402
from chartsrv.vp_service import MinuteState, ZoneWindows, svp_hours   # noqa: E402

RTHS = {"ES": ("08:30", "15:00"), "NQ": ("08:30", "15:00"), "CL": ("08:00", "13:30"), "GC": ("07:20", "12:30"),
        "SI": ("07:25", "12:25")}

EPOCH = datetime(1970, 1, 1)
MS = lambda t: int((t - EPOCH) / timedelta(milliseconds=1))   # noqa: E731
DT = lambda ms: EPOCH + timedelta(milliseconds=int(ms))     # noqa: E731
B = lambda x: str(struct.unpack("<q", struct.pack("<d", float(x)))[0])   # noqa: E731
CHICAGO = "America/Chicago"


# --- inputs ---------------------------------------------------------------------------------------------------------
def minutes(rng, first: date, days: int, price: float, tick: float, jump: bool = False, chart: bool = False):
    """M lines: Globex sessions (17:00 the evening before to 16:00) of the trading days, a bar per minute, some
    updated in place as a forming bar is (same index, more volume, a wider range). chart: each as a 1-minute chart's bar
    too (a C line before it, as NinjaTrader takes the chart's bar first at the same time), its prices as stored (2600.1,
    where a level is 26001 * 0.1 = 2600.1000000000004: PVP 1.0.1's touch margin)."""
    out, index, p = [], 0, round(price / tick)
    d = first
    while days > 0:
        if d.weekday() < 5:
            days -= 1
            begin = datetime(d.year, d.month, d.day) - timedelta(hours=7)
            for m in range(23 * 60):
                if 0 < m % 233 < 3:   # a few missing minutes
                    continue
                index += 1
                t = begin + timedelta(minutes=m + 1)       # NinjaTrader stamps a minute at its end
                p += rng.choice((-2, -1, -1, 0, 0, 1, 1, 2))
                lo, hi = p - rng.randint(0, 3), p + rng.randint(0, 3)
                if jump and index == 500:
                    lo, hi = p - 21000, p + 400              # a bar too wide for 20,000 one-tick rows
                vol = rng.choice((0, rng.randint(1, 40), rng.randint(40, 900)))
                up = rng.random() < 0.5
                for k in range(rng.choice((1, 1, 1, 2, 3))):   # updates of the forming minute
                    a, b = (round(lo * tick, 9), round(hi * tick, 9)) if chart else (lo * tick, hi * tick)
                    if chart:
                        out.append(f"C|{a!r}|{b!r}|{MS(t)}")
                    out.append(f"M|{d.isoformat()}|{MS(begin)}|{index}|{a!r}|{b!r}|{float(vol + k * 7)!r}|{int(up)}|{MS(t)}")
                    hi += rng.randint(0, 1)
                    if k == 0 and rng.random() < 0.2:      # the same update again
                        out.append(out[-1])
                if rng.random() < 0.002:
                    out.append("S")
        d += timedelta(days=1)
    return out


def trades(rng, first: date, days: int, price: float, tick: float):
    """T lines: trades from 06:00 to 17:00 on weekdays (and Sunday evening), flat runs included, some with no
    size, a few updated in place (same index, more size: a live tick bar)."""
    out, index, p = [], 0, round(price / tick)
    d = first
    for _ in range(days):
        hours = [(17, 23)] if d.weekday() == 6 else [] if d.weekday() == 5 else [(6, 17)]
        for h0, h1 in hours:
            t = datetime(d.year, d.month, d.day, h0)
            while t < datetime(d.year, d.month, d.day, h1):
                t += timedelta(milliseconds=rng.randint(200, 9000))
                if rng.random() < 0.55:
                    p += rng.choice((-1, 1))
                index += 1
                size = rng.choice((0, 1, 1, 2, 3, 5, 10, rng.randint(11, 200)))
                out.append(f"T|{index}|{p * tick!r}|{float(size)!r}|{MS(t)}")
                if rng.random() < 0.03:
                    out.append(f"T|{index}|{p * tick!r}|{float(size + 4)!r}|{MS(t)}")
            if rng.random() < 0.3:
                out.append("S")
        d += timedelta(days=1)
    return out


def in_zone(body: list, zone: str) -> list:
    """T lines (trades at CT times) at the same instants in zone's time: a chart in that zone."""
    if zone == CHICAGO:
        return body
    ct, z, out = ZoneInfo(CHICAGO), ZoneInfo(zone), []
    for line in body:
        if line[0] == "T":
            f = line.split("|")
            f[4] = str(MS(DT(f[4]).replace(tzinfo=ct).astimezone(z).replace(tzinfo=None)))
            line = "|".join(f)
        out.append(line)
    return out


def cases(seed: int):
    rng = random.Random(seed)
    yearend = minutes(rng, date(2025, 12, 1), 34, 6800.0, 0.25)
    cl = minutes(rng, date(2026, 3, 2), 12, 65.0, 0.01)
    wide = minutes(rng, date(2026, 5, 4), 3, 300.0, 0.01, jump=True)
    out = []
    for anchor in vp.ANCHORS:
        out.append((f"A|{anchor}|0.25|1|200|68|0", yearend))
    out += [("A|Year|0.25|0|1|70|1", yearend), ("A|Month|0.25|1|7|100|0", yearend), ("A|Week|0.01|1|200|68|0", cl),
            ("A|Session|0.01|0|3|1|0", cl), ("A|Year|0.01|0|1|68|0", wide), ("A|Year|0.01|1|50|68|1", wide)]
    for period, mult in (("Daily", 1), ("Daily", 3), ("Weekly", 1), ("Weekly", 2), ("Monthly", 1), ("Monthly", 5), ("Yearly", 1), ("Yearly", 3)):
        out.append((f"P|{period}|{mult}|20|0.25|1|200|68|0", yearend))
    out += [("P|Weekly|1|3|0.25|0|2|70|1", yearend), ("P|Daily|1|20|0.01|1|60|68|0", cl), ("P|Weekly|1|5|0.01|0|1|68|0", wide)]
    # PVP's touches (ObserveTouches) on a 1-minute chart: the port's from the minutes (vp_service.MinuteState)
    touch_gc, touch_es = minutes(rng, date(2026, 6, 1), 12, 2600.0, 0.1, chart=True), minutes(rng, date(2025, 12, 22), 10, 6800.0, 0.25, chart=True)
    out += [("P|Daily|1|20|0.1|1|50|68|0", touch_gc), ("P|Daily|1|20|0.1|0|1|68|0", touch_gc), ("P|Weekly|1|20|0.25|1|200|68|0", touch_es),
            ("P|Daily|2|3|0.25|1|30|70|1", touch_es)]
    es_t, cl_t = trades(rng, date(2026, 2, 1), 9, 6800.0, 0.25), trades(rng, date(2026, 3, 1), 6, 65.0, 0.01)
    for start, end in (("08:30", "15:00"), ("17:00", "16:00"), ("00:00", "00:00"), ("9:30", "16:00"), ("06:00:00", "06:00")):
        out.append((f"V|{start}|{end}|0.25|1|20|68|0", es_t))
    out += [("V|08:30|15:00|0.25|2|20|70|1", es_t), ("V|08:30|15:00|0.25|4|3|68|0", es_t), ("V|08:30|15:00|0.01|1|20|100|0", cl_t),
            ("V|08:30|15:00|0.01|5|20|1|0", cl_t), ("V|17:00|16:00|0.01|1|2|68|1", cl_t)]
    # Use Instrument Hours (SVP 1.1.0): each root's window on and off
    gc_t, si_t = trades(rng, date(2026, 4, 5), 6, 3500.0, 0.1), trades(rng, date(2026, 4, 5), 6, 40.0, 0.005)
    data = {"ES": (es_t, 0.25), "NQ": (es_t, 0.25), "CL": (cl_t, 0.01), "GC": (gc_t, 0.1), "SI": (si_t, 0.005)}
    for root, (body, tick) in data.items():
        for start, end in (("08:30", "15:00"), ("17:00", "16:00"), ("11:00", "12:00"), ("9:30", "14:15:30")):
            for on in (1, 0):
                out.append((f"V|{start}|{end}|{tick!r}|1|20|68|0|{root}|{on}", body))
    # the window in another time zone (session zone|chart zone), moved per date with the setting on
    # Oct 18 to Nov 6 2026: London's and Europe's daylight saving ends Oct 25, Chicago's and New York's Nov 1
    fall = trades(rng, date(2026, 10, 18), 20, 3500.0, 0.1)
    # Mar 7 to 31 2027: Chicago's and New York's starts Mar 14, London's Mar 28 (two weeks later; three in 2026)
    spring = trades(rng, date(2027, 3, 7), 25, 3500.0, 0.1)
    cl_z = trades(rng, date(2026, 6, 7), 5, 65.0, 0.01)
    # London's 05:00-21:00 and Berlin's 06:00-22:00 are 23:00-15:00 CT: across CT's midnight, not their own (00:00-16:00
    # CT in the weeks apart). Tokyo's 23:30-06:00 crosses its own.
    zoned = [("America/New_York", CHICAGO, "GC", 0.1, gc_t, (("09:30", "16:00"), ("18:00", "17:00"), ("12:00", "13:00"))),
             ("America/New_York", CHICAGO, "ES", 0.25, es_t, (("09:30", "16:00"),)),
             ("America/New_York", CHICAGO, "GC", 0.1, fall, (("09:30", "16:00"),)),
             ("America/New_York", CHICAGO, "GC", 0.1, spring, (("09:30", "16:00"),)),
             ("UTC", CHICAGO, "GC", 0.1, fall, (("14:30", "21:00"), ("13:30:15", "20:00"))),
             ("UTC", CHICAGO, "GC", 0.1, spring, (("14:30", "21:00"),)),
             ("Europe/London", "Europe/London", "GC", 0.1, fall, (("14:30", "21:00"), ("23:00", "22:00"))),
             ("Europe/London", "Europe/London", "GC", 0.1, spring, (("14:30", "21:00"), ("05:00", "21:00"))),
             ("Europe/Berlin", CHICAGO, "GC", 0.1, fall, (("15:30", "22:00"), ("06:00", "22:00"))),
             ("America/Los_Angeles", "America/Los_Angeles", "CL", 0.01, cl_z, (("06:30", "13:00"),)),
             ("Asia/Tokyo", "Asia/Tokyo", "GC", 0.1, fall, (("23:30", "06:00"), ("00:30", "06:00"))),
             (CHICAGO, "America/New_York", "GC", 0.1, gc_t, (("08:30", "15:00"),))]
    for zone, chart, root, tick, body, windows in zoned:
        moved = in_zone(body, chart)
        for start, end in windows:
            for on in (1, 0):
                out.append((f"V|{start}|{end}|{tick!r}|1|20|68|0|{root}|{on}|{zone}|{chart}", moved))
    return out


# --- the port, driven as the harness drives the C# ---------------------------------------------------------------------
def _minute(tag, p: vp.MinuteProfile, o: list):
    s = p.snapshot()
    rows = s["rows"]
    o.append(f"{tag}|{p.window[0].isoformat()}|{p.start_chart}|{p.last_chart}|{int(p.first_loaded)}|{len(rows)}")
    if not rows:
        return
    o.append("|".join(B(s[k]) for k in ("total", "va_volume", "max_volume", "max_abs_delta", "poc", "vah", "val", "low_edge", "high_edge"))
             + f"|{s['poc_index']}|{s['vah_index']}|{s['val_index']}|{s['ticks_per_row']}|{int(s['capped'])}")
    o.extend(f"{r[0]},{r[1]},{B(r[2])},{B(r[3])}" for r in rows)


def _session(s: vp.SvpSession, percent, at_least, o: list):
    p = s.snapshot(percent, at_least)
    rows = p["rows"]
    o.append(f"SESSION|{p['start']}|{p['end']}|{p['last']}|{len(rows)}")
    if not rows:
        return
    o.append("|".join(B(p[k]) for k in ("row_size", "max_volume", "total", "va_volume", "poc", "vah", "val", "low_edge", "high_edge"))
             + f"|{p['poc_key']}|{p['vah_key']}|{p['val_key']}")
    o.extend(f"{r[0]},{B(r[1])},{B(r[2])}" for r in rows)


def port(lines: list[str]) -> str:
    o, mode = [], None
    for line in lines:
        f = line.split("|")
        if f[0] == "A":
            mode, series = "A", vp.AnchorSeries(f[1], float(f[2]), f[3] == "1", int(f[4]), float(f[5]), f[6] == "1")
        elif f[0] == "P":
            mode, touches = "P", False
            series = vp.PeriodicSeries(f[1], int(f[2]), int(f[3]), float(f[4]), f[5] == "1", int(f[6]), float(f[7]), f[8] == "1")
            st = MinuteState(series)   # its touches from the minutes, as the server finds them
        elif f[0] == "C":
            touches = True
        elif f[0] == "V":
            mode, start, end = "V", vp.parse_time(f[1]), vp.parse_time(f[2])
            zone = ZoneInfo(f[10]) if len(f) > 10 else None             # the session's time zone and the chart's (OnBarUpdate)
            chart = ZoneInfo(f[11]) if len(f) > 11 else zone
            zw, label = None, None
            if len(f) > 10 and f[10] != CHICAGO and f[9] == "1":   # Use Instrument Hours outside CT: as vp_service.svp_zone_windows
                move = rth_mover(RTHS[f[8]])
                zw = ZoneWindows(start, end, move, zone) if move else None
            elif len(f) > 9 and f[9] == "1":   # Use Instrument Hours: as vp_service runs a window in Central time
                s = svp_hours({"SessionStart": f[1], "SessionEnd": f[2], "SessionTimeZone": "Central", "UseInstrumentHours": True},
                              RTHS[f[8]])
                start, end = vp.parse_time(s["SessionStart"]), vp.parse_time(s["SessionEnd"])
                o.append(f"H|{s['SessionStart']}-{s['SessionEnd']}")
            tick, tpr, keep, percent, at_least = float(f[3]), int(f[4]), int(f[5]), float(f[6]), f[7] == "1"
            sessions, cur, last_index, counted = [], None, -1, 0.0
        elif f[0] == "M":
            td = date.fromisoformat(f[1])
            args = (td, int(f[2]), int(f[3]), float(f[4]), float(f[5]), float(f[6]), f[7] == "1", int(f[8]))
            if mode == "A":
                series.upsert(*args)
            else:
                st.step(args)
        elif f[0] == "T":
            index, vol = int(f[1]), float(f[3])   # TickCounter.Consume
            if index < last_index or vol <= 0 or math.isnan(vol) or math.isinf(vol):
                continue
            if index != last_index:
                last_index, counted = index, 0.0
            volume = max(0.0, vol - counted)
            counted = max(counted, vol)
            if volume <= 0:
                continue
            t = DT(f[4])
            local = t if zone is None else t.replace(tzinfo=chart).astimezone(zone).replace(tzinfo=None)
            w = zw.try_window(local) if zw else vp.try_window(local, start, end)
            if w:
                if cur is None or cur.start_local != w[0]:
                    if cur is not None and w[0] < cur.start_local:
                        continue
                    back = [w[0], w[1]] if zone is None else [x.replace(tzinfo=zone).astimezone(chart).replace(tzinfo=None) for x in w]
                    cur = vp.SvpSession(w[0], MS(back[0]), MS(back[1]), tick, tpr)
                    sessions.append(cur)
                    del sessions[:-keep]
                    if zw and ZoneWindows.label(*w) != label:
                        label = ZoneWindows.label(*w)
                        o.append(f"W|{label}")
                cur.add(float(f[2]), volume, int(f[4]))
        elif f[0] in ("S", "E"):
            if mode == "A":
                if series.current is not None:
                    _minute("AAVP", series.current, o)
                else:
                    o.append("AAVP|none")
            elif mode == "P":
                o.append(f"PVP|{len(series.profiles)}")
                for p in series.profiles:
                    _minute("PROFILE", p, o)
                    if touches:
                        t = getattr(p, "touch", {})
                        o.append("TOUCH|" + "|".join(str(t[k]) if k in t else "-" for k in ("poc", "vah", "val")))
            else:
                o.append(f"SVP|{len(sessions)}")
                for s in sessions:
                    _session(s, percent, at_least, o)
            if f[0] == "E":
                o.append("END")
    return "\n".join(o) + "\n"


def run(harness: str, seed: int = 1) -> list[str]:
    """The cases where the two differ (empty: all the same), with the first differing line."""
    bad = []
    for head, body in cases(seed):
        lines = [head, *body, "E"]
        want = subprocess.run(["mono", harness], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True).stdout
        got = port(lines)
        if want != got:
            w, g = want.splitlines(), got.splitlines()
            i = next((k for k in range(min(len(w), len(g))) if w[k] != g[k]), min(len(w), len(g)))
            bad.append(f"{head}: line {i}: C# {w[i] if i < len(w) else '<end>'!r} vs port {g[i] if i < len(g) else '<end>'!r}")
        else:
            print(f"same  {head}  ({len(want.splitlines())} lines)")
    return bad


if __name__ == "__main__":
    problems = run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    for p in problems:
        print("DIFF", p)
    sys.exit(1 if problems else 0)
