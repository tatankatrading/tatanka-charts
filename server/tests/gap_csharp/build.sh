#!/bin/sh
# Builds harness.exe for tests/gap_parity.py from the original NinjaTrader Gap Analysis source.
#   tests/gap_csharp/build.sh <tatanka-indicators checkout> <workdir>
# The engine is the source's own ENGINE_BEGIN..ENGINE_END region, taken as is: v1.0.0, or v1.1.0 with its
# InstrumentHours (Use Instrument Hours; the harness takes the root and the setting then: HOURS).
set -eu
SRC="$1/ninjatrader/TatankaGapAnalysis.cs"
OUT="$2"
if grep -q 'Tatanka Gap Analysis v1.0.0' "$SRC"; then DEFINE=''
elif grep -q 'Tatanka Gap Analysis v1.1.0' "$SRC"; then DEFINE='-define:HOURS'
else echo "expected Gap Analysis v1.0.0 or v1.1.0 in $SRC" >&2; exit 1
fi
mkdir -p "$OUT"
{
  printf 'using System;\nusing System.Collections.Generic;\nusing System.Globalization;\n'
  sed -n '/^\/\/ ENGINE_BEGIN/,/^\/\/ ENGINE_END/p' "$SRC"
} | tr -d '\r' > "$OUT/Engine.cs"
cp "$(dirname "$0")/Harness.cs" "$OUT/"
mcs -langversion:Latest -optimize+ $DEFINE -out:"$OUT/harness.exe" "$OUT/Engine.cs" "$OUT/Harness.cs"
echo "built $OUT/harness.exe"
