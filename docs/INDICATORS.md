# Tatanka indicators

The NinjaTrader indicators, ported: their settings, the Plan tab and levels.

## Tatanka indicators: settings, Plan tab, levels

The Tatanka NinjaTrader indicators: AMT Toolkit, If/Then Bias Table, Level Lines, Gap Analysis and HTF EMA are on by
default; TPO Profile is there to turn on. Each is a row in Studies ▾.

- **Settings…** (Studies ▾, or the ⚙ on the Plan tab) opens the Indicators dialog, laid out like NinjaTrader's: the
  indicators on the left (the box shows or hides one), every property of the selected one on the right in its
  NinjaTrader groups, labels, choices, limits and descriptions, starting at NinjaTrader's defaults. Each group
  collapses (Expand all / Collapse all; the app remembers which are open), **Find a setting** searches, changed
  settings are marked, ↺ puts one back to NinjaTrader's default. Nothing changes until **Apply** or **OK**. Every value
  is checked as NinjaTrader checks it.
- **Each chart's own:** Apply and OK change the chart's open tab only (its chart's shared set for a linked tab; on a
  layout, the chart last clicked), on every device that opens the workspace. **Apply to all charts** puts the
  selected indicator's settings, as shown, on every tab of every chart, layout and workspace: they become the
  starting settings (what a new chart gets) and each chart's own for that indicator goes. The other indicators keep
  theirs, and so does a second one of it on any chart (HTF EMA 2). The AMT Toolkit's alert settings and Gap
  Analysis' alert switches are the exception: they go to the charts of the chart's symbol only (below).
- A few settings belong to one chart in NinjaTrader, so here they are per symbol (marked with the symbol in the
  dialog): the AMT Toolkit's Paste Levels Here, the If/Then block and instrument label, Level Lines' range and
  custom levels (a micro's: its full-size contract's).
- First start: the recommended values that differ from NinjaTrader's defaults are seeded once from
  `config/nt_settings.json` (AMT level colors and offset, Gap table bottom right, If/Then wrap 25). Change them
  there before the first start, or in the dialog any time.
- **Plan tab** (side panel), for the symbol on the chart:
  - **Daily levels**, as TradingView takes them: `2026-01-15, 5000.00, 5020.00, 5040.00, 4980.00` (date, pivot,
    then any prices: above the pivot is resistance, below is support), or the Pivot Level / Resistance Levels /
    Support Levels block. The AMT Toolkit draws them with its zones and labels, the **Daily levels** study (Studies ▾)
    as plain lines, and level alerts watch them. Where the levels come from, in NinjaTrader's order: the **Levels
    File or Link** AMT setting (a file under Documents, OneDrive's included, or anywhere with the server run on
    Windows without Docker; or an http(s) link; `{root}` = the symbol, MES reads ES), else
    `Documents\Tatanka\levels\ES.txt` if it exists, else the pasted line. The card says which one is in use and any
    error. A file is re-read within 5 seconds of a change. A link is asked at once when the setting is set or
    changed, then on the NinjaTrader Toolkit's schedule while the market is open (not while it is shut): until the
    levels of the latest 8:15 or 16:45 CT publish are in hand (their Published line, or a change since), every 15
    seconds for 10 minutes, then every minute to the end of the hour; every 2 minutes for 30 minutes after a change;
    otherwise every 30 minutes. Failures wait 1, 2, 4, 8, then 15 minutes (1 at most while a publish is due); a link
    the levels service says is not active, or has nothing for the symbol yet, is asked every 30 minutes. A bad file
    or link keeps the last good levels; a link that answers 401, 403, 404 or 410 says "The levels link was refused
    (HTTP n): check the link".
  - **If/Then**: the block (below), saved per symbol.
  - **Level Lines**: range top/bottom and custom lines `price|label|color|style|width`, e.g.
    `7845.75|VAH|Magenta|Dash|2, 7810|VAL`.
