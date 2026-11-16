// Copyright (c) 2026 Tatanka Trading LLC.
// This Source Code Form is subject to the Mozilla Public License, v. 2.0.
// If a copy of the MPL was not distributed with this file, obtain one at https://mozilla.org/MPL/2.0/.
// Tatanka Session Volume Profile v1.1.0 for NinjaTrader 8
// Source-only, chart-only indicator. No orders, external services, or paid Order Flow APIs.
// Guide (data, settings, TradingView differences): https://github.com/TatankaTrading/tatanka-indicators/blob/main/docs/Other-Indicators-Guide.md#session-volume-profile
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.Gui.Tools;
using SharpDX;
using D2D = SharpDX.Direct2D1;
using WpfBrush = System.Windows.Media.Brush;

namespace NinjaTrader.NinjaScript
{
    public enum TatankaVpAlignment { SessionRight, ChartRightLatest }
    public enum TatankaVpTimeZone { ExchangeTemplate, Central, Eastern, Chart, UTC }
    public enum TatankaVpValueAreaMethod { TradingViewStyle, AtLeastTarget }
}

namespace NinjaTrader.NinjaScript.Indicators
{
    public class TatankaSessionVolumeProfile : Indicator
    {
        private readonly object profileLock = new object();
        private readonly List<TatankaVpCore.Session> sessions = new List<TatankaVpCore.Session>();
        private TatankaVpCore.Session currentSession;
        private TimeZoneInfo chartZone;
        private TimeZoneInfo sessionZone;
        private TimeSpan startTime;
        private TimeSpan endTime;
        private string windowLabel;   // the window it runs on, when Use Instrument Hours moved it (another zone: the latest's)
        private TatankaVpCore.ZoneWindows zoneWindows;   // Use Instrument Hours outside Central time: each date's window
        private readonly TatankaVpCore.TickCounter tickCounter = new TatankaVpCore.TickCounter();
        private string configurationError;
        private int lastPublishedBar = -1;
        private DateTime lastPublish = DateTime.MinValue;
        private double latestPoc = double.NaN, latestVah = double.NaN, latestVal = double.NaN;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TatankaSessionVolumeProfile";
                Description = "Right-aligned custom-session volume profiles. Defaults match the supplied SVP HD style: 08:30-15:00, 68% VA, 20% width.";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                IsChartOnly = true;
                DrawOnPricePanel = true;
                DisplayInDataBox = true;
                PaintPriceMarkers = true;
                IsAutoScale = false;
                IsSuspendedWhileInactive = false;
                BarsRequiredToPlot = 0;
                ArePlotsConfigurable = false;

                SessionStart = "08:30";
                SessionEnd = "15:00";
                SessionTimeZone = TatankaVpTimeZone.ExchangeTemplate;
                UseInstrumentHours = true;
                ValueAreaPercent = 68;
                ValueAreaMethod = TatankaVpValueAreaMethod.TradingViewStyle;
                TicksPerRow = 1;
                SessionsToKeep = 20;
                Alignment = TatankaVpAlignment.SessionRight;
                WidthPercent = 20;
                RightPaddingPixels = 12;
                RowGapPixels = 1;
                ShowProfile = true;
                ShowRowVolumes = false;
                ShowPoc = true;
                ShowVah = true;
                ShowVal = true;
                ShowLevelLabels = false;
                ShowInputsInLabel = true;
                ShowValuesInLabel = true;
                ExtendPocToChartEdge = false;
                ExtendVahToChartEdge = false;
                ExtendValToChartEdge = false;
                LineWidth = 1;
                UpVolumeBrush = MakeBrush(0x50, 0x55, 0x61);
                DownVolumeBrush = MakeBrush(0x50, 0x55, 0x61);
                ValueAreaUpBrush = MakeBrush(0xC8, 0x0B, 0x5B);
                ValueAreaDownBrush = MakeBrush(0xEB, 0x14, 0x60);
                ValueAreaLineBrush = MakeBrush(0xED, 0x14, 0x61);
                PocLineBrush = MakeBrush(0xB7, 0x9D, 0xD6);
                TextBrush = Brushes.LightGray;
                HistogramBackgroundBrush = Brushes.Transparent;

