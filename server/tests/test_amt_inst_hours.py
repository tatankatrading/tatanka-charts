"""AMT Toolkit v1.2.4: Use Instrument Hours (inst_hours). The session fields are written for ES's regular hours; with it on
(the default) another instrument's move to its own, here (amt_hub.instrument_hours) as in the NinjaTrader original
(InstrumentHours.Apply, its own table of roots). Off, every field runs as written on ES's hours, on bars that fit them,
as NinjaTrader v1.2.3. The table is checked against config/futures.json, and the schema, the version and the NinjaTrader
Guide's hours table against the source, when a tatanka-indicators checkout is given (TATANKA_INDICATORS), as the parity
tests take one."""
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from chartsrv import amt, amt_hub
from chartsrv.config import Instrument, futures_catalog

CL = Instrument("CL", "Crude", 0.01, 1000, "CL", rth=("08:00", "13:30"), calendar="energy")
GC = Instrument("GC", "Gold", 0.10, 100, "GC", rth=("07:20", "12:30"), calendar="metals")
ES = Instrument("ES", "E-mini", 0.25, 50, "ES", rth=("08:30", "15:00"), calendar="equity")
CONFIG = Path(__file__).resolve().parents[2] / "config"
INDICATORS = os.environ.get("TATANKA_INDICATORS")
needs_indicators = pytest.mark.skipif(not INDICATORS, reason="needs a tatanka-indicators checkout (TATANKA_INDICATORS)")
SESSIONS = [k for k, spec in amt_hub.SCHEMA_BY_NAME.items() if spec.get("session")]


def _hub(*insts):
    hub = amt_hub.AmtHub.__new__(amt_hub.AmtHub)
    hub.m = SimpleNamespace(instruments={i.symbol: i for i in insts})
    hub._set({}, {})
    return hub


def _source():
    return (Path(INDICATORS) / "ninjatrader" / "TatankaAmtToolkit.cs").read_text(encoding="utf-8-sig")


def test_use_instrument_hours_is_a_global_setting_on_by_default():
    spec = amt_hub.SCHEMA_BY_NAME["inst_hours"]
    assert (spec["group"], spec["order"], spec["label"], spec["type"]) == ("1. Global Settings", 8, "Use Instrument Hours", "bool")
    assert amt.DEFAULTS["inst_hours"] is True and amt_hub.DEFAULTS["inst_hours"] is True
    names = [x["name"] for x in amt_hub.SCHEMA if x["group"].startswith("1.")]
    assert names[-2:] == ["tzguard_enable", "inst_hours"]   # after Warn if Session Times Were Changed, as in NinjaTrader
    assert amt_hub.clean_settings({"inst_hours": False}) == {"inst_hours": False} and amt_hub.clean_settings({"inst_hours": True}) == {}
    with pytest.raises(ValueError):
        amt_hub.clean_settings({"inst_hours": 1})


def test_on_gc_and_cl_the_session_fields_move_to_their_hours():
    hub = _hub(ES, CL, GC)
    gc, cl = hub.settings_for("GC"), hub.settings_for("CL")
    assert (gc.ib_session, gc.pd_sess_time, gc.cc_sess_def, gc.amt_sess, gc.spikeTimeRange) == \
        ("0720-0820", "0720-1230", "0720-1230:23456", "1700-0720:123456", "1200-1230")
    assert (cl.ib_session, cl.pd_sess_time, cl.amt_sess, cl.spikeTimeRange) == ("0800-0900", "0800-1330", "1700-0800:123456", "1300-1330")
    assert (gc.vwap_start_h, gc.vwap_start_m, gc.vwap_end_h, gc.vwap_end_m, gc.inv_end_h, gc.inv_end_m) == (7, 20, 12, 30, 7, 20)
    assert gc.mvp_sess_time == "0720-1230" and gc.mvp_sess_dur == 310 / 60   # the Session profile's own length, moved
    runner = hub.new_runner("GC", 5)
    assert (runner.rth_session, runner.tpo_shift) == ("0720-1230", -70)
    assert (hub.new_runner("CL", 5).rth_session, hub.tpo_shift("CL")) == ("0800-1330", -30)
    es = hub.settings_for("ES")
    assert all(getattr(es, k) == amt_hub.DEFAULTS[k] for k in SESSIONS)
    assert (hub.new_runner("ES", 5).rth_session, hub.tpo_shift("ES")) == ("0830-1500", 0)


