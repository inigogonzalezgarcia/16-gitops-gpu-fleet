#!/usr/bin/env bash
# Validate every state a release goes through on its way across the waves, before any of it reaches a
# cluster: each step is applied to a copy of fleet.toml, rendered, and checked by scripts/validate.sh.
#   scripts/promotion-path.sh 2026.10
set -euo pipefail
source "$(dirname "$0")/lib.sh"
REL=${1:?release}
P="$OUT/path"
rm -rf "$P" && mkdir -p "$P"
cp "$ROOT/fleet.toml" "$P/fleet.toml"
fl() { (cd "$ROOT" && python3 -m fleet --config "$P/fleet.toml" --root "$P" "$@"); }

read -r -a WAVES <<< "$(cd "$ROOT" && python3 -c 'import tomllib; print(" ".join(tomllib.load(open("fleet.toml","rb"))["fleet"]["waves"]))')"
steps=("start $REL")
for w in "${WAVES[@]:1}"; do steps+=("promote $w"); done
n=0
for s in "${steps[@]}"; do
  n=$((n + 1))
  # shellcheck disable=SC2086
  fl $s >/dev/null
  fl render >/dev/null
  step "$n. fleet $s"
  fl show | sed 's/^/  /'
  rm -rf "$OUT/path-$n" && cp -r "$P" "$OUT/path-$n"
  bash "$ROOT/scripts/validate.sh" "$OUT/path-$n"
done
