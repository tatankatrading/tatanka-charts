"""If/Then plan alerts: the server reads the pasted block as the chart does (ifthen.bias_parse against web/studies.js's
biasParse, run in node on the same inputs), and each row alerts by itself: a line on a touch of its price, a zone on
price coming into it. A new plan's new rows, a start and a roll take a first look and never fire; a row a new paste keeps
keeps its state; chop is held back by the re-arm distance."""
import json
import logging
import random
import re
import subprocess
from pathlib import Path

import pytest

from chartsrv import ifthen
from chartsrv.alerts import AlertEngine
from chartsrv.config import Instrument
from chartsrv.ifthen import alert_band, alert_rows, bias_number, bias_parse, one_line
from chartsrv.models import Tick
from chartsrv.store import Store

from test_hunt_web_chart import needs_node

WEB = Path(__file__).resolve().parents[2] / "web"
ES = Instrument("ES", "E-mini S&P 500", 0.25, 50)
MES = Instrument("MES", "Micro E-mini S&P 500", 0.25, 5)
NQ = Instrument("NQ", "E-mini Nasdaq-100", 0.25, 20)
ZN = Instrument("ZN", "10-Year Note", 1 / 64, 1000)
PLAN = "\n".join([
    "# ES plan",
    "U|7450|9999999|Accepts above 7450 → continuation\\nnext target 7480",
    "P|7395|7405|Pivot ~7400",
    "D|0|7385|Below 7385 → range break",
])


# ---- the parser: the same rows as the chart's, for every input ------------------------------------------------------

def js_part(lines: list[str], name: str) -> str:
    """One top-level declaration of a web/*.js file (a function, or the const naming it), to where its brackets close."""
    head = re.compile(rf"^(?:async )?function {re.escape(name)}\(|^const {re.escape(name)} = ")
    start = next(i for i, line in enumerate(lines) if head.match(line))
    depth = 0
    for i in range(start, len(lines)):
        depth += sum(lines[i].count(c) for c in "({[") - sum(lines[i].count(c) for c in ")}]")
        if depth <= 0:
            return "\n".join(lines[start:i + 1])
    raise AssertionError(f"{name}: no end")