                // PriceBox supplies native price-scale markers without developing plot lines.
                AddPlot(new Stroke(PocLineBrush, 1), PlotStyle.PriceBox, "POC");
                AddPlot(new Stroke(ValueAreaLineBrush, 1), PlotStyle.PriceBox, "VAH");
                AddPlot(new Stroke(ValueAreaLineBrush, 1), PlotStyle.PriceBox, "VAL");
            }
            else if (State == State.Configure)
            {
                // Hardcoded as required by NinjaTrader's multi-series lifecycle.
                // All historical and live volume comes through this SAME stream.
                AddDataSeries(BarsPeriodType.Tick, 1);
                Calculate = Calculate.OnEachTick;
                Plots[0].Brush = ShowPoc ? PocLineBrush : Brushes.Transparent;
                Plots[1].Brush = ShowVah ? ValueAreaLineBrush : Brushes.Transparent;
                Plots[2].Brush = ShowVal ? ValueAreaLineBrush : Brushes.Transparent;
            }
            else if (State == State.DataLoaded)
            {
                lock (profileLock)
                {
                    sessions.Clear();
                    currentSession = null;
                    tickCounter.Reset();
                }
                configurationError = null;
                lastPublishedBar = -1;
                lastPublish = DateTime.MinValue;
                latestPoc = latestVah = latestVal = double.NaN;
                windowLabel = null;
                zoneWindows = null;
                try
                {
                    if (!Bars.BarsType.IsIntraday)
                        throw new ArgumentException("Use an intraday chart (for example, 30 Minute).");
                    startTime = TatankaVpCore.SessionClock.ParseTime(SessionStart);
                    endTime = TatankaVpCore.SessionClock.ParseTime(SessionEnd);
                    chartZone = NinjaTrader.Core.Globals.GeneralOptions.TimeZoneInfo;
                    switch (SessionTimeZone)
                    {
                        case TatankaVpTimeZone.Central:
                            sessionZone = TimeZoneInfo.FindSystemTimeZoneById("Central Standard Time"); break;
                        case TatankaVpTimeZone.Eastern:
                            sessionZone = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); break;
                        case TatankaVpTimeZone.Chart: sessionZone = chartZone; break;
                        case TatankaVpTimeZone.UTC: sessionZone = TimeZoneInfo.Utc; break;
                        default: sessionZone = Bars.TradingHours.TimeZoneInfo; break;
                    }
                    // Use Instrument Hours (v1.1.0): the window moves to the instrument's own regular hours. In Central time
                    // as a whole; in another zone each date's window, at that date's offset from Central time (ZoneWindows).
                    if (UseInstrumentHours && sessionZone != null)
                    {
                        if (sessionZone.Id == TatankaVpCore.InstrumentHours.CentralZone)
                        {
                            if (TatankaVpCore.InstrumentHours.MoveTimes(ref startTime, ref endTime, Instrument.MasterInstrument.Name))
                                windowLabel = TatankaVpCore.InstrumentHours.Clock(startTime) + "-" + TatankaVpCore.InstrumentHours.Clock(endTime);
                        }
                        else
                        {
                            zoneWindows = TatankaVpCore.ZoneWindows.For(startTime, endTime, Instrument.MasterInstrument.Name, sessionZone,
                                TimeZoneInfo.FindSystemTimeZoneById(TatankaVpCore.InstrumentHours.CentralZone));
                            if (zoneWindows != null)   // today's window until the first session
                                windowLabel = zoneWindows.LabelOn(TimeZoneInfo.ConvertTime(DateTime.UtcNow, sessionZone).Date);
                        }
                    }
                    if (TickSize <= 0 || TicksPerRow < 1 || SessionsToKeep < 1)
                        throw new ArgumentException("Tick size, ticks per row, and session count must be positive.");
                }
                catch (Exception ex)
                {
                    configurationError = ex.Message;
                    Print(Name + ": " + configurationError);
                }
            }
            else if (State == State.Transition && configurationError == null)
                PublishLevels(true);
        }

        public override string DisplayName
        {
            get
            {
                string label = "Tatanka SVP";
                if (ShowInputsInLabel)
                    label += " (" + (windowLabel ?? (SessionStart + "-" + SessionEnd)) + ", " + SessionTimeZone
                        + ", VA " + ValueAreaPercent.ToString("0.#", CultureInfo.InvariantCulture) + "%, " + Alignment + ")";
                if (ShowValuesInLabel && !double.IsNaN(latestPoc) && Instrument != null)
                    label += "  POC " + FormatPriceMarker(latestPoc) + "  VAH " + FormatPriceMarker(latestVah)
                        + "  VAL " + FormatPriceMarker(latestVal);
                return label;
            }
        }

        public override string FormatPriceMarker(double price)
        {
            return Instrument == null ? price.ToString("0.########", CultureInfo.InvariantCulture)
                : Instrument.MasterInstrument.FormatPrice(price);
        }

        protected override void OnBarUpdate()
        {
            if (configurationError != null || chartZone == null || CurrentBars[BarsInProgress] < 0)
                return;

            if (BarsInProgress == 1)
            {
                double volume = tickCounter.Consume(CurrentBars[1], Volumes[1][0]);
                if (volume <= 0) return;
                DateTime chartTime = DateTime.SpecifyKind(Times[1][0], DateTimeKind.Unspecified);
                DateTime localTime = TimeZoneInfo.ConvertTime(chartTime, chartZone, sessionZone);
                DateTime windowStart, windowEnd;
                if (zoneWindows != null ? zoneWindows.TryWindow(localTime, out windowStart, out windowEnd)
                    : TatankaVpCore.SessionClock.TryWindow(localTime, startTime, endTime, out windowStart, out windowEnd))
                {
                    lock (profileLock)
                    {
                        if (currentSession == null || currentSession.StartLocal != windowStart)
                        {
                            // Regressing data must not reopen an already finalized session.
                            if (currentSession != null && windowStart < currentSession.StartLocal) return;
                            if (currentSession != null)
                                currentSession.GetSnapshot(ValueAreaPercent, UseAtLeastTarget, true);
                            currentSession = new TatankaVpCore.Session(windowStart,
                                TimeZoneInfo.ConvertTime(windowStart, sessionZone, chartZone),
                                TimeZoneInfo.ConvertTime(windowEnd, sessionZone, chartZone), TickSize, TicksPerRow);
                            sessions.Add(currentSession);
                            while (sessions.Count > SessionsToKeep) sessions.RemoveAt(0);
                            if (zoneWindows != null)   // the label shows the window this session runs (each date's)
                                windowLabel = TatankaVpCore.ZoneWindows.Label(windowStart, windowEnd);
                        }
                        currentSession.Add(Closes[1][0], volume, chartTime);
                    }
                }
                // Secondary-series values use the primary series' currently synchronized slot.
                // Publishing is throttled, while volume accumulation is never throttled.
                PublishLevels(false);
            }
            else if (BarsInProgress == 0)
                PublishLevels(CurrentBars[0] != lastPublishedBar);
        }

        private bool UseAtLeastTarget { get { return ValueAreaMethod == TatankaVpValueAreaMethod.AtLeastTarget; } }

        private void PublishLevels(bool newPrimaryBar)
        {
            if (CurrentBars[0] < 0) return;
            DateTime now = DateTime.UtcNow;
            bool calculateNow = newPrimaryBar || (State != State.Historical && (now - lastPublish).TotalMilliseconds >= 200);
            if (calculateNow)
            {
                lock (profileLock)
                {
                    if (currentSession != null)
                    {
                        TatankaVpCore.Snapshot snapshot = currentSession.GetSnapshot(ValueAreaPercent, UseAtLeastTarget, true);
                        latestPoc = snapshot.Poc;
                        latestVah = snapshot.Vah;
                        latestVal = snapshot.Val;
                    }
                }
                lastPublish = now;
            }
            if (!calculateNow && !newPrimaryBar) return;
            if (double.IsNaN(latestPoc))
            {
                Values[0].Reset(); Values[1].Reset(); Values[2].Reset();
            }
            else
            {
                Values[0][0] = latestPoc;
                Values[1][0] = latestVah;
                Values[2][0] = latestVal;
            }
            lastPublishedBar = CurrentBars[0];
        }

        protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
        {
            if (RenderTarget == null || ChartBars == null || ChartPanel == null || IsInHitTest) return;
            base.OnRender(chartControl, chartScale);

            List<TatankaVpCore.Snapshot> snapshots = new List<TatankaVpCore.Snapshot>();
            lock (profileLock)
            {
                foreach (TatankaVpCore.Session session in sessions)
                    snapshots.Add(session.GetSnapshot(ValueAreaPercent, UseAtLeastTarget, false));
            }

            // Resources belong to this render target and are disposed on every render,
            // including hit tests, resize/target changes, and exceptions.
            using (D2D.Brush up = UpVolumeBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush down = DownVolumeBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush vaUp = ValueAreaUpBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush vaDown = ValueAreaDownBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush vaLine = ValueAreaLineBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush pocLine = PocLineBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush text = TextBrush.ToDxBrush(RenderTarget))
            using (D2D.Brush background = HistogramBackgroundBrush.ToDxBrush(RenderTarget))
            using (SharpDX.DirectWrite.TextFormat font = new SimpleFont("Segoe UI", 11).ToDirectWriteTextFormat())
            {
                float panelRight = Math.Min(ChartPanel.X + ChartPanel.W, chartControl.CanvasRight);
                RectangleF clip = new RectangleF(ChartPanel.X, ChartPanel.Y, Math.Max(1, panelRight - ChartPanel.X), ChartPanel.H);
                RenderTarget.PushAxisAlignedClip(clip, D2D.AntialiasMode.Aliased);
                try
                {
                    if (configurationError != null || snapshots.Count == 0)
                    {
                        string message = configurationError ?? "Tatanka SVP: no ticks in the custom session. Check historical tick data, Trading Hours, and session time zone.";
                        RenderTarget.DrawText(message, font, new RectangleF(ChartPanel.X + 12, ChartPanel.Y + 35,
                            Math.Max(100, ChartPanel.W - 24), 50), text);
                        return;
                    }
                    DateTime visibleFirst = ChartBars.Bars.GetTime(ChartBars.FromIndex);
                    DateTime visibleLast = ChartBars.Bars.GetTime(ChartBars.ToIndex);
                    for (int s = 0; s < snapshots.Count; s++)
                    {
                        TatankaVpCore.Snapshot p = snapshots[s];
                        if (p.Rows.Length == 0) continue;
                        bool pinned = Alignment == TatankaVpAlignment.ChartRightLatest;
                        if (pinned && s != snapshots.Count - 1) continue;
                        // Do not present the latest profile as if it belonged to a scrolled-back date.
                        if (pinned && visibleLast < p.StartChart) continue;
                        if (!pinned && (p.EndChart < visibleFirst || p.StartChart > visibleLast)) continue;

                        // A forming session's right edge follows its latest tick. A completed
                        // session uses its scheduled end, including when trading stops early.
                        DateTime edgeTime = p.LastChart < p.EndChart ? p.LastChart : p.EndChart;
                        DateTime lastLoadedTime = ChartBars.Bars.GetTime(ChartBars.Bars.Count - 1);
                        if (lastLoadedTime >= p.EndChart) edgeTime = p.EndChart;
                        float sessionLeft = chartControl.GetXByTime(p.StartChart);
                        float sessionRight = chartControl.GetXByTime(edgeTime);
                        float right = pinned ? panelRight - RightPaddingPixels : sessionRight;
                        float span = pinned ? panelRight - ChartPanel.X : Math.Max(2, sessionRight - sessionLeft);
                        float width = Math.Max(1, span * (float)WidthPercent / 100f);
                        float lineLeft = pinned ? Math.Max(ChartPanel.X, right - width) : sessionLeft;
                        float boxTop = chartScale.GetYByValue(p.HighEdge);
                        float boxBottom = chartScale.GetYByValue(p.LowEdge);
                        RenderTarget.FillRectangle(new RectangleF(pinned ? right - width : sessionLeft, Math.Min(boxTop, boxBottom),
                            pinned ? width : Math.Max(1, right - sessionLeft), Math.Abs(boxBottom - boxTop)), background);

                        if (ShowProfile)
                        {
                            foreach (TatankaVpCore.Row row in p.Rows)
                            {
                                double lowEdge = row.Key * p.RowSize - TickSize / 2;
                                double highEdge = lowEdge + p.RowSize;
                                if (highEdge < chartScale.MinValue || lowEdge > chartScale.MaxValue) continue;
                                float top = chartScale.GetYByValue(highEdge);
                                float bottom = chartScale.GetYByValue(lowEdge);
                                float fullHeight = Math.Abs(bottom - top);
                                // Keep subpixel rows subpixel; forcing every row to 1px inflates dense profiles.
                                float height = Math.Max(0.2f, fullHeight - Math.Min(RowGapPixels, fullHeight * 0.35f));
                                float y = Math.Min(top, bottom) + (fullHeight - height) / 2;
                                float upWidth = (float)(width * row.Up / p.MaxVolume);
                                float downWidth = (float)(width * row.Down / p.MaxVolume);
                                bool inVa = row.Key >= p.ValKey && row.Key <= p.VahKey;
                                float left = right - upWidth - downWidth;
                                if (upWidth > 0) RenderTarget.FillRectangle(new RectangleF(left, y, upWidth, height), inVa ? vaUp : up);
                                if (downWidth > 0) RenderTarget.FillRectangle(new RectangleF(right - downWidth, y, downWidth, height), inVa ? vaDown : down);
                                if (ShowRowVolumes && fullHeight >= 12 && upWidth + downWidth >= 60)
                                    RenderTarget.DrawText(row.Up.ToString("0", CultureInfo.InvariantCulture) + " / " + row.Down.ToString("0", CultureInfo.InvariantCulture),
                                        font, new RectangleF(left + 3, y, upWidth + downWidth - 3, 14), text);
                            }
                        }
                        if (ShowVah) DrawLevel(p.Vah, "VAH", lineLeft, right, panelRight, ExtendVahToChartEdge, vaLine, font, chartScale, s == snapshots.Count - 1);
                        if (ShowVal) DrawLevel(p.Val, "VAL", lineLeft, right, panelRight, ExtendValToChartEdge, vaLine, font, chartScale, s == snapshots.Count - 1);
                        if (ShowPoc) DrawLevel(p.Poc, "POC", lineLeft, right, panelRight, ExtendPocToChartEdge, pocLine, font, chartScale, s == snapshots.Count - 1);
                    }
                }
                finally { RenderTarget.PopAxisAlignedClip(); }
            }
        }

        private void DrawLevel(double price, string name, float left, float right, float panelRight, bool extend,
            D2D.Brush brush, SharpDX.DirectWrite.TextFormat font, ChartScale scale, bool latest)
        {
            if (price < scale.MinValue || price > scale.MaxValue) return;
            float y = scale.GetYByValue(price);
            float end = extend ? panelRight : right;
            if (end >= ChartPanel.X && left <= panelRight)
                RenderTarget.DrawLine(new Vector2(Math.Max(ChartPanel.X, left), y), new Vector2(Math.Min(panelRight, end), y), brush, LineWidth);
            if (ShowLevelLabels && latest)
            {
                float labelX = Math.Max(ChartPanel.X + 4, Math.Min(right + 5, panelRight - 110));
                RenderTarget.DrawText(name + " " + FormatPriceMarker(price), font,
                    new RectangleF(labelX, y - 15, 110, 15), brush);
            }
        }

        private static WpfBrush MakeBrush(byte r, byte g, byte b)
        {
            SolidColorBrush brush = new SolidColorBrush(System.Windows.Media.Color.FromRgb(r, g, b));
            brush.Freeze();
            return brush;
        }

        #region Settings
        [Display(Name = "Session start (HH:mm)", GroupName = "01. Session", Order = 0)]
        public string SessionStart { get; set; }
        [Display(Name = "Session end (HH:mm)", GroupName = "01. Session", Order = 1)]
        public string SessionEnd { get; set; }
        [Display(Name = "Session time zone", Description = "ExchangeTemplate uses the chart's Trading Hours template time zone. For ES/NQ, Central explicitly selects Chicago time with DST.", GroupName = "01. Session", Order = 2)]
        public TatankaVpTimeZone SessionTimeZone { get; set; }
        [Display(Name = "Use Instrument Hours", Description = "Session start and end are written for ES's regular hours (08:30-15:00 CT), in the session time zone (09:30-16:00 on Eastern). On another market they move to its own (GC 07:20-12:30 CT, CL 08:00-13:30 CT): a time nearer the open moves with the open, one nearer the close with the close. Off: as typed on every market.", GroupName = "01. Session", Order = 3)]
        public bool UseInstrumentHours { get; set; }
        [Range(1, 100), Display(Name = "Value area volume (%)", GroupName = "01. Session", Order = 4)]
        public double ValueAreaPercent { get; set; }
        [Display(Name = "Value area method", Description = "TradingViewStyle stops before the next row exceeds the target. AtLeastTarget includes that row.", GroupName = "01. Session", Order = 5)]
        public TatankaVpValueAreaMethod ValueAreaMethod { get; set; }
        [Range(1, 1000), Display(Name = "Ticks per row", GroupName = "01. Session", Order = 6)]
        public int TicksPerRow { get; set; }
        [Range(1, 100), Display(Name = "Sessions to retain", GroupName = "01. Session", Order = 7)]
        public int SessionsToKeep { get; set; }

        [Display(Name = "Placement", GroupName = "02. Profile", Order = 0)]
        public TatankaVpAlignment Alignment { get; set; }
        [Range(1, 100), Display(Name = "Width (% of session / chart)", GroupName = "02. Profile", Order = 1)]
        public double WidthPercent { get; set; }
        [Range(0, 500), Display(Name = "Chart-right padding (pixels)", GroupName = "02. Profile", Order = 2)]
        public int RightPaddingPixels { get; set; }
        [Range(0, 5), Display(Name = "Row gap (pixels)", GroupName = "02. Profile", Order = 3)]
        public int RowGapPixels { get; set; }
        [Display(Name = "Volume profile", GroupName = "02. Profile", Order = 4)]
        public bool ShowProfile { get; set; }
        [Display(Name = "Row volumes (up / down)", GroupName = "02. Profile", Order = 5)]
        public bool ShowRowVolumes { get; set; }

        [Display(Name = "POC", GroupName = "03. Levels", Order = 0)]
        public bool ShowPoc { get; set; }
        [Display(Name = "VAH", GroupName = "03. Levels", Order = 1)]
        public bool ShowVah { get; set; }
        [Display(Name = "VAL", GroupName = "03. Levels", Order = 2)]
        public bool ShowVal { get; set; }
        [Display(Name = "Extend POC to chart edge", GroupName = "03. Levels", Order = 3)]
        public bool ExtendPocToChartEdge { get; set; }
        [Display(Name = "Extend VAH to chart edge", GroupName = "03. Levels", Order = 4)]
        public bool ExtendVahToChartEdge { get; set; }
        [Display(Name = "Extend VAL to chart edge", GroupName = "03. Levels", Order = 5)]
        public bool ExtendValToChartEdge { get; set; }
        [Range(1, 5), Display(Name = "Line width", GroupName = "03. Levels", Order = 6)]
        public int LineWidth { get; set; }
        [Display(Name = "Level name labels", GroupName = "03. Levels", Order = 7)]
        public bool ShowLevelLabels { get; set; }
        [Display(Name = "Inputs in chart label", GroupName = "03. Levels", Order = 8)]
        public bool ShowInputsInLabel { get; set; }
        [Display(Name = "Values in chart label", GroupName = "03. Levels", Order = 9)]
        public bool ShowValuesInLabel { get; set; }

        [XmlIgnore, Display(Name = "Up volume", GroupName = "04. Colors", Order = 0)]
        public WpfBrush UpVolumeBrush { get; set; }
        [Browsable(false)] public string UpVolumeBrushSerializable { get { return Serialize.BrushToString(UpVolumeBrush); } set { UpVolumeBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "Down volume", GroupName = "04. Colors", Order = 1)]
        public WpfBrush DownVolumeBrush { get; set; }
        [Browsable(false)] public string DownVolumeBrushSerializable { get { return Serialize.BrushToString(DownVolumeBrush); } set { DownVolumeBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "Value area up", GroupName = "04. Colors", Order = 2)]
        public WpfBrush ValueAreaUpBrush { get; set; }
        [Browsable(false)] public string ValueAreaUpBrushSerializable { get { return Serialize.BrushToString(ValueAreaUpBrush); } set { ValueAreaUpBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "Value area down", GroupName = "04. Colors", Order = 3)]
        public WpfBrush ValueAreaDownBrush { get; set; }
        [Browsable(false)] public string ValueAreaDownBrushSerializable { get { return Serialize.BrushToString(ValueAreaDownBrush); } set { ValueAreaDownBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "VAH / VAL", GroupName = "04. Colors", Order = 4)]
        public WpfBrush ValueAreaLineBrush { get; set; }
        [Browsable(false)] public string ValueAreaLineBrushSerializable { get { return Serialize.BrushToString(ValueAreaLineBrush); } set { ValueAreaLineBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "POC", GroupName = "04. Colors", Order = 5)]
        public WpfBrush PocLineBrush { get; set; }
        [Browsable(false)] public string PocLineBrushSerializable { get { return Serialize.BrushToString(PocLineBrush); } set { PocLineBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "Text", GroupName = "04. Colors", Order = 6)]
        public WpfBrush TextBrush { get; set; }
        [Browsable(false)] public string TextBrushSerializable { get { return Serialize.BrushToString(TextBrush); } set { TextBrush = Serialize.StringToBrush(value); } }
        [XmlIgnore, Display(Name = "Histogram box", GroupName = "04. Colors", Order = 7)]
        public WpfBrush HistogramBackgroundBrush { get; set; }
        [Browsable(false)] public string HistogramBackgroundBrushSerializable { get { return Serialize.BrushToString(HistogramBackgroundBrush); } set { HistogramBackgroundBrush = Serialize.StringToBrush(value); } }

        [Browsable(false), XmlIgnore] public Series<double> POC { get { return Values[0]; } }
        [Browsable(false), XmlIgnore] public Series<double> VAH { get { return Values[1]; } }
        [Browsable(false), XmlIgnore] public Series<double> VAL { get { return Values[2]; } }
        #endregion
    }
}

