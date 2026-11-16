// Drives the add-on's watchlist quotes for tests/test_hunt13_feeds.py and tests/test_hunt21_free.py: its real send
// loop, connected to the test's server (the add-on compiled with the test's port), and one watchlist future (its W
// line: this month's contract) whose market data is raised as NinjaTrader raises it, 10 times a second, then stopped,
// turned to Market Replay or rolled as the scenario says. Prints "# <what> <PC ms>" marks; the test reads the lines the
// server got.
//   mono quotes.exe <scenario> <root>
//     silent     3 s of data, then none for 7 s (the data connection lost, a weekend), then 3 s of data again
//     playback   3 s of data, then Market Replay connected (its data keeps coming) for 4 s
//     dropped    3 s of data, then NinjaTrader's live data connection drops (nothing comes) for 4 s
//     roll       3 s of data, then NinjaTrader's roll date: the quote moves to the contract 3 months on, while the old
//                one's subscription still delivers its trades for 1.5 s (NinjaTrader removes its handler later, on
//                its own thread), then the new one's data for 2 s
//     reconnect  3 s of data, then the server hangs up (the test's server, on the "#|hang up" line sent here); the
//                add-on connects again 5 s later, its data still coming
using System;
using System.Collections.Concurrent;
using System.Globalization;
using System.Reflection;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript.AddOns;

public static class QuoteHarness
{
	static TatankaChartFeed addon;
	static Instrument inst;
	static double price = 110.5;

	static object Call(string name, params object[] args)
	{
		return typeof(TatankaChartFeed).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance).Invoke(addon, args);
	}

	static void Set(string name, object value)
	{
		typeof(TatankaChartFeed).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).SetValue(addon, value);
	}

	static void Mark(string what) { Console.WriteLine("# " + what + " " + DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()); }

	static Instrument Contract(MasterInstrument master, DateTime month)
	{
		Instrument i = new Instrument { FullName = master.Name + " " + month.ToString("MM-yy", CultureInfo.InvariantCulture),
			MasterInstrument = master, Expiry = new DateTime(month.Year, month.Month, 1) };
		Instrument.Known[i.FullName] = i;
		return i;
	}

	// As the contract's subscription delivers it (Forward's handler, OnQuoteData).
	static void Raise(MarketDataType type, double p, long volume, Instrument i = null)
	{
		Call("OnQuoteData", null, new MarketDataEventArgs { MarketDataType = type, Instrument = i ?? inst, Price = p, Volume = volume });
	}

	// `ms` of market data: the bid and ask move, now and then a trade.
	static void Data(int ms, Instrument i = null)
	{
		for (int k = 0; k < ms / 100; k++)
		{
			if (k % 5 == 0)
			{
				price += 1 / 64.0;
				Raise(MarketDataType.Last, price, 1, i);
				Raise(MarketDataType.DailyVolume, 0, 1000 + k, i);
			}
			Raise(MarketDataType.Bid, price - 1 / 64.0, 10, i);
			Raise(MarketDataType.Ask, price, 12, i);
			Thread.Sleep(100);
		}
	}

	public static int Main(string[] args)
	{
		MasterInstrument master = new MasterInstrument { Name = args[1], TickSize = 1 / 64.0 };
		DateTime now = DateTime.UtcNow;
		inst = Contract(master, now);   // the add-on's AnyContract finds this month's (no roll dates yet: its front month)
		Connection live = new Connection { Status = ConnectionStatus.Connected, PriceStatus = ConnectionStatus.Connected };
		Connection.Connections.Add(live);
		addon = new TatankaChartFeed();
		Set("queue", new BlockingCollection<string>(new ConcurrentQueue<string>(), 200000));
		Set("cts", new CancellationTokenSource());
		new Thread(() => Call("SendLoop")) { IsBackground = true }.Start();
		Call("SetWatch", (object)new[] { args[1] });   // the server's W line: quoted from now
		Raise(MarketDataType.LastClose, 110.25, 0);   // the prior session's close, as NinjaTrader gives it on subscribing
		Mark("live");
		Data(3000);
		switch (args[0])
		{
			case "silent":
				Mark("silent");
				Thread.Sleep(7000);
				Mark("again");
				Data(3000);
				break;
			case "playback":
				Mark("playback");
				Connection.PlaybackConnection = new Connection { Status = ConnectionStatus.Connected, PriceStatus = ConnectionStatus.Connected };
				Data(4000);
				break;
			case "dropped":
				Mark("dropped");
				live.PriceStatus = ConnectionStatus.Disconnected;
				Thread.Sleep(4000);
				break;
			case "roll":
				Instrument next = Contract(master, now.AddMonths(3));
				Dispatcher.Defer = true;   // NinjaTrader's own threads run the subscriptions' changes later
				master.RolloverCollection.Add(new Rollover { Date = now.Date.AddDays(-1), ContractMonth = next.Expiry });
				Call("CheckRolls", (object)null);   // the add-on's check once a minute: its roll date has come
				Mark("roll");
				Data(1500);   // the old contract's trades, its handler still on
				Dispatcher.Pump();
				Mark("new");
				price = 111.5;
				Raise(MarketDataType.LastClose, 111.25, 0, next);
				Data(2000, next);
				break;
			case "reconnect":
				Call("Enqueue", "#|hang up");
				Mark("hang");
				Data(9000);
				break;
		}
		Mark("end");
		return 0;
	}
}
