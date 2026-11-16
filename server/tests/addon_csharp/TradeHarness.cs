// Drives the add-on's trades for tests/test_addon_replay.py: its real OnMarketData and send loop, connected to the
// test's server (the add-on compiled with the test's port), with Last events as NinjaTrader raises them.
//   mono trades.exe <scenario> <root>
//     replay   the last trade replayed on subscribing (its own time 40 minutes back), then two live trades
//     skewed   six trades whose own time is 30 minutes behind the PC's clock (a PC clock that is off)
//     live     three live trades
//     reconnects  a live trade, then NinjaTrader's replay of it at 4 reconnects, hours apart (Friday 16:40 to Sunday 16:40)
//     lull     a PC clock 30 minutes off: a burst of four trades, a minute with none, then two more
//     zone     a thousand live trades, then one more after a minute: prints how often NinjaTrader's zone was read
//     outage   a minute of live trades (the market's clock known), the last trade replayed after a 5-minute outage, a live one
//     delayed  a market on delayed data, every trade 10 minutes behind: a minute of them, then two more
//     ratchet  its clock known, the last live trade then replayed at 5 data reconnects, 100 s apart (the clock learnt
//              from each replay let through, so the next, older one went too)
//     thin     its clock known, then the PC's clock set 3 minutes forward and a quiet market's trades, 10 s apart
//     stall    its clock known, then NinjaTrader delivers 5 trades 150 s late, 50 ms apart, then one on time
//     playback the market's prior close, day volume and settlement, then Market Replay connected with its own (prints
//              the prior close, day volume, the next contract's volume and the settlement kept), then the live
//              settlement again
//     settleheld  (the server away until the end) subscribed: NinjaTrader's settlement, then a change heard live;
//              NinjaTrader's data connection down at a minute's check, back with the one it held, then a change; down
//              again, back with the same one (unchanged), then a change
//     rollqueued  (the server away until the end) the market's trades on 12-26, its roll to 03-27, then NinjaTrader's
//              replay of 03-27's last trade: all queued, then the server connects
//     settletoday  (the server away until the end) NinjaTrader's settlement on subscribing, then a change heard live:
//              both queued, then the server connects
//     settledayago  the same, then a day passes before the server connects
//     settlelost  the same as settletoday, but the lines were written into a connection that was closing: lost
//     settlelostold  the same as settlelost, then a day passes before the server connects
//     leftover  (the server away until the end) charted (the server's I line, R by dates, W), then the server restarts
//              with it taken off Instruments, still on a watchlist (I without it, W with it), then NinjaTrader's roll
//              date; once connected, prints the C lines a connect would name it with (QuoteContracts)
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript.AddOns;

public static class TradeHarness
{
	static TatankaChartFeed addon;
	static Instrument inst;

	static object Call(string name, params object[] args)
	{
		return typeof(TatankaChartFeed).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(addon, args);
	}

	static void Set(string name, object value)
	{
		typeof(TatankaChartFeed).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(addon, value);
	}

	// As if the PC's clock moved on this far since the last trade that looked old (the add-on's own clock is the PC's),
	// and the market's since its trades so far (add-on 22: the newest own time of each of its contracts').
	static void Later(string root, double minutes)
	{
		long by = (long)(minutes * 60000);
		FieldInfo f = typeof(TatankaChartFeed).GetField("replayAt", BindingFlags.NonPublic | BindingFlags.Instance);
		if (f == null) return;   // an add-on before 19: no such clock
		ConcurrentDictionary<string, long> at = (ConcurrentDictionary<string, long>)f.GetValue(addon);
		long t;
		if (at.TryGetValue(root, out t)) at[root] = t - by;
		FieldInfo n = typeof(TatankaChartFeed).GetField("newestAt", BindingFlags.NonPublic | BindingFlags.Instance);
		if (n == null) return;   // an add-on before 22
		ConcurrentDictionary<string, long> newest = (ConcurrentDictionary<string, long>)n.GetValue(addon);
		foreach (string k in newest.Keys)
			if (k.StartsWith(root + " ")) newest[k] -= by;
	}

	// As if a minute of the market's trades had come (add-on 21: its clock is known from the next one on).
	static void Minute(string root)
	{
		FieldInfo f = typeof(TatankaChartFeed).GetField("clocks", BindingFlags.NonPublic | BindingFlags.Instance);
		if (f == null) return;   // an add-on before 21: no such clock
		object clocks = f.GetValue(addon);
		object ck = clocks.GetType().GetProperty("Item").GetValue(clocks, new object[] { root });
		ck.GetType().GetField("Until").SetValue(ck, 1L);
	}

