"""Parity check: chartsrv/tpo.py against the original C# TPO Profile code (compiled with mono).

    tests/tpo_csharp/build.sh <tatanka-indicators checkout> <workdir>
    python tests/tpo_parity.py <workdir> [days] [minutes] [seed] [root] [settings json] [rth] [zone=<IANA name>] [holidays]

Generates synthetic 1-minute Globex data, runs it through tpo_service.run (recording every input the
indicator sees: daily bars, then each closed chart bar with its close stamp), replays the record through
both engines and compares every value they produce. rth: an RTH chart's bars, sessions and daily bars (the
instrument's regular hours), as on an RTH trading-hours template. holidays: the days generated end on Dec 1 2026, with
config/holidays.json's halts and early closes for the root (no minutes in them), so they hold Thanksgiving and the day
after: a bar a halt or early close cuts short ends there, as NinjaTrader ends a partial holiday's session.
Use Instrument Hours (UseInstrumentHours, on by default):
the port runs Session Time at the root's own hours (tpo_service.own_hours, config/futures.json's), the C# (v1.1.0) moves it
itself from the typed one and the root (RunSessionTime, its own table) and both print the session it runs; a v1.0.0 C#
runs it as typed, so it matches with UseInstrumentHours false only.

zone (v1.1.0): the C# runs as NinjaTrader set to that time zone (America/New_York, say): its record has the bars' times
and the typed Session Time in that zone (0830-1500 is 0930-1600 in New York), and it moves Session Time itself, in
Central time, at each bar's offset from it (SetSessionTime, SessionAt). Its times are put back in CT and compared with
this port's run in CT, which must be the same profiles; the Session Time it runs is checked too (W lines). A zone whose
offset from CT changes over the days generated is refused: the CT run has one Session Time.

The port's one deliberate change (tpo.py) is undone for the comparison: the C# prices a finished session's rows with
the row height of the bar that saves it. That is a new height whenever the daily ATR moved before the save: with
Filter by Time off (saved at the 17:00 open), and on an RTH chart (saved at the next RTH open)."""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from amt_parity import synth                  # noqa: E402
from chartsrv import sessions, tpo            # noqa: E402
from chartsrv.config import load_holidays     # noqa: E402
from chartsrv.tpo_service import Daily, own_hours, run   # noqa: E402

ROOTS = {"ES": (0.25, 6800.0), "NQ": (0.25, 25000.0), "CL": (0.01, 65.0), "GC": (0.10, 3500.0), "SI": (0.005, 40.0)}
RTHS = {"ES": ("08:30", "15:00"), "NQ": ("08:30", "15:00"), "CL": ("08:00", "13:30"), "GC": ("07:20", "12:30"),
        "SI": ("07:25", "12:25")}
CALENDARS = {"ES": "equity", "NQ": "equity", "CL": "energy", "GC": "metals", "SI": "metals"}
D = lambda x: repr(float(x))
T = lambda t: "MIN" if t == tpo.DMIN else t.strftime("%Y-%m-%dT%H:%M:%S")
PT = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def write_record(rec, s, tick, path, root, zone=None):
    """s: the settings as typed (the C# moves Session Time itself, given the root and Use Instrument Hours); zone: the
    chart's time zone (IANA), which rec's times and s's Session Time are in; None: CT, NinjaTrader on Central time."""
    lines = [f"S|{D(tick)}|{int(s['FilterByTime'])}|{s['SessionTime']}|{s['BlockMinutes']}|{int(s['AutoRowHeight'])}|"
             f"{s['TargetRows']}|{s['RowHeightTicks']}|{root}|{int(s['UseInstrumentHours'])}" + (f"|{zone}" if zone else "")]
    for r in rec:
        if r[0] == "D":
            lines.append(f"D|{D(r[1])}|{D(r[2])}|{D(r[3])}")
        else:
            lines.append(f"B|{T(r[1])}|{D(r[2])}|{D(r[3])}|{int(r[4])}|{T(r[5])}")
    lines.append("E")
    Path(path).write_text("\n".join(lines) + "\n")