// BEGIN STANDALONE PROFILE CORE -- also compiled independently by the test harness.
namespace TatankaVpCore
{
    // Each market's regular hours (CT) by NinjaTrader master instrument name (the root), micros with their parent's: the
    // Tatanka AMT Toolkit's table (v1.2.4), kept here so this file installs on its own. A root not listed has ES's hours.
    // The session window is written for ES's 08:30-15:00 CT; with Use Instrument Hours on, it moves to the chart
    // instrument's own (v1.1.0): in Central time with MoveTimes, in another zone with ZoneWindows.
    internal static class InstrumentHours
    {
        internal const int EsOpen = 8 * 60 + 30, EsClose = 15 * 60;
        internal const string CentralZone = "Central Standard Time";
        private static readonly Dictionary<string, int[]> hours = new Dictionary<string, int[]>(StringComparer.OrdinalIgnoreCase);

        static InstrumentHours()
        {
            Add("08:30", "15:00", "ES", "MES", "NQ", "MNQ", "RTY", "M2K", "YM", "MYM", "EMD", "NKD");   // equity index
            Add("08:00", "13:30", "CL", "MCL", "QM", "NG", "RB", "HO");                                    // energy
            Add("07:20", "12:30", "GC", "MGC");                                                            // metals
            Add("07:25", "12:25", "SI", "SIL");
            Add("07:10", "12:00", "HG", "MHG");
            Add("07:20", "12:05", "PL");
            Add("07:30", "12:00", "PA");
            Add("07:20", "14:00", "ZT", "ZF", "ZN", "TN", "ZB", "UB", "SR3");                              // interest rates
            Add("07:20", "14:00", "6E", "M6E", "6B", "6J", "6A", "6C", "6S", "6N", "6M");                  // currencies
        }