def run_js_parser(numbers: list[str], blocks: list[str]) -> dict:
    lines = (WEB / "studies.js").read_text(encoding="utf-8").split("\n")
    src = "\n".join(js_part(lines, n) for n in ("parse32", "BIAS_NUMBER", "biasNumber", "biasParse")) + """
let input = '';
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  const q = JSON.parse(input);
  console.log(JSON.stringify({ numbers: q.numbers.map(biasNumber), blocks: q.blocks.map(biasParse) }));
});"""
    r = subprocess.run(["node", "-e", src], input=json.dumps({"numbers": numbers, "blocks": blocks}),
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


NUMBERS = [
    "7450", "7450.25", " 7 450.25 ", "\t7450\t", "\x0b7450\x0c", "+7450", "-7450", "0", "-0", "9999999", "1.", ".5", "+.5",
    "1e3", "1E-3", "1.e2", "1e400", "-1e400", "1e-400", "007", "", " ", ".", "e5", "1e", "1e+", "7,450", "7450,25", "7_450",
    "0x10", "Infinity", "NaN", "7450a", "a7450", "\xa07450", "7450\xa0", "﻿7450", " 7450", "١٢", "7٤50",
    "110'16", "110'165", "110'16.5", "110'162", "110'167", "110'164", "110'169", "110'1", "110'31", "110'32", "110'99",
    "110'1,6", "110'16,5", "110'16x", "110'16'5", "110'1234", "-110'16", "+110'16", "110' 16", "110'16\xa0", "110'16 ",
    "110'16\x85", "110'", "'16", "110-16", "110-165", "1'0", "0'00", "-0'00", "9" * 400 + "'1", "9" * 400,
]
JUNK = list("0123456789'.+-eE ,|x_") + ["\t", "\x0b", "\x0c", "\r", "\xa0", "﻿", " ", "\x1c", "\x85", "١"]


def fuzz_number(rnd: random.Random) -> str:
    base = rnd.choice([
        str(rnd.randint(0, 99999)), f"{rnd.uniform(0, 9999):.{rnd.randint(0, 6)}f}", f"{rnd.randint(1, 9)}e{rnd.randint(-5, 5)}",
        f"{rnd.randint(90, 140)}'{rnd.randint(0, 40):02d}{rnd.choice(['', '0', '2', '4', '5', '7', '9', '.5', '.4'])}",
        f"{rnd.choice(['', '+', '-'])}{rnd.randint(0, 999)}.{rnd.randint(0, 99)}", "9999999", "0"])
    for _ in range(rnd.choice([0, 0, 0, 1, 2])):
        i = rnd.randint(0, len(base))
        base = base[:i] + rnd.choice(JUNK) + base[i:]
    return base


BIASES = ["U", "u", "d", "P", "v", "N", "", "x", "ß", "ﬀ", "ǅ", "é", "😀", "ı", "ŉ", "#", " U", "UP", "\ud83d"]
TEXTS = ["Accepts above → continuation", "", "a|b|c", "line one\\nline two", "\\\\n", "x\\n", "  spaced  ", "#not a comment",
         "émoji 😀 → ok", "tab\there", "|", "||"]


def fuzz_block(rnd: random.Random) -> str:
    lines = []
    for _ in range(rnd.randint(0, 8)):
        kind = rnd.random()
        if kind < 0.1:
            lines.append(rnd.choice(["", "# comment", "#U|1|2|x", "U|1|2", "U|1", "garbage", " # U|1|2|x"]))
            continue
        fields = [rnd.choice(BIASES), fuzz_number(rnd) if rnd.random() < 0.8 else rnd.choice(NUMBERS),
                  fuzz_number(rnd) if rnd.random() < 0.8 else rnd.choice(NUMBERS), rnd.choice(TEXTS)]
        lines.append("|".join(fields + [rnd.choice(TEXTS) for _ in range(rnd.choice([0, 0, 1, 2]))]))
    return "".join(line + rnd.choice(["\n", "\r\n", "\r", "\n\n"]) for line in lines)[:rnd.choice([10**6, rnd.randint(0, 200)])]


@needs_node
def test_the_server_reads_a_block_exactly_as_the_chart():
    """bias_parse and bias_number are biasParse and biasNumber: the same rows, numbers, bias letters and text for
    curated edge cases (32nds, comments, CRLF, extra '|' in the text, a lowercase bias, empty fields, JavaScript's own
    whitespace and digits) and a few thousand random ones."""
    rnd = random.Random(20261116)
    numbers = NUMBERS + [fuzz_number(rnd) for _ in range(4000)]
    blocks = [PLAN, PLAN.replace("\n", "\r\n"), PLAN.replace("\n", "\r"), "", "\n\n", "# only a comment",
              "u|7450|9999999|lowercase bias", "|7450|7460|no bias", "U|7450|7460|", "U||7460|empty low",
              "U|7450|7460|a|b|c", "U|7450|7460|one\\ntwo\\nthree", "ZN|110'16|110'165|bonds", "U| 7 450 |\t7460\t|spaces",
              "ß|1|2|sharp s", "😀|1|2|emoji", "U|1|2|x\r\nD|3|4|y\rP|5|6|z\n"] + [fuzz_block(rnd) for _ in range(1500)]
    js = run_js_parser(numbers, blocks)
    assert js["numbers"] == [bias_number(t) for t in numbers]
    assert js["blocks"] == [bias_parse(b) for b in blocks]
    got = [n for n in js["numbers"] if n is not None]
    assert len(got) > 1500 and any(n != int(n) for n in got)   # the random ones were numbers often enough
    assert sum(len(b) for b in js["blocks"]) > 2000


def test_the_parser_on_the_plan_format():
    rows = bias_parse("U|7450|9999999|Accepts\\nabove|x\r\n# note\r\nd|0|7385|\r\nZN|110'16|110'165|bond\n|1|2|none")
    assert rows == [{"Bias": "U", "Low": 7450.0, "High": 9999999.0, "Text": "Accepts\nabove|x"},
                    {"Bias": "D", "Low": 0.0, "High": 7385.0, "Text": ""},
                    {"Bias": "Z", "Low": 110.5, "High": 110.515625, "Text": "bond"},
                    {"Bias": "N", "Low": 1.0, "High": 2.0, "Text": "none"}]
    assert bias_number("7,450") is None and bias_number("110'32") is None and bias_number("1e400") is None
    assert bias_number(" 7 450.5 ") == 7450.5 and bias_number("110'16.5") == 110.515625


# ---- which rows are lines and which are zones -----------------------------------------------------------------------

def test_each_row_is_a_line_or_a_zone():
    assert alert_band(7450, 9999999) == ("line", 7450, 7450)     # no top: the line at its bottom
    assert alert_band(0, 7385) == ("line", 7385, 7385)           # no bottom: the line at its top
    assert alert_band(-5, 7385) == ("line", 7385, 7385)
    assert alert_band(7400, 7400) == ("line", 7400, 7400)
    assert alert_band(7395, 7405) == ("zone", 7395, 7405)
    assert alert_band(7405, 7395) == ("zone", 7395, 7405)        # written the wrong way round: the same zone
    assert alert_band(0, 9999999) is None                        # no bottom and no top: never alerts
    assert alert_band(1_000_000, 1_000_001) == ("line", 1_000_000, 1_000_000)
    rows = alert_rows(PLAN + "\nN|0|9999999|all day\nL|7420|7420|  two \t spaces  \\n\\n  and lines ")
    assert [r[:5] for r in rows] == [("line", 7450, 7450, 7450, 9999999), ("zone", 7395, 7405, 7395, 7405),
                                     ("line", 7385, 7385, 0, 7385), ("line", 7420, 7420, 7420, 7420)]
    assert rows[0][5] == "Accepts above 7450 → continuation / next target 7480"
    assert rows[3][5] == "two spaces / and lines"


@needs_node
def test_the_dialog_marks_each_row_as_the_server_alerts_on_it():
    """The alert dialog's line / zone marks (web/app.js ifAlertBand) are the engine's (alert_band)."""
    lines = (WEB / "app.js").read_text(encoding="utf-8").split("\n")
    vals = [0, -5, 0.25, 7385, 7395, 7400, 7405, 1_000_000, 1_000_000.5, 9999999]
    pairs = [[a, b] for a in vals for b in vals]
    src = js_part(lines, "ifAlertBand") + f"\nconsole.log(JSON.stringify({json.dumps(pairs)}.map(([a, b]) => ifAlertBand(a, b))));"
    r = subprocess.run(["node", "-e", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    want = [None if (b := alert_band(lo, hi)) is None else {"kind": b[0], "lo": b[1], "hi": b[2]} for lo, hi in pairs]
    assert json.loads(r.stdout) == want


PLAN_LINK_JS = """
const ids = {}, $ = (q) => (ids[q] ||= { dataset: {}, value: '' });
const S = { symbol: 'ES' }, did = [];
let saves = true;   // Save (the server's answer): kept, or refused (the box stays marked unsaved)
$('#pl-if-save').onclick = async () => { did.push(`save ${$('#pl-if').value}`); await null; if (saves) delete $('#pl-if').dataset.dirty; };
function openAlertDialog(o) { did.push(`dialog ${o.sym} ${o.src}`); }
/*LINK*/
(async () => {
  const out = {};
  $('#pl-if').value = 'U|7000|9999999|saved';
  await $('#pl-if-alert').onclick();
  out.saved = did.splice(0);
  $('#pl-if').value = 'U|7450|9999999|pasted'; $('#pl-if').dataset.dirty = '1';
  saves = false;
  await $('#pl-if-alert').onclick();
  out.refused = did.splice(0);
  saves = true;
  await $('#pl-if-alert').onclick();
  out.pasted = did.splice(0);
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_the_plan_tabs_create_alert_saves_a_paste_first():
    """The alert reads the saved plan, so Create alert under a paste not saved yet saves it first and the dialog lists
    the pasted rows, not the old plan's; a save that fails opens no dialog (the paste stays, unsaved)."""
    from test_hunt_web_chart import app_lines
    r = subprocess.run(["node", "-e", PLAN_LINK_JS.replace("/*LINK*/", app_lines("$('#pl-if-alert').onclick", "};"))],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"saved": ["dialog ES ifthen"], "refused": ["save U|7450|9999999|pasted"],
                                    "pasted": ["save U|7450|9999999|pasted", "dialog ES ifthen"]}


def test_a_long_text_is_cut_to_one_short_line():
    t = one_line("word " * 60)
    assert len(t) == 140 and t.endswith("…") and "  " not in t
    assert one_line("a\nb\r\nc\\nd") == "a / b / c / d" and one_line("  \n \n ") == "" and one_line("x" * 140) == "x" * 140


# ---- the engine ------------------------------------------------------------------------------------------------------

class Rig:
    """An alert engine on its own store, the trades fed one by one."""

    def __init__(self, tmp_path, blocks: dict[str, str] | None = None):
        self.store = Store(tmp_path / "t.db")
        if blocks:
            self.store.set_setting("ifthen", {s: {"rows": b, "updated": 1} for s, b in blocks.items()})
        self.fired: list[dict] = []
        self.eng = AlertEngine(self.store, {"ES": ES, "MES": MES, "NQ": NQ, "ZN": ZN}, None, self.fired.append)
        self.ts = 1_000_000

    def add(self, symbol="ES", repeat=True, note="", **params) -> int:
        i = self.store.add_alert(symbol, "ifthen", {"rows": "all", "tolerance_ticks": 0, "rearm_ticks": 8, **params}, repeat, note)
        self.eng.reload()
        return i

    def feed(self, prices, sym="ES"):
        for p in prices:
            self.ts += 1000
            self.eng.on_tick(Tick(sym, self.ts, p, 1, 0))

    def paste(self, sym, block):   # PUT /api/ifthen/{sym}
        blocks = self.store.get_setting("ifthen", {}) or {}
        blocks[sym] = {"rows": block, "updated": self.ts}
        self.store.set_setting("ifthen", blocks)
        self.eng.ifthen_changed(sym, block)

    def said(self) -> list[str]:
        return [e["message"].split(" (last ")[0] for e in self.fired]


def test_a_line_fires_on_a_touch_and_rearms_8_ticks_away(tmp_path):
    r = Rig(tmp_path, {"ES": PLAN})
    r.add()
    r.feed([7445.0, 7449.0, 7449.75])               # first look, then up to a tick under the line
    assert r.fired == []
    r.feed([7450.0])
    assert r.said() == ["ES touched 7450.00: Accepts above 7450 → continuation / next target 7480"]
    r.feed([7449.75, 7450.0, 7450.25, 7451.75, 7450.0, 7448.25, 7450.0])   # chop within 7 ticks: quiet
    assert len(r.fired) == 1
    r.feed([7452.0, 7450.5, 7450.0])                # 8 ticks above re-arms, then a touch again
    assert len(r.fired) == 2
    r.feed([7448.0, 7451.0])                        # re-armed below, then through the line between two prints
    assert len(r.fired) == 3
    r.feed([7400.0])                                # down into the pivot zone
    assert r.said()[3] == "ES entered 7395.00–7405.00: Pivot ~7400"
    r.feed([7390.0, 7386.0, 7385.0])                # the open-bottom row: its line is at 7385
    assert r.said()[4:] == ["ES touched 7385.00: Below 7385 → range break"]


def test_touch_within_widens_a_line_not_a_zone(tmp_path):
    r = Rig(tmp_path, {"ES": "U|7450|9999999|up\nP|7395|7405|pivot"})
    r.add(tolerance_ticks=2)
    r.feed([7390.0, 7394.5])                        # 2 ticks under the zone: not in it
    assert r.fired == []
    r.feed([7395.0])
    assert r.said() == ["ES entered 7395.00–7405.00: pivot"]
    r.feed([7440.0, 7449.25])
    assert len(r.fired) == 1
    r.feed([7449.5])                                # 2 ticks under the line: a touch
    assert r.said()[1] == "ES touched 7450.00: up"
    r.feed([7451.0, 7452.25, 7450.5])               # re-arm counts from the band's edge: 7450.50 + 8 ticks
    assert len(r.fired) == 2
    r.feed([7452.5, 7450.5])
    assert len(r.fired) == 3


def test_a_zone_fires_once_per_entry_and_rearms_8_ticks_outside(tmp_path):
    r = Rig(tmp_path, {"ES": "P|7395|7405|Pivot\\nhold it"})
    r.add()
    r.feed([7390.0, 7394.75])
    assert r.fired == []
    r.feed([7395.0])                                # an edge counts
    assert r.said() == ["ES entered 7395.00–7405.00: Pivot / hold it"]
    r.feed([7400.0, 7405.0, 7405.25, 7406.75, 7405.0, 7404.0, 7394.0, 7395.0])   # inside, and out less than 8 ticks
    assert len(r.fired) == 1
    r.feed([7407.0, 7406.0, 7405.0])                # 8 ticks above re-arms; back down into it
    assert r.said()[1] == "ES entered 7395.00–7405.00: Pivot / hold it"
    r.feed([7393.0, 7380.0, 7412.0])                # re-armed below, then through the whole zone in one print
    assert len(r.fired) == 3


def test_a_row_with_no_bottom_and_no_top_never_fires(tmp_path):
    r = Rig(tmp_path, {"ES": "N|0|9999999|neutral all day\n# U|7400|7410|commented out"})
    r.add()
    r.feed([7390.0, 7400.0, 7410.0, 7380.0, 7420.0])
    assert r.fired == []


def test_overlapping_rows_fire_each_on_their_own(tmp_path):
    r = Rig(tmp_path, {"ES": "P|7395|7405|zone A\nQ|7400|7410|zone B\nL|7400|7400|line C"})
    r.add()
    r.feed([7390.0, 7396.0])
    assert r.said() == ["ES entered 7395.00–7405.00: zone A"]
    r.feed([7400.0])
    assert r.said()[1:] == ["ES entered 7400.00–7410.00: zone B", "ES touched 7400.00: line C"]


def test_rows_chosen_by_their_prices_hold_through_a_new_paste(tmp_path):
    r = Rig(tmp_path, {"ES": PLAN})
    r.add(rows=[[7395.0, 7405.0], [0.0, 7385.0]])
    r.feed([7445.0, 7450.0])                        # 7450 is not one of them
    assert r.fired == []
    r.feed([7410.0, 7404.0])
    assert r.said() == ["ES entered 7395.00–7405.00: Pivot ~7400"]
    # a new paste: the pivot moved to another line, its text changed, another row came in front of it
    r.paste("ES", "X|7420|7430|new row\nP|7395|7405|Pivot, retested\nD|0|7385|Below")
    r.feed([7415.0, 7418.0, 7420.0, 7407.5, 7405.0])
    assert r.said()[1:] == ["ES entered 7395.00–7405.00: Pivot, retested"]
    r.paste("ES", "P|7396|7405|moved by a tick")    # a row that moved is another row: not chosen
    r.feed([7410.0, 7400.0])
    assert len(r.fired) == 2


def test_a_new_plan_never_fires_for_levels_moving_under_price(tmp_path):
    r = Rig(tmp_path)
    r.add()
    r.feed([7400.0, 7400.25])                       # no plan yet: nothing
    r.paste("ES", "P|7395|7405|price is in it\nL|7400.25|7400.25|on it\nB|7398|9999999|crossed by the paste")
    r.feed([7400.25, 7400.0, 7399.75])
    assert r.fired == []
    # the state is taken from the price when the plan came: a line 3 ticks away is armed and fires on the next touch
    r.paste("ES", "L|7400.5|7400.5|three ticks up")
    r.feed([7400.5])
    assert r.said() == ["ES touched 7400.50: three ticks up"]
    # a plan's text edited while price sits near a line that just fired: no fire for the paste itself
    r.paste("ES", "L|7400.5|7400.5|text changed")
    r.feed([7400.5, 7400.25, 7400.5])
    assert len(r.fired) == 1
    r.paste("ES", "")                               # the block deleted: nothing fires, the alert stays
    r.feed([7390.0, 7400.5, 7420.0])
    assert len(r.fired) == 1 and r.eng.alerts[1].enabled and r.eng.ifthen_rows("ES") == []


def test_saving_the_plan_again_keeps_a_row_that_just_fired_quiet(tmp_path):
    """A row still in the plan keeps its state through a paste (the same LO and HI, its text edited or not), so a
    second Save, or an edit to another row, never re-arms a row that just fired: chop near it stays quiet until price
    has been 8 ticks away. A row the paste brings in takes its first look at the current price."""
    r = Rig(tmp_path, {"ES": "P|7395|7405|Pivot zone\nU|7450|9999999|Above 7450"})
    r.add()
    r.feed([7390.0, 7395.0])
    assert r.said() == ["ES entered 7395.00–7405.00: Pivot zone"]
    r.feed([7394.5, 7395.0, 7394.0, 7395.0, 7394.5])        # chop at the edge: quiet
    r.paste("ES", "P|7395|7405|Pivot zone\nU|7450|9999999|Above 7450")   # Save again, the same block
    r.feed([7395.0])
    assert len(r.fired) == 1
    r.paste("ES", "P|7395|7405|Pivot zone, text edited\nU|7450|9999999|Above 7450\nL|7395|7395|new line on price")
    r.feed([7394.75, 7395.0])                       # the zone kept its state; the new line looked first, on price
    assert len(r.fired) == 1
    r.feed([7393.0, 7395.0])                        # 8 ticks under the zone and the line: both re-armed, both fire
    assert r.said()[1:] == ["ES entered 7395.00–7405.00: Pivot zone, text edited", "ES touched 7395.00: new line on price"]
    # a line that just fired, its text edited, price 2 ticks off it: quiet as well
    (tmp_path / "b").mkdir()
    r2 = Rig(tmp_path / "b", {"ES": "L|7400|7400|line\nP|7410|7420|zone"})
    r2.add()
    r2.feed([7390.0, 7400.0, 7400.5, 7400.0, 7400.5])
    r2.paste("ES", "L|7400|7400|line, text edited\nP|7410|7420|zone")
    r2.feed([7400.0])
    assert r2.said() == ["ES touched 7400.00: line"]
    r2.feed([7402.0, 7400.0])                       # 8 ticks away re-arms it as before
    assert r2.said()[1:] == ["ES touched 7400.00: line, text edited"]


def test_two_rows_on_one_price_each_keep_their_own_state(tmp_path):
    """U|7450|9999999 and D|0|7450 are both a line at 7450, and two rows: each keeps its own state through a paste
    (known by its LO and HI as written, wherever it moves in the block), so one that just fired stays quiet while one
    the plan just brought in fires on its first touch."""
    r = Rig(tmp_path, {"ES": "U|7450|9999999|above"})
    r.add()
    r.feed([7440.0, 7450.0, 7449.0])                # fires, then 4 ticks off: quiet until 8 ticks away
    assert r.said() == ["ES touched 7450.00: above"]
    r.paste("ES", "U|7450|9999999|above\nD|0|7450|below")   # a new row on the same price: a first look, 4 ticks off
    r.paste("ES", "D|0|7450|below\nU|7450|9999999|above")   # saved again, the rows swapped
    r.feed([7450.0])
    assert r.said()[1:] == ["ES touched 7450.00: below"]
    r.feed([7448.0, 7450.0])                        # 8 ticks off re-arms both: both fire, in the block's order
    assert r.said()[2:] == ["ES touched 7450.00: below", "ES touched 7450.00: above"]


def test_a_picked_row_matches_within_a_hundredth_of_a_tick_and_any_open_end(tmp_path):
    """Picked rows are looked up, not tried one by one, with the same result: a pick names a row whose LO and HI are
    each within a hundredth of a tick of it (7395.0012 and 7395.0013 sit on either side of that grid's line), and an
    open end written with any number of 9s, or 1e307, is a row like any other."""
    r = Rig(tmp_path, {"ES": "P|7395.0013|7405|zone\nU|7450|99999999999999|many nines\nU|7460|1e307|huge\nL|7470|7470|not picked"})
    r.add(rows=[[7395.0012, 7405.0], [7450.0, 99999999999999.0], [7460.0, 1e307]])
    r.feed([7390.0, 7396.0, 7440.0, 7450.0, 7458.0, 7460.0, 7470.0])
    assert r.said() == ["ES entered 7395.00–7405.00: zone", "ES touched 7450.00: many nines", "ES touched 7460.00: huge"]
    (tmp_path / "b").mkdir()
    r2 = Rig(tmp_path / "b", {"ES": "P|7395.0013|7405|zone"})
    near, off = r2.add(rows=[[7395.0037, 7405.0]]), r2.add(rows=[[7395.004, 7405.0]])   # 0.0024 and 0.0027 off
    r2.feed([7390.0, 7396.0])
    assert [e["alert_id"] for e in r2.fired] == [near] and off != near


def test_the_rows_an_alert_watches_are_worked_out_once_per_plan(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"ES": PLAN})
    r.add(rows=[[7395.0, 7405.0], [0.0, 7385.0]])
    calls = []
    bands = r.eng._ifthen_bands
    monkeypatch.setattr(r.eng, "_ifthen_bands", lambda a, rows: (calls.append(1), bands(a, rows))[1])
    r.feed([7300.0 + i * 0.25 for i in range(400)])
    assert len(calls) == 1 and len(r.fired) == 2
    r.paste("ES", "P|7395|7405|Pivot")              # a paste: worked out again, once
    calls.clear()
    r.feed([7390.0 - i * 0.25 for i in range(100)])
    assert len(calls) == 1


def test_the_first_look_after_a_start_never_fires(tmp_path):
    """After a restart, the market seeds the last stored trade as the previous price (a price alert's cross during
    the restart still fires); a plan alert's first live trade only looks, even across a line from that price."""
    r = Rig(tmp_path, {"ES": PLAN})
    r.add()
    r.eng.last_px["ES"], r.eng.last_ts["ES"] = 7440.0, r.ts   # market.seed
    r.paste("ES", PLAN)                             # a paste before the first live trade: that trade looks first
    r.feed([7460.0])                                # through 7450 from the stored price: no fire
    assert r.fired == []
    r.feed([7455.0, 7450.0])                        # armed by that look: the next touch fires
    assert r.said() == ["ES touched 7450.00: Accepts above 7450 → continuation / next target 7480"]
    (tmp_path / "b").mkdir()
    r2 = Rig(tmp_path / "b", {"ES": PLAN})
    r2.add()
    r2.feed([7400.0, 7401.0])                       # started inside the zone: no fire until it has been out
    assert r2.fired == []


def test_a_roll_takes_a_first_look_again(tmp_path):
    r = Rig(tmp_path, {"ES": PLAN})
    r.add()
    r.feed([7440.0, 7441.0])
    r.eng.rolled("ES")
    r.feed([7460.0])                                # the new contract's first trade, 19 points up: no fire for 7450
    assert r.fired == []
    r.feed([7451.0, 7450.0])                        # armed by that first look: a touch fires
    assert r.said() == ["ES touched 7450.00: Accepts above 7450 → continuation / next target 7480"]


def test_only_once_stops_after_the_first_row(tmp_path):
    r = Rig(tmp_path, {"ES": "A|7400|7400|first\nB|7400|7410|second"})
    i = r.add(repeat=False, note="plan")
    r.feed([7390.0, 7401.0])                        # both rows in one print: one notification, then off
    assert r.said() == ["ES touched 7400.00: first · plan"] and r.fired[0]["message"].endswith("(last 7401.00)")
    assert not r.eng.alerts[i].enabled and r.store.list_alerts()[0]["enabled"] is False
    r.feed([7420.0, 7405.0])
    assert len(r.fired) == 1
    r.store.update_alert(i, enabled=True)           # Restart: a first look (inside the zone: it waits)
    r.eng.reload()
    r.feed([7405.0, 7404.0])
    assert len(r.fired) == 1
    r.feed([7401.0, 7400.0])
    assert r.said()[1] == "ES touched 7400.00: first · plan" and not r.eng.alerts[i].enabled


def test_every_time_pause_and_expiry(tmp_path):
    r = Rig(tmp_path, {"ES": "L|7400|7400|line"})
    i = r.add(expires=1_009_500)                    # trades at 1_001_000, 1_002_000, ...
    r.feed([7390.0, 7400.0, 7392.0, 7400.0])        # every time: a touch, 8 ticks away, a touch
    assert len(r.fired) == 2
    r.store.update_alert(i, enabled=False)          # Pause
    r.eng.reload()
    r.feed([7392.0, 7400.0])
    assert len(r.fired) == 2
    r.store.update_alert(i, enabled=True)
    r.eng.reload()
    r.feed([7392.0, 7398.0])                        # restarted: first look, then past the expiry
    r.feed([7392.0, 7400.0])
    assert len(r.fired) == 2 and not r.eng.alerts[i].enabled and r.store.list_alerts()[0]["enabled"] is False


def test_each_symbol_reads_its_own_block_micros_too(tmp_path):
    r = Rig(tmp_path, {"ES": "L|7400|7400|ES line", "MES": "L|7410|7410|MES line"})
    es, mes, nq = r.add("ES"), r.add("MES"), r.add("NQ")
    r.feed([7390.0], "ES")
    r.feed([7390.0], "MES")
    r.feed([7390.0], "NQ")
    r.feed([7400.0, 7410.0], "MES")                 # MES reads MES's block, not ES's
    r.feed([7400.0, 7410.0], "NQ")                  # NQ has none
    assert r.said() == ["MES touched 7410.00: MES line"] and r.fired[0]["alert_id"] == mes
    r.feed([7400.0], "ES")
    assert r.said()[1] == "ES touched 7400.00: ES line" and r.fired[1]["alert_id"] == es
    r.paste("NQ", "L|7395|7395|NQ line")            # a paste for NQ: NQ's alert alone follows it
    r.feed([7395.0], "NQ")
    assert r.said()[2:] == ["NQ touched 7395.00: NQ line"] and r.fired[2]["alert_id"] == nq
    assert [x[5] for x in r.eng.ifthen_rows("ES")] == ["ES line"] and [x[5] for x in r.eng.ifthen_rows("MES")] == ["MES line"]


def test_prices_in_the_instruments_format(tmp_path):
    r = Rig(tmp_path, {"ZN": "U|110'16|9999999|above\nP|110'08|110'16|bonds zone"})
    r.add("ZN")
    r.feed([110.0, 110.25], "ZN")
    assert r.said() == ["ZN entered 110'080–110'160: bonds zone"]
    r.feed([110.5], "ZN")
    assert r.said()[1] == "ZN touched 110'160: above"


def test_the_block_is_read_once_not_on_every_trade(tmp_path, monkeypatch):
    r = Rig(tmp_path, {"ES": PLAN})
    r.add()
    reads = []
    get = r.store.get_setting
    monkeypatch.setattr(r.store, "get_setting", lambda k, d=None: (reads.append(k), get(k, d))[1])
    r.feed([7440.0 + i * 0.25 for i in range(200)])
    assert reads == ["ifthen"] and len(r.fired) == 1
    r.eng.ifthen_changed("ES", "L|7300|7300|x")     # a paste hands the block over: no read
    r.feed([7310.0, 7300.0])
    assert reads == ["ifthen"] and r.said()[1] == "ES touched 7300.00: x"


# ---- the API ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CHART_DB", str(tmp_path / "chart.db"))
    monkeypatch.setenv("CHART_FEED", "none")
    monkeypatch.setenv("CHART_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("CHART_LEVELS_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from chartsrv.app import app
    with TestClient(app) as c:
        yield c


def tick(client, prices, sym="ES"):
    """Trades as the feed sends them: on the server's own loop (the store is that thread's)."""
    m = client.app.state.market
    for p in prices:
        client.portal.call(m.alerts.on_tick, Tick(sym, m.alerts.last_ts.get(sym, 1_000_000) + 1000, p, 1, 0))


def test_api_creates_lists_and_edits_plan_alerts(client):
    a = client.post("/api/alerts", json={"symbol": "es", "kind": "ifthen"}).json()   # no plan yet: allowed
    assert a["kind"] == "ifthen" and a["symbol"] == "ES" and a["repeat"] is True
    assert a["params"] == {"rows": "all", "tolerance_ticks": 0, "rearm_ticks": 8}
    b = client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "rows": [[7395, 7405], [7395, 7405], [7450, 9999999]],
                                         "tolerance_ticks": 2, "repeat": False, "note": "plan"}).json()
    assert b["params"]["rows"] == [[7395.0, 7405.0], [7450.0, 9999999.0]] and b["repeat"] is False and b["note"] == "plan"
    assert [x["id"] for x in client.get("/api/alerts").json() if x["kind"] == "ifthen"] == [a["id"], b["id"]]
    p = client.patch(f"/api/alerts/{a['id']}", json={"rows": [[0, 7385]], "tolerance_ticks": 3}).json()
    assert p["params"]["rows"] == [[0.0, 7385.0]] and p["params"]["tolerance_ticks"] == 3
    assert client.patch(f"/api/alerts/{a['id']}", json={"rows": "all"}).json()["params"]["rows"] == "all"
    lv = client.post("/api/alerts", json={"symbol": "ES", "kind": "level"}).json()
    assert client.patch(f"/api/alerts/{lv['id']}", json={"rows": "all"}).status_code == 400
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "rows": []}).status_code == 400
    for bad in ([[1]], [[1, 2, 3]], "some", [["x", 2]], 5):
        assert client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "rows": bad}).status_code == 422, bad
    nan = client.post("/api/alerts", content=b'{"symbol": "ES", "kind": "ifthen", "rows": [[NaN, 2]]}',
                      headers={"content-type": "application/json"})
    assert nan.status_code == 422
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "rows": [[1, 2]] * 201}).status_code == 422
    # an open end written with more 9s than 9999999 is a row the dialog lists: it can be picked
    big = client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "rows": [[7450, 99999999999999], [7460, 1e307]]})
    assert big.status_code == 201 and big.json()["params"]["rows"] == [[7450.0, 99999999999999.0], [7460.0, 1e307]]
    assert client.post("/api/alerts", json={"symbol": "AAPL", "kind": "ifthen"}).status_code == 400   # a stock: price only
    assert client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen", "tolerance_ticks": 201}).status_code == 422


