    // ---- ReadPrice and TatankaBiasStyle.Color, pasted from TatankaIfThenBiasTable.cs by build.sh into these, with
    // stand-ins for NinjaTrader's price series (Open[0]..., Inputs[0][0]) and WPF's solid brush ----
    internal sealed class Series
    {
        private readonly double v;
        public Series(double x) { v = x; }
        public double this[int barsAgo] { get { return v; } }
    }
    public struct WpfColor { public byte A, R, G, B; }
    public class WpfBrush { }
    public sealed class WpfSolidBrush : WpfBrush
    {
        public WpfColor Color;
        public double Opacity = 1;
        public WpfSolidBrush(WpfColor c) { Color = c; }
    }
    internal sealed class PriceReader
    {
        public string PriceSource;
        public Series Open, High, Low, Close;
        public Series[] Inputs;
        public double Read() { return ReadPrice(); }