        private static void Add(string open, string close, params string[] roots)
        {
            int[] oc = { Minutes(open), Minutes(close) };
            foreach (string root in roots) hours[root] = oc;
        }

        private static int Minutes(string hm) { return int.Parse(hm.Substring(0, 2)) * 60 + int.Parse(hm.Substring(3, 2)); }

        // The root's regular hours, open and close in minutes of the day (ES's for a root not listed).
        internal static int[] Of(string root)
        {
            int[] oc;
            return root != null && hours.TryGetValue(root, out oc) ? new[] { oc[0], oc[1] } : new[] { EsOpen, EsClose };
        }

        internal static int Mod(int m) { return (m % 1440 + 1440) % 1440; }

        // A CT time (minutes of the day) written for ES's hours, at the same place in open-close: one nearer the open
        // moves with the open, one nearer the close with the close; the overnight (before 07:00, from 16:00) stays.
        internal static int Move(int m, int open, int close)
        {
            if (m < 7 * 60 || m >= 16 * 60) return m;
            return m + (Math.Abs(m - EsOpen) <= Math.Abs(m - EsClose) ? open - EsOpen : close - EsClose);
        }

        // A window's start and end moved as a whole: each end on its own, unless that changes the window's order. Two
        // ends either side of 11:45 that would land on the same minute or past each other (11:00-12:00: 10:30-10:30 on CL,
        // 09:50-09:30 on GC) both move as the window's middle does, keeping its length (CL 10:30-11:30, GC 09:50-10:50).
        internal static void MoveWindow(ref int a, ref int b, int open, int close)
        {
            int x = Move(a, open, close), y = Move(b, open, close);
            if (a == b || ((a < b) == (x < y) && x != y)) { a = x; b = y; return; }
            int mid = (a + Mod(b - a) / 2) % 1440, d = Move(mid, open, close) - mid;
            a = Mod(a + d); b = Mod(b + d);
        }

