#!/bin/sh
# Builds harness.exe for tests/vp_parity.py: Harness.cs plus the calculation cores of the three NinjaTrader
# indicators (the parts between their BEGIN/END STANDALONE ... CORE markers): the copies kept here, or a
# tatanka-indicators checkout's. A Session Volume Profile core with InstrumentHours (v1.1.0, Use Instrument Hours) builds
# with HOURS: the harness then takes the root and the setting on its V lines; one with ZoneWindows (the window moved
# outside Central time too) also with ZONES.
#   tests/vp_csharp/build.sh <workdir> [<tatanka-indicators checkout>]
set -eu
HERE="$(dirname "$0")"
OUT="$1"
SRC="${2:+$2/ninjatrader}"
SRC="${SRC:-$HERE}"
mkdir -p "$OUT"
core() { sed -n "/^\/\/ BEGIN STANDALONE $2/,/^\/\/ END STANDALONE $2/p" "$SRC/$1"; }
DEFINE=''
if core TatankaSessionVolumeProfile.cs "PROFILE CORE" | grep -q 'class InstrumentHours'; then DEFINE='-define:HOURS'; fi
if core TatankaSessionVolumeProfile.cs "PROFILE CORE" | grep -q 'class ZoneWindows'; then DEFINE="$DEFINE -define:ZONES"; fi
{
  cat "$HERE/Harness.cs"
  core TatankaAutoAnchoredVolumeProfile.cs "AUTO ANCHORED PROFILE CORE"
  core TatankaPeriodicVolumeProfile.cs "PERIODIC PROFILE CORE"
  core TatankaSessionVolumeProfile.cs "PROFILE CORE"
} | tr -d '\r' > "$OUT/Vp.cs"
mcs -langversion:Latest -optimize+ $DEFINE -out:"$OUT/harness.exe" "$OUT/Vp.cs"
echo "built $OUT/harness.exe"
