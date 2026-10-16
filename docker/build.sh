#!/bin/bash
# Build the campaign images. Run from anywhere.
#   docker/build.sh            both images
#   docker/build.sh rosa-exp   one of: rosa-exp, domoticz
set -eu
cd "$(dirname "$0")"
want=${1:-all}
if [ "$want" = all ] || [ "$want" = rosa-exp ]; then
  docker build -t rosa-exp:0.6.0 -f Dockerfile.rosa-exp .
fi
if [ "$want" = all ] || [ "$want" = domoticz ]; then
  docker build -t rosa-exp-domoticz:0.6.0 -f Dockerfile.domoticz .
fi
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E '^rosa-exp(-domoticz)?:0.6.0'