        // The session window (times of day) at the root's own hours, moved as a whole (MoveWindow), seconds kept. False,
        // and the window as it is, on a root with ES's hours.
        internal static bool MoveTimes(ref TimeSpan start, ref TimeSpan end, string root)
        {
            int[] oc = Of(root);
            if (oc[0] == EsOpen && oc[1] == EsClose) return false;
            int a = start.Hours * 60 + start.Minutes, b = end.Hours * 60 + end.Minutes;
            MoveWindow(ref a, ref b, oc[0], oc[1]);
            start = new TimeSpan(a / 60, a % 60, start.Seconds);
            end = new TimeSpan(b / 60, b % 60, end.Seconds);
            return true;
        }

        // "HH:mm", or "HH:mm:ss" with seconds: the window in the chart label.
        internal static string Clock(TimeSpan t)
        {
            string hm = t.Hours.ToString("00", CultureInfo.InvariantCulture) + ":" + t.Minutes.ToString("00", CultureInfo.InvariantCulture);
            return t.Seconds == 0 ? hm : hm + ":" + t.Seconds.ToString("00", CultureInfo.InvariantCulture);
        }
    }

    // Use Instrument Hours outside Central time (v1.1.0): Session time zone Eastern or UTC, ExchangeTemplate on a template
    // in another zone, Chart with NinjaTrader set to another zone. The window is typed in that zone (ES's hours are
    // 09:30-16:00 on Eastern). Each date's window is put in Central time at that date's offset from it (at noon), moved
    // there as a whole (InstrumentHours.MoveWindow) and put back, the move kept per offset: on Eastern 09:30-16:00 runs
    // 08:20-13:30 on GC. A zone whose daylight saving differs from Chicago's moves each date at its own offset: London's
    // 14:30-21:00 runs 13:20-18:30 on GC, and 13:20-21:00 in the week London's has ended and Chicago's hasn't.
    internal sealed class ZoneWindows
    {
        private readonly TimeSpan start, span;
        private readonly int startMinute, endMinute, open, close;
        private readonly TimeZoneInfo zone, central;
        private readonly Dictionary<int, int[]> shifts = new Dictionary<int, int[]>();   // offset: start, end moved by (minutes)
        private readonly Dictionary<DateTime, DateTime[]> windows = new Dictionary<DateTime, DateTime[]>();   // date: begin, finish