def test_off_every_field_runs_as_written_on_es_hours():
    """Use Instrument Hours off: nothing moves on GC or CL (settings_for, tpo_shift, new_runner), a chart's own or the
    symbol's starting settings, as NinjaTrader v1.2.3 ran them."""
    off = {"inst_hours": False, "ib_session": "0830-1000", "sp_rth_session": "1200-1500"}
    hub = _hub(ES, CL, GC)
    for sym in ("GC", "CL"):
        s = hub.settings_for(sym, off)
        written = amt_hub.engine_settings(off)
        assert all(getattr(s, k) == getattr(written, k) for k in SESSIONS), sym
        assert (s.vwap_start_h, s.vwap_end_h, s.inv_end_h, s.inv_end_m, s.mvp_sess_dur) == (8, 15, 8, 30, 6.5)
        assert hub.tpo_shift(sym, off) == 0
        r = hub.new_runner(sym, 5, off)
        assert (r.rth_session, r.tpo_shift, r.S.ib_session, r.S.sp_rth_session) == ("0830-1500", 0, "0830-1000", "1200-1500")
        assert hub.settings_for(sym, {"inst_hours": True, **{k: v for k, v in off.items() if k != "inst_hours"}}).ib_session != "0830-1000"
    hub._set({"inst_hours": False}, {})   # every chart's starting settings with it off
    assert hub.settings_for("GC").ib_session == "0830-0930" and hub.tpo_shift("GC") == 0
    assert hub.new_runner("GC", 5).rth_session == "0830-1500"
    assert amt_hub.instrument_hours(GC.rth, {"inst_hours": False}) == (None, "0830-1500", 0)
    assert amt_hub.instrument_hours(None, {}) == (None, "0830-1500", 0)   # no regular hours (crypto): ES's


def _table(src: str) -> dict:
    body = src[src.index("public static class InstrumentHours"):]
    body = body[:body.index("\n    }\n")]   # (read_text reads its CRLF as \n)
    out = {}
    for o, c, roots in re.findall(r'Add\("(\d\d:\d\d)", "(\d\d:\d\d)", ([^)]*)\);', body):
        for r in re.findall(r'"([^"]+)"', roots):
            assert r not in out, f"{r} listed twice"
            out[r] = (o, c)
    return out


@needs_indicators
def test_the_csharp_hours_table_is_config_futures_json():
    """Every root config/futures.json gives regular hours to has the same in the C# table, and no other root is in it
    (one with none, crypto, keeps ES's hours there as here)."""
    want = {x["symbol"]: tuple(x["rth"]) for x in futures_catalog(CONFIG) if x.get("rth")}
    assert want and _table(_source()) == want


@needs_indicators
def test_the_csharp_setting_is_the_schemas():
    """Its NinjaTrader display name, group, order and description, as tools/gen_amt_schema.py reads them."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_amt_schema", Path(__file__).resolve().parents[1] / "tools" / "gen_amt_schema.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    src = _source()
    (cs,) = [x for x in gen.parse(src) if x["name"] == "inst_hours"]
    assert cs == amt_hub.SCHEMA_BY_NAME["inst_hours"]
    assert re.search(r"public bool inst_hours = true;", src) and re.search(r"\binst_hours = true;", src.split("private void InitializeSettings()")[1])


def test_off_the_engine_bars_fit_es_hours():
    """Use Instrument Hours off: the engine's bars fit ES's 08:30-15:00 on every instrument, as NinjaTrader v1.2.3 ran
    on the chart's own (a 30-minute GC chart: 30-minute bars); on, GC's 07:20 (10). The chart's settings decide, the
    symbol's starting ones for its alert runner."""
    hub = _hub(ES, CL, GC)
    off = {"inst_hours": False}
    assert [hub.engine_minutes("GC", n, {}) for n in (5, 15, 30)] == [5, 5, 10]
    assert [hub.engine_minutes("GC", n, off) for n in (5, 15, 30)] == [5, 15, 30]
    assert hub.engine_minutes("ES", 20, off) == 10 and hub.engine_minutes("CL", 30, off) == 30
    hub.alert_cfg = {"GC": {"on": True, "minutes": 30, "set": off}}
    assert hub.alert_run("GC")[0] == 30
    hub.alert_cfg = {"GC": {"on": True, "minutes": 30, "set": {}}}
    assert hub.alert_run("GC")[0] == 10
    hub.alert_cfg = {"GC": {"on": True, "minutes": 30}}
    assert hub.alert_run("GC")[0] == 10
    hub._set(off, {})   # every chart's starting settings with it off
    assert hub.alert_run("GC")[0] == 30


