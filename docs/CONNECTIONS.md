# Connections, instruments, backups

Every feed, key and alert channel, the chart instruments, branding and backups: the dot at the top right.

## Connections

The **dot** at the top right (green: connected to the server) opens **Connections**: every data feed, data key and
alert channel, with its state (green working, yellow waiting, red failing, grey off) and a **Test** button that
tries what you typed before you save it. **Save** applies at once: that feed restarts, the others keep running.

**More feeds** (other brokers, data vendors, a program of your own): [FEEDS.md](FEEDS.md). Interactive
Brokers futures, Databento and "Your own program (TCP lines)" are under the **+** of Futures feed; each supplies the
instruments in its **Futures** field, NinjaTrader the rest. Anyone can add a feed as a small Python file in `feeds/`.

It is one list per kind: **Futures feed**, **Quotes**, **Data**, **Alerts and Discord**, **App**. Each folds open or
shut (tap its name; it stays as you left it); folded, it shows what is on. The main connections are always listed;
the **+** on a list adds the others (ntfy, Heartbeat) and **more than one** Discord alert channel, Discord chart-post
channel or ntfy topic, each with its own name. Every alert goes to every alert channel. With two or more chart-post
channels, Post to Discord asks which (one menu item each). **Delete** removes an added channel; **Remove from list**
clears and turns off one that isn't a main connection. A feed added with **+** stays on its list, on or off, until
**Remove from list** deletes its settings.

| Connection | What it gives | Account |
|---|---|---|
| NinjaTrader | live futures trades, gap fill, futures quotes, fills and positions | your NinjaTrader; the add-on's token here |
| Interactive Brokers | watchlist quotes and charts (history, real time) of stocks, ETFs, indices, FX, crypto, other futures; first for charts, Yahoo behind it | IB Gateway or TWS, API on (read-only), from Docker its address under Trusted IPs ([FEEDS.md](FEEDS.md)); IBKR market data subscriptions |
| Alpaca | real-time US stock and ETF quotes (IEX; SIP with a paid plan) | free at alpaca.markets: API key ID and secret |
| Coinbase | real-time crypto quotes | none |
| Yahoo Finance | backup quotes for everything; charts of symbols no other feed has | none |
| FRED | economic series; with a key the search finds all of them | key optional (free) |
| EIA | weekly petroleum and natural gas data | free key from eia.gov/opendata |
| App notifications, Discord, ntfy, Heartbeat | alert delivery (see [Alerts](ALERTS.md)) | |

**Branding** (App, above Updates) sets the app's name (header, sign-in, browser tab, Discord chart posts: "<name> Charts")
and its logo (a PNG, JPEG or WebP up to 200 KB; about 28 px high in the header; chart pictures carry it too), and
whether chart pictures say "Powered by Tatanka Charts" (on by default). Tatanka's by default; **Defaults** puts those
back. It applies on every device at once and is in the backups.

**Check all** (top of Connections) tries every connection in use at once (alert channels get a test message) and
**Copy report** copies the result, with no keys or URLs in it. The same from the PC, also with the login on:

```
docker compose exec chart python -m chartsrv.check
```

Values saved here win over `.env`; **Use .env** forgets the saved one, **Clear** turns a key or URL off whatever
`.env` says. Keys, tokens and URLs are kept in the server's database (`chart.db`), never shown again (the NinjaTrader
token only goes into the add-on downloaded from its card) and never logged. The trade feed stays the simulator when
`.env` says `CHART_FEED=sim`, so invented and real trades never mix. The NinjaTrader port (`CHART_NT_PORT`, 8801) is set in `.env`
and `docker-compose.yml` (Docker publishes it: change both). Read-only everywhere: the app never sends an order to any of them.

## Instruments

