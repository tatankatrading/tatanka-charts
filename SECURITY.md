# Security policy

## Supported versions

The latest version on `main`. `update.cmd` (Windows) or `update.sh` (Mac, Linux) gets it; fixes are not
back-ported to older copies.

## Reporting a vulnerability

Please report it privately, not in a public issue: on GitHub, this repository's **Security** tab >
**Report a vulnerability**, or by email to tatankatradingllc@gmail.com. Say what it is, how to reproduce it, and what someone could do with it. Leave out your
own keys, tokens, passwords and addresses.

This is a small project: expect a first answer within about a week. A confirmed problem is fixed on `main`, and the
report is credited when the fix is out, if you want that.

## How the app is meant to run

Knowing this helps tell a vulnerability from a setup that was never meant to be safe:

- The server listens on 127.0.0.1 only. Other devices reach it through Tailscale (`tailscale serve`), never the
  public internet (not `tailscale funnel`, not a port forward). See [docs/PHONE.md](docs/PHONE.md).
- A password (Connections > Login) is needed before any device other than the PC can use it; sessions are per
  device and can be signed out from the Login card.
- Keys, tokens and webhook URLs are stored in the server's database (`chart.db`), never shown again in the app and
  never logged. Backups contain them: keep the backups folder private.
- It reads market data and never places orders.

Things in scope: getting in without the password, reading keys or tokens back out, the add-on or a feed plugin
being made to run code, and anything that lets a page on another site act on the app.
