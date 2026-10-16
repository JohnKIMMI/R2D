#!/bin/bash
# Build the socket harness preloads next to their sources. launch.sh runs this inside
# every container before the rosacov command: the .so must match that container's
# libc, and the directory may be shared by several containers at once, so each build
# goes to a private temp file and is moved into place atomically. Takes < 1 s.
set -eu
cd "$(dirname "$0")"
for c in socketfuzz_plus socketfuzz_nb socketfuzz_dz; do
  gcc -shared -fPIC -O2 -o "$c.so.tmp.$$" "$c.c" -ldl && mv -f "$c.so.tmp.$$" "$c.so"
done
