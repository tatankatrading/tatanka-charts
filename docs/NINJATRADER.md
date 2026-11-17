# NinjaTrader

The NinjaTrader 8 add-on that sends every trade (and your fills) to the app. Other feeds: [FEEDS.md](FEEDS.md).

## Updating the add-on

`update.cmd` copies it in with the rest (README, Updating). By hand: **Connections > NinjaTrader > Download the
add-on** (it has the server's Token in it, when one is set), put it in `Documents\NinjaTrader 8\bin\Custom\AddOns\`
(replace the old one), then New > NinjaScript Editor, F5. When the add-on running in NinjaTrader is older than the
server, its card turns yellow and says so.

## The NinjaTrader feed

`ninjatrader/TatankaChartFeed.cs` is a NinjaTrader 8 add-on that forwards every live trade (price, size,
buy/sell side from bid/ask) to this server over `127.0.0.1:8801`, and your fills and positions for the journal
([CHARTS.md](CHARTS.md#your-fills-and-the-journal)). Read-only: it places no orders and changes nothing in any account.

Setting it up:

1. Off the simulator: **Leave the simulator**, in the setup guide on the first run or on Connections > NinjaTrader
   while the simulator runs (README, First run). It deletes the simulator's invented trades and the prices it made
   up for your stock and formula alerts, and restarts on NinjaTrader.
2. Copy `ninjatrader\TatankaChartFeed.cs` to `Documents\NinjaTrader 8\bin\Custom\AddOns\` (or **Download the
   add-on** on Connections > NinjaTrader, which has the server's Token in it).
3. In NinjaTrader, connected to your data feed: New > NinjaScript Editor, press F5. The bottom of the editor must
   show no errors. It starts right away and with NinjaTrader from then on.
4. New > NinjaScript Output should show `TatankaChartFeed: forwarding ES 12-26` (one line per market) and
   `TatankaChartFeed: connected to 127.0.0.1:8801`.
5. Check http://127.0.0.1:8800/api/health: `feed_status.ninjatrader.connected` is true and `ticks` climbs while the
   market trades; the chart's last price matches NinjaTrader's.
6. History: the add-on fills the last 90 days by itself (below), the rest of the last year in the background, and anything
   older as a chart is scrolled back (add-on 20, Older history below), as far as your data provider's history goes.
   A backfill or a Databento import ([DATA.md](DATA.md)) is for what it doesn't have.

Over a weekend (Friday 17:00 to Sunday 16:00 CT) and on a closed CME holiday (Christmas, New Year's Day, Good Friday
in `config/holidays.json`: from 17:00 the evening before) no future trades, so a trade line stamped then is left out:
NinjaTrader replays its last trade when it connects, and the add-on stamps it with the PC's clock, which made a
Saturday (or Christmas) bar of one price on the daily charts. Such prints stored before this was in are taken out
once, in the background after the next start (feed plugins' trades the same; not the simulator's, which leaving it
deletes anyway). Add-on 18 leaves that replay out itself, by NinjaTrader's own time for the trade (over 20 minutes
old when it comes), so a reconnect in the 16:00-17:00 break no longer opens the next session at the last one's close;
when several trades in a row look that old, seconds apart, it is the PC's clock that is off, and they go (add-on 19:
replays at separate reconnects, hours apart over a weekend or a holiday, never count as that). Add-on 21 also leaves
out the replay after a data outage of a few minutes: once a minute of a market's trades has shown its own clock
against the PC's, a trade over 2 minutes old by that clock is a replay (before, one under 20 minutes went as a print
at the old price). Each market has its own clock, so one on delayed data keeps all its trades. Add-on 22 also tells a
replay by the trade's own time: NinjaTrader's replay repeats the last trade, so a trade newer than any before on its
contract is not one, and goes if it is under 20 minutes old by its market's clock (before, after the PC's clock was
set a few minutes forward or NinjaTrader delivered trades late, the first three were lost, and a quiet market lost all
of them). Replays no longer set a market's clock, so data reconnects a minute or two apart no longer let older and
older replays through. It takes no settlement, prior close or day volume from Market Replay or the Simulated Data Feed
either: a replayed day's settlement replaced the real one in the watchlist's change. At a contract roll in the 16:00
CT break, NinjaTrader's replay of the new contract's last trade is left out by the server (any add-on), not put in the
old contract's last minute, which gave the roll day's close the new contract's price. Add-on 22 sends a roll made
while the server was away after the old contract's last trades queued before it, so those stay in their session
(before, it sent the new contract's name first on connecting, and the server left them out as after the roll). It also
marks a settlement NinjaTrader held: its first after subscribing or after NinjaTrader's data connection was down.
After a day missed that one may be the day before's; a change heard live is the day's. One heard live while the
server was away goes unmarked if the server is back within 30 minutes, and marked held if later (it may be an earlier
day's by then). On connecting, the add-on sends each settlement again the same way, unless its line is still queued.

One add-on at a time: a second NinjaTrader running it (another Windows user on the PC) is turned away while the first
is connected (`another NinjaTrader add-on is connected to the server; trying again in 30 s`) and takes over, gap fill
and all, once the first closes. One whose `Token` is not the server's (`CHART_NT_TOKEN`, or the Token field in
Connections > NinjaTrader) is turned away the same way (`wrong token: ...`), and the card says so.

**Your fills and positions** (for the journal): every account's fills as they happen, and the session's again on
each connect; NinjaTrader's position for a contract a second after its last change (at most 5 s after its first,
once 0.2 s pass with no change, so both events of a fill are in; 10 s if changes never stop), and every open one on
each connect. A position's time is on the clock of that account's own fills (add-on 14), measured on its live fills:
the simulator stamps a Sim account's fills with the PC's clock and a broker with its own, and neither need be the
market data's, so a PC clock seconds off the exchange no longer splits a trade into "exit unseen" rows. It is put
2.25 s early, the longest a position waits to be read, but never before a fill of that contract already sent or the
account's last connect (add-on 15): a fill made before a position was read but reported a second or two later (a fast
market) still comes after it, also when the account filled another contract in between (add-on 16; 15 put the
position at that other fill's time, after the one it did not count). Until the account's first fill since the add-on
started, the PC's clock or the market data's, whichever is earlier.

**Saved going forward**: every trade the add-on sends is stored (with its buy/sell side) along with the 1-minute
bars (roughly 100-150 MB a day for six markets). Trades are kept 5 years and 1-minute bars always (Connections >
Backups, Keep each trade). Gaps fill themselves: when the
add-on connects (NinjaTrader started, the server restarted, the PC back on), the server tells it where each
symbol's stored data stops, and the add-on fetches that stretch from NinjaTrader's own history: trades (side
unknown, so footprints use the tick rule there), or 1-minute bars when the gap is over 3 days or the data provider
keeps no tick history. Only the missing stretch is kept, so nothing is counted twice; open charts reload with it.
History stamped while the market is shut (the 16:00 break, a weekend, a holiday) is left out: NinjaTrader's 1-minute
history has a bar after the close holding the session's whole volume at the settlement price, which would put a day's
volume in one minute on every chart and volume profile. Such bars stored before this was in are taken out once, at
the next start.
NinjaScript Output shows `filling ES 12-26 ... from NinjaTrader's trades` and `sent N trades of ES history`;
`/api/health` shows `gap_fill` per symbol. It reaches back 90 days (a 2-month absence comes back whole), and a
future with nothing stored yet (one just added in Instruments) gets its last 90 days, as 1-minute bars. A stretch
across contract rolls comes from each contract in turn, switching at NinjaTrader's roll dates (Tools > Instruments >
the symbol > Contract months): before a roll the old contract's prices, as charted then. Anything older than 90 days
comes from the backfill instead.

History comes from your data provider, not only from what NinjaTrader recorded itself while it ran (add-on 7);
where the provider has no trades for a stretch (tick history often reaches back only a few days), it is asked again
as 1-minute bars. If NinjaTrader's own 1-minute chart has no bars there either, the provider has none: import that
stretch from Databento instead (DATA.md).

NinjaTrader starts the add-on before its data connection is up, so the add-on (version 8) waits for the connection
before it asks for any history (`history for ES waits for NinjaTrader's data connection`), then asks right up to its
first live trade: a NinjaTrader restart leaves no hole, however long the connection takes. Holes inside the stored
data of the last 7 days (5 or more minutes with no bar while the market was open; 30 for a quiet market) are found on
each connect, and when a market ticks again after a Feed stale alert with the add-on still connected (NinjaTrader's
data connection lost and back), and filled the same way, one at a time, each once: if NinjaTrader has no history for
one, it stays as it is. Never from before the market's last contract roll (the history would be the other
contract's); and each is asked from 3 minutes before it, so if NinjaTrader's prices there don't match the stored ones
(a roll before this version kept roll times), the answer is dropped.

The minute the live trades start in holds the history's trades from before them too (before, that minute's bar was
short of them at every reconnect, though the trades themselves were stored): the live feed's own bar is changed in
place, or the stored one once the live feed has moved on.

**NinjaTrader started mid-session** raises a one-time catch-up of the last trades as live ones, and the add-on sends
them. Raised with its last trade first, or newest first, the history (asked up to the first one's second) holds the
others again, and they were stored twice. Add-on 23 marks each trade older than that second with NinjaTrader's own
time for it, and the server keeps one of each, at the history's (the exchange's) time with the live trade's buy/sell
side, in the exchange's order (several trades in one millisecond too, however the catch-up came), and builds those
minutes' bars again from their trades, stored and the live feed's own (open charts reload). That takes add-on 23 and a
server that says it takes the mark (this one, on connecting; to an older server the add-on sends its trades unmarked,
as before); the marked trades the add-on sent in the first 30 s after the first live trade (by its stamps, not the
stored times: a catch-up raised in the 16:00 CT break, whose trades are stored in a session, is compared too); and a run
of 50 or more of them matching the history trade for trade (price and size), in the same or the reverse order, with at
least 5 different prints in it. Raised oldest first, the history stops before the catch-up, so nothing comes twice:
those trades keep the time the add-on sent them at (in the break, as any trade stamped then: before 16:30 the closing
session's last millisecond, from 16:30 the next 17:00 open). When the history fails, nothing is compared and nothing is
lost. `/api/health` shows how many were kept once as `copies` under `gap_fill` (the server's log says so too). The
add-on's volume check in NinjaScript Output still counts the catch-up, so it warns after such a start all the same
(see [CONNECTIONS.md](CONNECTIONS.md)): that warning does not mean those trades were stored twice.

Still stored twice:
- a shorter run (a quiet market, one price and size again and again, never matches);
- marked trades that come after the history's answer, or sent 30 s or more after the first live trade;
- of a catch-up of more than 20,000 trades, those before the history's last 20,000 (only those are held back to
  compare; the history writes the older ones as they come);
- trades the add-on queued before the server's version line came on that connection (a catch-up raised while the
  add-on was not yet connected to the server): it sends them unmarked;
- catch-up trades from before where the stored data ended (the history is asked from there, so it never holds them);
- the add-on reconnecting before the history's answer: a stretch of 5 minutes or more of open market with no
  1-minute bar comes back as a hole, which is not compared; a shorter one is not asked again, so nothing in it is
  stored twice, but its trades from before the catch-up never come (as before add-on 23);
- live trades still waiting to be written when the history's answer comes (the database not writable just then): both
  kept, nothing lost;
- a history answered as 1-minute bars: a stretch over 3 days (the add-on asks it as bars from the start: NinjaTrader
  closed Friday at noon and started Monday afternoon, or after a long weekend), a data provider with no tick history,
  or trades failing where bars work. Bars have no trade to pair, so nothing is compared: the catch-up's trades are
  stored once, at the add-on's stamps, but count twice in the bars, in the history's bars of their own minutes and
  again in the live minute's;
- everything from add-on 22 or older.

When NinjaTrader answers a history request with an error (on Nov 16 it answered every one with `Panic` until it was
restarted), add-on 23 asks twice more each way it asks, 30 s apart, then answers the error: a stretch with a whole
minute in it is asked 3 times as trades, then 3 times as 1-minute bars, 6 requests in all (before, 11 and 11: 11
minutes a symbol); older history (1-minute bars only) 3 times. No answer, or an error while NinjaTrader's data
connection is down, is still asked again 10 times. A stretch with no whole minute in it (a short reconnect's) is not
asked again as 1-minute bars, which could fill none of it: after its 3 tries as trades the error is the answer, and no
trades there is answered as no trades.

**Older history** (add-on 20): the 1-minute bars from before anything stored, also from NinjaTrader, nothing to run.
Once a future's gap and holes are filled, the server asks for the month before its first stored minute, then the
month before that, until it has a year (`CHART_HISTORY_PREFILL_DAYS` in `.env`, 365; 0 turns it off), one request at a
time for all futures, after any other history. A chart scrolled back past the stored minutes asks at once, as many
months as it needs (up to 6 a time), ahead of that ("loading older bars from NinjaTrader…"), so it keeps going as far
back as the data provider's history does. Each part comes from its own contract by NinjaTrader's roll dates (Tools >
Instruments > the symbol > Contract months), however old, none from before the first roll date NinjaTrader lists. A
month that comes back empty is where the provider's history ends: the chart says so, and it isn't asked again for a
week. Answered with an error (no data connection, NinjaTrader's `Panic`): asked again 10 minutes later, then 20, 40,
and from then on every hour, and at once when the add-on connects again (NinjaTrader restarted). An error is never
taken as where the history ends (before, three in a row were, and nothing older was asked for a week). Not answered
in 6 minutes (NinjaTrader's data connection down, a slow download): the other futures go on, and that one's answer is
kept whenever it comes; nothing else is asked for that future meanwhile, so answers never mix. A chart waiting at its
first bar says NinjaTrader didn't send older history and asks again as soon as it does.
Charts don't reload for it (they load the older bars as they are scrolled back); a future's studies (AMT Toolkit,
Daily SMA x4, TPO's days...) load again once its older months are in, if any are from the last 400 days. About 50-70
MB per future per year. NinjaScript Output shows `filling ES 06-26 ... from NinjaTrader's 1-minute bars`;
`/api/health` shows it under `gap_fill` (`"older": true`). An add-on before 20 isn't asked: the stored history is
all there is.

Which futures: the server's list (**Connections > App > Instruments**, [CONNECTIONS.md](CONNECTIONS.md#instruments)). On connect the server tells the add-on
which roots it charts and the add-on forwards each one's trades; its own `Contracts` list (ES, NQ, CL, GC, MES, MNQ)
is forwarded from its start. A root the server's list leaves out (one of those, or one taken off Instruments while
NinjaTrader runs) is quoted only from then, as a watchlist future, and the add-on names its quote's contract at each
roll, so a price alert on it never fires on the jump between contracts.
