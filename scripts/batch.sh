#!/bin/bash
# Batch coverage for a finished campaign: replay every input with drcov in N
# parallel containers, then compute the metrics once.
#
#   scripts/batch.sh <target> <campaign dir (host)> [run dir (host) | -] [shards=8] [cutoff=60] [extra rosacov metrics args...]
#   run dir defaults ("-") to <campaign>/rosa-out/coverage/batch
#
# Example
#   scripts/batch.sh domoticz ~/Lab/ROSA/TEST/campaigns/domoticz-1 - 8 60
#
# Host paths must be under ~/Lab/ROSA/TEST (mounted at /root/TEST). Logs are kept
# (batch mode). Refuses to touch a run directory that already has summary.json.
set -eu
# repo root: R2D lives at <root>/TEST/analysis/coverage/R2D, so derive it from this script's location
ROSA=${ROSA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)}
RND=$ROSA/TEST/analysis/coverage/R2D
target=$1; camp=$2; run=${3:--}; shards=${4:-8}; cutoff=${5:-60}; shift 5 2>/dev/null || shift $#
[ "$run" = - ] && run="$camp/rosa-out/coverage/batch"
c2r() { echo "/root/TEST${1#$ROSA/TEST}"; }
[ -f "$run/summary.json" ] && { echo "$run already has summary.json -- pick another run dir (no overwrite)"; exit 1; }
n_inputs=$(ls "$camp/rosa-out/traces" | grep -vc '\.trace$\|README')
echo "$target: $n_inputs inputs; drcov logs are ~$(python3 -c "print(round($n_inputs*2.9/1024,1))") GB for domoticz-sized targets. $(df -h "$ROSA" | awk 'NR==2{print "free "$4}')"
mkdir -p "$run"
for i in $(seq 0 $((shards-1))); do
  "$RND/scripts/launch.sh" "$target" "cov-$target-$i" --detach -- replay "$target" \
      --rosa-out "$(c2r "$camp")/rosa-out" --run "$(c2r "$run")" --shard "$i/$shards" >/dev/null
done
echo "started $shards replay containers (cov-$target-0..$((shards-1)))"
for i in $(seq 0 $((shards-1))); do docker wait "cov-$target-$i" >/dev/null; done
for i in $(seq 0 $((shards-1))); do docker logs "cov-$target-$i" 2>&1 | tail -1; done
"$RND/scripts/launch.sh" "$target" "cov-$target-metrics" -- metrics "$target" \
    --run "$(c2r "$run")" --campaign "$(c2r "$camp")" --cutoff "$cutoff" "$@"
