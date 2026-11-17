# Copyright (c) 2026 Tatanka Trading LLC.
# This Source Code Form is subject to the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, obtain one at https://mozilla.org/MPL/2.0/.
"""Tatanka If/Then Bias Table v1.0.0, ported from the NinjaTrader 8 source
(tatankatrading/tatanka-indicators, ninjatrader/TatankaIfThenBiasTable.cs).

The rows, the active band, labels, zones and table run in the browser (web/studies.js, the TatankaBiasLogic
and TatankaBiasPainter port) on every tick. The server keeps each symbol's pasted block, so every device
shows the same plan, and supplies the previous day's ATR (TatankaBiasDailyAtr) from stored 1-minute bars.

The block is also read here, for the If/Then alerts (alerts.py): bias_parse and bias_number are web/studies.js's
biasParse and biasNumber line for line, so the server reads every block exactly as the chart does
(tests/test_ifthen_alerts.py runs both on the same inputs)."""
import math
import re

VERSION = "1.0.0"
MAX_BLOCK = 20_000
NO_TOP = 1_000_000          # a High above this: the band has no top (9999999)

# JavaScript's own whitespace (String.prototype.trim), not Python's: str.strip() takes \x1c-\x1f and \x85 too, and
# leaves \ufeff
_JS_SPACE = "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" \
            "\u2028\u2029\u202f\u205f\u3000\ufeff"
# JavaScript's \d is 0-9 alone; Python's takes any script's digits
_BIAS_NUMBER = re.compile(r"[\t\n\x0b\x0c\r ]*[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?[\t\n\x0b\x0c\r ]*")
_IS_32 = re.compile(r"[+-]?[0-9]+'[0-9]")
_P32 = re.compile(r"([+-]?)([0-9]+)['-]([0-9][0-9]?)(?:\.?([0-9]))?")
_EIGHTHS = {"0": 0, "1": 0.125, "2": 0.25, "3": 0.375, "5": 0.5, "6": 0.625, "7": 0.75, "8": 0.875}


def _parse32(text: str) -> float:
    """studies.js parse32 (with no tick): 110'165, 110'16.5, 110'16 or 110-165 in 32nds, else parseFloat's number."""
    s = text.strip(_JS_SPACE).replace(",", "")
    m = _P32.fullmatch(s)
    if not m:
        lead = re.match(r"[+-]?[0-9]+", s)   # parseFloat: the number it starts with (here always digits, then ')
        return float(lead.group()) if lead else math.nan
    eighth = _EIGHTHS.get(m[4] or "0", int(m[4] or 0) / 10)
    if int(m[3]) > 31:
        return math.nan
    return (-1 if m[1] == "-" else 1) * (float(m[2]) + (int(m[3]) + eighth) / 32)


def bias_number(text: str) -> float | None:
    """studies.js biasNumber: a band's LO or HI, None where the row is skipped. NumberStyles.Float with the spaces
    taken out, or 32nds (110'165)."""
    t = text.replace(" ", "")
    if _IS_32.match(t):
        v = _parse32(t)
        return v if math.isfinite(v) else None
    if not _BIAS_NUMBER.fullmatch(t):
        return None
    v = float(t.strip("\t\n\x0b\x0c\r "))
    return v if math.isfinite(v) else None