**You rarely need this list.** With NinjaTrader as the feed, opening any future from `config/futures.json` (search
"RTY", type it on the chart, or click RTY1! in a watchlist) adds it by itself: live at once, no restart, NinjaTrader
sends its trades and its last 90 days as 1-minute bars, and it stays on the list. Only the futures you open are
stored, so the database doesn't grow with markets you never look at.

**Connections > App > Instruments** lists the futures charted from the server's own trades: symbol (the root as
NinjaTrader names it: RTY, YM, SI, ZN), name, tick size, point value, regular hours (CT; for RTH VWAP, profiles and
the RTH view; empty for none), holiday group and where the simulator starts it. **+ Instrument** opens a list of
CME futures with their specs filled in (`config/futures.json`: equity index, energy, metals, interest rates,
currencies, crypto): search by symbol or name ("russell", "yen", "10-year") and tap one; **Other…** types one in. **Edit** opens one, **×** takes it off
the list (its stored data and alerts stay, and the alerts run again once it is back), **Save** checks and keeps the list on the server and applies it: a future added is live at once; a change or a
removal restarts the server in place and every open page comes back by itself in a few seconds. NinjaTrader sends a
new one's trades, and its last 90 days as 1-minute bars. **File's list** goes back to `config/instruments.json`.
Every instrument runs on CME Globex hours (17:00-16:00 CT); products with other hours (grains) are not supported.

Rollover: nothing to edit. Each future's **Contract** (Edit) says how the NinjaTrader add-on rolls it:

- **By volume** (the default): the add-on also watches the next contract's day volume, and in the 16:00-17:00 CT
  break (or a weekend) after a session in which the next contract traded more, it switches, so the whole next session
  is on the new contract. If the volumes never come, it rolls two days after NinjaTrader's roll date. It never goes
  back to an older contract. After NinjaTrader restarts it carries on from the contract the server charted last, and
  sends no trades until the server has said which one that is, so a restart never charts the other contract.
- **NinjaTrader's roll dates**: the front month by NinjaTrader's own rollover table (Tools > Instruments > ES >
  Contract months), switching in the break before each roll date.
- **A fixed month** (MM-YY, e.g. `03-27`): that contract until you change it.

