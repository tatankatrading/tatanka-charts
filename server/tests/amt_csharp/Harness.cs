// Replays a recorded input file through the original C# engine and prints every frame canonically.
//   mono harness.exe <record> <output> [settings file: one name=value a line, as typed in NinjaTrader]
//   mono harness.exe --next-ask <cases> <output>   (LevelLink.NextAsk on each "now|stamp|changed|failures|status" line)
// Late ticks (the indicator's PatchPrevDay, copied too): HOLD|date keeps that session's ticks back (the load's wait for
// them over), ARRIVE|date hands them over before the next bar as TickDayLoaded does live, PATCH|<fill samples cleared>
// is printed; FILLS prints the 80% Rule fill samples the chart draws (Commit's samples) as FILL lines.
// The settings run as the record's instrument (its E line's root) runs them, as NinjaTrader does: InstrumentHours.Apply
// moves the session fields to its own hours with Use Instrument Hours (inst_hours) on (v1.2.4). Value From Ticks
// (Sessions) (PdTickSessions) and Profile From Ticks (ProfileTicks) are the indicator's: from the record's N line (the
// load's time) the indicator's own RequestTickDays, RequestClosedSession and TickValue (Ticks.cs) pick the sessions
// whose trades (T lines) the engine gets; a record without an N line gives it every day's, as before.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using TatankaTrading.AmtToolkit;
#pragma warning disable 649   // PatchPrevDay's idle members

namespace NinjaTrader.Core { public static class Globals { public static DateTime Now; } }
namespace TatankaTrading.AmtToolkit
{
    public enum State { Historical, Realtime }
    // The members of the indicator that its tick code (Ticks.cs) reads; RequestTickDay hands it the recorded trades
    // instead of a download (each session's at its first minute, done at once).
    public sealed partial class TickSource
    {
        public Settings cfg; public int PdTickSessions = 5; public bool ProfileTicks = true; public State State = State.Historical;
        public Dictionary<string, List<KeyValuePair<double, double>>> Trades = new Dictionary<string, List<KeyValuePair<double, double>>>();
        private List<TickDay> tickDays = new List<TickDay>();
        private DateTime tickWaitUntil;
        public HashSet<string> Held = new HashSet<string>();   // sessions (their dates) whose ticks come late
        private static DateTime CT(DateTime t) { return t; }
        private void RequestTickDay(DateTime start, DateTime end)
        {
            if (tickDays.Any(x => x.Start == start)) return;
            var day = new TickDay { Start = start, End = end, Minutes = new Dictionary<DateTime, Dictionary<double, double>>(), Ok = true };
            List<KeyValuePair<double, double>> rows;
            if (Trades.TryGetValue(start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), out rows))
                day.Minutes[start] = rows.ToDictionary(x => x.Key, x => x.Value);
            if (!Held.Contains(start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture))) day.Done.Set();
            tickDays.Add(day);
        }
        // the indicator's lines: DataLoaded (if (cfg.cc_enable && PdTickSessions > 0) RequestTickDays();), each live bar's
        // first tick (... RequestClosedSession(current.Start);), and engine.TradeValue = TickValue
        public void Load() { if (cfg.cc_enable && PdTickSessions > 0) RequestTickDays(); if (Held.Count > 0) tickWaitUntil = DateTime.MinValue; }
        // PatchPrevDay's members: the engine, Commit's samples (what the chart draws as the 80% Rule fill), the rest idle
        public Engine engine; private bool tickArrived, terminated; private readonly object sync = new object(); private string fault = "";
        private RenderHistory<Sample> samples = new RenderHistory<Sample>(50000); private object pending, ChartControl;
        private void Preview(bool historical) { } private void ForceRefresh() { } private void Fail(Exception ex) { throw ex; }
        public void Committed(Frame f) { samples.Add(ToSample(f)); }
        // TickDayLoaded live: the session's ticks are in, then PatchPrevDay; returns the fill samples it cleared
        public int Arrive(string date)
        {
            Held.Remove(date);
            foreach (TickDay d in tickDays.Where(x => x.Start.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) == date)) d.Done.Set();
            int before = samples.Snapshot().Count(x => x.Fill);
            tickArrived = true; PatchPrevDay();
            return before - samples.Snapshot().Count(x => x.Fill);
        }
        public IEnumerable<string> Fills()
        {
            return samples.Snapshot().Where(x => x.Fill).Select(x => "FILL|" + x.Bar.Start.ToString("yyyy-MM-ddTHH:mm:ss", CultureInfo.InvariantCulture)
                + "|" + (x.Confirmed ? 1 : 0) + "|" + x.VaHigh.ToString("R", CultureInfo.InvariantCulture) + "|" + x.VaLow.ToString("R", CultureInfo.InvariantCulture));
        }
        public void LiveBar(DateTime start) { if (cfg.cc_enable && PdTickSessions > 0) RequestClosedSession(start); }
        public List<KeyValuePair<double, double>> Value(DateTime first, DateTime end, double minuteVolume) { return TickValue(first, end, minuteVolume); }
    }
}

