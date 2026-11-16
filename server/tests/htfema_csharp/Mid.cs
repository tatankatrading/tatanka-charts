
public class TatankaHtfEma : Indicator
{
    // The NinjaTrader members the pasted code reads; Main sets them for each bar and tick.
    int[] CurrentBars = new int[] { -1, -1 };
    int BarsInProgress;
    State State = State.Historical;
    Calculate Calculate = Calculate.OnPriceChange;
    bool[] IsTickReplays = new bool[] { false, false };
    BarsStub[] BarsArray = new BarsStub[] { new BarsStub(), new BarsStub() };
    TimeSeriesStub[] Times = new TimeSeriesStub[] { new TimeSeriesStub(), new TimeSeriesStub() };
    Series<double> Close = new Series<double>();
    Plot[] Values = new Plot[] { new Plot() };
    object[] Closes = new object[] { null, null };
    BarsPeriodStub BarsPeriod = new BarsPeriodStub();
    EMA EMA(object input, int period) { return new EMA(period); }   // the EMA indicator on a series; Bar() feeds it
    public BarsPeriodType HtfPeriodType { get; set; }
    public int HtfPeriod { get; set; }
    public int EmaLength { get; set; }
    public TatankaHtfEmaMode Mode { get; set; }

    // ---- pasted from TatankaHtfEma.cs: fields, OnBarUpdate and the helpers after it ----
