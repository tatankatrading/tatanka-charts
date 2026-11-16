"""Writes chartsrv/nt_schema.json: every setting of the Tatanka NinjaTrader indicators other than the AMT Toolkit
(TPO Profile, Gap Analysis, HTF EMA, If/Then Bias Table, Level Lines) as NinjaTrader's indicator dialog shows them
(group, order, label, description, type, choices, range) with NinjaTrader's defaults, read from the sources:

    python tools/gen_nt_schema.py <tatanka-indicators checkout>/ninjatrader

The AMT Toolkit has its own generator (gen_amt_schema.py). Types: bool, int, float, str, text (multi-line), choice,
color ("#rrggbb", or "#rrggbbaa" where "alpha" is set), stroke ({color, dash, width}, and opacity 0-100 where "opacity" is set), font ({family, size,
bold, italic}). It writes these five only: run
tools/gen_vp_schema.py and tools/gen_dsma_schema.py (from the repository root) after it for the volume profiles and Daily
SMA x4. LOCAL holds this app's own wording where NinjaTrader's doesn't fit it."""
import json
import re
import sys
from pathlib import Path

INDICATORS = {   # app key -> source file, display name
    "tpo": ("TatankaTpoProfile.cs", "TPO Profile"),
    "gap": ("TatankaGapAnalysis.cs", "Tatanka Gap Analysis"),
    "hema": ("TatankaHtfEma.cs", "HTF EMA"),
    "ifthen": ("TatankaIfThenBiasTable.cs", "If/Then Bias Table"),
    "lines": ("TatankaLevelLines.cs", "Level Lines"),
}
SKIP = {   # NinjaTrader-only: sounds, chart-to-chart sharing (one app here), editor buttons, input series
    "AlertSound", "SharingMode", "OpenCustomLevelsEditor", "CustomLevelsExample",
}
# WPF named colors the sources use.
NAMED = {"Blue": "#0000ff", "Fuchsia": "#ff00ff", "White": "#ffffff", "Yellow": "#ffff00", "Gray": "#808080",
         "Teal": "#008080", "LimeGreen": "#32cd32", "IndianRed": "#cd5c5c", "SlateGray": "#708090", "Orange": "#ffa500",
         "Magenta": "#ff00ff", "Black": "#000000", "Red": "#ff0000", "Green": "#008000", "Transparent": "#ffffff"}
BARS_PERIOD = ["Second", "Minute", "Day", "Week", "Month", "Year"]   # the HTF EMA source types this app builds: NinjaTrader's
# time bars (it lists every bar type; tick, volume, range and the others need trades the chart would have to send)
# Strokes whose Opacity this app draws, as NinjaTrader's Stroke has one (0-100, on its BrushDX). Level Lines' Default
# line has none here: NinjaTrader builds each custom level a new Stroke from its brush, dash and width, at 100.
OPACITY = {("lines", "RangeStroke"), ("lines", "RangeMidStroke")}
# Colors whose alpha this app draws ("#rrggbbaa"), as the original draws a brush's alpha and opacity
# (TatankaBiasStyle.Color). If/Then's Position colors have none: every place it draws them sets its own alpha.
ALPHA = {("ifthen", "BackgroundBrush"), ("ifthen", "BorderBrush")}
MOVES = ("Written for ES/NQ's 08:30-15:00 CT: with Use Instrument Hours on, on another instrument it moves with that one's "
         "regular hours (CL's 08:30 is 08:00, its 15:00 13:30).")
