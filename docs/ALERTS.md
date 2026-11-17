# Alerts and Discord

Price, level, If/Then plan, drawing, indicator and news alerts, and where they go: app notifications, Discord, ntfy.

## Alerts

All alerts are evaluated on the server on every tick, so they fire with no chart open. Setting one works as on
TradingView:

On a stock's, an index's, an economic series' or a formula's chart (`AAPL`, `VIX`, `FRED:DGS10`, `VIXM/VIXY`) only
price alerts can be set (the dialog offers Price alone; the watchlist's **Create alert…** too): they run on the
symbol's quotes, as the watchlist shows them, so as often as its feed quotes it (IBKR or Alpaca live, Yahoo every
15 s and delayed, FRED and EIA every 15 minutes). A formula's is worked out from its parts' last quotes (a live
future's from its last trade) each time one moves. The symbol is quoted while its alert is on, listed or not.
Level, study, If/Then, indicator and drawn-line alerts need a live future's trades.

Each value is checked when it arrives. A FRED or EIA value is dated by its observation, often days or weeks before it
is published: its alert fires, expires and is stamped at the time the value came in. A new, edited or restarted alert
(**Restart** after it fired Only once, or after a pause) is checked at once on the value shown now, so **Greater than**
or **Less than** already true fires then, not at the next value that differs (weeks away for a monthly series). An
alert turned back on, or set again, starts afresh: a cross while it was off never fires it. A cross while the server
was restarting does fire on the first value after, as for a future, unless the symbol or formula has a future only
quoted (on a list, not charted): its contract may have rolled meanwhile, so its first value after is only where the
alert looks from.

A future's roll crosses nothing. For a future only quoted, NinjaTrader says when its quote rolls (by NinjaTrader's roll
dates). Add-on 22 also names the contract it quotes each time it connects, so a roll while NinjaTrader was closed
counts too, and it sends none of the old contract's prices after the roll. Yahoo and IBKR never say when their quote of
a future rolls, so an alert on one only they quote can fire on the jump. A formula with a rolled future waits for the
new contract's first trade (a quote-only one's first price), so no other part's move is set against the old
contract's last price.

A stock whose ticker is also a future's root (MetLife's MET, Micro Ether's MET) is the future's once that future runs
live here, and the stock is quoted no more. Its alert, alone or in a formula, is then paused with a notice, and can't
be turned back on: set it again on the future, or delete it. A future added while the server runs pauses it at once.
One added while it was off (an Instruments change that needs a restart, a restored backup, an update) pauses it at
the next start, and the notice comes as the server starts.

- **Quick alert**: right-click the chart (or hover it and click the **+** just left of the price axis) and pick
  **Add alert at …**: price crossing that level, once. `+ Alert` in the header then a tap (Esc or a right-click
  backs out), or Alt+click, does the same. Phone: hold a finger on the chart until the crosshair shows, lift it, then tap the **+** (it stays 3 s; the crosshair
  ends when the finger lifts, so a drag after it always moves the chart).
