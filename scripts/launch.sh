#!/bin/bash
# Start a container for a target and run a rosacov command in it.
#
#   scripts/launch.sh <target> <container-name> [--detach] -- <rosacov args...>
#
# Examples
#   scripts/launch.sh domoticz rosa-domoticz --detach -- campaign domoticz --dir /root/TEST/campaigns/domoticz-1 --fuzz 18600 --live
#   scripts/launch.sh dlink cov-dlink-0 -- replay dlink --rosa-out /root/TEST/Full_Hour/results/dlink/rosa-out --run /root/TEST/runs/x/batch --shard 0/8
#
# Mounts (see targets.toml header):  TEST -> /root/TEST, rosarum/targets -> /root/rosarum/targets,
# New_Bench -> /root/New_Bench, DynamoRIO -> /opt/dr. Network is always off.
# Isolated targets (isolate = true): no bind mounts; New_Bench and this package are
# docker-cp'd in (plus any --copy host:container), and the caller copies results
# out (scripts/collect.sh).
set -eu
# repo root: R2D lives at <root>/TEST/analysis/coverage/R2D, so derive it from this script's location
ROSA=${ROSA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)}
RND=$ROSA/TEST/analysis/coverage/R2D
DR=$ROSA/TEST/tools/DynamoRIO-Linux-11.91.20615
target=$1; cname=$2; shift 2
detach=""; copies=()
while [ "${1:-}" != "--" ] && [ -n "${1:-}" ]; do
  case $1 in
    --detach) detach="-d" ;;
    --copy) copies+=("$2"); shift ;;      # host:container, for isolated targets (docker cp before start)
    *) echo "unknown option $1"; exit 1 ;;
  esac
  shift
done
[ "${1:-}" = "--" ] && shift

python3 -I "$RND/bin/rosacov" targets --export "$RND/targets.json" >/dev/null   # for Python 3.10 containers
read -r image isolate < <(python3 -I - "$RND/targets.json" "$target" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))["targets"][sys.argv[2]]
print(d["image"], "1" if d.get("isolate") else "0")
EOF
)
docker rm -f "$cname" >/dev/null 2>&1 || true
common=(--name "$cname" --network none --hostname fuzzhost --add-host fuzzhost:127.0.0.1
        -e HOST_UID="$(id -u)" -e PYTHONPATH=/root/TEST/analysis/coverage/R2D -e ROSACOV_TARGETS=/root/TEST/analysis/coverage/R2D/targets.json
        -v "$DR:/opt/dr/DynamoRIO-Linux-11.91.20615:ro")
if [ "$isolate" = 1 ]; then
  docker create "${common[@]}" "$image" bash -c 'bash /root/TEST/analysis/coverage/R2D/harness/build.sh >/dev/null 2>&1; exec python3 -m rosacov.cli "$@"' -- "$@" >/dev/null
  tmp=$(mktemp -d)
  mkdir -p "$tmp/TEST/analysis/coverage"
  cp -a "$RND" "$tmp/TEST/analysis/coverage/R2D"
  cp -a "$ROSA/TEST/New_Bench" "$tmp/New_Bench"
  rm -rf "$tmp/New_Bench/results" "$tmp/New_Bench/domotcz"
  docker cp "$tmp/TEST" "$cname:/root/TEST" >/dev/null
  docker cp "$tmp/New_Bench" "$cname:/root/New_Bench" >/dev/null
  rm -rf "$tmp"
  for c in "${copies[@]:-}"; do
    [ -n "$c" ] || continue
    docker exec "$cname" true 2>/dev/null || true
    docker cp "${c%%:*}" "$cname:${c#*:}" >/dev/null
  done
  docker start $( [ -n "$detach" ] || echo -a ) "$cname"
else
  docker run $detach "${common[@]}" \
    -v "$ROSA/TEST:/root/TEST" -v "$ROSA/rosarum/targets:/root/rosarum/targets" \
    -v "$ROSA/TEST/New_Bench:/root/New_Bench" \
    "$image" bash -c 'bash /root/TEST/analysis/coverage/R2D/harness/build.sh >/dev/null 2>&1; exec python3 -m rosacov.cli "$@"' -- "$@"
fi
