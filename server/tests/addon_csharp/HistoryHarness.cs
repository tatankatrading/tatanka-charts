// Drives the add-on's history for tests/test_addon_history.py: its real History, Fetch, OnMarketData and send loop, with
// NinjaTrader's BarsRequest answered as each case says (Stubs.cs). ES is on the contract of next month; NinjaTrader's
// time zone (Tools > Options) is UTC here.
//   mono history.exe <case>        History() called directly for ES, nothing connected; prints
//                                  "<requests> <as trades> <as 1-minute bars> <its Z line>"
//     panic-short   NinjaTrader answers every request with Panic; the stretch is 40 s, no whole minute in it
//     panic-long    the same, 10 minutes long
//     panic-older   the same, an older request (GAP|..|older: 1-minute bars from the start)
//     empty-short   no error, no trades; 40 s
//     empty-long    no error, no trades, 3 minutes as 1-minute bars; 10 minutes
//     silent        no answer, ever; 40 s
//     offline       Panic while NinjaTrader's data connection is down (it comes back 0.1 s later), 4 times; then 5 trades
//     mixed         Panic twice with it up, then three times with it down, then 5 trades
//   mono history.exe catchup <tape>  the add-on connected to the test's server (port TCF_PORT, the add-on compiled to
//     read it) as at NinjaTrader's start: once the server has said how ES rolls (and its V line, if it sends one),
//     NinjaTrader's data connection comes up, its catch-up comes (the tape's C lines, as raised), then live trades (L);
//     its history holds the tape's H and C trades (or answers Panic: a "fail" line), its 1-minute bars too (theirs,
//     each stamped at its close; "no-ticks": it keeps no tick history, a request for trades answers none; "ticks-fail":
//     one answers Panic). Prints "ready", waits for a line on stdin (the server has the history), then the live trades
//     after it (A), and exits once they are sent; "daily": NinjaTrader's count of the day's contracts (DailyVolume)
//     comes too, as it does: first before the catch-up (which it holds already), then after each live trade, and the
//     add-on's volume check lines in NinjaScript Output are printed ("printed <line>") before the last line.
//     Tape lines: "H|C <exchange ms> <price> <size> <side>", "L|A <price> <size> <side>", "fail", "no-ticks", "ticks-fail",
//     "daily".
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript.AddOns;

public static class HistoryHarness
{
	static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
	static readonly BindingFlags Own = BindingFlags.NonPublic | BindingFlags.Instance;
	static TatankaChartFeed addon;
	static Instrument es;
	static Connection data;

	static object Call(string name, params object[] args)
	{
		return typeof(TatankaChartFeed).GetMethod(name, Own).Invoke(addon, args);
	}

	static object Get(string name) { return typeof(TatankaChartFeed).GetField(name, Own).GetValue(addon); }

	static void Set(string name, object value) { typeof(TatankaChartFeed).GetField(name, Own).SetValue(addon, value); }

	// The add-on's waits, made short (add-on 23); false for an add-on that has none to set (30 s between tries).
	static bool Waits(int retryMs, int answerMs, int dataMs)
	{
		string[] names = { "HistoryRetryMs", "HistoryWaitMs", "DataWaitMs" };
		int[] values = { retryMs, answerMs, dataMs };
		for (int k = 0; k < names.Length; k++)
		{
			FieldInfo f = typeof(TatankaChartFeed).GetField(names[k], BindingFlags.NonPublic | BindingFlags.Static);
			if (f == null) return false;
			f.SetValue(null, values[k]);
		}
		return true;
	}

