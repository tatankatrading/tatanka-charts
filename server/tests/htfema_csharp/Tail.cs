    // ---- harness ----
    // The record, a line per event in NinjaTrader's order:
    //   S|HtfPeriodType|HtfPeriod|EmaLength|Mode|chart BarsPeriodType|chart value   the settings (State.DataLoaded)
    //   C|chart bars|source bars    the bars loaded for the historical pass (BarsArray[n].Count; the last can be forming)
    //   T|0 or 1|0 or 1             Tick Replay on the chart series, on the source series
    //   K|OnBarClose                Calculate (OnPriceChange by default)
    //   P|close|close ms            a historical chart bar (BarsInProgress 0), run once with its last price; writes V|value
    //   H|close|close ms            a historical source bar (BarsInProgress 1)
    //   R                           State.Realtime from here
    //   t|series|first|price|close ms   a tick of a series (Calculate.OnPriceChange: the first of a bar, or a new
    //                               price; OnBarClose: a bar at its close); first: it opens the bar closing at close
    //                               ms. The chart's write v|bar|value.
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    static DateTime At(string[] p, int i)
    {
        return p.Length > i ? new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddMilliseconds(long.Parse(p[i], Inv)) : DateTime.MinValue;
    }
    void Bar(int series, bool first, double price, DateTime close)
    {
        EMA ema = series == 0 ? smoothEmaInd : htfEmaInd;
        if (first)
        {
            CurrentBars[series]++;
            BarsArray[series].Current = CurrentBars[series];
            Times[series].List.Add(close);
            ema.Add(price);
            if (series == 0)
                Values[0] = new Plot();
        }
        else
            ema.Update(price);
        if (series == 0)
            Close.Value = price;
        BarsInProgress = series;
        OnBarUpdate();
    }
    string Plotted() { return Values[0].Set ? Values[0].Value.ToString("G17", Inv) : "-"; }
    public static void Main(string[] args)
    {
        var x = new TatankaHtfEma();
        var o = new StringBuilder();
        foreach (string line in File.ReadAllLines(args[0]))
        {
            string[] p = line.Split('|');
            switch (p[0])
            {
                case "S":
                    x.HtfPeriodType = (BarsPeriodType)Enum.Parse(typeof(BarsPeriodType), p[1]);
                    x.HtfPeriod = int.Parse(p[2]); x.EmaLength = int.Parse(p[3]);
                    x.Mode = (TatankaHtfEmaMode)Enum.Parse(typeof(TatankaHtfEmaMode), p[4]);
                    x.BarsPeriod.BarsPeriodType = (BarsPeriodType)Enum.Parse(typeof(BarsPeriodType), p[5]);
                    x.BarsPeriod.Value = int.Parse(p[6]);
                    x.State = State.DataLoaded;
                    x.DataLoaded();
                    x.State = State.Historical;
                    o.Append("S|").Append(x.scaledLen).Append('\n');
                    break;
                case "C":
                    x.BarsArray[0].Loaded = int.Parse(p[1]); x.BarsArray[1].Loaded = int.Parse(p[2]);
                    break;
                case "T":
                    x.IsTickReplays[0] = p[1] == "1"; x.IsTickReplays[1] = p[2] == "1";
                    break;
                case "K":
                    x.Calculate = (Calculate)Enum.Parse(typeof(Calculate), p[1]);
                    break;
                case "R":
                    x.State = State.Realtime;
                    break;
                case "P":
                    x.Bar(0, true, double.Parse(p[1], Inv), At(p, 2));
                    o.Append("V|").Append(x.Plotted()).Append('\n');
                    break;
                case "H":
                    x.Bar(1, true, double.Parse(p[1], Inv), At(p, 2));
                    break;
                case "t":
                    int series = int.Parse(p[1]);
                    x.Bar(series, p[2] == "1", double.Parse(p[3], Inv), At(p, 4));
                    if (series == 0)
                        o.Append("v|").Append(x.CurrentBars[0]).Append('|').Append(x.Plotted()).Append('\n');
                    break;
            }
        }
        File.WriteAllText(args[1], o.ToString());
    }
}
