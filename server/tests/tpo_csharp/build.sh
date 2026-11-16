#!/bin/sh
# Builds harness.exe for tests/tpo_parity.py from the original NinjaTrader TPO Profile source.
#   tests/tpo_csharp/build.sh <tatanka-indicators checkout> <workdir>
# Pastes the NinjaTrader-free parts of TatankaTpoProfile.cs (fields, OnBarUpdate, session/row math, save and value
# area, the row/profile classes; from v1.1.0 also Use Instrument Hours' RunSessionTime, SetSessionTime, SessionAt and
# InstrumentHours) between Head.cs and Tail.cs. The line numbers are each version's: v1.0.0 and v1.1.0.
set -eu
SRC="$1/ninjatrader/TatankaTpoProfile.cs"
OUT="$2"
HERE="$(dirname "$0")"
if grep -q 'Tatanka TPO Profile v1.0.0' "$SRC"; then
  LINES='27,41p;101,172p;175,283p;297,302p;306,413p;818,832p'; DEFINE=''
elif grep -q 'Tatanka TPO Profile v1.1.0' "$SRC"; then
  LINES='27,45p;107,178p;180,316p;319,427p;441,446p;450,557p;965,979p'; DEFINE='-define:HOURS'
else
  echo "expected TPO Profile v1.0.0 or v1.1.0 in $SRC" >&2; exit 1
fi
mkdir -p "$OUT"
{
  cat "$HERE/Head.cs"
  sed -n "$LINES" "$SRC" | sed 's/^\t//'
  cat "$HERE/Tail.cs"
} | tr -d '\r' > "$OUT/Tpo.cs"
mcs -langversion:Latest -optimize+ $DEFINE -out:"$OUT/harness.exe" "$OUT/Tpo.cs"
echo "built $OUT/harness.exe"
