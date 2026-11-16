#!/bin/sh
# Builds harness.exe for tests/amt_parity.py from the original NinjaTrader AMT Toolkit source.
#   tests/amt_csharp/build.sh <tatanka-indicators checkout> <workdir>
# Takes the NinjaTrader-free parts of TatankaAmtToolkit.cs v1.2.4 (Settings through Engine with InstrumentHours,
# LevelsDocument, LevelWatch/LevelAlertTracker), found by their declarations. Ticks.cs takes the indicator's own choice
# of the sessions whose prior-day value comes from ticks (TickDay, RthWindow, RequestTickDays, RequestClosedSession,
# TickValue: Value From Ticks (Sessions)), what it does when they come late (PatchPrevDay and its samples: Sample,
# ToSample, RenderHistory) and the levels link's schedule (LevelLink.NextAsk and what it calls), each found by its
# declaration and copied whole; Harness.cs gives them the few members of the indicator they read.
set -eu
SRC="$1/ninjatrader/TatankaAmtToolkit.cs"
OUT="$2"
grep -q 'Version = "1.2.4"' "$SRC" || { echo "expected AMT Toolkit v1.2.4 in $SRC" >&2; exit 1; }
# the first two namespace blocks (Settings ... Engine), LevelsDocument, LevelWatch through LevelAlertTracker
RANGES=$(tr -d '\r' < "$SRC" | awk '
/^namespace TatankaTrading\.AmtToolkit/ { ns++; if (ns <= 2) s[ns] = NR; next }
/^}/ { for (i = 1; i <= 2; i++) if (s[i] && !e[i] && NR > s[i]) { e[i] = NR; break } }
/public sealed class LevelsDocument/ { ld = NR }
ld && !lde && /^    }/ && NR > ld { lde = NR }
/public sealed class LevelWatch/ { lw = NR }
lw && !lwe && /^}/ && NR > lw { lwe = NR - 1 }
END { printf "%d,%dp;%d,%dp %d,%dp;%d,%dp", s[1], e[1], s[2], e[2], ld, lde, lw, lwe }')
ENGINE=${RANGES% *}
LEVELS=${RANGES#* }
mkdir -p "$OUT"
{
  printf 'using System;\nusing System.Collections.Generic;\nusing System.Globalization;\nusing System.Linq;\nusing System.Text.RegularExpressions;\n'
  sed -n "$ENGINE" "$SRC"
  printf 'namespace TatankaTrading.AmtToolkit\n{\n'
  sed -n "$LEVELS" "$SRC"
  printf '}\n'
} | tr -d '\r' > "$OUT/Engine.cs"
# one member: its first line through the line where its braces close (a field: its line)
member() {
  n=$(tr -d '\r' < "$SRC" | awk -v re="$1" '
    !on && $0 ~ re { on = 1; d = 0; o = 0 }
    on { print; t = $0; a = gsub(/\{/, "", t); b = gsub(/\}/, "", t); d += a - b; if (a) o = 1
         if ((o && d <= 0) || (!o && $0 ~ /;[ \t]*(\/\/.*)?$/)) exit }' | tee -a "$OUT/Ticks.cs" | wc -l)
  [ "$n" -gt 0 ] || { echo "no $1 in $SRC" >&2; exit 1; }
}
printf 'using System;\nusing System.Collections.Generic;\nusing System.Globalization;\nusing System.Linq;\nusing System.Text.RegularExpressions;\nusing System.Threading;\nnamespace TatankaTrading.AmtToolkit\n{\n    public sealed partial class TickSource\n    {\n' > "$OUT/Ticks.cs"
member '^        private sealed class TickDay'
member '^        private bool RthWindow\('
member '^        private void RequestTickDays\('
member '^        private void RequestClosedSession\('
member '^        private List<KeyValuePair<double, double>> TickValue\('
member '^        private void PatchPrevDay\('
member '^        private sealed class Sample'
member '^        private Sample ToSample\('
printf '    }\n' >> "$OUT/Ticks.cs"
member '^    public sealed class RenderHistory<T>'
printf '    public static class LinkSchedule\n    {\n' >> "$OUT/Ticks.cs"
member '^        private static readonly Regex PublishedLine'
member '^        private static readonly TimeSpan\[\] publishTimes'
member '^        public static DateTime Published\('
member '^        public static DateTime NextAsk\('
member '^        private static bool InHand\('
member '^        private static IEnumerable<DateTime> PublishesAround\('
member '^        private static TimeSpan Shorter\('
printf '    }\n}\n' >> "$OUT/Ticks.cs"
sed -i 's/^        private static DateTime NextAsk/        public static DateTime NextAsk/; s/^        private static bool InHand/        public static bool InHand/' "$OUT/Ticks.cs"
cp "$(dirname "$0")/Harness.cs" "$OUT/"
mcs -langversion:Latest -optimize+ -r:System.Core.dll -out:"$OUT/harness.exe" "$OUT/Engine.cs" "$OUT/Ticks.cs" "$OUT/Harness.cs"
echo "built $OUT/harness.exe"
