// Parity harness for chartsrv/vp.py: drives the unchanged calculation cores of TatankaAutoAnchoredVolumeProfile 1.1.0,
// TatankaPeriodicVolumeProfile 1.0.1 and TatankaSessionVolumeProfile 1.1.0 (or 1.0.0; pasted in after this file by
// build.sh) the way each indicator's OnBarUpdate does, and prints every number of their snapshots as raw double bits,
// and PVP's touches (Touches.Observe on the chart bars given) as epoch ms.
//
// Input lines (stdin), times as epoch ms in one time zone (chart and session time the same, unless a V line names zones):
//   A|anchor|tick|numberOfRows|rowSize|percent|atLeast           AAVP: then M lines, then E
//   P|period|multiple|keep|tick|numberOfRows|rowSize|percent|atLeast   PVP: then M lines, then E
//   M|tradingDay yyyy-MM-dd|sessionBegin|index|low|high|volume|up|time
//   C|low|high|time   PVP: a chart bar (BarsInProgress 0, its close stamp): ObserveTouches, as OnBarUpdate calls it there
//     and after each M line from then on (with the latest chart bar); each profile's touches are then printed (TOUCH lines)
//   V|start HH:mm|end HH:mm|tick|ticksPerRow|keep|percent|atLeast[|root|useInstrumentHours[|sessionZone|chartZone]]
//     SVP: then T lines, then E
//     (HOURS, SVP 1.1.0: with useInstrumentHours 1 the window moves to the root's own hours, as DataLoaded moves a window
//     in Central time, and an H line prints it; without them, or 0: as typed, printing what 1.0.0 does)
//     (sessionZone, chartZone: IANA names, or UTC as NinjaTrader's UTC; the chart's defaults to the session's. The T
//     times are then in chart time, put in session time as OnBarUpdate does, and each session's start and end back in
//     chart time. America/Chicago is NinjaTrader's Central time: the window moves as above. ZONES, SVP 1.1.0 with
//     ZoneWindows: in another zone each date's window moves at that date's offset from Central time, and a W line
//     prints the window after each session that runs another; without ZONES that window runs as typed)
//   T|index|price|volume|time
//   B|time   SVP: a chart bar (BarsInProgress 0, its close stamp): PublishLevels, as OnBarUpdate calls it on a new bar,
//     prints the POC, VAH and VAL its plots take there (PLOT lines; "-" before any session)
//   S   print the snapshot(s) now         E   print them and end the run
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace VpHarness
{
    public static class Program
    {
        static readonly DateTime Epoch = new DateTime(1970, 1, 1);
        static DateTime T(string ms) { return Epoch.AddMilliseconds(long.Parse(ms, CultureInfo.InvariantCulture)); }
        static long Ms(DateTime t) { return (long)Math.Round((t - Epoch).TotalMilliseconds); }
        static double D(string s) { return double.Parse(s, CultureInfo.InvariantCulture); }
        static string B(double x) { return BitConverter.DoubleToInt64Bits(x).ToString(CultureInfo.InvariantCulture); }
        static TimeZoneInfo Zone(string id) { return id == "UTC" ? TimeZoneInfo.Utc : TimeZoneInfo.FindSystemTimeZoneById(id); }
        static StringBuilder Out = new StringBuilder();

        static void Minute(string tag, TatankaAavpCore.Profile p)
        {
            TatankaAavpCore.Snapshot s = p.GetSnapshot(true);
            MinuteSnap(tag, Ms(p.StartChart), Ms(p.LastChart), p.FirstLoadedProfile, p.Window.Start, s.Rows, s.TotalVolume,
                s.ValueAreaVolume, s.MaxVolume, s.MaxAbsDelta, s.Poc, s.Vah, s.Val, s.LowEdge, s.HighEdge, s.PocIndex, s.VahIndex,
                s.ValIndex, s.TicksPerRow, s.WasCapped);
        }

        static void Minute(string tag, TatankaPvpCore.Profile p)
        {
            TatankaPvpCore.Snapshot s = p.GetSnapshot(true);
            TatankaAavpCore.Row[] rows = new TatankaAavpCore.Row[s.Rows.Length];
            for (int i = 0; i < rows.Length; i++)
                rows[i] = new TatankaAavpCore.Row { LowTick = s.Rows[i].LowTick, HighTick = s.Rows[i].HighTick, Up = s.Rows[i].Up, Down = s.Rows[i].Down };
            MinuteSnap(tag, Ms(p.StartChart), Ms(p.LastChart), p.FirstLoadedProfile, p.Window.Start, rows, s.TotalVolume,
                s.ValueAreaVolume, s.MaxVolume, s.MaxAbsDelta, s.Poc, s.Vah, s.Val, s.LowEdge, s.HighEdge, s.PocIndex, s.VahIndex,
                s.ValIndex, s.TicksPerRow, s.WasCapped);
        }

        static void MinuteSnap(string tag, long start, long last, bool first, DateTime window, TatankaAavpCore.Row[] rows,
            double total, double va, double max, double maxDelta, double poc, double vah, double val, double lowEdge,
            double highEdge, int pocI, int vahI, int valI, long tpr, bool capped)
        {
            Out.Append(tag).Append("|").Append(window.ToString("yyyy-MM-dd")).Append("|").Append(start).Append("|").Append(last)
               .Append("|").Append(first ? 1 : 0).Append("|").Append(rows.Length).Append("\n");
            if (rows.Length == 0) return;
            Out.Append(string.Join("|", new[] { B(total), B(va), B(max), B(maxDelta), B(poc), B(vah), B(val), B(lowEdge), B(highEdge) }))
               .Append("|").Append(pocI).Append("|").Append(vahI).Append("|").Append(valI).Append("|").Append(tpr).Append("|").Append(capped ? 1 : 0).Append("\n");
            foreach (TatankaAavpCore.Row r in rows)
                Out.Append(r.LowTick).Append(",").Append(r.HighTick).Append(",").Append(B(r.Up)).Append(",").Append(B(r.Down)).Append("\n");
        }

        // TatankaPeriodicVolumeProfile.ObserveTouches with ExtendPoc, ExtendVah and ExtendVal on (the defaults)
        static void Observe(List<TatankaPvpCore.Profile> profiles, double low, double high, DateTime time)
        {
            foreach (TatankaPvpCore.Profile p in profiles)
            {
                if (!p.Closed || time <= p.LastChart) continue;
                p.Touch.Observe(low, high, time, p.GetSnapshot(false), true, true, true);
            }
        }

        static string Touch(DateTime? t) { return t.HasValue ? Ms(t.Value).ToString(CultureInfo.InvariantCulture) : "-"; }

        static void Session(string tag, TatankaVpCore.Session s, double percent, bool atLeast)
        {
            TatankaVpCore.Snapshot p = s.GetSnapshot(percent, atLeast, true);
            Out.Append(tag).Append("|").Append(Ms(p.StartChart)).Append("|").Append(Ms(p.EndChart)).Append("|").Append(Ms(p.LastChart))
               .Append("|").Append(p.Rows.Length).Append("\n");
            if (p.Rows.Length == 0) return;
            Out.Append(string.Join("|", new[] { B(p.RowSize), B(p.MaxVolume), B(p.TotalVolume), B(p.ValueAreaVolume), B(p.Poc), B(p.Vah),
                B(p.Val), B(p.LowEdge), B(p.HighEdge) })).Append("|").Append(p.PocKey).Append("|").Append(p.VahKey).Append("|").Append(p.ValKey).Append("\n");
            foreach (TatankaVpCore.Row r in p.Rows)
                Out.Append(r.Key).Append(",").Append(B(r.Up)).Append(",").Append(B(r.Down)).Append("\n");
        }

        public static void Main()
        {
            string line, mode = null;
            TatankaAavpCore.AnchorSeries aavp = null;
            List<TatankaPvpCore.Profile> profiles = new List<TatankaPvpCore.Profile>();
            TatankaPvpCore.Profile current = null;
            NinjaTrader.NinjaScript.TatankaPvpPeriod period = 0;
            int multiple = 1, keep = 20, rowSize = 0, tpr = 1;
            double tick = 0, percent = 0;
            bool numberOfRows = true, atLeast = false;
            List<TatankaVpCore.Session> sessions = new List<TatankaVpCore.Session>();
            TatankaVpCore.Session session = null;
            TatankaVpCore.TickCounter counter = new TatankaVpCore.TickCounter();
            TimeSpan start = TimeSpan.Zero, end = TimeSpan.Zero;
            TimeZoneInfo sessionZone = null, chartZone = null;
            bool touches = false;                       // C lines seen: the chart bar ObserveTouches reads
            double chartLow = 0, chartHigh = 0;
            DateTime chartTime = DateTime.MinValue;
#if ZONES
            TatankaVpCore.ZoneWindows zoneWindows = null;
            string label = null;
#endif
            while ((line = Console.ReadLine()) != null)
            {
                string[] f = line.Split('|');
                switch (f[0])
                {
                    case "A":
                        mode = "A";
                        aavp = new TatankaAavpCore.AnchorSeries((NinjaTrader.NinjaScript.TatankaAavpAnchor)Enum.Parse(typeof(NinjaTrader.NinjaScript.TatankaAavpAnchor), f[1]),
                            D(f[2]), f[3] == "1" ? NinjaTrader.NinjaScript.TatankaAavpRows.NumberOfRows : NinjaTrader.NinjaScript.TatankaAavpRows.TicksPerRow,
                            int.Parse(f[4]), D(f[5]), f[6] == "1");
                        break;
                    case "P":
                        mode = "P";
                        period = (NinjaTrader.NinjaScript.TatankaPvpPeriod)Enum.Parse(typeof(NinjaTrader.NinjaScript.TatankaPvpPeriod), f[1]);
                        multiple = int.Parse(f[2]); keep = int.Parse(f[3]); tick = D(f[4]); numberOfRows = f[5] == "1";
                        rowSize = int.Parse(f[6]); percent = D(f[7]); atLeast = f[8] == "1";
                        profiles.Clear(); current = null; touches = false;
                        break;
                    case "V":
                        mode = "V";
                        start = TatankaVpCore.SessionClock.ParseTime(f[1]); end = TatankaVpCore.SessionClock.ParseTime(f[2]);
                        tick = D(f[3]); tpr = int.Parse(f[4]); keep = int.Parse(f[5]); percent = D(f[6]); atLeast = f[7] == "1";
                        sessionZone = f.Length > 10 ? Zone(f[10]) : null;
                        chartZone = f.Length > 11 ? Zone(f[11]) : sessionZone;
#if HOURS
#if ZONES
                        zoneWindows = null; label = null;
                        if (f.Length > 10 && f[10] != "America/Chicago" && f[9] == "1")
                            zoneWindows = TatankaVpCore.ZoneWindows.For(start, end, f[8], sessionZone, Zone("America/Chicago"));
                        else
#endif
                        if (f.Length > 9 && f[9] == "1" && (f.Length < 11 || f[10] == "America/Chicago"))
                        {
                            bool moved = TatankaVpCore.InstrumentHours.MoveTimes(ref start, ref end, f[8]);
                            Out.Append("H|").Append(moved ? TatankaVpCore.InstrumentHours.Clock(start) + "-" + TatankaVpCore.InstrumentHours.Clock(end)
                                : f[1] + "-" + f[2]).Append("\n");
                        }
#endif
                        sessions.Clear(); session = null; counter.Reset();
                        break;
                    case "M":
                    {
                        DateTime td = DateTime.ParseExact(f[1], "yyyy-MM-dd", CultureInfo.InvariantCulture);
                        DateTime begin = T(f[2]); int index = int.Parse(f[3]);
                        double low = D(f[4]), high = D(f[5]), vol = D(f[6]); bool up = f[7] == "1"; DateTime time = T(f[8]);
                        if (mode == "A") aavp.Upsert(td, begin, index, low, high, vol, up, time);
                        else
                        {   // TatankaPeriodicVolumeProfile.OnBarUpdate, BarsInProgress 1
                            TatankaPvpCore.Window window = TatankaPvpCore.PeriodClock.Get(td, period, multiple);
                            if (current == null || current.Window.Start != window.Start)
                            {
                                if (current != null && window.Start < current.Window.Start) break;
                                bool first = current == null;
                                if (current != null) current.Complete();
                                current = new TatankaPvpCore.Profile(window, begin, first, tick,
                                    numberOfRows ? NinjaTrader.NinjaScript.TatankaPvpRows.NumberOfRows : NinjaTrader.NinjaScript.TatankaPvpRows.TicksPerRow,
                                    rowSize, percent, atLeast);
                                profiles.Add(current);
                                while (profiles.Count > keep) profiles.RemoveAt(0);
                            }
                            current.Upsert(index, low, high, vol, up, time);
                            if (touches) Observe(profiles, chartLow, chartHigh, chartTime);
                        }
                        break;
                    }
                    case "C":
                        touches = true; chartLow = D(f[1]); chartHigh = D(f[2]); chartTime = T(f[3]);
                        Observe(profiles, chartLow, chartHigh, chartTime);
                        break;
                    case "T":
                    {   // TatankaSessionVolumeProfile.OnBarUpdate, BarsInProgress 1 (chart time = session time without zones)
                        double volume = counter.Consume(int.Parse(f[1]), D(f[3]));
                        if (volume <= 0) break;
                        DateTime time = T(f[4]), ws, we;
                        DateTime local = sessionZone == null ? time : TimeZoneInfo.ConvertTime(time, chartZone, sessionZone);
                        bool inWindow;
#if ZONES
                        if (zoneWindows != null) inWindow = zoneWindows.TryWindow(local, out ws, out we);
                        else
#endif
                        inWindow = TatankaVpCore.SessionClock.TryWindow(local, start, end, out ws, out we);
                        if (inWindow)
                        {
                            if (session == null || session.StartLocal != ws)
                            {
                                if (session != null && ws < session.StartLocal) break;
                                if (session != null) session.GetSnapshot(percent, atLeast, true);
                                session = new TatankaVpCore.Session(ws, sessionZone == null ? ws : TimeZoneInfo.ConvertTime(ws, sessionZone, chartZone),
                                    sessionZone == null ? we : TimeZoneInfo.ConvertTime(we, sessionZone, chartZone), tick, tpr);
                                sessions.Add(session);
                                while (sessions.Count > keep) sessions.RemoveAt(0);
#if ZONES
                                if (zoneWindows != null && TatankaVpCore.ZoneWindows.Label(ws, we) != label)
                                {
                                    label = TatankaVpCore.ZoneWindows.Label(ws, we);
                                    Out.Append("W|").Append(label).Append("\n");
                                }
#endif
                            }
                            session.Add(D(f[2]), volume, time);
                        }
                        break;
                    }
                    case "B":
                    {   // TatankaSessionVolumeProfile.PublishLevels(true): the current session's snapshot, else none yet
                        Out.Append("PLOT|").Append(f[1]);
                        if (session == null) Out.Append("|-\n");
                        else
                        {
                            TatankaVpCore.Snapshot p = session.GetSnapshot(percent, atLeast, true);
                            Out.Append("|").Append(B(p.Poc)).Append("|").Append(B(p.Vah)).Append("|").Append(B(p.Val)).Append("\n");
                        }
                        break;
                    }
                    case "S":
                    case "E":
                        if (mode == "A") { if (aavp.Current != null) Minute("AAVP", aavp.Current); else Out.Append("AAVP|none\n"); }
                        else if (mode == "P")
                        {
                            Out.Append("PVP|").Append(profiles.Count).Append("\n");
                            foreach (TatankaPvpCore.Profile p in profiles)
                            {
                                Minute("PROFILE", p);
                                if (touches) Out.Append("TOUCH|").Append(Touch(p.Touch.Poc)).Append("|").Append(Touch(p.Touch.Vah)).Append("|")
                                    .Append(Touch(p.Touch.Val)).Append("\n");
                            }
                        }
                        else if (mode == "V") { Out.Append("SVP|").Append(sessions.Count).Append("\n"); foreach (TatankaVpCore.Session s in sessions) Session("SESSION", s, percent, atLeast); }
                        if (f[0] == "E") Out.Append("END\n");
                        break;
                }
            }
            Console.Out.Write(Out.ToString());
        }
    }
}