def dump_profile(o, tag, p):
    vah, val = tpo.TpoProfile.GetValueArea(p)
    o.append(f"{tag}|{T(p.TimeStart)}|{T(p.TimeEnd)}|{D(p.TickStep)}|{D(p.PocPrice)}|{p.MaxCount}|{D(vah)}|{D(val)}|{len(p.Rows)}")
    for r in p.Rows:
        o.append(f"R|{D(r.Price)}|{','.join(map(str, r.Letters))}|{''.join(tpo.TpoProfile.GetLetter(i) for i in r.Letters)}")


CT = ZoneInfo("America/Chicago")


def to_zone(t, zone, back=False):
    """A naive CT time in zone (back: a naive time in zone in CT); MIN as it is."""
    if t == tpo.DMIN:
        return t
    a, b = (ZoneInfo(zone), CT) if back else (CT, ZoneInfo(zone))
    return t.replace(tzinfo=a).astimezone(b).replace(tzinfo=None)


def shift_session(v, minutes):
    """A Session Time the profile reads, both ends shifted by minutes (HHmm-HHmm); one it doesn't, as it is."""
    ok, a, b = tpo.TpoProfile.ParseSession(v)
    return "-".join(f"{(m + minutes) % 1440 // 60:02d}{(m + minutes) % 60:02d}" for m in (a, b)) if ok else v


def in_zone(rec, s, zone):
    """The record and settings as a chart in zone has them: every bar time there, Session Time typed there at the
    zone's offset from CT (minutes), which must be one over the record."""
    out, offsets = [], set()
    for r in rec:
        if r[0] == "D":
            out.append(r)
            continue
        t = to_zone(r[1], zone)
        offsets.add(round((t - r[1]).total_seconds() / 60))
        out.append((r[0], t, r[2], r[3], r[4], to_zone(r[5], zone), *r[6:]))
    if len(offsets) != 1:
        sys.exit(f"zone={zone}: its offset from CT changes over these days ({sorted(offsets)}); use a zone that keeps one")
    off = offsets.pop()
    return out, {**s, "SessionTime": shift_session(s["SessionTime"], off)}, off


def from_zone(line, zone):
    """A C# output line from a chart in zone with its times in CT."""
    f = line.split("|")
    at = {"T": (5, 6, 7), "P": (1, 2), "LIVE": (1, 2)}.get(f[0], ())
    for i in at:
        if f[i] != "MIN":
            f[i] = T(to_zone(PT(f[i]), zone, back=True))
    return "|".join(f)


def as_csharp_saves(x, p, step):
    """The profile as the C# saves it: the same rows, priced with the row height current then."""
    if p.TickStep == step:
        return p
    return x.BuildProfileFromMap({int(tpo.round_away(r.Price / p.TickStep)): r.Letters for r in p.Rows},
                                 p.TimeStart, p.TimeEnd, step)


def replay_python(rec_path, s, rth):
    """s: the settings as typed; rth: the root's regular hours, for Use Instrument Hours."""
    lines = Path(rec_path).read_text().splitlines()
    x, atr, o = None, tpo.NtAtr(), []
    for line in lines:
        p = line.split("|")
        if p[0] == "S":
            run_s = own_hours(s, rth)
            x = tpo.TpoProfile(run_s, float(p[1]))
            if s["UseInstrumentHours"]:
                o.append(f"H|{run_s['SessionTime']}")
        elif p[0] == "D":
            x.dailyAtr = atr.add(float(p[1]), float(p[2]), float(p[3]))
            x.CurrentBars[1] += 1
        elif p[0] == "B":
            x.CurrentBars[0] += 1
            x.Time0, x.High0, x.Low0 = PT(p[1]), float(p[2]), float(p[3])
            x.IsFirstBarOfSession, x.TemplateSessionOpen = p[4] == "1", PT(p[5])
            before = len(x.saved)
            x.OnBarUpdate()
            o.append(f"T|{D(x.tickStep)}|{int(x.hasLive)}|{int(x.liveInSession)}|{len(x.liveRows)}|{T(x.sessionStartTime)}|"
                     f"{T(x.drawStartTime)}|{T(x.lastSessBarTime)}")
            for prof in x.saved[before:]:
                dump_profile(o, "P", as_csharp_saves(x, prof, x.tickStep))
        elif p[0] == "E" and x.liveRows:
            live = x.BuildProfileFromMap(x.liveRows, x.drawStartTime, x.Time0, x.liveStep)
            dump_profile(o, "LIVE", as_csharp_saves(x, live, x.tickStep))
    return o