- API: `GET /api/indicators` (schema with defaults, changes, per-symbol values), `PUT /api/indicators/{key}` (the
  changes), `PUT /api/indicators/{key}/{symbol}` (per-symbol values); `GET/PUT /api/amt/levels/{symbol}` (levels
  source and the pasted line). `server/tools/gen_nt_schema.py <tatanka-indicators checkout>/ninjatrader` regenerates
  the schema from the C# sources, then `tools/gen_vp_schema.py` (the volume profiles, from the copies in
  `server/tests/vp_csharp`) and `tools/gen_dsma_schema.py`. Where NinjaTrader's wording doesn't fit this app (its
  exchange template is CME's, in CT), the generators' `LOCAL` holds this app's.
- Drawn as NinjaTrader draws them: NinjaTrader's colors, fonts and dash styles. An AMT Toolkit line set to
  **Dotted** does not show in NinjaTrader (the Toolkit's Direct2D dots have zero length and flat caps), so it does
  not show here either: pick Dashed.

## Level Lines

Studies ▾ → **Level Lines**. A port of the NinjaTrader Level Lines v1.1.1 OnRender: a range (top, bottom, optional
midline and shading) and any number of custom lines, labels on the right above each line with the price, moved
down instead of overlapping. Range and lines are per symbol, on the Plan tab.

- **Shared, as NinjaTrader's Sharing mode** (Source and Follow): every chart of a symbol shows its range and levels,
  and a micro shows its full-size contract's: ES/MES, NQ/MNQ, YM/MYM, RTY/M2K, CL/MCL, GC/MGC, SI/SIL, HG/MHG. Typed
  on an MES chart, they are ES's. NinjaTrader shares within one contract month; here a symbol's levels stay through
  a roll. Only the range and levels are shared: each chart keeps its own line styles, labels and shading, where a
  NinjaTrader Follow chart draws its Source's. Levels an MES chart had of its own before this went to ES, unless ES
  had some: then ES's show on both, and MES's old ones are listed on the Plan tab of both charts to copy from until
  you dismiss them.
- **Level Lines 2** (its row's **+ Add another**, up to 4): its range and levels are this chart's only, in its
  Settings, as a second Level Lines with Sharing mode Off in NinjaTrader's guide.

- Colors in a level as NinjaTrader reads them: a WPF color name (Orange, Magenta, Gray; not the CSS-only ones such as
  Grey) or hex `#RGB`, `#ARGB`, `#RRGGBB`, `#AARRGGBB` (alpha first: `#8F0F` is a see-through green). Anything else
  takes the Default line's color.
- A line between ticks (a range's mid on an odd number of ticks, a level typed off the tick) is labelled at its own
  price, with the decimals the instrument shows and up to 4 more: ES 5027.625, ZN 110.50781 (not the 32nd it rounds to).
- The range lines' Opacity (in their line settings, 0-100) applies to the line and its label, as NinjaTrader's Stroke
  opacity. The Default line has none: NinjaTrader draws each custom level at full opacity, whatever the Default
  line's. A see-through custom level takes an `#AARRGGBB` color.
- The label font has Bold and Italic, as NinjaTrader's.

## Tatanka AMT Toolkit

Studies ▾ → **Tatanka AMT Toolkit**. A line-for-line Python port of the NinjaTrader AMT Toolkit v1.2.4
from tatankatrading/tatanka-indicators (MPL 2.0; the ported files keep its license header). It runs on the
server, so it draws the same lines the NinjaTrader version does and can alert with no chart open.

- Draws: IB high/low and extensions, overnight high/low, prior day/week/month levels, naked POCs, poor
  highs/lows, single prints, the developing profile in the right margin, past profiles, the 80% rule fill,
  and the dashboard (session, day type, pivot, overnight inventory, open type, ATR, 80% rule).
- The daily levels (Plan tab) feed it as PIVOT/RES/SUPP lines (the Daily levels study's plain lines are hidden
  while it is on). Levels more than one trading day old show a STALE LEVELS warning, as in the original.
- Engine bars are 1-30 minutes: minute charts use their own size, longer time charts 30, tick/volume/range
  charts 5, made smaller where the instrument's regular hours don't open or close on them (GC's 07:20: 10-minute bars
  on a 30-minute or longer chart, 5 on a 15-minute one; a 20-minute ES chart 10), so the IB and the RTH open start
  where they should. With Use Instrument Hours off (below) those are ES's hours on every instrument (a 30-minute GC
  chart runs on 30-minute bars, as NinjaTrader v1.2.3). NinjaTrader works on the chart's own bars: there a 15- or
  30-minute GC chart starts the IB and the RTH open at 07:30, the first bar at or after 07:20. It loads the last 90
  days of stored 1-minute bars, or 400 with Profile Period on Yearly (NinjaTrader's guide sets Days to load the same
  way), so the year's profile starts at January 1. The server keeps 12 months and more; backfill first. A Yearly load
  takes about four times as long (a 5-minute chart on a year of minutes: about 12 s, against 3 s), on the server's
  study workers (one on a 4-core computer), so other Toolkit loads can wait behind it. While the alerting Toolkit
  loads again (after each close's prior-day job, or a gap fill), the one it replaces keeps watching the live prices
  for level alerts.
- **The prior day's value** (pdPOC, pdVAH, pdVAL and the 80% Rule) comes from that day's **trades** at one-tick
  rows, as a tick volume profile and the Session VP do, for the RTH sessions NinjaTrader downloads ticks for: **Value
  From Ticks (Sessions)** in *4. Previous Day Value & 80% Rule* (5 by default, 0 to 20) counted back from when the
  Toolkit loads (weekdays of the RTH Session for VA, a holiday's included), then each one that closes while it runs.
  Older sessions, and every one at 0 (or with the 80% Rule section off), come from 1-minute bars, as NinjaTrader's.
  For the last 7 days, with the NinjaTrader add-on connected,
  the trades are **NinjaTrader's tick history** for the session (asked of the add-on as a gap fill is, not stored), so
  the values are worked out from the same trades as the NinjaTrader AMT Toolkit (the trades
  recorded here can differ from them by a few hundred contracts, a few ticks on a value area edge); a trade stamped on
  the minute counts in the minute it ends, as NinjaTrader's bars. Otherwise, the trades stored here (a minute with none:
  its 1-minute bar's volume spread evenly over its ticks); a recent day worked out that way is asked of NinjaTrader
  again after a restart. Each day is worked out once in the background and kept; until it is, and for older
  sessions, it comes from the day's minutes as in the NinjaTrader original (each minute's volume spread over
  its range, in rows of the Profile Row Scale), which on a fast day can put the POC 20 or more points away (NQ, Nov 5:
  26902.50 from minutes, 26880.00 from trades). Its value area is **Value Area %** in *4. Previous Day Value & 80%
  Rule*, a fraction (0.68 by default; 0.70 to compare with a 70% profile). The one in *11. Volume Profile* is the
  developing profile's.
- With *11. Volume Profile → Profile Period* on Session (its Session Time on the same hours, 08:30-15:00 CT), the
  prior day's drawn profile comes from the same trades as its pd lines at one-tick rows (v1.2.1), so its tallest row
  is pdPOC and its shaded value area ends at pdVAH and pdVAL when the two Value Area % match. A Weekly or longer
  profile shades the whole period's value area.
- **The current profile** (Profile Period Session or Weekly, the default), its POC and value area and the Developing
  POC, VAH and VAL lines come from the period's **trades** at the Row Scale's rows (v1.2.2), as a tick volume profile:
  the stored ones from the start of the session or week as the Toolkit loads, then each live trade. A minute whose
  trades don't add up to its 1-minute bar's volume (gap-filled history is bars only) is spread over its range as
  before. Monthly and Yearly profiles and older weeks come from 1-minute bars (at Calc Precision's bars, v1.2.3).
  **Profile From Ticks** (*11. Volume Profile*, on by default) off: 1-minute bars only, as before v1.2.2.
  Loaded over a weekend or a holiday (Monday before the session, say), a Session profile is the last session that
  traded, from its trades (v1.2.3); a Weekly one after a Friday holiday halt starts at Thursday's 17:00 open. A
  Session Time through midnight (1700-1600, a whole Globex day, or 1700-0830) is one profile from its start (v1.2.3;
  it split at midnight).
- Every pd line (pdHigh, pdLow, pdOpen, pdPOC, pdVAH, pdVAL) switches to the session just closed at its close
  (15:00 CT; CL's 13:30), and that session's profile is redrawn from its trades at the same moment (v1.2.2; before,
  they waited for the 17:00 open, all weekend after a Friday). Live, the close's trades are worked out in a few
  seconds: the lines and profile come from its minutes until then. From the close to the 17:00 open the new lines
  start at the close and the chart keeps the old ones over the day before it; today's 80% Rule fill, OPEN and day type
  stay measured against the value in force at today's open.
- When a session's trades are worked out after the Toolkit loaded (at its close, or on a first load), it loads again
  with them, so it draws what NinjaTrader draws once it has the ticks (its load waits up to a minute for them). For a
  chart that stays open, that reload, and one after a gap fill, keeps the sessions Value From Ticks took from trades
  (those counted when the chart loaded the Toolkit and each one closed since), as a running NinjaTrader chart keeps
  them: at the close it shows what that chart shows when the ticks come. A Toolkit no chart shows (the alerts' alone,
  or one whose charts closed) counts them afresh at the reload, so a chart opened later draws what a NinjaTrader chart
  opened then draws. New settings count them again from then, as NinjaTrader's reload does. Charts that run the
  Toolkit on the same symbol, bars and settings share one: one opened while another is open draws the sessions of the
  one open longest (its load and each close since), and its own from the first reload after that one closes, where
  NinjaTrader counts them from each chart's own load (the pd lines of the oldest of them can differ). A NinjaTrader
  chart whose download outlasts its load's wait clears today's 80% Rule fill drawn before they came (until it
  reloads); this one draws the whole fill. Prior days worked out for a replay load only the replaying charts again.
- The **DAY TYPE** is measured against today's IB (IB Calc Period) once it is complete, and against the overnight
  range before that. It says Non-Trend Day only after today's IB (v1.2.3). With an IB Calc Period from 09:00, it read
  yesterday's IB from 08:30 to 08:59; with one to 10:00, NinjaTrader said Non-Trend Day from 09:30.
- **Alerts** run on the timeframe and own settings of the chart you switched them on from (as NinjaTrader alerts per
  chart), so they fire at the lines that chart draws. To move them to another chart's, switch them off and on there.
  Alerts switched on before this version run on a 5-minute engine with the symbol's settings until switched again.
- **Settings…** opens every Toolkit setting in the Indicators dialog (above): the same 19 groups, checked the way
  the indicator's own validation does; the Toolkit reloads on Apply. Each chart's own, like the other indicators
  (the server runs one Toolkit per distinct set of settings; charts on the same settings share it), except:
  - the **alert settings** (the Alerts group, every group's Alert switches and the level zones' width, which Zone
    Entry alerts use; marked with the symbol in the dialog), which are the **symbol's**: the same on every chart of
    it, as AMT alerts come from one run per symbol. Turning one off on an NQ chart turns it off on every NQ chart, tab
    and device, and leaves ES's as they are; Apply to all charts on an ES chart gives them to ES's charts only. Each
    instrument has its own (micros too: MES's are not ES's); one added later starts at NinjaTrader's defaults.
  - the **levels file**, which is the same on every chart and symbol (`{root}` makes it each one's file or link);
  - **Paste Levels Here**, which is the symbol's.

  A change to those on any chart, tab or device shows at once on every other showing them (each saves only what it
  changed). Presets (Presets ▾) are the chart's own settings only: loading one leaves those as they are. From a
  version before the alert settings were each symbol's, the first start gives every instrument (and each symbol whose
  AMT alerts are kept off Instruments) the ones every chart had, once, so nothing set is lost. Left out because they
  only mean something inside NinjaTrader: alert sound and the indicator's own Discord webhook (this app has its own
  alert channels). With the session-change warning on (the default), the chart says so when a session field differs
  from its default: all session fields are Central exchange time.
- **The alerts follow their Alert switches only.** The alert run works out every group whose Alert switch is on
  (Alert VWAP Cross computes the RTH VWAP, Alert IB High Cross the IB, Alert nPOC Touch every kind of nPOC...), whatever
  a chart shows: a group's Enable and Show switches (Enable IB, Enable RTH VWAP, Show Late Spike, Enable Prev Day
  Levels, Show pdPOC, Show Weekly Open, Show pmHigh/pmLow, Enable Volume Profile...) are each chart's own and only show
  or hide the group or line on that chart. To stop a group's alerts, untick its Alert switch (on a chart of that
  symbol). An Alert switch a chart's tab had turned off in its own settings before they were shared is turned off on
  every chart of that tab's symbol once (a linked tab's: its symbol's), the first time that chart opens (it only ever
  turns alerts off), and a message says so.
- **Each instrument's own hours.** The session fields are written for ES/NQ's regular hours (08:30-15:00 CT), as
  NinjaTrader's defaults; on another instrument they move with its own (config/instruments.json `rth`): a time nearer
  the open moves with the open, one nearer the close with the close, the overnight stays. CL (08:00-13:30): IB
  08:00-09:00, overnight 17:00-08:00, its settle the 13:30 close. GC (07:20-12:30): IB 07:20-08:20. ES and NQ as
  written. TPO Profile, Gap Analysis and Session VP have the same setting of their own (below), as in NinjaTrader since
  their v1.1.0. A short
  window either side of 11:45 whose two ends would land on the same minute or past each other moves as a whole, as
  its middle does, and keeps its length: 11:00-12:00 is 10:30-11:30 on CL and 09:50-10:50 on GC (it was all day on
  CL and through the night on GC). Gap Analysis refuses such times instead (below). The TPO periods behind single
  prints and Poor High/Low move as the Toolkit's TPO Session does, so they fall in it as they do on ES. On GC the
  default TPO Session runs 07:20-12:30 and its periods 07:20-07:50, 07:50-08:20 and so on (its first one was
  07:20-07:30). A TPO Session of 1200-1500 runs 09:30-12:30 on GC. Its periods are 09:30-10:00, 10:00-10:30 and so
  on, as before. In the Toolkit this is *1. Global Settings → Use Instrument Hours*, on by default, as in the NinjaTrader
  Toolkit since v1.2.4, which moves its fields the same way at the hours config/futures.json gives each root (before,
  it ran ES's hours on every market). Off, the Toolkit runs every field as written, on ES's 08:30-15:00 and on
  bars that fit them, on every instrument, as NinjaTrader v1.2.3 did.
- **Holiday halts** (MLK Day, Thanksgiving, ...: no settlement that day): the short session counts toward the next
  trade date, as CME's daily bars: Tuesday's prior day and settle after MLK Day are Friday's, and Monday's trading
  is Tuesday's overnight.
- **IB Stop Time and Delete OVN Lines At** are NinjaTrader's defaults: 0830-1515 and 1500-1505. The OVN High/Low lines
  show from the RTH open to the RTH close, and their alerts end with them (NinjaTrader since v1.2.2). With Use
  Instrument Hours on both move with the market: the OVN lines go at 12:30 on GC and 13:30 on CL. Before, this app
  kept them to the 17:00 open (1700-1705) and stopped the IB at 15:00, so OVN alerts came from 15:00 to 17:00 that
  NinjaTrader doesn't send. Set Delete OVN Lines At to 1700-1705 to keep the lines to the 17:00 open. A chart that has
  its own 1700-1705 or 0830-1500 keeps it, and so do AMT alerts switched on from it before the update.
- Profile: Day, Week, Month or Year is also a quick switch in the menu.
- Alerts: "Alerts to Discord/phone" turns on the Toolkit's own level alerts for that symbol (on the chart's timeframe
  and own settings, with the symbol's alert settings; its storm cap applies). How levels alert is its Level Alert Mode setting (Cross by
  default). Off by default.
  Turned back on, they start afresh: nothing that happened while they were off is sent.
- API: `GET /api/amt` gives the settings schema, the defaults and the current changes: every chart's (`overrides`)
  and each symbol's alert settings (`symbols`, by symbol; their names in `per_symbol`). `PUT /api/amt/settings`
  takes the changes as a JSON object (same names as the NinjaTrader properties, e.g. `ib_session`), all of them;
  `PATCH /api/amt/settings` takes only those that change (one at its default goes back to it). With `?symbol=NQ` the
  alert settings in either are NQ's; with no symbol, every symbol's. Every open page hears each change
  (`amt_settings`, with every chart's and each symbol's).
  `server/tools/gen_amt_schema.py` regenerates the schema from a new version of the C# source.

Parity check against the original C# (needs mono), from `server/`:
`tests/amt_csharp/build.sh <tatanka-indicators checkout> <workdir>`, then
`python tests/amt_parity.py <workdir> [days] [minutes] [seed] [root] [settings file]`. It replays the same synthetic
bars through both engines and compares every value. Each completed bar runs on the engine itself, carrying the IB,
settle, prior day, 80% Rule and profiles from the bars before; only the forming bar's preview runs on a copy. The
runner's own frames must equal the replayed ones, so its choice of the sessions taken from trades is checked too.
Both run the settings (the file: one `name=value` a line, as typed, `inst_hours=False` for Use Instrument Hours off)
at the root's own hours: this port at config/futures.json's, the C# at its own table (InstrumentHours). With
`TATANKA_INDICATORS=<tatanka-indicators checkout>`, `tests/test_amt_inst_hours.py` also checks that table against
config/futures.json, root for root (skipped without it).

## Tatanka TPO Profile

Studies ▾ → **TPO Profile**. A port of the NinjaTrader TPO Profile v1.1.0 from
tatankatrading/tatanka-indicators (MPL 2.0). Letters for each 30-minute block of the session, coloured from
the open (blue) to the close (pink), drawn one bar apart from the session's first bar as on NinjaTrader; the
last 3 days get letters, the 15 before them a grey outline. POC in yellow, value area optional.

- Minute and hour charts only (it is built from the chart's bars). The server rebuilds those bars from
  stored 1-minute bars, so it draws all 18 days even when the chart holds fewer.
- Session Time is in CT, NinjaTrader's 0830-1500 (Settings…). With **Session → Use Instrument Hours** on (the
  default, as NinjaTrader v1.1.0) it runs at each instrument's own hours, moved as a whole as the AMT Toolkit's fields
  are (above): 0800-1330 on CL, 0720-1230 on GC, 0725-1225 on SI. Off, it runs as typed on every instrument, as
  NinjaTrader v1.0.0 (and v1.1.0 with it off). The Studies panel shows the session it runs. A Session Time the profile
  can't read (0860-1500) filters nothing on every instrument, as on NinjaTrader. Untick "Filter by Time" for one
  profile per 17:00-16:00 Globex day.
- Row height comes from the daily ATR(10) / 80 rows, as on NinjaTrader; the daily bars are built from your
  stored 1-minute bars, so it needs 11+ days of history (8-tick rows until then).
- On an RTH chart it runs on that chart's bars, as on a NinjaTrader chart with an RTH trading-hours template: the
  regular hours only, counted from the RTH open (a 60-minute chart's first letter is the 08:30-09:30 bar, with no
  pre-market in it), one session per regular session (with "Filter by Time" off too), and the ATR from daily bars
  of the regular hours.
- Like the original it only uses closed bars; "Developing" adds today's profile, updated as each bar closes.
- One deliberate difference: with "Session only" off, the NinjaTrader version re-prices a finished day's rows
  when the daily ATR changes the row height at the 17:00 open, so that day draws at the wrong prices. The
  port keeps the height the rows were built with. On an RTH chart the same happens with it on (a day is saved
  at the next RTH open, after the daily bar).

Parity check against the original C# (needs mono), from `server/`:
`tests/tpo_csharp/build.sh <tatanka-indicators checkout> <workdir>` (v1.0.0 or v1.1.0), then
`python tests/tpo_parity.py <workdir> [days] [minutes] [seed] [ES|NQ|CL|GC|SI] ['{"UseInstrumentHours": false}'] [rth]
[zone=America/New_York]` (`rth`: an RTH chart's bars; the deliberate difference above is undone for the comparison).
The record carries the typed Session Time, the root and the setting: the C# v1.1.0 moves it itself (its own hours
table), this port at config/futures.json's hours, and both print the session they run. A v1.0.0 build matches with
the setting off only. `zone`: the C# runs as NinjaTrader set to that time zone (its Session Time is in chart time, so
0830-1500 is typed as 0930-1600 in New York), moving it in CT each day; its profiles must be this port's in CT. A zone
whose offset from CT changes over the days generated is refused.

## Tatanka volume profiles: Auto Anchored, Periodic, Session

Studies ▾ → **Auto Anchored Volume Profile** (AAVP 1.1.0), **Periodic Volume Profile** (PVP 1.0.1) and **Session
Volume Profile** (SVP 1.1.0): ports of the three Tatanka NinjaTrader indicators, with their settings (Settings…, the
same on every device), defaults, colors, labels, price markers and on-chart messages. Up to 4 of each on a chart,
each with its own settings (a Weekly and a Monthly PVP, a Year and a Quarter AAVP: its row's **+ Add another**,
CHARTS.md Studies).

- AAVP and PVP are built from 1-minute bars, each bar's volume spread evenly over its ticks (up volume when it
  closed at or above its open), exactly as the indicators do it. AAVP is one profile from the anchor (default the
  Year) to now; PVP one per day, week (default), month or year, the last 20 kept. They read the stored 1-minute bars
  as far back as the anchor or the oldest period needs; when the stored bars start later, the notice at the bottom
  says so (backfill to fill it in). An AAVP whose anchor is older than the chart's first bar (a Year on 30 minutes)
  holds the whole stretch but is drawn from where that first bar opens, where NinjaTrader's (which loads only the
  chart's days) starts.
