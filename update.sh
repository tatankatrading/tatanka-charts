#!/bin/sh
# Updates Tatanka Charts (Mac, Linux): the newest version, then the server rebuilt and restarted. Open pages reload
# by themselves. (NinjaTrader runs on Windows: there, update.cmd also copies its add-on in.)
set -e
cd "$(dirname "$0")"
echo "1/2  Getting the newest version (git pull)..."
git pull --ff-only --autostash   # your own edits (config/holidays.json, docs/DATA.md) go back on top of the new version
clash=$(git diff --name-only --diff-filter=U)
for f in $clash; do   # yours and the update's on the same lines: the update's is used, yours kept in git's stash
  if git cat-file -e "HEAD:$f" 2>/dev/null; then
    git checkout HEAD -- "$f"
    echo "     Your change to $f clashed with the update's: the update's is in place, yours is kept ('git stash show -p')."
  else   # the update removed a file you changed
    git rm -q -- "$f"
    echo "     The update removed $f, which you had changed: yours is kept ('git stash show -p')."
  fi
done
echo "2/2  Rebuilding and restarting the server (docker compose)..."
CHART_COMMIT=$(git rev-parse HEAD) docker compose build
# Trades a NinjaTrader started mid-session stored twice before add-on 23 (docs/CONNECTIONS.md, "Each trade once"),
# looked for since the last update's look. Exit 3: some found and listed, asked about below; else: nothing to ask.
found=0
docker compose run --rm chart python -m chartsrv.fix_catchup --update || found=$?
docker compose up -d
# asked with the new server running; no terminal (or no answer): not asked, nothing remembered, the next update asks
if [ "$found" -eq 3 ] && [ -t 0 ]; then
  printf 'Fix the trades stored twice listed above now? The server stops for a few minutes. [y/N] '
  if read -r answer; then
    case "$(printf '%s' "$answer" | tr -d ' \t')" in
      [yY]*) if docker compose stop chart; then
               docker compose run --rm chart python -m chartsrv.fix_catchup --update --yes ||
                 echo "     Not fixed (see above)."
             else
               echo "     Not fixed: the server did not stop."
             fi
             docker compose up -d ;;
      *) docker compose run --rm chart python -m chartsrv.fix_catchup --decline || true ;;
    esac
  fi
fi
echo "Done. Open pages and the phone app reload by themselves in a few seconds."
