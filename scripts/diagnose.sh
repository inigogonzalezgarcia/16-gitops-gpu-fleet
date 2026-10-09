#!/usr/bin/env bash
# What to look at when a lab step fails.
source "$(dirname "$0")/lib.sh"
for c in $(clusters); do
  echo "=================== $c"
  flux --context "kind-$c" get all -A 2>&1 | head -n 40
  kubectl --context "kind-$c" get nodes -L fleet.lab/pool,lifecycle.lab/version
  kubectl --context "kind-$c" get pods -A -o wide | grep -v Running | head -n 30
  kubectl --context "kind-$c" get nvidiadrivers,nicnodepolicies -o wide 2>&1
  kubectl --context "kind-$c" -n gpu-lifecycle get cm rollout-status -o jsonpath='{.data.status\.json}' 2>&1; echo
  kubectl --context "kind-$c" -n gpu-lifecycle logs deploy/gpu-lifecycle --tail=30 2>&1
  kubectl --context "kind-$c" -n flux-system logs deploy/helm-controller --tail=20 2>&1
  kubectl --context "kind-$c" -n flux-system logs deploy/kustomize-controller --tail=20 2>&1
  kubectl --context "kind-$c" get events -A --sort-by=.lastTimestamp 2>&1 | tail -n 25
done
git -C "$WORK" log --oneline 2>/dev/null | head -n 15
