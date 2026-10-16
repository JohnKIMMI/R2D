#!/bin/bash
# Copy a campaign directory out of an isolated container (isolate = true targets).
#   scripts/collect.sh <container> <container path> <host path>
set -eu
c=$1; src=$2; dst=$3
docker wait "$c" >/dev/null
mkdir -p "$(dirname "$dst")"
[ -e "$dst" ] && { echo "$dst exists -- not overwriting"; exit 1; }
docker cp "$c:$src" "$dst"
docker logs "$c" > "$dst.container.log" 2>&1
echo "collected $dst ($(du -sh "$dst" | cut -f1))"