public static class Harness
{
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    static string D(double x) { return double.IsNaN(x) ? "NaN" : x.ToString("G17", Inv); }
    static string T(DateTime t) { return t == DateTime.MinValue ? "MIN" : t.ToString("yyyy-MM-ddTHH:mm:ss", Inv); }
    static DateTime PT(string s) { return DateTime.ParseExact(s, "yyyy-MM-ddTHH:mm:ss", Inv); }
    static double PD(string s) { return double.Parse(s, Inv); }
    static Bar PB(string[] p, int o) { return new Bar { Start = PT(p[o]), End = PT(p[o + 1]), Open = PD(p[o + 2]), High = PD(p[o + 3]), Low = PD(p[o + 4]), Close = PD(p[o + 5]), Volume = PD(p[o + 6]) }; }
    public static void Main(string[] args)
    {
        if (args[0] == "--next-ask") { NextAsk(args[1], args[2]); return; }
        var lines = File.ReadAllLines(args[0]);
        var outp = new StringBuilder();
        Settings s = new Settings();
        var ticks = new TickSource();
        if (args.Length > 2) foreach (var kv in File.ReadAllLines(args[2]))
        {
            var p = kv.Split('=');
            if (p[0] == "PdTickSessions") { ticks.PdTickSessions = int.Parse(p[1], Inv); continue; }
            if (p[0] == "ProfileTicks") { ticks.ProfileTicks = bool.Parse(p[1]); continue; }
            var fld = typeof(Settings).GetField(p[0]); fld.SetValue(s, Convert.ChangeType(p[1], fld.FieldType, Inv));
        }
        Engine e = null; DailyHistory daily = new DailyHistory(s.daily_atr_length);
        var tracker = new LevelAlertTracker(); Frame last = null;
        var trades = ticks.Trades;
        var minuteTrades = new Dictionary<DateTime, List<KeyValuePair<double, double>>>();
        DateTime? loaded = null;   // the N line: the indicator's tick code picks the sessions from here
        for (int i = 0; i < lines.Length; i++)
        {
            string[] p = lines[i].Split('|');
            switch (p[0])
            {
                case "E":
                    ticks.cfg = InstrumentHours.Apply(s, p[1]);
                    e = new Engine(ticks.cfg, p[1], PD(p[2]), PD(p[3]), int.Parse(p[4]));
                    e.TradeValue = (first, end, vol) =>
                    {
                        if (loaded != null) return ticks.Value(first, end, vol);
                        List<KeyValuePair<double, double>> r; return trades.TryGetValue(first.ToString("yyyy-MM-dd", Inv), out r) ? r : null;
                    };
                    if (ticks.ProfileTicks) e.MinuteTrades = (start, vol) => { List<KeyValuePair<double, double>> r; return minuteTrades.TryGetValue(start, out r) ? r : null; };
                    break;
                case "N":
                    loaded = PT(p[1]); NinjaTrader.Core.Globals.Now = loaded.Value; ticks.engine = e; ticks.Load(); break;
                case "HOLD": ticks.Held.Add(p[1]); break;
                case "ARRIVE": outp.Append("PATCH|").Append(ticks.Arrive(p[1])).Append('\n'); break;
                case "FILLS": foreach (string x in ticks.Fills()) outp.Append(x).Append('\n'); break;
                case "Q":
                    var mrows = new List<KeyValuePair<double, double>>();
                    if (p[2].Length > 0) foreach (var x in p[2].Split(';')) { var q = x.Split(','); mrows.Add(new KeyValuePair<double, double>(PD(q[0]), PD(q[1]))); }
                    minuteTrades[PT(p[1])] = mrows; break;
                case "T":
                    var rows = new List<KeyValuePair<double, double>>();
                    if (p[2].Length > 0) foreach (var x in p[2].Split(';')) { var q = x.Split(','); rows.Add(new KeyValuePair<double, double>(PD(q[0]), PD(q[1]))); }
                    trades[p[1]] = rows; break;
                case "L":
                    var lv = new List<Level>();
                    if (p.Length > 1 && p[1].Length > 0) foreach (var x in p[1].Split(';')) { var q = x.Split(','); lv.Add(new Level { Name = q[0], Price = PD(q[1]), Kind = int.Parse(q[2]) }); }
                    e.ReplaceLevels(lv); break;
                case "D": daily.Add(PT(p[1]), PD(p[2]), PD(p[3]), PD(p[4])); break;
                case "M": e.AddMacro(PB(p, 1)); break;
                case "O":
                    foreach (Notice n in tracker.Observe(last.Watches, PD(p[1]), p[2], e.Root, e.Tick)) outp.Append("O|").Append(n.Key).Append('|').Append(n.Text).Append('\n');
                    break;
                case "B":
                    Bar b = PB(p, 2); b.Index = int.Parse(p[1]); int ns = int.Parse(p[9]); bool clone = p[10] == "1";
                    var subs = new List<Bar>(); for (int k = 0; k < ns; k++) subs.Add(PB(lines[++i].Split('|'), 1));
                    if (loaded != null && b.Start >= loaded.Value) ticks.LiveBar(b.Start);   // a bar after the load: live
                    Engine target = clone ? e.Clone() : e;
                    Frame f = target.Process(b, subs, daily.Before(MathEx.TradingDate(b.Start)));
                    if (!clone) ticks.Committed(f);
                    last = f; Dump(outp, f);
                    break;
            }
        }
        File.WriteAllText(args[1], outp.ToString());
    }
    // LevelLink.NextAsk (the levels link's schedule) on each case: now|stampCt|changedCt|failures|status, CT, MIN for none
    static void NextAsk(string cases, string output)
    {
        var o = new StringBuilder();
        Func<string, DateTime> at = x => x == "MIN" ? DateTime.MinValue : PT(x);
        foreach (var line in File.ReadAllLines(cases))
        {
            if (line.StartsWith("P|")) { o.Append(T(LinkSchedule.Published(line.Substring(2).Replace("\\n", "\n")))).Append('\n'); continue; }
            var p = line.Split('|');
            o.Append(T(LinkSchedule.NextAsk(at(p[0]), at(p[1]), at(p[2]), int.Parse(p[3]), int.Parse(p[4])))).Append('\n');
        }
        File.WriteAllText(output, o.ToString());
    }
    static void Dump(StringBuilder o, Frame f)
    {
        o.Append("F|").Append(f.Bar.Index).Append('\n');
        foreach (var n in f.Notices) o.Append("N|").Append(n.Key).Append('|').Append(n.Text).Append('\n');
        foreach (var w in f.Watches) o.Append("W|").Append(w.Name).Append('|').Append(D(w.Price)).Append('|').Append(D(w.HalfWidth)).Append('|').Append(w.Enabled ? 1 : 0).Append('|').Append(w.Moving ? 1 : 0).Append('\n');
        foreach (var l in f.Lines) o.Append("LN|").Append(l.Name).Append('|').Append(l.Group).Append('|').Append(D(l.Price)).Append('|').Append(T(l.Start)).Append('|').Append(T(l.End)).Append('|').Append(l.Color).Append('|').Append(l.TextColor).Append('|').Append(l.Width).Append('|').Append(l.Style).Append('|').Append(l.Offset).Append('|').Append(l.Size).Append('|').Append(l.Label ? 1 : 0).Append('|').Append(l.Extend).Append('\n');
        foreach (var z in f.Zones) o.Append("Z|").Append(D(z.Top)).Append('|').Append(D(z.Bottom)).Append('|').Append(T(z.Start)).Append('|').Append(T(z.Created)).Append('|').Append(z.Color).Append('|').Append(z.Live ? 1 : 0).Append('|').Append(z.BorderColor).Append('|').Append(z.BorderWidth).Append('|').Append(z.BorderStyle).Append('\n');
        foreach (var p in f.Profiles)
        {
            var keys = p.Profile.Rows.Keys.OrderBy(k => k).ToArray(); double sum = 0, wsum = 0;
            foreach (var k in keys) { sum += p.Profile.Rows[k]; wsum += k * p.Profile.Rows[k]; }
            o.Append("P|").Append(T(p.Start)).Append('|').Append(T(p.End)).Append('|').Append(p.EndIndex).Append('|').Append(D(p.Profile.Step)).Append('|').Append(D(p.Stats.Poc)).Append('|').Append(D(p.Stats.Vah)).Append('|').Append(D(p.Stats.Val)).Append('|').Append(D(p.Stats.Total)).Append('|').Append(keys.Length).Append('|').Append(keys.Length > 0 ? keys[0] : 0).Append('|').Append(D(sum)).Append('|').Append(D(wsum)).Append('\n');
        }
        if (f.Labels != null) o.Append("DB|").Append(string.Join(";", f.Labels)).Append('|').Append(string.Join(";", f.Values)).Append('|').Append(string.Join(";", f.Colors.Select(c => c.ToString()))).Append('\n');
        o.Append("X|").Append(D(f.RthVwap)).Append('|').Append(D(f.DailyAtr)).Append('|').Append(f.RuleActive ? 1 : 0).Append('|').Append(f.RuleConfirmed ? 1 : 0).Append('|').Append(D(f.VaHigh)).Append('|').Append(D(f.VaLow)).Append('|').Append(f.Warning).Append('\n');
    }
}