        private ZoneWindows(TimeSpan start, TimeSpan end, int[] oc, TimeZoneInfo zone, TimeZoneInfo central)
        {
            this.start = start;
            span = end > start ? end - start : end + TimeSpan.FromDays(1) - start;   // SessionClock.TryWindow's
            startMinute = start.Hours * 60 + start.Minutes;
            endMinute = end.Hours * 60 + end.Minutes;
            open = oc[0];
            close = oc[1];
            this.zone = zone;
            this.central = central;
        }

        // The windows of start-end typed in zone, at the root's own hours; null on a root with ES's hours (as typed).
        // central: Central time.
        internal static ZoneWindows For(TimeSpan start, TimeSpan end, string root, TimeZoneInfo zone, TimeZoneInfo central)
        {
            int[] oc = InstrumentHours.Of(root);
            if (oc[0] == InstrumentHours.EsOpen && oc[1] == InstrumentHours.EsClose) return null;
            return new ZoneWindows(start, end, oc, zone, central);
        }

        // The window typed for date d (midnight in zone), moved at d's offset from Central time: [begin, finish), seconds
        // kept. A start moved back past midnight begins the day before.
        internal void Window(DateTime d, out DateTime begin, out DateTime finish)
        {
            DateTime[] w;
            if (!windows.TryGetValue(d, out w))
            {
                DateTime noon = DateTime.SpecifyKind(d.AddHours(12), DateTimeKind.Unspecified);
                int offset = (int)Math.Round((noon - TimeZoneInfo.ConvertTime(noon, zone, central)).TotalMinutes);
                int[] by;
                if (!shifts.TryGetValue(offset, out by))
                {
                    int a = InstrumentHours.Mod(startMinute - offset), b = InstrumentHours.Mod(endMinute - offset), x = a, y = b;
                    InstrumentHours.MoveWindow(ref x, ref y, open, close);
                    by = new[] { InstrumentHours.Mod(x - a + 720) - 720, InstrumentHours.Mod(y - b + 720) - 720 };
                    shifts[offset] = by;
                }
                w = new[] { d.Add(start).AddMinutes(by[0]), d.Add(start + span).AddMinutes(by[1]) };
                windows[d] = w;
            }
            begin = w[0];
            finish = w[1];
        }

