#!/bin/bash
# Start a container for a target and run a rosacov command in it.
#
#   scripts/launch.sh <target> <container-name> [--detach] [--copy host:container]... -- <rosacov args...>
#
# Examples
#   scripts/launch.sh domoticz rosa-domoticz --detach -- campaign domoticz --dir /root/R2D/campaigns/domoticz-1 --fuzz 18600 --live
#   scripts/launch.sh domoticz cov-0 -- replay domoticz --rosa-out /root/R2D/campaigns/domoticz-1/rosa-out --shard 0/8
#
# Layout: this directory (R2D) is mounted at /root/R2D, so tools/, targets/, campaigns/
# and harness/ keep their relative places inside the container. Extra host directories
# can be added with R2D_MOUNTS="host:container host2:container2". Network is always off.
# Isolated targets (isolate = true): no bind mount of R2D; the package, the target's
# files and any --copy are docker-cp'd in, DynamoRIO is mounted read-only, and the
# caller copies results out (scripts/collect.sh).
set -eu
R2D=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DR_NAME=DynamoRIO-Linux-11.91.20615
target=$1; cname=$2; shift 2
detach=""; copies=()
while [ "${1:-}" != "--" ] && [ -n "${1:-}" ]; do
  case $1 in
    --detach) detach="-d" ;;
    --copy) copies+=("$2"); shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
  shift
done
[ "${1:-}" = "--" ] && shift
[ -d "$R2D/tools/$DR_NAME" ] || { echo "DynamoRIO not found at $R2D/tools/$DR_NAME (see README: 요구 사항)"; exit 1; }

python3 -I "$R2D/bin/rosacov" targets --export "$R2D/targets.json" >/dev/null   # for Python 3.10 containers
read -r image isolate < <(python3 -I - "$R2D/targets.json" "$target" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))["targets"][sys.argv[2]]
print(d["image"], "1" if d.get("isolate") else "0")
EOF
)
docker rm -f "$cname" >/dev/null 2>&1 || true
common=(--name "$cname" --network none --hostname fuzzhost --add-host fuzzhost:127.0.0.1
        -e HOST_UID="$(id -u)" -e PYTHONPATH=/root/R2D -e ROSACOV_TARGETS=/root/R2D/targets.json
        -v "$R2D/tools/$DR_NAME:/root/R2D/tools/$DR_NAME:ro")
for m in ${R2D_MOUNTS:-}; do common+=(-v "${m%%:*}:${m#*:}"); done
run_cmd=(bash -c 'bash /root/R2D/harness/build.sh >/dev/null 2>&1; exec python3 -m rosacov.cli "$@"' -- "$@")
if [ "$isolate" = 1 ]; then
  docker create "${common[@]}" "$image" "${run_cmd[@]}" >/dev/null
  tmp=$(mktemp -d); mkdir -p "$tmp/R2D/targets"
  for d in bin rosacov scripts harness targets.toml targets.json; do cp -a "$R2D/$d" "$tmp/R2D/"; done
  tdir=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1]))['targets'][sys.argv[2]]; p=d['binary'].split('/root/R2D/targets/')[-1].split('/')[0] if '/root/R2D/targets/' in d['binary'] else ''; print(p)" "$R2D/targets.json" "$target")
  [ -n "$tdir" ] && cp -a "$R2D/targets/$tdir" "$tmp/R2D/targets/"
  # /root/R2D already exists in the container (the DynamoRIO mount point), so copy the
  # directory's contents, not the directory (docker cp would nest it otherwise)
  docker cp "$tmp/R2D/." "$cname:/root/R2D" >/dev/null
  rm -rf "$tmp"
  for c in "${copies[@]:-}"; do
    [ -n "$c" ] || continue
    docker cp "${c%%:*}" "$cname:${c#*:}" >/dev/null
  done
  docker start $( [ -n "$detach" ] || echo -a ) "$cname"
else
  docker run $detach "${common[@]}" -v "$R2D:/root/R2D" "$image" "${run_cmd[@]}"
fi