def _utf16_len(s: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def bias_parse(block: str | None) -> list[dict]:
    """studies.js biasParse: the block's rows, {Bias, Low, High, Text}, in order. A line is BIAS|LO|HI|TEXT (more
    '|' stay in the text); empty lines, # comments and rows whose LO or HI is not a number are left out."""
    result = []
    for line in (block or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if len(line) == 0 or line[0] == "#":
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        lo, hi = bias_number(parts[1]), bias_number(parts[2])
        if lo is None or hi is None:
            continue
        c = parts[0][0] if parts[0] else ""
        if c and ord(c) > 0xFFFF:   # JavaScript's string index: a UTF-16 unit, the high surrogate of an emoji
            c = chr(0xD800 + ((ord(c) - 0x10000) >> 10))
        u = c.upper()
        result.append({"Bias": (u if _utf16_len(u) == 1 else c) if c else "N", "Low": lo, "High": hi,
                       "Text": "|".join(parts[3:]).replace("\\n", "\n")})
    return result


# ---- If/Then alerts (alerts.py): each row alerts by itself, a line on its price, a zone on entering it -------------
TEXT_CAP = 140


def one_line(text: str, cap: int = TEXT_CAP) -> str:
    """A row's text for a notification: its lines joined with " / ", spaces squeezed, cut to `cap` with an ellipsis."""
    parts = (" ".join(p.split()) for p in text.replace("\\n", "\n").replace("\r", "\n").split("\n"))
    t = " / ".join(p for p in parts if p)
    return t if len(t) <= cap else t[:cap - 1].rstrip() + "…"


def alert_band(low: float, high: float) -> tuple[str, float, float] | None:
    """What a row alerts on: ("line", p, p), ("zone", lo, hi), or None (no bottom and no top: never). A line is one
    price: LO equal to HI, or one end open (LO 0: the line is at HI; HI 9999999: at LO). A zone has both ends, LO below
    HI. Written the wrong way round (HI below LO), it is the same band."""
    lo, hi = min(low, high), max(low, high)
    if lo <= 0 and hi > NO_TOP:
        return None
    if lo <= 0:
        return "line", hi, hi
    if hi > NO_TOP or lo == hi:
        return "line", lo, lo
    return "zone", lo, hi


def alert_rows(block: str | None) -> list[tuple]:
    """The block's rows an alert can fire on: (kind, lo, hi, Low, High, text), kind "line" or "zone"; Low and High
    as written (what an alert's row choice names), text on one line."""
    out = []
    for r in bias_parse(block):
        band = alert_band(r["Low"], r["High"])
        if band:
            out.append((*band, r["Low"], r["High"], one_line(r["Text"])))
    return out


class TatankaBiasDailyAtr:
    """Pine ta.atr: RMA of the true range, seeded with an SMA after `length` days."""

    def __init__(self, length: int):
        self.length = max(1, length)
        self.closes: list[float] = []
        self.sums: list[float] = []
        self.values: list[float] = []
        self.current = -1

    @property
    def Previous(self) -> float:
        return self.values[self.current - 1] if self.current > 0 else math.nan

    @property
    def Current(self) -> float:
        return self.values[self.current] if self.current >= 0 else math.nan

    def Update(self, index: int, high: float, low: float, close: float):
        if index < 0:
            return
        if index > len(self.values):
            raise ValueError("Nonsequential daily ATR data.")
        if index == len(self.values):
            self.values.append(math.nan)
            self.closes.append(0.0)
            self.sums.append(0.0)
        tr = (high - low if index == 0
              else max(high - low, max(abs(high - self.closes[index - 1]), abs(low - self.closes[index - 1]))))
        self.sums[index] = (0 if index == 0 else self.sums[index - 1]) + tr
        # Pine ta.atr = RMA(TR): seed with an SMA only after length observations.
        n = self.length
        self.values[index] = (math.nan if index < n - 1 else self.sums[index] / n if index == n - 1
                              else (1.0 / n) * tr + (1.0 - 1.0 / n) * self.values[index - 1])
        self.closes[index] = close
        self.current = index


def previous_atr(hlc: list[list[float]], length: int, today_has_bars: bool) -> float | None:
    """dailyAtr.Previous as NinjaTrader shows it: the ATR of the day before the latest daily bar. While
    today's session trades, the latest bar is today's (not in `hlc`, which holds completed days); before
    its first trade (a weekend), it is the last completed day."""
    atr = TatankaBiasDailyAtr(length)
    for i, (h, l, c) in enumerate(hlc):
        atr.Update(i, h, l, c)
    v = atr.Current if today_has_bars else atr.Previous
    return None if math.isnan(v) else v