def test_a_chart_with_it_off_runs_on_its_own_bars(tmp_path, monkeypatch):
    """A 30-minute GC chart with Use Instrument Hours off runs on 30-minute bars at ES's hours (v1.2.3's), one with it
    on on 10-minute bars at GC's."""
    import asyncio
    from chartsrv import sessions
    from chartsrv.market import Client
    from test_review_fixes import build_market
    sessions.set_calendar({"days": []})

    async def go():
        m, _ = build_market(tmp_path, monkeypatch)
        a, b = Client(), Client()
        for cl in (a, b):
            cl.subs[1] = object()
        await m.amt.subscribe(a, 1, "GC", 30, {"inst_hours": False})
        await m.amt.subscribe(b, 1, "GC", 30)
        off = next(k for k in m.amt.entries if len(k) == 3)
        assert off[:2] == ("GC", 30) and ("GC", 10) in m.amt.entries
        r = m.amt.entries[off].runner
        assert (r.rth_session, r.tpo_shift, r.S.ib_session) == ("0830-1500", 0, "0830-0930")
        assert m.amt.entries[("GC", 10)].runner.rth_session == "0720-1230"
    asyncio.run(go())


def test_the_dashboard_header_is_this_version():
    """The dashboard's header and loading text and the docs that quote them name amt.VERSION (index.html kept
    v1.2.3 after the 1.2.4 bump)."""
    root = Path(__file__).resolve().parents[2]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert f'<span class="t">Tatanka AMT v{amt.VERSION}</span>' in html
    for f in ("web/index.html", "web/app.js", "docs/CHARTS.md"):
        found = re.findall(r"Tatanka AMT v(\d+\.\d+\.\d+)", (root / f).read_text(encoding="utf-8"))
        assert found and set(found) == {amt.VERSION}, f


def test_no_setting_label_names_an_es_time():
    """A label with a clock time would be wrong on another market, where the times move (Show RTH Open said 8:30)."""
    assert amt_hub.SCHEMA_BY_NAME["cc_showOpen"]["label"] == "Show RTH Open"
    assert not [x["label"] for x in amt_hub.SCHEMA if re.search(r"\d:\d\d|\b\d{4}\b", x["label"])]


@needs_indicators
def test_the_schema_is_the_csharp_properties():
    """Every setting's NinjaTrader name, group, order, description, choices and checks as tools/gen_amt_schema.py reads
    them from the source (Paste Levels Here keeps this app's own description: no Discord here)."""
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("gen_amt_schema", Path(__file__).resolve().parents[1] / "tools" / "gen_amt_schema.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    want = json.loads((Path(amt_hub.__file__).parent / "amt_schema.json").read_text(encoding="utf-8"))
    got = gen.parse(_source())
    strip = lambda xs: [{k: v for k, v in x.items() if not (x["name"] == "lvl_text" and k == "description")} for x in xs]
    assert strip(got) == strip(want)


@needs_indicators
def test_the_ninjatrader_docs_name_this_version():
    """The C# version is this port's, and the README and NinjaTrader Guide quote it in the dashboard header (the Guide
    said v1.0.0)."""
    src = _source()
    assert f'public const string Version = "{amt.VERSION}";' in src
    for f in ("README.md", "docs/NinjaTrader-Guide.md"):
        found = re.findall(r"Tatanka AMT v(\d+\.\d+\.\d+)", (Path(INDICATORS) / f).read_text(encoding="utf-8"))
        assert found and set(found) == {amt.VERSION}, f


@needs_indicators
def test_the_guides_hours_table_is_the_csharps():
    """The NinjaTrader Guide's table of each market's hours: its roots' hours are the C# table's, the IB the first hour,
    and its minute bars those (of 1, 5, 10, 15, 30) the open and close fall on (NinjaTrader runs on the chart's bars:
    on 15-minute GC bars the IB starts at 07:30)."""
    guide = (Path(INDICATORS) / "docs" / "NinjaTrader-Guide.md").read_text(encoding="utf-8")
    body = guide[guide.index("### Each market's own hours"):]
    rows = re.findall(r"^\| ([^|]+) \| (\d\d:\d\d)-(\d\d:\d\d) \| (\d\d:\d\d)-(\d\d:\d\d) \| (\d\d:\d\d) \| ([^|]+) \|$",
                      body[:body.index("\n\n", body.index("| Market"))], re.M)
    table, mins = _table(_source()), lambda t: int(t[:2]) * 60 + int(t[3:])
    assert len(rows) == 8 and {r for x in rows for r in re.findall(r"\b[A-Z][A-Z0-9]+\b", x[0]) if r in table} >= {"ES", "CL", "GC", "SI", "HG", "PL", "PA"}
    for market, o, c, ib_a, ib_b, close, bars in rows:
        for r in re.findall(r"\b[A-Z][A-Z0-9]+\b", market):
            if r in table:
                assert table[r] == (o, c), r
        assert (ib_a, mins(ib_b) - mins(ib_a), close) == (o, 60, c), market
        fits = [n for n in (1, 5, 10, 15, 30) if mins(o) % n == 0 and mins(c) % n == 0]
        assert [int(n) for n in re.findall(r"\d+", bars)] == fits, market
