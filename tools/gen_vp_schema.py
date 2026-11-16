"""Writes the settings of the three NinjaTrader volume-profile indicators into server/chartsrv/nt_schema.json, read
from their sources (server/tests/vp_csharp, copies of tatanka-indicators' ninjatrader files, unchanged but for the site left out of the title line): every browsable property with its [Display] name, group,
order and description, its [Range], its type (enums become choices) and its default from SetDefaults. The keys are
the app's study keys: prof (Session), aavp (Auto Anchored), pvp (Periodic).

    python tools/gen_vp_schema.py"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "server" / "tests" / "vp_csharp"
OUT = ROOT / "server" / "chartsrv" / "nt_schema.json"
FILES = {"prof": "TatankaSessionVolumeProfile.cs", "aavp": "TatankaAutoAnchoredVolumeProfile.cs",
         "pvp": "TatankaPeriodicVolumeProfile.cs"}
NAMED = {"LightGray": "#d3d3d3", "Transparent": "transparent", "White": "#ffffff", "Black": "#000000"}
# This app's own wording where NinjaTrader's doesn't fit it (its exchange template is CME's, CT): (app key, setting) ->
# description.
LOCAL = {
    ("prof", "SessionStart"): "In the session time zone (CT for ExchangeTemplate and Central; Chart: the chart's), written "
                              "for ES/NQ's 08:30-15:00 CT (09:30-16:00 Eastern): with Use Instrument Hours on, on another "
                              "instrument it moves with that one's regular hours, in any zone (CL's 08:30 CT is 08:00, its "
                              "15:00 13:30).",
    ("prof", "SessionTimeZone"): "ExchangeTemplate is Chicago time here (CME's Trading Hours template, with DST); Central the "
                                 "same, Eastern New York, UTC. Chart is the chart's time zone (click the clock).",
}


def parse(text: str, key: str = "") -> dict:
    text = text.replace("\r", "")
    title, version = re.search(r"^// (Tatanka [A-Za-z ]+?) v?(\d+\.\d+\.\d+)", text, re.M).groups()   # "1.0.0" or "v1.0.1"
    enums = {m[1]: [x.strip() for x in m[2].split(",")] for m in re.finditer(r"public enum (\w+) \{([^}]*)\}", text)}
    defaults = {}
    body = text[text.index("State.SetDefaults"):text.index("State.Configure")]
    for line in body.split(";"):
        parts = [p.strip() for p in line.split("=")]
        if len(parts) < 2:
            continue
        value = parts[-1]
        for name in parts[:-1]:
            name = name.split()[-1] if name else name
            if re.fullmatch(r"[A-Z]\w*", name or ""):
                defaults[name] = value
    out = []
    for m in re.finditer(r"\[([^\]]*Display\([^\]]*)\]\s*public (\w+) (\w+) \{ get; set; \}", text):
        attrs, typ, name = m.groups()
        if "Browsable(false)" in attrs:
            continue
        d = dict(re.findall(r'(\w+) = "([^"]*)"', attrs))
        order = int(re.search(r"Order = (\d+)", attrs)[1])
        x = {"name": name, "group": d["GroupName"], "order": order, "label": d["Name"]}
        if "Description" in d:
            x["description"] = d["Description"]
        if (key, name) in LOCAL:
            x["description"] = LOCAL[(key, name)]
        rng = re.search(r"Range\(([-\d.]+), ([-\d.]+)\)", attrs)
        raw = defaults.get(name)
        if typ in enums:
            x.update(type="choice", choices=enums[typ], default=raw.split(".")[-1])
        elif typ == "bool":
            x.update(type="bool", default=raw == "true")
        elif typ in ("int", "double"):
            x["type"] = "int" if typ == "int" else "float"
            x["default"] = int(raw) if typ == "int" else float(raw)
        elif typ == "string":
            x.update(type="str", default=raw.strip('"'))
        elif typ == "WpfBrush":
            rgb = re.search(r"\(0x(\w\w), 0x(\w\w), 0x(\w\w)\)", raw)
            x.update(type="color", default=("#" + "".join(rgb.groups())).lower() if rgb else NAMED[raw.split(".")[-1]],
                     none=True)   # NinjaTrader's brush picker also has Transparent
        else:
            raise ValueError(f"{name}: type {typ}")
        if rng and x["type"] in ("int", "float"):
            lo, hi = (float(v) for v in rng.groups())
            x["min"], x["max"] = (int(lo), int(hi)) if x["type"] == "int" else (lo, hi)
        out.append(x)
    return {"title": title, "version": version, "settings": out}


def main():
    schema = json.loads(OUT.read_text())
    for key, fn in FILES.items():
        schema[key] = parse((SRC / fn).read_text(), key)
        print(f"{key}: {schema[key]['title']} {schema[key]['version']}, {len(schema[key]['settings'])} settings")
    OUT.write_text(json.dumps(schema, indent=1) + "\n")


if __name__ == "__main__":
    main()