        // SessionClock.TryWindow over the moved windows: the latest one holding local, of those typed for the day after
        // (its start moved back past midnight), the same day and the day before (overnight). False if none does.
        internal bool TryWindow(DateTime local, out DateTime begin, out DateTime finish)
        {
            DateTime day = local.Date;
            for (int k = 1; k >= -1; k--)
            {
                Window(day.AddDays(k), out begin, out finish);
                if (local >= begin && local < finish) return true;
            }
            begin = finish = DateTime.MinValue;
            return false;
        }

        // A window as the chart label shows it: "HH:mm-HH:mm" (InstrumentHours.Clock).
        internal static string Label(DateTime begin, DateTime finish)
        {
            return InstrumentHours.Clock(begin.TimeOfDay) + "-" + InstrumentHours.Clock(finish.TimeOfDay);
        }

        // The label of the window typed for date d.
        internal string LabelOn(DateTime d)
        {
            DateTime begin, finish;
            Window(d, out begin, out finish);
            return Label(begin, finish);
        }
    }

    internal static class SessionClock
    {
        internal static TimeSpan ParseTime(string value)
        {
            DateTime parsed;
            if (!DateTime.TryParseExact(value, new string[] { "HH:mm", "H:mm", "HH:mm:ss" },
                CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed))
                throw new ArgumentException("Session times must use 24-hour HH:mm format, for example 08:30 and 15:00.");
            return parsed.TimeOfDay;
        }

        internal static bool TryWindow(DateTime time, TimeSpan start, TimeSpan end, out DateTime begin, out DateTime finish)
        {
            begin = time.Date.Add(start);
            finish = time.Date.Add(end);
            if (end <= start)
            {
                if (time.TimeOfDay < start) begin = begin.AddDays(-1);
                finish = begin.Date.AddDays(1).Add(end);
            }
            return time >= begin && time < finish;
        }
    }

    internal sealed class TickCounter
    {
        private int lastIndex = -1;
        private double counted;
        internal void Reset() { lastIndex = -1; counted = 0; }
        internal double Consume(int index, double volume)
        {
            if (index < lastIndex || volume <= 0 || double.IsNaN(volume) || double.IsInfinity(volume)) return 0;
            if (index != lastIndex) { lastIndex = index; counted = 0; }
            double delta = Math.Max(0, volume - counted);
            counted = Math.Max(counted, volume);
            return delta;
        }
    }

    internal sealed class Row
    {
        internal long Key;
        internal double Up, Down;
        internal double Total { get { return Up + Down; } }
    }

    internal sealed class Snapshot
    {
        internal Row[] Rows;
        internal DateTime StartChart, EndChart, LastChart;
        internal double RowSize, MaxVolume, TotalVolume, ValueAreaVolume, Poc, Vah, Val, LowEdge, HighEdge;
        internal long PocKey, VahKey, ValKey;
    }

    internal sealed class Session
    {
        private readonly Dictionary<long, Row> rows = new Dictionary<long, Row>();
        internal readonly DateTime StartLocal, StartChart, EndChart;
        private readonly double tickSize;
        private readonly int ticksPerRow;
        private DateTime lastChart;
        private double lastPrice = double.NaN;
        private bool lastWasUp = true;
        private long version, snapshotVersion = -1;
        private DateTime snapshotTime;
        private Snapshot cached;
        private double cachedPercent;
        private bool cachedAtLeast;