# This app's own wording where NinjaTrader's doesn't fit it: its exchange template is CME's, in CT, and the If/Then
# labels sit at the chart's right edge. (app key, setting) -> what replaces NinjaTrader's label or description.
LOCAL = {
    ("tpo", "SessionTime"): {"description": "HHmm-HHmm in CT (Chicago time, as the chart's Trading Hours template here), e.g. "
                                            "0830-1500. " + MOVES},
    ("tpo", "UseInstrumentHours"): {"description": "Session Time is written for ES's regular hours (08:30-15:00 CT), in CT here. "
                                                   "On another market it moves to its own (GC 07:20-12:30, CL 08:00-13:30): a time "
                                                   "nearer the open moves with the open, one nearer the close with the close. Off: "
                                                   "as typed on every market."},
    ("gap", "RthStart"): {"description": "Chicago time, including daylight saving time. Default 08:30:00. " + MOVES},
    ("gap", "RthEnd"): {"description": "Default 15:00:00. Reference price is the last loaded RTH chart-bar close. " + MOVES},
    ("ifthen", "LabelOffset"): {"label": "Line label room (bars right of last candle)",
                                "description": "The labels sit at the chart's right edge, so they stay put when you zoom or "
                                               "scroll; this is the room kept for them right of the last candle (NinjaTrader "
                                               "draws them this many bars after it)."},
}
PROP = re.compile(r"^\s*public\s+([\w.<>]+)\s+(\w+)\s*(\{.*)?$")


def color(expr: str) -> str | None:
    expr = expr.strip()
    if m := re.match(r"(?:System\.Windows\.Media\.)?Brushes\.(\w+)$", expr):
        return NAMED[m.group(1)]
    if m := re.match(r"MakeBrush\((\d+),\s*(\d+),\s*(\d+)\)$", expr):
        return "#%02x%02x%02x" % tuple(int(x) for x in m.groups())
    if m := re.search(r'"(#[0-9A-Fa-f]{6,8})"', expr):
        h = m.group(1).lower()
        return "#" + h[-6:]
    return None


def value(expr: str, typ: str, src: str):
    expr = expr.strip().rstrip(";").strip()
    if typ == "color":
        return color(expr)
    if typ == "stroke":
        m = re.match(r"new Stroke\(([^,]+),\s*(?:DashStyleHelper\.(\w+),\s*)?([\d.]+)\)", expr)
        return {"color": color(m.group(1)), "dash": m.group(2) or "Solid", "width": float(m.group(3))}
    if typ == "font":   # SimpleFont: Bold and Italic false unless set in an initializer
        m = re.match(r'new SimpleFont\("([^"]+)",\s*(\d+)\)\s*(\{[^}]*\})?$', expr)
        init = m.group(3) or ""
        return {"family": m.group(1), "size": int(m.group(2)), "bold": bool(re.search(r"Bold\s*=\s*true", init)),
                "italic": bool(re.search(r"Italic\s*=\s*true", init))}
    if typ == "bool":
        return expr == "true"
    if typ in ("int", "float"):
        return float(expr) if typ == "float" else int(float(expr))
    if typ in ("str", "text", "choice"):
        if expr.startswith('"') or expr.startswith('@"'):
            return re.match(r'@?"((?:[^"\\]|\\.)*)"', expr).group(1).encode().decode("unicode_escape")
        if expr == "string.Empty":
            return ""
        if "." in expr:   # an enum value or a constant
            name = expr.split(".")[-1]
            if m := re.search(r"const string " + name + r"\s*=\s*((?:\s*\"(?:[^\"\\]|\\.)*\"\s*\+?)+);", src):
                return "".join(json.loads('"' + s + '"') for s in re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))
            return name
    raise ValueError(f"cannot read default {expr!r} ({typ})")