	static long NowMs() { return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); }

	static DateTime At(long ms) { return DateTime.SpecifyKind(DateTimeOffset.FromUnixTimeMilliseconds(ms).UtcDateTime, DateTimeKind.Unspecified); }

	static void Setup()
	{
		DateTime now = DateTime.UtcNow, month = new DateTime(now.Year, now.Month, 1).AddMonths(1);
		MasterInstrument mi = new MasterInstrument { Name = "ES" };
		mi.RolloverCollection.Add(new Rollover { Date = new DateTime(2020, 1, 2), ContractMonth = month });
		es = new Instrument { FullName = "ES " + month.ToString("MM-yy", Inv), MasterInstrument = mi, Expiry = month };
		Instrument.Known[es.FullName] = es;
		data = new Connection { Status = ConnectionStatus.Connected, PriceStatus = ConnectionStatus.Connected };
		Connection.Connections.Add(data);
		addon = new TatankaChartFeed();
		Set("queue", new BlockingCollection<string>(new ConcurrentQueue<string>(), 200000));
		Set("cts", new CancellationTokenSource());
	}

	// NinjaTrader's data connection down now, back `ms` later.
	static void DownFor(int ms)
	{
		data.PriceStatus = ConnectionStatus.Disconnected;
		new Thread(() => { Thread.Sleep(ms); data.PriceStatus = ConnectionStatus.Connected; }) { IsBackground = true }.Start();
	}

	static void Trades(BarsRequest r, IEnumerable<long[]> trades)
	{
		foreach (long[] t in trades)
		{
			double px = t[1] / 100.0;
			r.Bars.Add(At(t[0]), px, px, px, px, t[2]);
		}
	}

	// The 1-minute bars of these trades (in time order), each stamped at its close, as NinjaTrader's.
	static void Minutes(BarsRequest r, List<long[]> trades)
	{
		for (int i = 0; i < trades.Count; )
		{
			long k = trades[i][0] / 60000 * 60000, v = 0;
			double o = trades[i][1] / 100.0, h = o, l = o, c = o;
			for (; i < trades.Count && trades[i][0] / 60000 * 60000 == k; i++)
			{
				c = trades[i][1] / 100.0;
				h = Math.Max(h, c);
				l = Math.Min(l, c);
				v += trades[i][2];
			}
			r.Bars.Add(At(k + 60000), o, h, l, c, v);
		}
	}

	public static int Main(string[] args)
	{
		Setup();
		return args[0] == "catchup" ? CatchUp(args[1]) : Retries(args[0]);
	}

	static int Retries(string name)
	{
		if (!Waits(1, 50, 20))
		{
			Console.WriteLine("no short waits: an add-on before 23");
			return 0;
		}
		Type ft = typeof(TatankaChartFeed).GetNestedType("Feed", BindingFlags.NonPublic);   // ES forwarded, as at its start
		object f = Activator.CreateInstance(ft, true);
		ft.GetField("Master").SetValue(f, es.MasterInstrument);
		ft.GetField("Current").SetValue(f, es);
		((IList)Get("feeds")).Add(f);
		long m0 = (NowMs() - 600000) / 60000 * 60000;
		bool longer = name.EndsWith("-long") || name == "panic-older";
		long from = longer ? m0 - 600000 : m0 + 10000, to = m0 + 50000;
		List<long[]> five = new List<long[]>();
		for (int k = 0; k < 5; k++) five.Add(new[] { from + 1000 + k * 1000, 700000 + k * 25, 1 + k });
		int n = 0, ticks = 0, minutes = 0;
		BarsRequest.Answer = r =>
		{
			int k = Interlocked.Increment(ref n);
			if (r.BarsPeriod.BarsPeriodType == BarsPeriodType.Tick) ticks++; else minutes++;
			switch (name)
			{
				case "empty-short":
					return ErrorCode.NoError;
				case "empty-long":
					if (r.BarsPeriod.BarsPeriodType == BarsPeriodType.Minute)
						for (int i = 1; i <= 3; i++) r.Bars.Add(At(m0 - i * 60000 + 60000), 7000, 7001, 6999, 7000.5, 10);   // stamped at their close
					return ErrorCode.NoError;
				case "silent":
					return null;
				case "offline":
					if (k <= 4) { DownFor(100); return ErrorCode.Panic; }
					Trades(r, five);
					return ErrorCode.NoError;
				case "mixed":
					if (k <= 2) return ErrorCode.Panic;
					if (k <= 5) { DownFor(100); return ErrorCode.Panic; }
					Trades(r, five);
					return ErrorCode.NoError;
				default:
					return ErrorCode.Panic;
			}
		};
		bool older = name == "panic-older";
		Call("History", "ES", from, to, false, !older, 0, older);
		string z = "none";
		foreach (string line in (BlockingCollection<string>)Get("queue"))
		{
			string l = line.Substring(line.IndexOf('\u0001', 1) + 1);
			if (l.StartsWith("Z|")) z = l;
		}
		Console.WriteLine(n + " " + ticks + " " + minutes + " " + z);
		return 0;
	}

	static bool Waiting()
	{
		lock (NinjaTrader.NinjaScript.AddOnBase.Printed)
			return NinjaTrader.NinjaScript.AddOnBase.Printed.Contains("TatankaChartFeed: history for ES waits for NinjaTrader's data connection");
	}

	static void Raise(MarketData md, DateTime time, double price, long size, int side)
	{
		md.Raise(new MarketDataEventArgs { MarketDataType = MarketDataType.Last, Price = price, Volume = size, Time = time,
			Ask = side > 0 ? price : price + 0.25, Bid = side < 0 ? price : price - 0.25 });
	}

	static void Daily(MarketData md, long volume)
	{
		md.Raise(new MarketDataEventArgs { MarketDataType = MarketDataType.DailyVolume, Volume = volume });
	}

	static int CatchUp(string tape)
	{
		Waits(10, 2000, 300);   // (an add-on before 23: its own, slower)
		List<long[]> history = new List<long[]>(), catchup = new List<long[]>(), live = new List<long[]>(), after = new List<long[]>();
		bool fail = false, noTicks = false, ticksFail = false, daily = false;
		foreach (string line in File.ReadAllLines(tape))
		{
			string[] p = line.Split(' ');
			if (p[0] == "fail") { fail = true; continue; }
			if (p[0] == "no-ticks") { noTicks = true; continue; }
			if (p[0] == "ticks-fail") { ticksFail = true; continue; }
			if (p[0] == "daily") { daily = true; continue; }
			long[] v = Array.ConvertAll(p, s => s.Length == 1 && char.IsLetter(s[0]) ? 0 : long.Parse(s, Inv));
			if (p[0] == "H" || p[0] == "C") (p[0] == "H" ? history : catchup).Add(new[] { v[1], v[2], v[3], v[4] });
			else (p[0] == "L" ? live : after).Add(new[] { 0, v[1], v[2], v[3] });
		}
		List<long[]> tapeTrades = new List<long[]>(history);
		tapeTrades.AddRange(catchup);
		tapeTrades.Sort((x, y) => x[0].CompareTo(y[0]));
		BarsRequest.Answer = r =>
		{
			if (fail) return ErrorCode.Panic;
			if (r.BarsPeriod.BarsPeriodType != BarsPeriodType.Tick) Minutes(r, tapeTrades);
			else if (ticksFail) return ErrorCode.Panic;
			else if (!noTicks) Trades(r, tapeTrades);
			return ErrorCode.NoError;
		};
		data.PriceStatus = ConnectionStatus.Disconnected;   // NinjaTrader starting: the add-on runs before its data connection
		new Thread(() => Call("SendLoop")) { IsBackground = true }.Start();
		MarketData md = null;
		IDictionary held = (IDictionary)Get("held");
		for (int k = 0; k < 200 && md == null; k++)   // the server's I and R lines: ES forwarded, its contract settled
		{
			Thread.Sleep(50);
			IList feeds = (IList)Get("feeds");
			lock (feeds)
				foreach (object f in feeds)
					if ((string)((MasterInstrument)f.GetType().GetField("Master").GetValue(f)).Name == "ES"
						&& !(bool)f.GetType().GetField("QuoteOnly").GetValue(f) && !held.Contains("ES"))
						md = (MarketData)f.GetType().GetField("Data").GetValue(f);
		}
		if (md == null)
		{
			Console.WriteLine("ES never forwarded");
			return 1;
		}
		FieldInfo said = typeof(TatankaChartFeed).GetField("serverVersion", Own);
		for (int k = 0; k < 60 && said != null && (int)said.GetValue(addon) == 0; k++) Thread.Sleep(50);   // its V line
		for (int k = 0; k < 200 && !Waiting(); k++) Thread.Sleep(50);   // ES's GAP read: its history waits for the data
		data.PriceStatus = ConnectionStatus.Connected;   // NinjaTrader's data: its catch-up, at once, then live trades
		long day = 1000000;   // its count of the day's contracts as it starts: the catch-up's among them
		if (daily) Daily(md, day);
		foreach (long[] t in catchup) Raise(md, At(t[0]), t[1] / 100.0, t[2], (int)t[3]);
		foreach (long[] t in live)
		{
			Raise(md, At(NowMs()), t[1] / 100.0, t[2], (int)t[3]);
			if (daily) Daily(md, day += t[2]);
			Thread.Sleep(5);
		}
		Console.WriteLine("ready");
		Console.Out.Flush();
		Console.In.ReadLine();
		foreach (long[] t in after)
		{
			Raise(md, At(NowMs()), t[1] / 100.0, t[2], (int)t[3]);
			if (daily) Daily(md, day += t[2]);
			Thread.Sleep(5);
		}
		BlockingCollection<string> queue = (BlockingCollection<string>)Get("queue");
		for (int k = 0; k < 100 && queue.Count > 0; k++) Thread.Sleep(20);
		Thread.Sleep(600);   // the send loop's last lines out
		lock (NinjaTrader.NinjaScript.AddOnBase.Printed)
			foreach (string s in NinjaTrader.NinjaScript.AddOnBase.Printed)
				if (s.Contains("volume check") || s.Contains("one for one")) Console.WriteLine("printed " + s);
		Console.WriteLine("asked " + BarsRequest.Asked);
		return 0;
	}
}

// NinjaTrader's Tools > Options > General, which the add-on reads by name (NtZone): UTC here.
namespace NinjaTrader.Core
{
	public class Options { public TimeZoneInfo TimeZoneInfo { get { return TimeZoneInfo.Utc; } } }

	public static class Globals
	{
		public static Options GeneralOptions { get { return new Options(); } }
	}
}
