#!/bin/sh
# Builds harness.exe for tests/htfema_parity.py from the original NinjaTrader HTF EMA source.
#   tests/htfema_csharp/build.sh <tatanka-indicators checkout> <workdir>
# Pastes the mode enum, the fields, the State.DataLoaded lines (as DataLoaded()) and OnBarUpdate with the helpers after
# it (to DisplayName) of TatankaHtfEma.cs between the stand-ins; any version that has those (1.0.0 on).
set -eu
SRC="$1/ninjatrader/TatankaHtfEma.cs"
OUT="$2"
HERE="$(dirname "$0")"
VERSION=$(sed -n 's/.*Tatanka 1hr 21 EMA (HTF EMA) v\([0-9.]*\) .*/\1/p' "$SRC" | tr -d '\r')
[ -n "$VERSION" ] || { echo "no HTF EMA version line in $SRC" >&2; exit 1; }
mkdir -p "$OUT"
tr -d '\r' < "$SRC" > "$OUT/src.cs"
{
  cat "$HERE/Head.cs"
  sed -n '/^public enum TatankaHtfEmaMode/,/^}/p' "$OUT/src.cs"
  cat "$HERE/Mid.cs"
  sed -n '/private EMA htfEmaInd;/,/protected override void OnStateChange()/p' "$OUT/src.cs" | sed '$d'
  echo '    void DataLoaded()'
  echo '    {'
  sed -n '/else if (State == State.DataLoaded)/,/^\t\t\t}/p' "$OUT/src.cs" | sed '1,2d;$d'
  echo '    }'
  sed -n '/protected override void OnBarUpdate()/,/public override string DisplayName/p' "$OUT/src.cs" | sed '$d'
  cat "$HERE/Tail.cs"
} > "$OUT/HtfEma.cs"
for want in 'enum TatankaHtfEmaMode' 'void DataLoaded()' 'htfEmaInd[[:space:]]*= EMA' 'void OnBarUpdate()' 'int GetScaledLength()'; do
  grep -Eq "$want" "$OUT/HtfEma.cs" || { echo "could not paste '$want' from $SRC" >&2; exit 1; }
done
mcs -langversion:Latest -optimize+ -out:"$OUT/harness.exe" "$OUT/HtfEma.cs"
echo "built $OUT/harness.exe from HTF EMA v$VERSION"
