"""The prior day's ticks coming after the Toolkit loaded. Here the hub works the session's trades out (in seconds, or
after NinjaTrader's tick history) and loads the Toolkit again with them (AmtHub._fill_pd, _rebuild): what NinjaTrader
draws once it has the ticks at load, which its load waits up to 60 s for (TickValue). A running NinjaTrader chart whose
ticks come later instead patches the prior day (PatchPrevDay: RefreshPrevDay, and before the close clears today's 80%
Rule fill). The C# harness runs the indicator's own PatchPrevDay (copied by build.sh) on the late ticks and compares it
with a load that had them (the state this port draws: tests/amt_parity.py with the ticks at load is PARITY OK):

- at the close (the usual case live: the closed session's ticks asked for at the close): from the moment they come every
  frame and every drawn fill is the load's. Loading again shows the same as NinjaTrader's patch.
- mid-session (NinjaTrader's download outlasting the load's wait): from then on every frame is the load's too, but
  NinjaTrader has cleared the fill drawn from the open to then, which its own reload (and this port) draws. Here the
  values always come after a load, so patching would clear today's fill on every load mid-session; reloading shows what
  NinjaTrader shows when its ticks come during its load. Kept, not copied.

Needs TATANKA_INDICATORS and mono."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
INDICATORS = os.environ.get("TATANKA_INDICATORS")
pytestmark = pytest.mark.skipif(not (INDICATORS and shutil.which("mcs") and shutil.which("mono")),
                                reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS) and mono")


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    w = tmp_path_factory.mktemp("amt_late")
    r = subprocess.run(["sh", str(HERE / "amt_csharp" / "build.sh"), INDICATORS, str(w)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return w


def _record(work: Path, loaded: str) -> list[str]:
    """A synthetic ES record (12 days to Fri 2026-10-16 15:30, 5-minute bars) loaded at `loaded`; PARITY OK first."""
    r = subprocess.run([sys.executable, str(HERE / "amt_parity.py"), str(work), "12", "5", "3", "ES"], capture_output=True,
                       text=True, cwd=HERE.parent, env={**os.environ, "AMT_LOADED": loaded})
    assert r.returncode == 0 and "PARITY OK" in r.stdout, r.stdout + r.stderr
    return (work / "record.txt").read_text().rstrip("\n").split("\n")


def _run(work: Path, lines: list[str], name: str) -> list[str]:
    (work / f"{name}.txt").write_text("\n".join(lines + ["FILLS"]) + "\n")
    subprocess.run(["mono", str(work / "harness.exe"), str(work / f"{name}.txt"), str(work / f"{name}.out")], check=True)
    return (work / f"{name}.out").read_text().rstrip("\n").split("\n")


def _late(work: Path, loaded: str, session: str, arrive: str):
    """The load's run and the run whose `session` ticks come just before the first bar from `arrive` on: (frames and
    fills of the load, of the late run, the late run's PATCH line, where its frames from the arrival start)."""
    lines = _record(work, loaded)
    base = _run(work, lines, "base")
    late = list(lines)
    late.insert(next(i for i, x in enumerate(late) if x.startswith("N|")), f"HOLD|{session}")
    late.insert(next(i for i, x in enumerate(late) if x.startswith("B|") and x.split("|")[2] >= arrive), f"ARRIVE|{session}")
    out = _run(work, late, "late")
    k = next(i for i, x in enumerate(out) if x.startswith("PATCH|"))
    return base, out, out[k], k


def _frames(lines):
    return [x for x in lines if not x.startswith("FILL|")]


def _fills(lines):
    return [x for x in lines if x.startswith("FILL|")]


def test_ticks_after_the_close_patch_to_what_a_load_with_them_draws(work):
    """Loaded Wednesday 15:30; Thursday's ticks, asked for at its 15:00 close, come at 15:20 (after the close's roll
    took Thursday's value from its minutes): from then on the patched chart is the load's, fills included."""
    base, late, patch, k = _late(work, "2026-10-14T15:30:00", "2026-10-15", "2026-10-15T15:20:00")
    assert patch == "PATCH|0"                                   # after the close: today's fill is left as it is
    j = base.index(late[k + 1])
    assert _frames(late[k + 1:]) == _frames(base[j:])          # every frame from the arrival on
    assert _fills(late) == _fills(base) and len(_fills(base)) > 100
    before = [(a, b) for a, b in zip(_frames(late[:k]), base[:j]) if a != b]
    assert before and before[0][0].startswith("W|pdPOC|")       # until then the pd lines were the minutes' (they differ)


def test_ticks_mid_session_clear_the_fill_drawn_before_them_on_ninjatrader_only(work):
    """Loaded Friday 09:00 without Thursday's ticks (the load's wait over); they come at 10:30. From then on every frame
    is the load's, but NinjaTrader cleared Friday's fill from the open to then (24 bars), which a load with the ticks,
    NinjaTrader's own reload and this port's reload draw."""
    base, late, patch, k = _late(work, "2026-10-16T09:00:00", "2026-10-15", "2026-10-16T10:30:00")
    assert patch == "PATCH|24"
    j = base.index(late[k + 1])
    assert _frames(late[k + 1:]) == _frames(base[j:])
    gone = sorted({x.split("|")[1] for x in _fills(base)} - {x.split("|")[1] for x in _fills(late)})
    assert gone[0] == "2026-10-16T08:30:00" and gone[-1] == "2026-10-16T10:25:00" and len(gone) == 24
    assert not {x.split("|")[1] for x in _fills(late)} - {x.split("|")[1] for x in _fills(base)}
