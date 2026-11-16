// Parity harness for the HTF EMA port (studies.js htfEmaValues fed by chartsrv/htfema.py): the indicator code
// of TatankaHtfEma.cs (pasted in by build.sh) with stand-ins for the NinjaTrader members it reads.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

public enum BarsPeriodType { Tick, Volume, Range, Second, Minute, Day, Week, Month, Year }
public enum State { SetDefaults, Configure, DataLoaded, Historical, Transition, Realtime, Terminated }
public enum Calculate { OnBarClose, OnEachTick, OnPriceChange }

// NinjaTrader 8's EMA indicator: Add for a new bar, Update for the forming bar's next price (Calculate.OnPriceChange)
public class EMA
{
    readonly double constant1, constant2;
    readonly List<double> values = new List<double>();
    public EMA(int period) { constant1 = 2.0 / (1 + period); constant2 = 1 - (2.0 / (1 + period)); }
    public void Add(double input)
    {
        values.Add(values.Count == 0 ? input : input * constant1 + constant2 * values[values.Count - 1]);
    }
    public void Update(double input)
    {
        int i = values.Count - 1;
        values[i] = i == 0 ? input : input * constant1 + constant2 * values[i - 1];
    }
    public double this[int barsAgo] { get { return values[values.Count - 1 - barsAgo]; } }
}
public class Series<T> { public T Value; public T this[int barsAgo] { get { return Value; } } }
public class Plot { public double Value = double.NaN; public bool Set; public double this[int barsAgo] { get { return Value; } set { Value = value; Set = true; } } }
public class BarsPeriodStub { public BarsPeriodType BarsPeriodType; public int Value; }
// A series' bar times (NinjaTrader stamps time bars with their close)
public class TimeSeriesStub
{
    public readonly List<DateTime> List = new List<DateTime>();
    public DateTime this[int barsAgo] { get { return List[List.Count - 1 - barsAgo]; } }
}
// BarsArray[n]: Count is the bars loaded (historical; the last one can still be forming), then one more per new bar
public class BarsStub
{
    public int Loaded = int.MaxValue;
    public int Current = -1;
    public int Count { get { return Math.Max(Loaded, Current + 1); } }
}
public abstract class Indicator { protected virtual void OnBarUpdate() { } }

// ---- pasted from TatankaHtfEma.cs: the mode enum ----
