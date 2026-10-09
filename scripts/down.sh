#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
for c in $(clusters); do kind delete cluster --name "$c"; done
docker rm -f "$GIT_CONTAINER" >/dev/null 2>&1 || true
rm -rf "$WORK"