A change applies at once (no restart). The list shows the contract each one is on now. The months in the add-on's
`Contracts` list are only a starting point; any month NinjaTrader knows will do. A roll sends a "Contract roll"
message to Discord/phone, and `/api/health` shows the contract each symbol is on. Bars before a roll are not adjusted, so the chart steps by
the spread between the contracts at the roll (as NinjaTrader's unadjusted merge does). That step crosses no line or
level for the alerts: price, level, study, If/Then and indicator alerts start again from the new contract's first
trade, the AMT Toolkit's alerts 15 minutes after the roll, and a gap from the old contract's RTH close to the new
one's open is not alerted (nor its fill), though Gap Analysis still draws it.

Each trade once: the add-on has a market-data subscription of its own per contract, as NinjaTrader's add-on
framework has it (an earlier version listened on the instrument's shared one, and a day forwarded that way came out
at twice NinjaTrader's volume). Once 5,000 contracts have traded after it starts, NinjaScript Output says
`NQ volume check OK: sent …, NinjaTrader counted …`; if the two ever disagree it says so instead (`trades are not
reaching the server one for one`), every 30 minutes while they do. A NinjaTrader started mid-session sends its last
trades again as a one-time catch-up that its gap fill's history also holds: with add-on 23 the server keeps those
once, on the conditions in [NINJATRADER.md](NINJATRADER.md#the-ninjatrader-feed) (a run of 50 or more matching the
history, with 5 or more different prints, in the first 30 s); add-on 22 and older store them twice. A history that
comes as 1-minute bars (a stretch over 3 days, no tick history, or its trades failing) is not compared: the catch-up
counts twice in those minutes' volume. The volume check still counts the catch-up (NinjaTrader's count it starts from
holds it already), so after a mid-session start that warning comes with add-on 23 too (as on Nov 16: ES sent 13,527
while NinjaTrader counted 5,010), every 30 minutes until the day's later trades bring the two within 25%: it does not
mean the server stored those trades twice (`/api/health` shows how many it kept once, `copies` under `gap_fill`).

A catch-up stored twice before add-on 23 (a NinjaTrader started mid-session with add-on 22 or older) is looked for by
`update.cmd` (and `update.sh`) on each update, since its last look (the first time, the last 90 days): it lists what
it finds and, once the new server runs, asks before fixing it (the server stops for a few minutes meanwhile). Either
answer, or nothing found, moves the last look to now. The update that brings this in still runs the old update
script, so the first look is on the next `update.cmd` (run it twice to look at once). To fix them yourself (or look
further back),
stop the server first. The first `fix_catchup` lists each one it finds (when, how many trades and contracts) and
changes nothing; with `--yes` it backs up chart.db (it checks there is room for the copy first), then fixes. It looks
at the last 30 days; `--days N` for more:
```
docker compose stop chart
docker compose run --rm chart python -m chartsrv.fix_catchup
docker compose run --rm chart python -m chartsrv.fix_catchup --yes
docker compose up -d
```
It compares, as add-on 23 does, the catch-up (the burst of live trades sent right after a gap fill) with the gap fill
history's trades before it, and only removes a live trade inside a run of 50 or more matching the history one for one
(with 5 or more different prints): the history's copy stays, at the exchange's time, with the live trade's aggressor
side, and every minute either was in is built again. Where it can't tell, it leaves a trade twice: a catch-up whose
history came as 1-minute bars, or that came with no aggressor side when the history was written first. The dry run's
contracts for a day should come close to the excess NinjaScript Output's volume check showed for it.

Without Docker, from `server`: `python -m chartsrv.fix_catchup [--yes] [--days N]`. The backup is `chart.db.bak-<date>`
next to chart.db (in Docker, inside its data volume: `docker compose run --rm chart rm /data/chart.db.bak-...` once
the charts look right).

NinjaTrader must be running and connected to its data feed. Market Replay (Playback) and the Simulated
Data Feed are never forwarded: while either is connected, nothing is sent (disconnect the Simulated Data Feed
when you connect the live one). While the server is down the add-on buffers up to 200k trades and sends them on
reconnect; those older trades are charted but never fire alerts (a new gap among them is sent with the next live
trade, if its first minute is under 6 minutes old). If NinjaTrader stops sending, the stale-feed alert fires after
`CHART_STALE_AFTER_S`.

Compiled here against stand-ins for NinjaTrader's classes, not NinjaTrader itself: if F5 shows errors, open an issue
with a screenshot of them.

## Backups

Every day the server writes a backup of what you made (alerts and their history, the newest 10,000 fired, drawings,
watchlists, workspaces, indicator and chart settings, Connections with their keys, notification devices, fills and
positions, each day's daily levels kept for replay) to the backups folder on the PC (`BACKUP_DIR` in `.env`), and keeps the newest 30 (about 50 KB each). Market data (ticks, 1m bars)
is left out: it is large and comes back from NinjaTrader's gap fill or a Databento import. **Connections > Backups**
shows them, **Back up now**, and **Download** (with Login on) to keep one elsewhere. The backups hold your keys: keep the folder
private, out of git and out of shared drives.

**Keep each trade** (1, 2, 3, 5 or 10 years, or always; 5 by default): each day in CME's 16:00 CT break the server
removes older trades a slice at a time, folds the database's write-ahead log back in and refreshes its statistics.
1-minute bars are always kept, so candle charts reach back as far as ever; tick, footprint and tape detail goes for
the older days. The file doesn't shrink: the freed space is reused, so it stops growing. Trades imported from
Databento files can be imported again from those files.

The card also shows the database's size and the free space where it lives. Under 5 GB free it turns red and an alert
goes out once a day on every channel, before new trades stop being saved. In Docker that is Docker's own disk; Docker
Desktop's disk sits on the PC's C: drive, so keep an eye on that too.

Restore one (the server stopped, since it holds these in memory):

```
docker compose stop chart
docker compose run --rm chart python -m chartsrv.backup restore tatanka-20261018-040000.db --yes
docker compose start chart
```

What it replaces is saved first as a `-before-restore` backup. The market data stays, and so do the settings about
it: whether the simulator was left, the setup guide, the contracts and their rolls. One exception: a backup from an
install off the simulator (it had left it, or ran NinjaTrader from `.env`), restored onto one still on it (a new PC's
fresh install), leaves the simulator at the next start, as **Leave the simulator** does, so the restored alerts never
fire at its invented prices. The prices it made up for the stock and formula alerts go with the restore.

The login stays too when this install has had one: its password (or none, if the login was turned off since, as when
you forgot it), signed-in devices and app-notification devices are left as they are. So a device signed out since the
backup stays signed out, and app notifications removed since stay removed. An install that never had a password (a new
PC's fresh install) takes the backup's password, with its signed-in and notification devices.

`docker compose exec chart python -m chartsrv.backup full tatanka-full.db` copies the whole database, market data too,
into the backups folder (a name alone goes there; it can be large).

## Updates

**Connections > App > Updates** checks every 6 hours whether the repository's `main` branch has commits this
server wasn't built from, and lists what changed (each commit's first line). Taking it is still `update.cmd` on the
PC: the server can't rebuild itself.

- It knows its own version because `update.cmd` (and `update.sh`) pass the commit to the build. A server built with a
  plain `docker compose up -d --build` says its version is unknown until the next `update.cmd`.
- **Repository**: `owner/name` on GitHub (`CHART_UPDATE_REPO` in `.env` sets the default).
- **GitHub token**: only for a private repository. github.com > Settings > Developer settings > Fine-grained tokens >
  Generate new token: Repository access "Only select repositories" (this one), Permissions > Contents: Read-only.
  Paste it here; it stays on the server. **Test** checks now, and a newer version it finds shows on open pages at once.

Open pages show a small card, **Update available**: how many changes, the newest ones, and what to do: run
`update.cmd` on the PC that runs Tatanka Charts (Mac or Linux: `./update.sh`); it says at the end if NinjaTrader needs
anything. It isn't a dialog you must answer: the chart keeps working around it.

- **Where**: at the bottom of the side panel, beside the chart, with the newest five changes. With the panel hidden, or
  on a window 760 px wide or less, it sits over the chart at the bottom right (on a phone, from 16 px in on the left to
  the price axis) and lists three (on a phone two), each cut to a line; the rest are counted. On a window under 520 px
  tall, or a phone under 360 px wide, it gives the count alone.
- Over the chart it stops short of the chart's price axis, so the prices, the live price and its bar countdown are
  never under it. It moves as the axis does: wider prices (NQ's 25000.00 against ES's 6896.25), another symbol or
  timeframe, log or %, the window resized or the phone turned, the panel shown or hidden. In a layout with the panel
  hidden it stays inside the chart it sits over (normally the bottom right one), short of that chart's axis, so the
  charts beside it are clear too; where that chart is too narrow for the titles (three charts across on a window under
  about 1000 px), it gives the count alone.
- **Later** hides it on this device for 24 hours; a newer version brings it back at once. **Esc** does the same while
  the card has the keyboard focus.
- **Got it** hides it on this device until there is a newer version.
- **Alt+U** (Option+U on a Mac) takes the keyboard focus to the card, from the chart or a chart of a layout; a screen
  reader hears that when the card shows.
- **All changes** opens Connections at App > Updates, which lists up to ten.
- With several charts open in a layout, the page shows one card, not one per chart.
- A browser that keeps no site data (private mode, storage full) still closes it with Later or Got it, until the page
  is opened again.
