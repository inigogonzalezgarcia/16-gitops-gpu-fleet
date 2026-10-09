#!/usr/bin/env bash
# Offline checks of what Flux would apply, for the fleet.toml in ROOT (default: this checkout):
#   1. helm template of the GPU and Network Operator charts at the versions and values each cluster uses
#   2. kubeconform, strict, on the chart output and on clusters/<name>/, with the CRD schemas of exactly
#      those chart versions (an NVIDIADriver field that only exists in v26.7 fails against v26.3)
# Needs helm, kubeconform, flux (for its CRDs), python3 with PyYAML. Network: the NVIDIA Helm repository.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
TARGET=$(cd "${1:-$ROOT}" && pwd)
V="$OUT/validate/$(basename "$TARGET")"
rm -rf "$V" && mkdir -p "$V"
fl() { (cd "$ROOT" && python3 -m fleet --config "$TARGET/fleet.toml" --root "$TARGET" "$@"); }

[[ -n "${NO_RENDER_CHECK:-}" ]] || fl render --check >/dev/null   # NO_RENDER_CHECK: test a hand-edited copy
flux install --export --components=source-controller,kustomize-controller,helm-controller > "$V/flux.yaml"
read -r -a NAMES <<< "$(cd "$ROOT" && python3 -c 'import sys,tomllib; print(" ".join(c["name"] for c in tomllib.load(open(sys.argv[1],"rb"))["cluster"]))' "$TARGET/fleet.toml")"
REPO=$(cd "$ROOT" && python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["fleet"]["helm_repository"])' "$TARGET/fleet.toml")

for c in "${NAMES[@]}"; do
  d="$V/$c"; mkdir -p "$d"
  for chart in gpu-operator network-operator; do
    fl values "$c" "$chart" > "$d/$chart.json"
    ver=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$d/$chart.json")
    python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["values"]))' "$d/$chart.json" > "$d/$chart.values.json"
    ns=$([[ $chart == gpu-operator ]] && echo gpu-operator || echo nvidia-network-operator)
    helm template "$chart" "$chart" --repo "$REPO" --version "$ver" --namespace "$ns" --include-crds \
      --kube-version "$K8S_SCHEMA_VERSION" -f "$d/$chart.values.json" > "$d/$chart.rendered.yaml"
    echo "  $c: helm template $chart $ver: $(grep -c '^kind:' "$d/$chart.rendered.yaml") objects"
  done
  python3 "$ROOT/scripts/crd2schema.py" "$d/schemas" "$V/flux.yaml" "$d"/*.rendered.yaml >/dev/null
  # the chart output: Kubernetes objects and the operators' own CRs (ClusterPolicy...)
  kubeconform -summary -kubernetes-version "$K8S_SCHEMA_VERSION" -schema-location default \
    -schema-location "$d/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
    -skip CustomResourceDefinition "$d"/*.rendered.yaml | sed "s/^/  $c charts: /"
  # what Flux applies from Git, strict
  kubeconform -strict -summary -kubernetes-version "$K8S_SCHEMA_VERSION" -schema-location default \
    -schema-location "$d/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
    "$TARGET/clusters/$c" | sed "s/^/  $c git: /"
done
