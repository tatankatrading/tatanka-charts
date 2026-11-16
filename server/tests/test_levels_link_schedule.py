"""The levels link's schedule against the NinjaTrader AMT Toolkit's own: LevelLink.NextAsk and LevelLink.Published,
copied from TatankaAmtToolkit.cs by tests/amt_csharp/build.sh and run with mono on the same cases as
daily_levels.next_ask and daily_levels.published (needs TATANKA_INDICATORS and mono). Without them, a few cases written
out from the C#."""
import os
import random
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chartsrv.daily_levels import NONE, next_ask, published

HERE = Path(__file__).resolve().parent
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_mono = pytest.mark.skipif(not (INDICATORS and shutil.which("mcs") and shutil.which("mono")),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS) and mono")
T = lambda t: "MIN" if t == NONE else t.strftime("%Y-%m-%dT%H:%M:%S")   # noqa: E731


def test_the_schedule_by_hand():
    d = lambda h, mi, s=0: datetime(2026, 10, 21, h, mi, s)   # noqa: E731
    # 08:15's levels not in hand (yesterday's 16:45 publish): every 15 s for 10 minutes, then each minute to 09:15
    yday = datetime(2026, 10, 20, 16, 45)
    assert next_ask(d(8, 14), yday, NONE, 0, 200) == d(8, 14, 15)
    assert next_ask(d(8, 24, 50), yday, NONE, 0, 200) == d(8, 25, 5)
    assert next_ask(d(8, 25), yday, NONE, 0, 200) == d(8, 26)
    assert next_ask(d(9, 14, 30), yday, NONE, 0, 200) == d(9, 15, 30)
    assert next_ask(d(9, 15), yday, NONE, 0, 200) == d(9, 45)
    assert next_ask(d(8, 0), yday, NONE, 0, 200) == d(8, 14)               # woken for the publish
    # in hand: published 08:15, or changed since 08:14; then every 2 minutes for 30 minutes after a change
    assert next_ask(d(8, 20), d(8, 15), d(8, 16), 0, 200) == d(8, 22)
    assert next_ask(d(8, 47), d(8, 15), d(8, 16), 0, 200) == d(9, 17)
    assert next_ask(d(8, 20), NONE, d(8, 14), 0, 200) == d(8, 22)
    # failures: 1, 2, 4, 8, 15, 15 minutes; 1 at most while due; 403 or 404 from the service: 30 minutes
    assert [next_ask(d(11, 0), d(8, 15), NONE, n, 500) - d(11, 0) for n in range(1, 7)] == \
        [timedelta(minutes=m) for m in (1, 2, 4, 8, 15, 15)]
    assert next_ask(d(8, 30), yday, NONE, 5, 500) == d(8, 31)
    assert next_ask(d(8, 20), yday, NONE, 0, 403) == d(8, 50) and next_ask(d(8, 20), yday, NONE, 3, 404) == d(8, 50)
    # the Published line: 24-hour only
    assert published("x\nPublished: 2026-10-15 16:45 CT\n2026-10-15, 1") == datetime(2026, 10, 15, 16, 45)
    assert published("Published: 2026-10-15 4:45 PM") == NONE and published("Published: 2026-10-15 8:15") == datetime(2026, 10, 15, 8, 15)
    assert published("published:2026-02-30 08:15") == NONE and published("") == NONE


def _cases(seed: int, n: int) -> list[tuple]:
    rng = random.Random(seed)
    base = datetime(2026, 10, 21)
    out = []
    marks = [timedelta(hours=8, minutes=15), timedelta(hours=16, minutes=45), timedelta(days=1, hours=8, minutes=15)]
    for _ in range(n):
        at = base + rng.choice(marks)
        now = at + timedelta(seconds=rng.choice([rng.randint(-7200, 7200), rng.randint(-120, 4000), rng.randint(-86400, 86400)]))
        def near():
            return rng.choice([NONE, NONE, now - timedelta(seconds=rng.randint(0, 7200)), at - timedelta(minutes=rng.choice([31, 30, 29, 2, 1, 0])),
                               at + timedelta(seconds=rng.randint(-3600, 3600)), at - timedelta(days=1)])
        out.append((now, near(), near(), rng.choice([0, 0, 0, 1, 2, 3, 4, 5, 6, 9]),
                    rng.choice([200, 200, 304, 403, 404, 502, 0, 500])))
    return out


@needs_mono
def test_the_schedule_is_ninjatraders(tmp_path):
    r = subprocess.run(["sh", str(HERE / "amt_csharp" / "build.sh"), INDICATORS, str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    cases = _cases(7, 6000)
    texts = ["Published: 2026-10-15 16:45 CT\n1, 2", "  published :2026-10-15T8:15\n", "Published: 2026-10-15 16:45:59",
             "Published: 2026-10-15 4:45 PM", "Published: 2026-10-15 4:45pm", "Published: 2026-10-15 16:45:00 AM",
             "x Published: 2026-10-15 16:45", "Published: 2026-13-15 16:45", "Published: 2026-10-15 24:00",
             "Published: 2026-10-15 16:451", "Published:\t2026-10-15  09:05\nPublished: 2026-10-16 09:05", ""]
    lines = [f"{T(a)}|{T(b)}|{T(c)}|{f}|{st}" for a, b, c, f, st in cases] + ["P|" + t.replace("\n", "\\n") for t in texts]
    (tmp_path / "cases.txt").write_text("\n".join(lines) + "\n")
    subprocess.run(["mono", str(tmp_path / "harness.exe"), "--next-ask", str(tmp_path / "cases.txt"), str(tmp_path / "out.txt")],
                   check=True)
    cs = (tmp_path / "out.txt").read_text().splitlines()
    py = [T(next_ask(*c)) for c in cases] + [T(published(t)) for t in texts]
    assert len(cs) == len(py)
    diff = [(lines[i], py[i], cs[i]) for i in range(len(py)) if py[i] != cs[i]]
    assert not diff, diff[:5]
    assert len({x for x in py[:len(cases)]}) > 1000   # the cases reach every branch, not one answer