def test_api_a_paste_moves_the_alerts_to_the_new_plan(client, caplog):
    caplog.set_level(logging.WARNING, logger="chartsrv.alerts")
    m = client.app.state.market
    fired = []
    m.alerts.on_fire = lambda e: fired.append(e["message"].split(" (last ")[0])
    client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen"})
    tick(client, [7390.0, 7391.0])                  # before any plan: the alert looks, finds no rows
    assert client.put("/api/ifthen/ES", json={"rows": "P|7395|7405|Pivot\nL|7391|7391|on price"}).status_code == 200
    tick(client, [7391.0, 7392.0, 7395.0])
    assert fired == ["ES entered 7395.00–7405.00: Pivot"]
    # the indicator's settings save the block too (the Indicators dialog): the alerts follow that as well
    assert client.put("/api/indicators/ifthen/ES", json={"RowsBlock": "L|7380|7380|lower"}).status_code == 200
    tick(client, [7386.0, 7380.0])
    assert fired[1:] == ["ES touched 7380.00: lower"]
    client.put("/api/ifthen/ES", json={"rows": ""})   # deleted: no rows, nothing fires, the alert stays
    tick(client, [7395.0, 7380.0, 7450.0])
    assert len(fired) == 2 and [x["enabled"] for x in client.get("/api/alerts").json()] == [True]
    assert [r.getMessage() for r in caplog.records if r.name == "chartsrv.alerts"] == []


