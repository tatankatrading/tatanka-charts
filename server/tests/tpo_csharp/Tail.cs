    // ---- harness ----
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    static string D(double x) { return double.IsNaN(x) ? "NaN" : x.ToString("G17", Inv); }
    static string T(DateTime t) { return t == DateTime.MinValue ? "MIN" : t.ToString("yyyy-MM-ddTHH:mm:ss", Inv); }
    static DateTime PT(string s) { return DateTime.ParseExact(s, "yyyy-MM-ddTHH:mm:ss", Inv); }
    static double PD(string s) { return double.Parse(s, Inv); }

    void DumpProfile(StringBuilder o, string tag, SavedProfile p)
    {
        double vah, val;
        GetValueArea(p, out vah, out val);
        o.Append(tag).Append('|').Append(T(p.TimeStart)).Append('|').Append(T(p.TimeEnd)).Append('|').Append(D(p.TickStep))
         .Append('|').Append(D(p.PocPrice)).Append('|').Append(p.MaxCount).Append('|').Append(D(vah)).Append('|').Append(D(val))
         .Append('|').Append(p.Rows.Count).Append('\n');
        foreach (TpoRow r in p.Rows)
        {
            o.Append("R|").Append(D(r.Price)).Append('|');
            for (int j = 0; j < r.Letters.Count; j++) o.Append(j > 0 ? "," : "").Append(r.Letters[j]);
            o.Append('|');
            for (int j = 0; j < r.Letters.Count; j++) o.Append(GetLetter(r.Letters[j]));
            o.Append('\n');
        }
    }

    public static void Main(string[] args)
    {
        var x = new TatankaTpoProfile();
        x.dailyAtr = new ATR();
        x.sessionIterator = new SessionIterator();
        var atr = new NtAtr(10);
        var o = new StringBuilder();
#if HOURS
        TimeZoneInfo zone = null;
        string window = null;
#endif
        foreach (string line in File.ReadAllLines(args[0]))
        {
            string[] p = line.Split('|');
            switch (p[0])
            {
                case "S":
                    x.TickSize = PD(p[1]); x.FilterByTime = p[2] == "1"; x.SessionTime = p[3]; x.BlockMinutes = int.Parse(p[4]);
                    x.AutoRowHeight = p[5] == "1"; x.TargetRows = int.Parse(p[6]); x.RowHeightTicks = int.Parse(p[7]);
#if HOURS
                    // v1.1.0, Use Instrument Hours: the root, the setting and the chart's time zone (an IANA name; none:
                    // NinjaTrader on Central time), as DataLoaded runs them; a record without them: off. On in Central
                    // time, the Session Time it runs is printed too (off prints what v1.0.0 does); in another zone, each
                    // Session Time it runs, as it changes (W, after the bar it changed on).
                    x.UseInstrumentHours = p.Length > 9 && p[9] == "1";
                    zone = p.Length > 10 && p[10].Length > 0 ? TimeZoneInfo.FindSystemTimeZoneById(p[10]) : null;
                    x.SetSessionTime(p.Length > 8 ? p[8] : null, zone, TimeZoneInfo.FindSystemTimeZoneById("America/Chicago"));
                    if (x.UseInstrumentHours && zone == null) o.Append("H|").Append(x.sessionTime).Append('\n');
                    window = x.sessionTime;
#endif
                    break;
#if HOURS
                case "Q":   // RunSessionTime(typed, on, root, offset in minutes)
                    o.Append("Q|").Append(RunSessionTime(p[1], p[2] == "1", p[3], int.Parse(p[4]))).Append('\n');
                    break;
#endif
                case "D":
                    x.dailyAtr.Value = atr.Add(PD(p[1]), PD(p[2]), PD(p[3]));
                    x.CurrentBars[1]++;
                    break;
                case "B":
                    x.CurrentBars[0]++;
                    x.Time.Value = PT(p[1]); x.High.Value = PD(p[2]); x.Low.Value = PD(p[3]);
                    x.Bars.IsFirstBarOfSession = p[4] == "1"; x.sessionIterator.Next = PT(p[5]);
                    int before = x.saved.Count;
                    x.OnBarUpdate();
                    o.Append("T|").Append(D(x.tickStep)).Append('|').Append(x.hasLive ? 1 : 0).Append('|').Append(x.liveInSession ? 1 : 0)
                     .Append('|').Append(x.liveRows.Count).Append('|').Append(T(x.sessionStartTime)).Append('|').Append(T(x.drawStartTime))
                     .Append('|').Append(T(x.lastSessBarTime)).Append('\n');
                    for (int i = before; i < x.saved.Count; i++) x.DumpProfile(o, "P", x.saved[i]);
#if HOURS
                    if (zone != null && x.sessionTime != window) { window = x.sessionTime; o.Append("W|").Append(window).Append('\n'); }
#endif
                    break;
                case "E":
                    if (x.liveRows.Count > 0) x.DumpProfile(o, "LIVE", x.BuildProfileFromMap(x.liveRows, x.drawStartTime, x.Time[0]));
                    break;
            }
        }
        File.WriteAllText(args[1], o.ToString());
    }
}