def main():
    zone = next((a[5:] for a in sys.argv if a.startswith("zone=")), None)
    argv = [a for a in sys.argv if a not in ("rth", "holidays") and not a.startswith("zone=")]
    work = Path(argv[1])
    days = int(argv[2]) if len(argv) > 2 else 40
    minutes = int(argv[3]) if len(argv) > 3 else 5
    seed = int(argv[4]) if len(argv) > 4 else 7
    root = argv[5] if len(argv) > 5 else "ES"
    s = tpo.settings(json.loads(argv[6]) if len(argv) > 6 else {})
    rth = RTHS[root] if "rth" in sys.argv else None
    tick, price = ROOTS[root]
    calendar = CALENDARS[root] if "holidays" in sys.argv else None
    bars = synth(days, seed, datetime(2026, 12, 1, 12, 7) if calendar else datetime(2026, 10, 16, 12, 7), tick, price)
    if calendar:
        sessions.set_calendar(load_holidays(Path(__file__).resolve().parents[2] / "config"))
        bars = [b for b in bars if sessions.is_open(b.t, calendar) and not sessions._no_session(b.t)]
    daily = Daily(calendar or "equity", rth)
    daily.add_minutes(bars, 2**62)
    rec = []
    run(own_hours(s, RTHS[root]), tick, minutes, bars, daily, 2**62, rec, rth, calendar)
    rec_path, cs_out = work / "tpo_record.txt", work / "tpo_cs.txt"
    write_record(rec, s, tick, rec_path, root)
    cs_rec = rec_path
    if zone:   # the C# on a chart in zone; this port in CT
        z_rec, z_s, off = in_zone(rec, s, zone)
        cs_rec = work / "tpo_record_zone.txt"
        write_record(z_rec, z_s, tick, cs_rec, root, zone)
    subprocess.run(["mono", str(work / "harness.exe"), str(cs_rec), str(cs_out)], check=True)
    cs = cs_out.read_text().splitlines()
    py = replay_python(rec_path, s, RTHS[root])
    if zone:
        ran = [l[2:] for l in cs if l.startswith("W|")]
        moved = own_hours(s, RTHS[root])["SessionTime"]
        want = [shift_session(moved, off)] if s["FilterByTime"] and shift_session(moved, off) != z_s["SessionTime"] else []
        if ran != want:
            print(f"PARITY FAIL: in {zone} the C# ran Session Time {ran}, not {want} ({z_s['SessionTime']} typed there)")
            sys.exit(1)
        cs = [from_zone(l, zone) for l in cs if not l.startswith("W|")]
        py = [l for l in py if not l.startswith("H|")]

    def same(a, b):
        ta, tb = a.split("|"), b.split("|")
        if len(ta) != len(tb):
            return False
        for x, y in zip(ta, tb):
            if x == y:
                continue
            try:
                if float(x) != float(y):
                    return False
            except ValueError:
                return False
        return True

    bad = [(i, a, b) for i, (a, b) in enumerate(zip(cs, py)) if not same(a, b)]
    saved = sum(1 for l in py if l.startswith("P|"))
    if len(cs) != len(py) or bad:
        print(f"PARITY FAIL: {len(cs)} C# lines, {len(py)} Python lines, {len(bad)} differ")
        for i, a, b in bad[:10]:
            print(f"  line {i}\n    C#: {a}\n    py: {b}")
        sys.exit(1)
    steps = sorted({l.split("|")[1] for l in py if l.startswith("T|")}, key=float)
    hours = own_hours(s, RTHS[root])["SessionTime"] if s["FilterByTime"] else "all"
    where = f", NinjaTrader on {zone}: {z_s['SessionTime']} typed, {(ran or [z_s['SessionTime']])[-1]} run there" if zone else ""
    print(f"PARITY OK: {root} {minutes}m{' RTH' if rth else ''}{' holidays' if calendar else ''} {hours} (Use Instrument Hours {'on' if s['UseInstrumentHours'] else 'off'}{where}), {days} days, {sum(1 for r in rec if r[0] == 'B')} bars, {saved} profiles, "
          f"row heights {steps}, {len(py)} lines; every value equal")


if __name__ == "__main__":
    main()