def parse(path: Path, key: str = "") -> list[dict]:
    src = path.read_text(encoding="utf-8")
    enums = {n: [v.split("=")[0].strip() for v in body.split(",") if v.strip() and not v.strip().startswith("//")]
             for n, body in re.findall(r"public enum (\w+)\s*\{([^}]*)\}", src)}
    enums = {n: [re.sub(r"//.*", "", v).strip() for v in vs if re.sub(r"//.*", "", v).strip()] for n, vs in enums.items()}
    convert = {k: json.loads("[" + v + "]") for k, v in re.findall(r'case "(\w+)": return new StandardValuesCollection\(new\[\] \{([^}]*)\}\)', src)}
    defaults_block = src[src.index("State == State.SetDefaults"):]
    defaults_block = defaults_block[:re.search(r"else if \(State == State\.\w+\)", defaults_block).start()]
    defaults = dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?);\s*$", defaults_block, re.M))
    out, attrs = [], []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("["):
            attrs.append(s)
            continue
        m = PROP.match(ln)
        if not m or not attrs:
            attrs = [] if s and not s.startswith("//") else attrs
            continue
        a = " ".join(attrs)
        attrs = []
        d = re.search(r"Display\((.*)\)\]", a)
        typ, name = m.group(1), m.group(2)
        if not d or "Browsable(false)" in a or name in SKIP:
            continue
        t = d.group(1)
        g = lambda k: (re.search(k + r'\s*=\s*"((?:[^"\\]|\\.)*)"', t) or [None, None])[1]
        typ = typ.split(".")[-1]
        kind = {"bool": "bool", "int": "int", "double": "float", "float": "float", "string": "str", "Brush": "color", "WpfBrush": "color",
                "Stroke": "stroke", "SimpleFont": "font"}.get(typ)
        item = {"name": name, "group": g("GroupName") or "Parameters", "order": int(re.search(r"Order\s*=\s*(\d+)", t).group(1)),
                "label": g("Name")}
        if typ in enums or typ == "BarsPeriodType":
            kind, item["choices"] = "choice", enums.get(typ, BARS_PERIOD)
        elif name in convert:
            kind, item["choices"] = "choice", convert[name]
        elif kind == "str" and ("MultilineEditor" in a or name in ("RowsBlock", "CustomLevels")):
            kind = "text"
        if kind is None:
            raise ValueError(f"{path.name}: unknown type {typ} for {name}")
        item["type"] = kind
        if g("Description"):
            item["description"] = g("Description").replace('\\"', '"')
        item.update(LOCAL.get((key, name), {}))
        if r := re.search(r"Range\(([^,]+),\s*([^)]+)\)", a):
            num = lambda x: None if "MaxValue" in x or "MinValue" in x else float(x) if "." in x else int(x)
            item["min"], item["max"] = num(r.group(1).strip()), num(r.group(2).strip())
        if name not in defaults:
            raise ValueError(f"{path.name}: no default for {name}")
        item["default"] = value(defaults[name], kind, src)
        if (key, name) in ALPHA:
            item["alpha"] = True
        if (key, name) in OPACITY:
            item["opacity"], item["default"]["opacity"] = True, 100
        out.append(item)
    for stroke, style, label in re.findall(r'AddPlot\((new Stroke\([^)]*\)),\s*PlotStyle\.(\w+),\s*"([^"]+)"\)', src):
        out.append({"name": "Plot" + re.sub(r"\W", "", label), "group": "Plots", "order": 1, "label": label, "type": "stroke",
                    "default": value(stroke, "stroke", src)})
    rank = lambda grp: int(grp.split(".")[0]) if grp[0].isdigit() else {"Plots": 98}.get(grp, 50)
    seen = []
    for x in out:
        if x["group"] not in seen:
            seen.append(x["group"])
    return sorted(out, key=lambda x: (rank(x["group"]), seen.index(x["group"]), x["order"]))


def main(root: str):
    out = {}
    for key, (fname, title) in INDICATORS.items():
        src = (Path(root) / fname).read_text(encoding="utf-8")
        version = re.search(r"v(\d+\.\d+\.\d+)", src)
        out[key] = {"title": title, "version": version.group(1) if version else "", "settings": parse(Path(root) / fname, key)}
    dest = Path(__file__).resolve().parents[1] / "chartsrv" / "nt_schema.json"
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(dest, {k: len(v["settings"]) for k, v in out.items()})


if __name__ == "__main__":
    main(sys.argv[1])