	static void Trade(double price, double minutesBack)
	{
		TradeAt(price, DateTime.Now.AddMinutes(-minutesBack));
	}

	static void TradeAt(double price, DateTime time)
	{
		Call("OnMarketData", null, new MarketDataEventArgs { MarketDataType = MarketDataType.Last, Instrument = inst, Price = price,
			Volume = 2, Time = time });
		Thread.Sleep(50);
	}

	static void Raise(string handler, MarketDataType type, double price, long volume)
	{
		Call(handler, null, new MarketDataEventArgs { MarketDataType = type, Instrument = inst, Price = price, Volume = volume });
	}

	// A market the add-on forwards, as at NinjaTrader's start (its first contract): its subscription.
	static MarketData Feed(string contract, out object f)
	{
		Type t = typeof(TatankaChartFeed).GetNestedType("Feed", BindingFlags.NonPublic);
		f = Activator.CreateInstance(t, true);
		t.GetField("Master").SetValue(f, inst.MasterInstrument);
		IList feeds = (IList)typeof(TatankaChartFeed).GetField("feeds", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(addon);
		lock (feeds) feeds.Add(f);
		return Forward(f, contract);
	}

	// Its subscription moved to `contract` (a roll).
	static MarketData Forward(object f, string contract)
	{
		Instrument i = new Instrument { FullName = contract, MasterInstrument = inst.MasterInstrument, Expiry = inst.Expiry };
		IList feeds = (IList)typeof(TatankaChartFeed).GetField("feeds", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(addon);
		lock (feeds) Call("Forward", f, i);
		return (MarketData)f.GetType().GetField("Data").GetValue(f);
	}

	// NinjaTrader's data connection down at the add-on's minute check (add-on 22), then back.
	static void DataDown(Connection data)
	{
		data.PriceStatus = ConnectionStatus.Disconnected;
		MethodInfo m = typeof(TatankaChartFeed).GetMethod("CheckData", BindingFlags.NonPublic | BindingFlags.Instance);
		if (m != null) m.Invoke(addon, null);   // (none before 22)
		data.PriceStatus = ConnectionStatus.Connected;
	}

	static void Settle(MarketData md, double price)
	{
		md.Raise(new MarketDataEventArgs { MarketDataType = MarketDataType.Settlement, Price = price });
	}

	static BlockingCollection<string> Queue()
	{
		return (BlockingCollection<string>)typeof(TatankaChartFeed).GetField("queue", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(addon);
	}

	// The lines queued so far written into a connection that was closing: gone (the server never read them).
	static void Lost()
	{
		string line;
		while (Queue().TryTake(out line)) { }
	}

	// As if the PC's clock moved on this far since the settlements heard so far (add-on 22: when each was heard, kept
	// by symbol, and on the queued line of one heard live).
	static void SettleLater(string root, double minutes)
	{
		long by = (long)(minutes * 60000);
		FieldInfo f = typeof(TatankaChartFeed).GetField("settleSeen", BindingFlags.NonPublic | BindingFlags.Instance);
		if (f == null) return;   // an add-on that keeps no such time
		ConcurrentDictionary<string, KeyValuePair<long, bool>> seen = (ConcurrentDictionary<string, KeyValuePair<long, bool>>)f.GetValue(addon);
		KeyValuePair<long, bool> s;
		if (seen.TryGetValue(root, out s)) seen[root] = new KeyValuePair<long, bool>(s.Key - by, s.Value);
		List<string> lines = new List<string>();
		string line;
		while (Queue().TryTake(out line)) lines.Add(line);
		foreach (string l in lines)
		{
			int end = l.Length > 0 && l[0] == '\u0002' ? l.IndexOf('\u0002', 1) : -1;
			Queue().Add(end < 0 ? l : "\u0002" + (long.Parse(l.Substring(1, end - 1), CultureInfo.InvariantCulture) - by).ToString(CultureInfo.InvariantCulture) + l.Substring(end));
		}
	}

	static Instrument Contract(MasterInstrument master, DateTime month)
	{
		Instrument i = new Instrument { FullName = master.Name + " " + month.ToString("MM-yy", CultureInfo.InvariantCulture),
			MasterInstrument = master, Expiry = new DateTime(month.Year, month.Month, 1) };
		Instrument.Known[i.FullName] = i;
		return i;
	}

	static void StartSending()
	{
		new Thread(() => Call("SendLoop")) { IsBackground = true }.Start();
	}

	static object Field(string name, string key)
	{
		object d = typeof(TatankaChartFeed).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(addon);
		object[] args = { key, null };
		bool had = (bool)d.GetType().GetMethod("TryGetValue").Invoke(d, args);
		return had ? args[1] : "none";
	}

	public static int Main(string[] args)
	{
		inst = new Instrument { FullName = args[1] + " 12-26", MasterInstrument = new MasterInstrument { Name = args[1], TickSize = 0.25 },
			Expiry = new DateTime(2026, 12, 1) };
		Connection.Connections.Add(new Connection { Status = ConnectionStatus.Connected, PriceStatus = ConnectionStatus.Connected });
		addon = new TatankaChartFeed();
		Set("queue", new BlockingCollection<string>(new ConcurrentQueue<string>(), 200000));
		Set("cts", new CancellationTokenSource());
		if (!args[0].StartsWith("settle") && args[0] != "rollqueued" && args[0] != "leftover") StartSending();   // (those: the server away)
		switch (args[0])
		{
			case "settleheld":
			{
				object f;
				MarketData md = Feed(args[1] + " 12-26", out f);
				Connection data = Connection.Connections[0];
				Settle(md, 112.0);    // NinjaTrader's on subscribing
				Settle(md, 112.25);   // published, heard live
				DataDown(data);
				Settle(md, 112.5);    // the one it held as its data came back (a day missed meanwhile)
				Settle(md, 113.0);    // published, heard live
				DataDown(data);
				Settle(md, 113.0);    // back with the same: nothing to send
				Settle(md, 113.25);   // published, heard live
				StartSending();
				break;
			}
			case "rollqueued":
			{
				object f;
				MarketData md = Feed(args[1] + " 12-26", out f);
				for (int k = 0; k < 3; k++)
				{
					md.Raise(new MarketDataEventArgs { MarketDataType = MarketDataType.Last, Price = 6900 + k * 0.25, Volume = 1, Time = DateTime.Now });
					Thread.Sleep(5);
				}
				MarketData next = Forward(f, args[1] + " 03-27");
				next.Raise(new MarketDataEventArgs { MarketDataType = MarketDataType.Last, Price = 6962.50, Volume = 1, Time = DateTime.Now.AddSeconds(-40) });
				StartSending();
				break;
			}
			case "settletoday":
			case "settledayago":
			case "settlelost":
			case "settlelostold":
			{
				object f;
				MarketData md = Feed(args[1] + " 12-26", out f);
				Settle(md, 96.36);   // NinjaTrader's on subscribing
				Settle(md, 96.37);   // published, heard live
				if (args[0].StartsWith("settlelost")) Lost();
				if (args[0] == "settledayago" || args[0] == "settlelostold") SettleLater(args[1], 24 * 60);
				StartSending();
				break;
			}
			case "leftover":
			{
				DateTime now = DateTime.UtcNow;
				MasterInstrument master = inst.MasterInstrument;
				Instrument cur = Contract(master, now), next = Contract(master, now.AddMonths(2));
				master.RolloverCollection.Add(new Rollover { Date = now.Date.AddDays(-20), ContractMonth = cur.Expiry });
				Call("SetCharts", (object)new[] { args[1] });   // charted (the server's I line)
				Call("SetMode", args[1], "dates", "");          // its R line: released, its contract named
				Call("SetWatch", (object)new[] { args[1] });    // on a watchlist too
				Call("SetCharts", (object)new string[0]);       // the server restarted: taken off Instruments
				Call("SetWatch", (object)new[] { args[1] });    // still on the watchlist
				master.RolloverCollection.Add(new Rollover { Date = now.Date.AddDays(-1), ContractMonth = next.Expiry });
				Call("CheckRolls", (object)null);               // NinjaTrader's roll date (the add-on's check once a minute)
				StartSending();
				Thread.Sleep(1000);
				Console.WriteLine(string.Join(" ", ((List<string>)Call("QuoteContracts")).ToArray()));
				break;
			}
			case "replay":
				Trade(6905.25, 40);
				Trade(6910.50, 0);
				Trade(6910.75, 0);
				break;
			case "skewed":
				for (int k = 0; k < 6; k++) Trade(6900 + k * 0.25, 30);
				break;
			case "live":
				for (int k = 0; k < 3; k++) Trade(6920 + k * 0.25, 0);
				break;
			case "reconnects":
				Trade(6930.00, 0);
				Trade(6930.25, 41);
				Later(args[1], 10 * 60 + 20);
				Trade(6930.50, 11 * 60);
				Later(args[1], 18 * 60);
				Trade(6930.75, 29 * 60);
				Later(args[1], 19 * 60 + 41);
				Trade(6931.00, 48 * 60 + 41);
				Trade(6931.25, 0);
				break;
			case "lull":
				for (int k = 0; k < 4; k++) Trade(6940 + k * 0.25, 30);
				Later(args[1], 1);
				Trade(6941.00, 31);
				Trade(6941.25, 31);
				break;
			case "outage":
				Trade(6960.00, 0);
				Minute(args[1]);
				Trade(6960.25, 0);
				Trade(6960.50, 5);
				Trade(6960.75, 0);
				break;
			case "delayed":
				for (int k = 0; k < 3; k++) Trade(6970 + k * 0.25, 10);
				Minute(args[1]);
				Trade(6971.00, 10);
				Trade(6971.25, 10);
				break;
			case "ratchet":
				Trade(6900.00, 0);
				Minute(args[1]);
				DateTime last = DateTime.Now;
				TradeAt(6900.25, last);   // closes a minute of the market's clock: known, and nothing in the next yet
				for (int k = 1; k <= 5; k++)
				{
					Later(args[1], 100 / 60.0);
					Minute(args[1]);   // over a minute since: this one ends the clock's minute
					TradeAt(6900 + k, last.AddSeconds(-100 * k));   // its replay, 100 s older each time
				}
				break;
			case "thin":
				Trade(6980.00, 0);
				Minute(args[1]);
				Trade(6980.25, 0);
				Later(args[1], 3.5);
				for (int k = 0; k < 5; k++)
				{
					if (k > 0) Later(args[1], 10 / 60.0);
					Trade(6981 + k * 0.25, 3);
				}
				break;
			case "stall":
				Trade(6990.00, 0);
				Minute(args[1]);
				Trade(6990.25, 0);
				Later(args[1], 2.5);
				for (int k = 0; k < 5; k++) Trade(6991 + k * 0.25, 2.5);
				Trade(6992.50, 0);
				break;
			case "playback":
				Raise("OnMarketData", MarketDataType.LastClose, 6890.00, 0);
				Raise("OnMarketData", MarketDataType.DailyVolume, 0, 1000);
				Raise("OnMarketData", MarketDataType.Settlement, 6900.25, 0);
				Trade(6905.00, 0);
				Connection.PlaybackConnection = new Connection { Status = ConnectionStatus.Connected, PriceStatus = ConnectionStatus.Connected };
				Raise("OnMarketData", MarketDataType.LastClose, 6870.00, 0);
				Raise("OnMarketData", MarketDataType.DailyVolume, 0, 50);
				Raise("OnMarketData", MarketDataType.Settlement, 6880.50, 0);
				Raise("OnQuoteData", MarketDataType.Settlement, 6881.00, 0);
				Call("OnNextData", null, new MarketDataEventArgs { MarketDataType = MarketDataType.DailyVolume,
					Instrument = new Instrument { FullName = args[1] + " 03-27", MasterInstrument = inst.MasterInstrument }, Volume = 99999 });
				Trade(6885.00, 0);
				Console.WriteLine(Field("priorClose", args[1]) + " " + Field("dayVolume", args[1]) + " " + Field("nextVolume", args[1] + " 03-27")
					+ " " + Field("settlement", args[1]));
				Connection.PlaybackConnection = null;
				Raise("OnMarketData", MarketDataType.Settlement, 6900.25, 0);   // live again: the same as before it
				break;
			case "zone":
				var stamp = typeof(TatankaChartFeed).GetField("ntZoneAt", BindingFlags.NonPublic | BindingFlags.Static);
				for (int k = 0; k < 1000; k++)
					Call("OnMarketData", null, new MarketDataEventArgs { MarketDataType = MarketDataType.Last, Instrument = inst,
						Price = 6950 + (k % 2) * 0.25, Volume = 1, Time = DateTime.Now });
				int once = NinjaTrader.Core.Globals.Reads;
				if (stamp != null) stamp.SetValue(null, (long)stamp.GetValue(null) - 60000);   // a minute on (none before 19)
				Trade(6951.00, 0);
				Console.WriteLine(once + " " + NinjaTrader.Core.Globals.Reads);
				break;
		}
		Thread.Sleep(1500);   // the send loop's last lines out
		return 0;
	}
}

// NinjaTrader's Tools > Options > General, which the add-on reads by name (NtZone): its time zone, the PC's here.
namespace NinjaTrader.Core
{
	public class Options { public TimeZoneInfo TimeZoneInfo { get { return TimeZoneInfo.Local; } } }

	public static class Globals
	{
		public static int Reads;   // how often the add-on read them
		static readonly Options options = new Options();
		public static Options GeneralOptions { get { Interlocked.Increment(ref Reads); return options; } }
	}
}