- SVP is built from every stored trade in each custom session (default 08:30-15:00 CT; the last 20), split into up
  and down volume by the tick rule. Trades are stored here only while the NinjaTrader add-on is connected (or from a
  Databento import); history filled in from NinjaTrader is 1-minute bars. A minute with no stored trades takes its
  1-minute bar instead (its volume spread over its range, up or down by its close), and a note at the bottom says
  how many sessions have such minutes. For a profile that matches NinjaTrader exactly, keep the add-on connected
  through the session.
- SVP's **01. Session → Use Instrument Hours** (on by default, as NinjaTrader v1.1.0) runs the window at each
  instrument's own hours, moved as a whole as the AMT Toolkit's fields are (above), in every Session time zone. In CT
  (ExchangeTemplate and Central) 08:30-15:00 runs 07:20-12:30 on GC and 08:00-13:30 on CL. An Eastern, UTC or Chart
  window is typed in that zone: each date's is put in CT at that date's offset, moved there and put back, as
  NinjaTrader's. Eastern's 09:30-16:00 (ES's hours there) runs 08:20-13:30 on GC all year. UTC follows
  Chicago's daylight saving date by date: 14:30-21:00 is 08:30-15:00 CST and runs 13:20-18:30 on GC; in CDT it is
  09:30-16:00 and runs 13:20-21:00. With it off every window runs as typed. Its label and the Studies panel show the
  window it runs (outside CT, the newest session's).
- SVP's **Session time zone → Chart** is the chart's own time zone (click the clock), as NinjaTrader's Chart is its
  General options' zone: 09:30-16:00 on a New York chart is 08:30-15:00 CT. Changing the clock's zone works the
  sessions out again. A chart in Chicago time runs it as Central. With the clock on **Local** (the default) it is each
  device's own zone, so a phone in another zone runs another window (the Studies panel names the zone); pick a city or
  Exchange on the clock to have one window on every device. Before, Chart was always CT.
- On an RTH chart (the ETH/RTH button) SVP takes only what traded in the instrument's regular hours, as NinjaTrader's
  tick series follows the chart's RTH Trading Hours template: a 17:00-16:00 window holds that day's RTH session.
- Every window with trades in it is a session, as on NinjaTrader: a holiday halt's own window is its partial session
  (MLK Day's 08:30-15:00 runs to the 12:00 halt), and the window that opens on a halt's evening is kept (17:00-16:00
  from MLK Day's 17:00: Tuesday's Globex day). Before, both were left out.
- They run on the server, so every chart and device shows the same profiles (SVP's Chart zone aside, above); the chart
  asks again every 3 seconds and gets only what changed. Finished SVP sessions are kept in the database; those older
  than any Sessions to retain can reach (about 160 days) go at the daily upkeep.
- PVP's "until touched" lines end at the first chart bar after their period whose high and low reach the level, as
  NinjaTrader checks it (v1.0.1: within a billionth of the level's price). It is the chart's own bars, so on Renko the
  bricks and on Heikin Ashi the Heikin Ashi highs and lows, as drawn. A period that ended before the chart's first
  loaded bar keeps its 1-minute bars' touch when that came before the first bar. Not copied: NinjaTrader's history
  pass skips chart bars that close inside the next period's first minute, except the last (seconds, tick and Renko
  bars at a fast open); live it checks them all.
- Times are placed as NinjaTrader's GetXByTime does: inside a bar between the previous bar's center and its own, in a
  gap (a session's open, a weekend) on the previous bar. So a period or anchor that opens with a session starts one
  bar left of its first bar, and a forming profile ends partway to the forming bar, by its last minute.
- Where a profile starts before the chart's first loaded bar, minute and hour charts place it where those bars would
  be, as if more were loaded (NinjaTrader loads both from the same days, so it never has to).
- POC, VAH and VAL are written to a tick (NinjaTrader's FormatPrice): an SVP with an even Ticks per row has its POC
  between two ticks.
- The **data window** (Chart settings) shows each profile's POC, VAH and VAL at the bar under the crosshair, as
  NinjaTrader's Data Box has the plots. SVP: the session's as of that bar's close, to the minute (the last session's
  between sessions, none before the oldest of Sessions to retain; NinjaTrader keeps the values it set on older bars).
  A finished session's are worked out in the background, newest first, the first time it is asked for with those
  settings (a busy session takes a second or two), and kept; until then its bars show none (the chart asks again every
  3 seconds, in a paused replay too). A late trade (stored after its minute was counted) is taken into the forming
  session's values from its minute on.
  AAVP and PVP: NinjaTrader leaves the bars of history empty and fills the last one at load and the bars that form
  while it runs; so does this, from when the chart loaded. Changing a setting empties them again, as NinjaTrader loads
  the indicator again. NinjaTrader has the new setting's levels at once; here they take a moment to come, and a bar
  that closes before they do stays empty (never the old setting's). The legend row stays NinjaTrader's label, with the
  latest values.
- Their texts on the chart are NinjaTrader's, but name this app's settings: the 1-minute history stored here
  (Backfill) for AAVP and PVP, the trades stored here and ETH/RTH for SVP, instead of Days to load and Trading Hours.
- As on NinjaTrader: AAVP needs an intraday or 1 Day chart, PVP and SVP an intraday chart (seconds to hours, tick,
  volume, range, Renko); otherwise they say so on the chart. The colors can be None (NinjaTrader's Transparent).

Parity check against the unchanged C# in `server/tests/vp_csharp` (copies of tatanka-indicators' files; needs mono),
from `server/`: `tests/vp_csharp/build.sh <workdir> [<tatanka-indicators checkout>]` (a checkout's files instead of the
copies), then `python tests/vp_parity.py <workdir>/harness.exe [seed]`. It compares every number of 116 cases (every
anchor and period, year ends, a bar too wide for 20,000 rows, PVP's touches on a 1-minute chart, overnight and odd
session times, SVP's window on ES, NQ, CL, GC and SI with Use Instrument Hours on and off, and SVP's window in other
time zones) as raw double bits. `tests/test_parity_vp.py` runs the same harness for PVP's touches on 5-minute, Heikin
Ashi and Renko bars against `pvpTouches` in web/studies.js. The zone
cases run the C# as NinjaTrader set to a time zone, with the window in that zone or another: Eastern and UTC with
NinjaTrader on Central, ExchangeTemplate on a Berlin template, Chart on London, Los Angeles and Tokyo, and Central with
NinjaTrader on New York. UTC, Berlin, London and Tokyo cross the weeks their offset from Chicago changes (London's
daylight saving ends a week before Chicago's and starts two or three weeks after, two in the 2027 data), and Eastern
and UTC cross both US changes. A window can cross midnight in one zone and not the other: Tokyo's 23:30-06:00 crosses
its own, London's 05:00-21:00 and Berlin's 06:00-22:00 cross Chicago's (23:00-15:00 CT). The C# and this port each move
every date's window and print each one they run. A C# from before NinjaTrader's zone fix runs those windows as typed,
so its zone cases with the setting on differ.

## Tatanka Gap Analysis

Studies ▾ → **Gap Analysis**. A port of the NinjaTrader Gap Analysis v1.1.0 from
tatankatrading/tatanka-indicators (MPL 2.0). A gap is the RTH open against the last RTH bar's close the day
before (the chart's close, not the exchange settlement). Unfilled gaps extend right and shrink as price
trades into them; the table shows the latest gap, its fill, bars elapsed and the consecutive-gap count.

- Minute, hour and second charts, as NinjaTrader's Minute and Second charts (any other chart says to use one). On a
  minute or hour chart the server runs the engine over the last 90 days of the chart's bars (rebuilt from stored
  1-minute bars, so old unfilled gaps show even when the chart holds fewer bars), up to the bar forming now; the
  chart then fills gaps from every live tick, as the indicator does on each price change.
  On an RTH chart those are its regular-hours bars from the RTH open, as on an RTH trading-hours template: the gap
  opens at the 08:30 bar's open, not at a pre-market price, and its bar counts are the chart's.
- On a second chart (1s to 59s) it runs over the chart's own bars, as NinjaTrader runs it over the bars loaded: built
  from the stored trades from the chart's first bar on, each stamped at its end as NinjaTrader stamps it. A gap shows
  once the chart holds the RTH close before it: scroll left to load older bars and older gaps come with them. History
  (days) does not apply; at most the last million stored trades are read, and older bars show no gaps. With a bar
  size that fits the minute (1s, 5s, 15s, 30s, ...) the gaps are the 1-minute chart's (same open, RTH close and size);
  a size that does not (7s) can straddle the RTH open, its open then that bar's, and the RTH close, which is then the
  close of the bar before it (on an RTH chart the last bar ends at the close), as in NinjaTrader. "in N bars" counts
  the chart's bars.
- RTH start and end are NinjaTrader's HHmmss settings, Chicago time (83000 and 150000). With **01. Gap Settings → Use
  Instrument Hours** on (the default, as NinjaTrader v1.1.0) they run at each instrument's own hours, each moved on
  its own (CL 80000 and 133000, GC 72000 and 123000); off, as typed on every instrument, as NinjaTrader v1.0.0. With it
  on, times that an instrument's hours move to the same minute or past each other (110000-120000 is 10:30-10:30 on CL
  and 09:50-09:30 on GC, a session through the night) are refused on that instrument's chart and for its alerts,
  naming it, as NinjaTrader shows on its chart (fine on ES, they are ES's: CL's alerts keep theirs); a change for
  every symbol (the API with no symbol, below) is checked on every instrument's hours, and on
  those of one taken off Instruments whose gap alerts are kept on (its saved or catalog hours). Alerts stored with
  such times from before stay off, the Studies panel saying why. History (days scanned) is in the Studies menu.
- Alerts: "Alerts to Discord/phone" turns on new-gap and full-fill alerts for that symbol, with its current RTH times,
  Use Instrument Hours and minimum size. They run on the server on the bars of the chart that turned them on (its
  minute or hour timeframe), as NinjaTrader alerts from its chart's bars, so "filled in N bars" counts that chart's
  bars. Turned on from a second chart they run on 1-minute bars ("in N bars" counting minutes), and so do alerts
  turned on before the timeframe was kept. With a size that fits the minute (1s, 5s, 15s, 30s, ...) that is the
  chart's gap and fill; with one that does not (7s, 13s, ...) it is the 1-minute chart's open and RTH close, which
  differ from the chart's when its bar straddles the RTH open or close (NinjaTrader alerts from that chart's own bars,
  so its alert says what the chart draws). Turned on from an RTH chart they watch only the regular
  hours, so a gap fills when it does on that chart (the Studies panel then says "Alerts: ... (RTH)"); from an ETH
  chart, all hours. One setting per symbol: the timeframe and hours of the chart that last turned them on. Off by
  default; history never alerts, only live data (and none while the stretch up to the live trades is still being
  filled: see [Alerts](ALERTS.md#alerts)).
- A contract roll: the gap from the old contract's RTH close to the new one's open is drawn but not alerted, nor its
  fill. NinjaTrader's chart merges contracts back-adjusted by default (Merge Back Adjusted): the old contract's bars
  are shifted by the spread between the two at the roll, so the gap it finds and alerts there is the market's own.
  This app's bars are not adjusted (as NinjaTrader's Merge non back adjusted), so the drawn gap holds that spread, and
  the market's own gap can't be measured: the new contract's prices from before the roll are not stored. Rather than
  alert a gap that is mostly the spread, it sends neither. See [Connections](CONNECTIONS.md) for how futures roll.
- **The alert settings are each symbol's**, as the AMT Toolkit's. The alert switches (Alert on new gap, Alert on full
  gap fill; marked with the symbol in the dialog) are the same on every chart of the symbol: the dialog shows the
  server's for the chart's symbol, and unticking one on an NQ chart turns it off on every NQ chart, tab and device at
  once and leaves ES's as they are. Saving Gap Analysis' settings on a chart gives its RTH times, Use Instrument Hours
  and minimum size to that symbol's alerts only (a minimum is in the instrument's points, and the hours are each
  product's), which stay on or off at their own hours, whichever chart or device last switched them. Apply to all
  charts on an ES chart gives the alert switches to ES's charts only, and only ES's alerts take its RTH times, Use
  Instrument Hours and minimum size (every chart starts from them as usual). Each instrument has its own (micros too:
  MES's are not ES's); one added later starts with both on. A preset leaves the switches as they are, and one a
  chart's tab had turned off in its own settings before they were shared is turned off on every chart of that tab's
  symbol once (as above).
- From a version where the switches were the same on every chart and symbol, the first start gives every instrument
  (and each symbol whose gap alerts are kept off Instruments) the ones it had, once, so nothing set is lost; each
  symbol's alerts keep their own RTH and minimum size. From one older still, the server works the switches out first
  from each symbol's alerts as saved (off where a symbol whose alerts are on had it off; with no alerts saved, as the
  starting settings have it), and the next page to open says which it turned off.
- API: `GET /api/gaps/alerts` gives each instrument's gap alerts (`alerts`: on, rth, RTH times, minimum size and
  switches) and its alert switches (`switches`, by symbol); `?symbol=NQ` gives NQ's only. `PUT
  /api/gaps/alerts?symbol=NQ` takes a chart's Gap settings for NQ (`new`, `fill`, `RthStart` with `RthEnd`,
  `MinimumGapPoints`, `UseInstrumentHours`; what is left out stays as it is); with no symbol, every symbol's, as
  before. New times or `UseInstrumentHours` are checked with what each symbol's alerts keep: `UseInstrumentHours`
  alone is refused (400, nothing stored) where it would make their times no session. `PUT /api/gaps/alerts/NQ`
  switches NQ's alerts on or off (its body takes `UseInstrumentHours` too). Every open page hears each change
  (`gap_alerts`, with each symbol's).

Parity check against the original C# (needs mono), from `server/`:
`tests/gap_csharp/build.sh <tatanka-indicators checkout> <workdir>` (v1.0.0 or v1.1.0), then
`python tests/gap_parity.py <workdir> [days] [minutes] [seed] [ES|NQ|CL|GC|SI] ['{"UseInstrumentHours": false}']
[rth]` (`rth`: an RTH chart's bars). The record carries the typed times, the root and the setting: the C# v1.1.0 moves
them itself (its own hours table), this port at config/futures.json's hours, and both print the times they run or the
same refusal. A v1.0.0 build matches with the setting off only. `tests/test_gap_seconds.py` runs the same C# engine over
second bars built from synthetic trades by NinjaTrader's rule (7s, 10s, 15s and 30s, ETH and RTH, ES and GC) and checks
every gap against this port's.

## Tatanka HTF EMA

Studies ▾ → **HTF EMA**. A port of the NinjaTrader HTF EMA v1.0.1 from
tatankatrading/tatanka-indicators (MPL 2.0): a 21-period EMA of a higher timeframe (1 hour by default) on any
chart, including tick and range charts, updated on every price change. Up to 4 on a chart, each with its own timeframe, length and color
(its row's **+ Add another**, CHARTS.md Studies). Three modes, as in the original:

- **Smooth** (default): an EMA of this chart's own bars with the length scaled to the higher timeframe
  (21 x 60/5 = 252 on a 5-minute chart, 1260 on a 1-minute chart, 5040 on a 15-second chart; tick and range charts
  use 21).
- **Exact**: the EMA of the last closed higher-timeframe bar, a step line. It steps at the first chart bar after that
  bar closes, and an hour counts as closed with the first trade after it (or once the market is shut), as in
  NinjaTrader 1.0.1. A live chart and a reload draw the same line where the chart and the source are both Second or
  Minute bars and the source's bars end on chart bar closes; elsewhere a live bar can keep a value a reload draws
  differently, as on NinjaTrader (below).
- **Live**: one EMA step from the last closed higher-timeframe bar, with the current price as the forming bar's close.

The line is left out of the price auto-scale: after a big move it can sit hundreds of points from price, and fitting
it squashed the candles flat. Far from price, it goes off screen.

The server computes the higher-timeframe EMA and, for seconds, minute and hour charts, the chart EMA before the
chart's first bar, from enough stored history (8 EMA lengths) that the line starts settled; the
browser runs the indicator's OnBarUpdate on the chart's bars. Source timeframe, length,
mode and the plot's color, dash style and width are in Settings…; the legend shows its value. On tick, volume and range charts a bar's close time is taken as the next
bar's open, so an hour that ends in the gap between two bars can land one bar later than on NinjaTrader.

Source timeframe and length take what NinjaTrader's dialog takes for time bars:

- **Second**, **Minute**, **Day**, **Week**, **Month** and **Year**, any number of them (2 Day, 3 Week, 90 Second).
  A Minute or Second bar counts from the session open (the RTH open on an RTH chart) and ends at its close; one longer
  than the session is the session. Second bars are built from the stored trades, as the seconds charts are, one only
  where a trade printed; a Second source in whole minutes is that Minute source.
- Day bars are the 1D chart's (a holiday halt's short session in the next day's). Week, Month and Year bars group those
  days by their trade date; 2 Day, 2 Week and longer group them from fixed starts, as this app's 2D and week charts do.
  NinjaTrader groups from the first day it loads, so its multi-day and multi-week bars can start on another day.
  These come from the daily bars kept for Daily SMA x4, up to 20 years back.
- Length 1 to 2147483647 (NinjaTrader's Int32). A length longer than the stored history shows no line, as on
  NinjaTrader with too few bars loaded. A Smooth length past Int32 is 1, as NinjaTrader's arithmetic makes it.
- Tick, Volume, Range, Renko and NinjaTrader's other bar types are not offered as sources: they would need every trade
  of the chart's span, and their bars are not this app's to rebuild for a study.
- A Second source works on seconds, minute, hour and 1D charts. On tick, volume, range and Renko charts and on 2D, week
  and month charts the server would have to send every source bar of the chart's span (it can't tell which one each
  chart bar ends on), so it says so: use a Minute or longer source there. It reads at most 4 million stored trades a
  request; further back, and where only 1-minute bars are stored, each minute counts as the source bars closing in
  it, all at its close and no more than its trades. The first answer for a chart reads its span of trades; each
  after goes on from the last, reading only the trades since.

Live bars keep their last value, as NinjaTrader's do (Exact and Live). On NinjaTrader (Calculate.OnPriceChange) a chart
bar that ran live keeps what it showed at its last price change, and its chart series runs first on the trade that
opens the next source bar. So a source bar that closes inside a chart bar counts from the chart bar's next price change
after that trade; with none, from the next bar on. A reload counts it in the bar it closed in. The chart here does the
same:

- A live bar keeps the value of its last price change (or of the trade that opened it), also once it has closed and
  later answers have come; a source bar that closed inside it after that counts from its next price change or the
  next bar. A trade at the same price changes nothing.
- A source bar that closed by the bar's open counts at the bar's first trade where both the chart and the source are
  Second or Minute bars (seconds, minute and hour charts; NinjaTrader's catch-up), here once the server's answer to
  that trade is in, a moment later. With a Day or longer source, or on a tick, range, 1D or longer chart, it counts
  from the bar's next price change, as on NinjaTrader. One the server has closed while the market was shut (no trade
  after it yet) follows the same rule at the session's first trade.
- A reload draws every bar as of its close, as NinjaTrader's does: a new symbol, timeframe or session, Reload, a
  reconnect, a gap fill's reload, another tab and back where that tab shows another symbol, timeframe or session, and
  bar replay starting, jumping or ending. A tab with the same symbol, timeframe and session shows the same bars, and
  they keep their live values (an HTF EMA with other settings there starts anew, as below). Changing an HTF EMA's
  source, length or mode, or turning it off and on, also from another device, starts that one anew the same way
  (NinjaTrader adds the indicator anew); an answer the server was still working on for the old settings is dropped.
  Its color, style and width keep what it shows. Older bars loaded at the left leave the live bars' values as they
  were. Each HTF EMA on a chart keeps its own.
- With a Second source in a quiet market such bars are common: in the parity check's thin market (4 to 10 trades a
  minute) 176 of 330 live bars of a 1m chart with 15 Second, and 25 of 67 of a 5m chart with 45 Second and gaps, keep
  a value a reload draws differently, on NinjaTrader and here alike (no bar differs between the two). Where the
  chart and the source are both Second or Minute bars and the source's bars end on chart bar closes (a whole number of
  chart bars), live and reload draw the same, as does Smooth (this chart's own bars) on any chart. With a Day or
  longer source, or on a tick, range, 1D or longer chart, the first bar after a source close keeps the value without
  it when it has no price change after its opening trade, as on NinjaTrader's live chart: in the parity check, 1 of
  306 live bars of a 1m chart with 1 Day and gaps (the session's first bar, 17:01, with one trade).
- The chart has a closed source bar once the server's answer with the trade after it is in. The server sends bar
  updates to browsers in batches every 150 ms, and the page asks on the first update after the source bar's close, at
  most every 10 seconds (with a Second source shorter than 10 seconds, or a close within 10 seconds of the last ask,
  up to 10 seconds later). Until the answer is in, about 150 ms plus its round trip after that trade, a price change
  keeps the value without the closed source bar where NinjaTrader's counts it. Price changes within one batch reach
  the page as one update, so as one price change (NinjaTrader's later ones in that batch would count a source bar
  that closed in it), and a batch that ends at the price shown before is no price change here. The parity check gives
  the browser each trade as an update, with the answer to it before the next, so it does not cover these.
- Bar replay steps whole bars, each at its close, with no trades inside them: each bar shows what a reload shows.
  NinjaTrader's Playback runs replayed trades as realtime, so its bars would keep their last values as on a live
  chart; a replayed bar here can show a source bar that NinjaTrader's playback counts from the next bar. With
  NinjaTrader's Tick Replay on, its historical bars keep their last values too; history here loads as a reload.

On an RTH chart the higher timeframe follows the chart's hours, as NinjaTrader's added series follows an RTH
trading-hours template: hour bars 08:30-09:30 ... 14:30-15:00 (7 a day, not 23), Day bars of the regular hours, and
the Smooth line's start from the RTH bars before the chart's first.

A seconds chart's length is far more bars than the chart holds (75600 on a 1-second chart), so the server rebuilds
its last 4 lengths of bars from the stored trades, at most 4 million trades a request. Further back, and where only
1-minute bars are stored (bars imported, or a gap filled from a provider without tick history), each minute counts as
the seconds bars that open in it, all at its close and no more than its trades. With the trades stored the line is
NinjaTrader's to well under a tick. From 1-minute bars alone it is close on 15- and 30-second charts but can be a few
points off on 1- and 5-second ones: NinjaTrader's seconds bars exist only where a trade printed, and a minute bar
doesn't say how many seconds did.

Parity check against the original C# (needs mono and node), from `server/`:
`tests/htfema_csharp/build.sh <tatanka-indicators checkout> <workdir>`, then
`python tests/htfema_parity.py <workdir> [days] [5m|1D|t3|15s] [seed] [ES|NQ|CL|GC] ['{"Mode": "ExactHTF"}'] [rth]`
(`rth`: an RTH chart's bars; the settings can name any source, e.g. `'{"HtfPeriodType": "Week", "HtfPeriod": 1}'`).
Add `live` (chart 5m, 1m or 4h) to run it as NinjaTrader runs a live chart with
Calculate.OnPriceChange: every call is checked against what the browser shows then (htfEmaLive, with the server's
answer to that trade), every bar the live chart ends on against the browser's, and a reload of each against the other
(see the script's header for its variants). `tests/test_parity_hema.py` runs a set of these for every source type when `TATANKA_INDICATORS` points
at a checkout and mono is installed.

## Tatanka Daily SMA x4

Studies ▾ → **Daily SMA x4** (off until you turn it on). A port of the NinjaTrader Tatanka Daily SMA x4 (kept
unchanged in `server/tests/dsma_csharp/`): four simple moving averages of real daily closes, 8, 21, 50 and 200 days
by default, on any chart: tick, minute, hour, day, week or month.

- The daily bars come from the 1-minute bars stored here, as CME's daily bars are: one per trading day (17:00-16:00
  CT; a session cut short by a holiday halt joins the next day's, as it has no settlement). On an RTH chart, one per
  regular session, a halt's joining the next day's there too: the RTH 1D chart's bars. Like NinjaTrader's
  `AddDataSeries(Day, 1000 bars)`, at most the last 1000.
- A line only starts once a full window is stored (Pine's `ta.sma`): the 200 needs 200 trading days of 1-minute
  history. The Studies panel says when there are too few.
- Each chart bar shows the SMAs as of the last day that closed by that bar's close, as NinjaTrader runs it: today's
  bars show yesterday's values, and the last bar of a day (and a daily bar) its own. The forming bar shows the
  developing values with the live price; a bar that formed live keeps the values it had then, until the chart
  reloads (NinjaTrader's realtime bars do the same).
- Labels "200 SMA - 6897.25" in each line's color, Label Offset bars right of the last bar, only while the last bar
  is on screen; the chart's right margin grows to fit them. Price markers on the axis; the legend shows each value.
- Settings…: the four lengths (1-1000), Line Width, Show Labels, Label Offset, Label Size, and each plot's color and
  dash style (their width is Line Width, as in the original). Futures only, not on free-data charts.

Parity check against the original C# (needs mono and node), from `server/`:
`tests/dsma_csharp/build.sh <workdir>`, then
`python tests/dsma_parity.py <workdir> [days] [5m|30m|4h|t3] [seed] [ES|NQ|CL|GC] [8,21,50,200]`: history plus 90
realtime minutes, every value equal.

## Tatanka If/Then

Studies ▾ → **If/Then Bias Table**. A port of the NinjaTrader If/Then Bias Table v1.0.0 from
tatankatrading/tatanka-indicators (MPL 2.0). Paste an If/Then block on the **Plan tab**, one band per
line, top band first, and press **Save**:

```
BIAS|LO|HI|TEXT
U|7450|9999999|Accepts above 7450 → continuation, next target 7480
P|7405|7420|Pivot ~7412
D|0|7385|Below 7385 → range break
```

`0` = no bottom, `9999999` = no top, a literal `\n` breaks the text, `#` at the start of a line is a comment.
The block is stored on the server per symbol, so the phone shows what you pasted on the PC (open charts update
right away). A line and a label at each band's bottom; the band price is in now is highlighted, on every tick.
On a phone-width chart the labels are one line each (… where cut, at the chart's edge) and the table shows only the
band price is in, so the chart stays readable; the whole block is on the Plan tab. **Tap a label** (or the table) for
the full text, wrapped to fit; tap again for one line. The same switch is **Full text** in Studies > If/Then (this
device).
Color by **Bias** (U/V/D/P/N) or **Position** (above price = resistance, below = support, within 10% of the
previous day's ATR = neutral). The table and ATR zones are off by default, as in the original.

- **Price source** as NinjaTrader's: Close (default), Open, High, Low, HL2, HLC3, OHLC4, HLCC4, or **Input series**,
  which reads the **Input series** setting (NinjaTrader's Data Series > Input series, at the top of its Settings): the
  bars as a price type (Close by default, Open, High, Low, Median, Typical, Weighted) or a line on this chart: an
  indicator's (EMA 21, Bollinger 14 2: Upper), the session or RTH VWAP and the bands shown, an HTF EMA, or a Daily SMA
  x4 plot. The anchored VWAP and the Tatanka profiles are not offered. It is this chart's own, and Apply to all charts
  leaves it as each chart has it. A line taken off the chart reads Close.
- The table's **Background** and **Border** colors have an opacity, as NinjaTrader honors their alpha. The Position
  colors have none: NinjaTrader sets the zones', lines' and labels' alpha itself, so theirs never shows.
- The header's **Instrument label** starts as the chart's symbol, not NinjaTrader's fixed "ES": NinjaTrader's is typed
  on each chart, while here it is kept per symbol with the block, so each symbol's starts as its own name (ES shows
  ES, as NinjaTrader's default does).

Labels sit at the chart's right edge, so they stay in the same spot when you zoom or scroll (NinjaTrader draws them
75 bars after the last candle, which moves them as the bars get wider or narrower). The chart keeps Label Offset bars
of room right of the last candle while labels are shown (75); lower it in Settings… to take that room back. The previous day's ATR comes from your stored 1-minute bars.

Parity check against the original C# (needs mono and node), from `server/`:
`tests/ifthen_csharp/build.sh <tatanka-indicators checkout> <workdir>`, then `python tests/ifthen_parity.py <workdir>`.
