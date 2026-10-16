#!/bin/bash
# Build the campaign images. Run from anywhere.
#   docker/build.sh            rosa-exp:0.6.0 (the general image; all you need for a new target)
#   docker/build.sh domoticz   rosa-exp-domoticz:0.6.0 (example of a target that needs extra libraries)
#   docker/build.sh all        both
set -eu
cd "$(dirname "$0")"
want=${1:-rosa-exp}
if [ "$want" = all ] || [ "$want" = rosa-exp ]; then
  docker build -t rosa-exp:0.6.0 -f Dockerfile.rosa-exp .
fi
if [ "$want" = all ] || [ "$want" = domoticz ]; then
  docker build -t rosa-exp-domoticz:0.6.0 -f Dockerfile.domoticz .
fi
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E '^rosa-exp(-domoticz)?:0.6.0'