        internal Session(DateTime startLocal, DateTime startChart, DateTime endChart, double tickSize, int ticksPerRow)
        {
            if (tickSize <= 0 || ticksPerRow < 1) throw new ArgumentException("Invalid row size.");
            StartLocal = startLocal; StartChart = startChart; EndChart = endChart;
            this.tickSize = tickSize; this.ticksPerRow = ticksPerRow;
        }

        internal void Add(double price, double volume, DateTime time)
        {
            if (volume <= 0 || double.IsNaN(volume) || double.IsInfinity(volume)
                || double.IsNaN(price) || double.IsInfinity(price)) return;
            // Floor division remains correct for negative prices. Round before binning to
            // avoid floating-point values just below a valid exchange tick boundary.
            long tick = checked((long)Math.Round(price / tickSize, MidpointRounding.AwayFromZero));
            long key = (long)Math.Floor((double)tick / ticksPerRow);
            if (!double.IsNaN(lastPrice))
            {
                if (price > lastPrice) lastWasUp = true;
                else if (price < lastPrice) lastWasUp = false;
            }
            Row row;
            if (!rows.TryGetValue(key, out row))
            {
                row = new Row { Key = key };
                rows.Add(key, row);
            }
            if (lastWasUp) row.Up += volume; else row.Down += volume;
            lastPrice = price;
            lastChart = time;
            version++;
        }

        internal Snapshot GetSnapshot(double percent, bool atLeast, bool force)
        {
            if (cached != null && cachedPercent == percent && cachedAtLeast == atLeast)
            {
                if (snapshotVersion == version) return cached;
                if (!force && (DateTime.UtcNow - snapshotTime).TotalMilliseconds < 200) return cached;
            }
            List<long> keys = new List<long>(rows.Keys);
            keys.Sort();
            Snapshot result = new Snapshot { Rows = new Row[keys.Count], StartChart = StartChart,
                EndChart = EndChart, LastChart = lastChart, RowSize = tickSize * ticksPerRow };
            if (keys.Count == 0) return result;
            long min = keys[0], max = keys[keys.Count - 1];
            double midpoint = min / 2.0 + max / 2.0;
            int pocIndex = 0;
            for (int i = 0; i < keys.Count; i++)
            {
                Row source = rows[keys[i]];
                Row row = new Row { Key = source.Key, Up = source.Up, Down = source.Down };
                result.Rows[i] = row;
                result.TotalVolume += row.Total;
                // Tied POCs: nearest range midpoint; lower price if still tied.
                if (row.Total > result.MaxVolume || (row.Total == result.MaxVolume &&
                    Math.Abs(row.Key - midpoint) < Math.Abs(keys[pocIndex] - midpoint)))
                { result.MaxVolume = row.Total; pocIndex = i; }
            }
            long poc = keys[pocIndex], low = poc, high = poc;
            int below = pocIndex - 1, above = pocIndex + 1;
            double included = result.Rows[pocIndex].Total;
            double target = result.TotalVolume * Math.Max(1, Math.Min(100, percent)) / 100.0;
            while (included < target && (low > min || high < max))
            {
                bool hasUp = high < max, hasDown = low > min;
                double upVolume = hasUp && above < keys.Count && keys[above] == high + 1 ? result.Rows[above].Total : 0;
                double downVolume = hasDown && below >= 0 && keys[below] == low - 1 ? result.Rows[below].Total : 0;
                // Skip runs of empty rows in O(number of populated rows), preserving
                // the same contiguous VA boundaries and tie rule as one-row stepping.
                if (upVolume == 0 && downVolume == 0)
                {
                    if (!hasUp) low = keys[below] + 1;
                    else if (!hasDown) high = keys[above] - 1;
                    else
                    {
                        long upDistance = keys[above] - poc;
                        long downDistance = poc - keys[below];
                        if (upDistance <= downDistance)
                        {
                            high = keys[above] - 1;
                            low = Math.Min(low, poc - Math.Min(downDistance - 1, upDistance - 2));
                        }
                        else
                        {
                            low = keys[below] + 1;
                            high = Math.Max(high, poc + Math.Min(upDistance - 1, downDistance - 1));
                        }
                    }
                    continue;
                }
                bool takeUp = hasUp && (!hasDown || upVolume > downVolume ||
                    (upVolume == downVolume && high + 1 - poc <= poc - (low - 1)));
                double candidate = takeUp ? upVolume : downVolume;
                // Follow the current published TradingView rule by default: do not add
                // a row that would overshoot the target. The POC is always included.
                if (!atLeast && included + candidate > target + result.TotalVolume * 1e-12) break;
                included += candidate;
                if (takeUp) { high++; if (above < keys.Count && keys[above] == high) above++; }
                else { low--; if (below >= 0 && keys[below] == low) below--; }
            }
            result.PocKey = poc; result.ValKey = low; result.VahKey = high;
            result.ValueAreaVolume = included;
            result.Poc = poc * result.RowSize + (ticksPerRow - 1) / 2.0 * tickSize;
            result.Val = low * result.RowSize;
            result.Vah = high * result.RowSize + (ticksPerRow - 1) * tickSize;
            result.LowEdge = min * result.RowSize - tickSize / 2;
            result.HighEdge = max * result.RowSize + (ticksPerRow - 0.5) * tickSize;
            cached = result; cachedPercent = percent; cachedAtLeast = atLeast;
            snapshotVersion = version; snapshotTime = DateTime.UtcNow;
            return result;
        }
    }
}
// END STANDALONE PROFILE CORE
