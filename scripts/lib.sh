#!/usr/bin/env bash
# Helpers shared by the lab scripts.
# shellcheck disable=SC2034
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=../cluster/versions.env
source "$ROOT/cluster/versions.env"
OUT=${OUT:-$ROOT/out}
WORK=${WORK:-$OUT/gitops}          # working copy of the lab Git repository
GIT_CONTAINER=fleet-git
GIT_USER=fleet
GIT_PASSWORD=lab-only-not-a-secret # local Gitea in a throwaway container
GIT_HTTP=http://localhost:3000
mkdir -p "$OUT"

PASS=0
ok()   { PASS=$((PASS + 1)); echo "  ok  $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }
step() { echo; echo "== $*"; }

wait_for() {  # wait_for <seconds> <description> <command...>
  local t=$1 what=$2; shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    (( SECONDS - start >= t )) && fail "$what (after ${t}s)"
    sleep 3
  done
  ok "$what ($((SECONDS - start))s)"
}
expect() {    # expect <description> <command...>
  local what=$1; shift
  if "$@"; then ok "$what"; else fail "$what"; fi
}
refuses() {   # refuses <description> <command...>: the command must fail
  local what=$1; shift
  if "$@" >"$OUT/last-refusal.txt" 2>&1; then fail "$what (it was accepted)"; fi
  ok "$what"
  sed 's/^/    | /' "$OUT/last-refusal.txt" | head -n 6
}

clusters() { (cd "$ROOT" && python3 -c 'import tomllib; print(" ".join(c["name"] for c in tomllib.load(open("fleet.toml","rb"))["cluster"]))'); }

# fleet against the lab working copy (what the clusters follow), not the files in this checkout
lab()   { (cd "$ROOT" && python3 -m fleet --config "$WORK/fleet.toml" --root "$WORK" "$@"); }

# q <cluster> <python expression on d, the live state JSON>
q()  { lab status "$1" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))' "$2"; }
is() { [[ "$(q "$1" "$2" 2>/dev/null)" == "$3" ]]; }
clean() { lab status "$1" --strict >/dev/null; }   # runs exactly what Git says and finished its rollout

publish() {   # publish <message>: render, commit and push the working copy; Flux picks it up
  lab render >/dev/null
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "$1"
  git -C "$WORK" push -q origin main
  echo "  pushed $(git -C "$WORK" rev-parse --short HEAD): $1"
  for c in $(clusters); do
    flux --context "kind-$c" -n flux-system reconcile source git fleet >/dev/null 2>&1 || true
  done
}

snapshot() {  # snapshot <title>: what Git says and what each cluster runs, for the report
  {
    echo "### $1"
    echo '```'
    lab show
    for c in $(clusters); do echo; lab status "$c"; done
    echo '```'
  } >> "$OUT/e2e-report.md"
}