def test_api_saving_the_same_plan_again_sends_no_second_alert(client, caplog):
    """The Plan tab's Save always saves: a second click, with price chopping at a zone that just fired, sends nothing
    until price has been 8 ticks out."""
    caplog.set_level(logging.WARNING, logger="chartsrv.alerts")
    m = client.app.state.market
    fired = []
    m.alerts.on_fire = lambda e: fired.append(e["message"].split(" (last ")[0])
    plan = {"rows": "P|7395|7405|Pivot zone\nU|7450|9999999|Above 7450"}
    assert client.put("/api/ifthen/ES", json=plan).status_code == 200
    client.post("/api/alerts", json={"symbol": "ES", "kind": "ifthen"})
    tick(client, [7390.0, 7395.0, 7394.5, 7395.0, 7394.0, 7395.0, 7394.5])
    assert fired == ["ES entered 7395.00–7405.00: Pivot zone"]
    assert client.put("/api/ifthen/ES", json=plan).status_code == 200
    tick(client, [7395.0])
    assert len(fired) == 1
    tick(client, [7393.0, 7395.0])                  # 8 ticks out, then back in
    assert fired[1:] == ["ES entered 7395.00–7405.00: Pivot zone"]
    assert [r.getMessage() for r in caplog.records if r.name == "chartsrv.alerts"] == []


def test_the_stored_rows_are_the_only_plan(tmp_path):
    """The engine reads the setting /api/ifthen keeps ({symbol: {rows, updated}}), and nothing else."""
    assert ifthen.alert_rows(None) == [] and ifthen.alert_rows("garbage\n|||") == []
    store = Store(tmp_path / "t.db")
    store.set_setting("ifthen", {"ES": {"rows": "L|7400|7400|x", "updated": 1}, "NQ": "not a block"})
    eng = AlertEngine(store, {"ES": ES, "NQ": NQ}, None, lambda e: None)
    assert [r[:3] for r in eng.ifthen_rows("ES")] == [("line", 7400.0, 7400.0)] and eng.ifthen_rows("NQ") == []