- **Create alert…** (same menu, **Alt+A**, or **+ Create alert** in the Alerts tab) opens the dialog:
  - **Condition**: Price, Session VWAP, RTH VWAP, POC, VAH, VAL, Daily levels, If/Then bands (your If/Then plan,
    below), or an indicator (the **Indicators** groups below them).
  - For price: Crossing, Crossing up, Crossing down, Greater than, Less than (fires while price is past it),
    Entering channel, Exiting channel (two prices).
  - For a VWAP: the line or a ±1/2/3σ band. For POC/VAH/VAL: the ETH or RTH profile, developing or previous (this
    app's own 70% session profiles, not the Tatanka Session Volume Profile).
    Studies and daily levels fire on a touch within *Touch within* ticks, then re-arm once price has moved **Re-arm
    after** ticks (8 unless you change it) beyond that band. The levels are the ones the chart draws, kept running on
    the server. New daily levels (pasted again, or a new levels file or link) leave each level still at its price as
    it was, even renamed (R1 is R2 once a level is added below it). So one that just fired stays quiet until price has
    moved that far beyond it. A new or moved level starts from the next trade, so one that lands on price never fires
    for that.
  - **Indicator alerts**, as TradingView's: any indicator with a line (EMA, SMA, RSI, MACD, Stochastics, Bollinger
    Bands, CCI, ADX and the rest), with its own settings and the line to watch (MACD or its signal line, a band). It
    runs on its own timeframe, 1m to 1D, whatever the chart shows (on regular hours when the chart is set to RTH).
    It is compared with the price, a value, or another of its lines: an oscillator starts at its overbought level
    (RSI 70), MACD and Stochastics at their signal line, the rest at the price. **Check**: **Bar close** (when each
    bar closes; TradingView's Once Per Bar Close) or **Intrabar** (the first trade that meets it; Once Per Bar). The
    server uses the chart's formulas (NinjaTrader's), so an alert fires on the value the chart shows, with no chart
    open.
  - **Trigger**: how often it fires.
    - **Only once**: it stops after firing (a Daily levels or If/Then alert stops after its first level or row).
    - **Every time**: price alerts at most once a minute (moved or restarted, one starts afresh: its next cross
      fires), indicator alerts at most once a bar, levels, studies and If/Then rows each time price touches again
      after it has re-armed.
    - **Once a minute** (not offered for a price alert, whose Every time is just that), **Once every 5 / 15 / 30
      min**, **Once an hour**, or **Once every … min** and type the minutes (1 to 1440): at most once in that many
      minutes. A price alert (or a drawn line) waits that long from its last alert instead of a minute; a cross
      meanwhile is dropped, not sent later (**Greater than** or **Less than** still true then fires again). Moving it
      (dragging its line, a new price or condition, moving the drawn line) or restarting it starts it afresh: no
      wait from an alert sent before. An indicator alert stays once a bar, and at most once in that many minutes. A
      Daily levels, study or If/Then alert waits **per level or row**: after S2 fires it stays quiet for those
      minutes **and** until price has re-armed it (moved **Re-arm after** ticks away), while R1, S1 and the plan's
      other rows still fire as usual. A touch during the wait sends nothing; once the wait is over, price still
      sitting on a level that re-armed meanwhile fires on its next trade there, and one that has left it fires on the
      next touch. A zone fires on price coming into it: one held back by its wait fires on the next entry after the
      wait, never on price that has stayed inside. A level is known by its price, so the wait holds when new levels
      rename it or a levels poll drops it for a moment, and two names at one price (P and S1 both 27425) are one
      level: one alert, where Every time sends one for each name. A row is known by its LO and HI, so its wait holds
      through a new paste. Editing the alert (its levels, rows or Touch within) keeps their waits; **Restart** (or
      saving a stopped or paused one) starts it afresh with none. Waits survive a restart and an update (kept with
      the alerts in the database), as does a price, line or indicator alert's last fire. A contract roll clears the
      symbol's waits: the new contract at a number the old one touched is another place (50 points away on ES).
    - **Re-arm after** (Daily levels, studies, If/Then, when it repeats): how far price must move away, in ticks
      (a whole number, 1 to 2000), before a level or row can fire again. 8 ticks is 2.00 points on ES and NQ alike,
      which on NQ is a small move: raise it there if quick swings through a level send a burst. Changing it, or the
      Trigger, keeps the alert as it is: a level that just fired stays quiet. Save checks the fields it shows (Touch
      within 0 to 200, Re-arm after, the minutes) and says what is wrong; one it has hidden is not checked.
  - **Expiration**: **Open-ended** (the default: it stays armed until you delete it) or **Until…** a date and time
    (the chart's time zone) when it switches itself off, within a minute, with no trade or new value needed.
    **Message** is added to the notification.
- **On the chart**: each active price alert is a dashed line with a 🔔 label and an axis tag (a channel shows both
  edges, shaded). Drag a line to move the alert (it re-arms); click it to edit.
- **Sound** (bottom of the Alerts tab, this device): a chime here when an alert fires: two notes for price alerts
  and drawn lines, three rising for levels, studies, If/Then plans, indicators, AMT and gaps, four for news warnings.
  Volume slider; **Test** plays each in turn. Browsers allow sound only after you have clicked or tapped the page once.
- **The Alerts tab** lists every alert: active ones first, with the condition, trigger (*Only once*, *Every time*,
  *Once every 5 min*, *Once a bar* for an indicator), how far it re-arms (*re-arms 8 ticks away*), expiry, message and
  state (Active, Paused, Triggered, Expired). Click a row to edit it (saving restarts a stopped alert), Pause/Restart,
  right-click (long-press) for Edit, Pause and Delete, **Delete stopped** to clear out the ones that are done.
- **If/Then plan** (Condition → **If/Then bands**, or **Create alert** under the block on the Plan tab): alerts on the
  If/Then block you pasted for the symbol, so your plan's levels reach you with no chart open. **Create alert** saves
  a paste you have not saved yet first, so the dialog lists the rows in the box. Each row decides by itself:
  - A **line** fires when price touches it, within **Touch within** ticks (0: the price itself). A row is a line when
    LO and HI are the same, or when one end is open: `0` for LO puts the line at HI (`D|0|7385|...` is a line at
    7385), `9999999` for HI puts it at LO (`U|7450|9999999|...` is a line at 7450).
  - A **zone** (LO and HI both set, LO below HI) fires when price comes into it from outside. Touching an edge counts.
    It fires once each time price comes in, not on every trade inside. Touch within does not widen a zone.
  - A row with no bottom and no top (`0` and `9999999`) never fires.

  After it fires, a line or zone stays quiet until price has been **Re-arm after** ticks (8 unless you change it)
  beyond it (beyond its Touch within for a line, outside a zone), so chop narrower than that sends one alert. A swing
  that wide re-arms it: on NQ 8 ticks is 2 points, so quick 2-point swings through a level send an alert each. Raise
  **Re-arm after**, or pick **Once every … min** (each row then waits by itself). Overlapping zones each fire on
  their own, and a zone whose edge sits next to a Daily levels alert's level fires as well as that level.
  **Every time** is the default; **Only once** stops the whole alert after the first row fires.

  **Rows**: **All rows** follows whatever the plan holds. Or untick it and pick rows: each shows its price or zone and
  its text, marked line or zone. A picked row is known by its LO and HI, so it keeps alerting after you paste a new
  plan that still has that row (its text can change). A row whose prices changed is a new row. In the Alerts tab it
  reads *ES If/Then plan: all rows*, or the rows picked.

  The alert always reads the plan saved now. Paste a new one (or change the block in If/Then Settings) and it follows
  at once. A row that is still in the plan (the same LO and HI; its text can change) carries on as it was: one that
  just fired stays quiet until price has been Re-arm after ticks beyond it (and its wait, with Once every … min), so
  saving the plan again never sends a second alert.
  A new row never fires just because its level landed on or across price: it starts from where price is now, as after
  a restart or a roll. Clear the block (Save it empty) and nothing fires; the alert stays, ready for the next plan. You
  can set the alert before any plan is pasted.

  The notification reads `ES touched 7450.00: Accepts above 7450 → continuation` for a line and
  `ES entered 7395.00–7405.00: Pivot ~7400` for a zone: prices as the chart writes them (32nds on Treasuries), the
  row's text on one line (`\n` and line breaks become ` / `, cut at 140 characters), then your **Message** and the
  last price, as other alerts.

  Each symbol has its own plan: an alert on MES reads the block pasted for MES, not ES's.
- **Drawn lines**: lines, rays and trend lines carry their own alert (see [Drawings](CHARTS.md#drawings)), with its
  trigger next to its direction: once, every time, or every 1 to 1440 minutes (changed on a line that fired, it
  restarts the alert, as Save does here). Edit those on the line;
  deleting one here switches it off on the line. A trend line's level is where the line is drawn at that moment on
  the chart its alert was switched on from (shown as *now …*): on a minute or hour chart it moves on only while the
  market trades, as their bars do; on a W chart by the clock; on an M chart in its month bars; on a log scale in log
  price.
- **Indicators** (Condition → any indicator in Studies ▾ except cumulative delta and COT), as TradingView's: one of
  its lines on a timeframe you pick (1m to 1D, with its own settings: Bollinger's period and deviations and its upper,
  middle or lower band, ADX's +DI, a pivot's R1, ...) **crossing**, **crossing up**, **crossing down**, **greater
  than** or **less than** the price, a value (RSI 70, ADX 25) or another line (another of its own, as MACD against
  its signal, %K against %D, +DI against -DI; or an EMA or SMA of any length). **Check** on **bar close** (TradingView's Once Per Bar Close) or **intrabar** (the first
  trade that meets it, Once Per Bar). NinjaTrader 8's formulas, the same as the chart's, on bars the server builds
  from history, so it fires with no chart open. Every time = once a bar; Once every … min = once a bar, and at most
  once in that many minutes. After a restart, a gap fill or turning it on, an alert first works its line out over
  the timeframe's last 3000 bars, never firing on that first look; on long
  settings (Regression 1000, Ichimoku 500) that takes a second or two in a worker process, and the alert checks from
  then on, a bar that closed meanwhile too (intrabar: one of the same session, not the last one's at the next open).
- **While history is still coming**: when a symbol's stored data stops 2 minutes or more short of its live trades
  (NinjaTrader just started, an instrument just put back on Connections > Instruments), its study, indicator, AMT and
  gap alerts send nothing until that stretch is filled (any part of it asked again as 1-minute bars too), as their
  lines would be worked out from bars days old; then they load again with it and alert as usual, a new gap seen
  meanwhile too if its first minute is under 6 minutes old (with its fill if it filled meanwhile; when the stretch's
  history failed, only if the stretch has no regular-hours minute, overnight or over a weekend, as the gap is measured
  from the last RTH close before it). A shorter stretch (the add-on reconnecting after a server restart) holds
  nothing back.

Delivery, set under **Connections > Alerts and Discord** (or in `.env`):

- **App notifications**: the installed app on each iPhone, Android phone or PC that turned them on (see [Phone and
  PC app](PHONE.md)). The server encrypts each alert for the device; Apple's, Google's or Microsoft's push service only
  carries it. No other app or account.
- **Discord** (`CHART_DISCORD_WEBHOOK`): a **private** channel's webhook.
- **ntfy** (`CHART_NTFY_URL`): phone push through the ntfy app. Subscribe to a long random topic and put
  `https://ntfy.sh/<topic>` here. Alert text (symbol, level, price) passes through ntfy.sh; self-host ntfy if that
  matters.
- **Heartbeat** (`CHART_HEARTBEAT_URL`): optional dead-man's switch (e.g. a healthchecks.io check). Pinged every 30 s
  while all symbols are ticking, so you hear about it if the PC, power or internet dies.
- **Discord: chart posts** (`CHART_DISCORD_CHARTS_WEBHOOK`): where **Post to Discord** sends chart pictures.
  Its own webhook, separate from the alert channel; mentions in a caption never ping anyone. **Check all** shows its
  state without posting in the channel; **Test** posts one line there.

Alerts that come together (the open: gaps, AMT and level alerts within a second) go to each channel one at a time,
waiting when Discord or ntfy says to slow down, and are tried again if it fails for a moment, so each one arrives.

Each one's **Test** button sends a test message (`POST /api/notify-test` sends one to all). The server also alerts when a symbol stops ticking for
`CHART_STALE_AFTER_S` during Globex hours, and again when it recovers. Weekends, the daily break and exchange
holidays (below) never count as stale, and the heartbeat keeps pinging through them.

## Chart pictures to Discord

The camera button (or the chart's right-click menu) has **Post to Discord…**: the chart picture with a caption, to
the channel set in Connections > **Discord: chart posts** (with two or more, one menu item each). See
[Charts](CHARTS.md) and [Connections](CONNECTIONS.md).

