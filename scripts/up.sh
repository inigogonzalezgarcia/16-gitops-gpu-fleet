#!/usr/bin/env bash
# The lab: a Git server, one kind cluster per [[cluster]] in fleet.toml, Flux in each one following
# clusters/<name>/ in that Git server, and repo 11's rollout controller image. Needs docker, kind,
# kubectl, flux, git, python3.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

for c in $(clusters); do
  step "kind cluster $c"
  kind get clusters 2>/dev/null | grep -qx "$c" || \
    kind create cluster --name "$c" --image "$KIND_NODE_IMAGE" --config "$ROOT/kind/$c.yaml" --wait 3m
done

step "Git server ($GITEA_IMAGE) on the kind network"
if ! docker ps --format '{{.Names}}' | grep -qx "$GIT_CONTAINER"; then
  docker run -d -q --name "$GIT_CONTAINER" --network kind -p 3000:3000 \
    -e GITEA__security__INSTALL_LOCK=true -e GITEA__service__DISABLE_REGISTRATION=true \
    -e GITEA__server__ROOT_URL="$GIT_HTTP/" -e GITEA__database__DB_TYPE=sqlite3 "$GITEA_IMAGE" >/dev/null
fi
wait_for 120 "Gitea answers" curl -fsS "$GIT_HTTP/api/healthz"
docker exec -u git "$GIT_CONTAINER" gitea admin user create --admin --username "$GIT_USER" \
  --password "$GIT_PASSWORD" --email fleet@lab.invalid --must-change-password=false >/dev/null 2>&1 || true
curl -fsS -u "$GIT_USER:$GIT_PASSWORD" -H 'Content-Type: application/json' -X POST "$GIT_HTTP/api/v1/user/repos" \
  -d '{"name":"fleet","private":false,"default_branch":"main"}' >/dev/null 2>&1 || true
GIT_IP=$(docker inspect -f '{{.NetworkSettings.Networks.kind.IPAddress}}' "$GIT_CONTAINER")
echo "  Git server at $GIT_IP:3000 for the clusters"

step "Lab Git repository: this checkout, as the clusters will see it"
if [[ ! -d "$WORK/.git" ]]; then
  mkdir -p "$WORK"
  tar -C "$ROOT" --exclude=./.git --exclude=./out --exclude=__pycache__ -cf - . | tar -C "$WORK" -xf -
  git -C "$WORK" init -q -b main
  git -C "$WORK" config user.name "fleet-lab"
  git -C "$WORK" config user.email "fleet@lab.invalid"
  git -C "$WORK" add -A && git -C "$WORK" commit -q -m "Fleet as in this checkout"
  git -C "$WORK" remote add origin "http://$GIT_USER:$GIT_PASSWORD@localhost:3000/$GIT_USER/fleet.git"
  git -C "$WORK" push -q -u origin main
fi
lab render --check >/dev/null

step "Rollout controller from repo 11"
read -r LC_REPO LC_COMMIT LC_IMAGE LC_VALIDATION < <(cd "$ROOT" && python3 -c '
import tomllib; l = tomllib.load(open("fleet.toml", "rb"))["lifecycle"]
print(l["repo"], l["commit"], l["image"], l["validation_image"])')
if ! docker image inspect "$LC_IMAGE" >/dev/null 2>&1; then
  rm -rf "$OUT/lifecycle-src"
  git clone -q "$LC_REPO" "$OUT/lifecycle-src"
  git -C "$OUT/lifecycle-src" checkout -q "$LC_COMMIT"
  docker build -q -t "$LC_IMAGE" --build-arg VERSION="${LC_COMMIT:0:7}" "$OUT/lifecycle-src" >/dev/null
fi
docker pull -q "$LC_VALIDATION" >/dev/null
for c in $(clusters); do
  kind load docker-image "$LC_IMAGE" "$LC_VALIDATION" --name "$c" >/dev/null
done
ok "built $LC_IMAGE from ${LC_COMMIT:0:7} and loaded it into $(clusters)"

for c in $(clusters); do
  step "Flux $FLUX_VERSION in $c, following clusters/$c"
  flux --context "kind-$c" install --components=source-controller,kustomize-controller,helm-controller \
    --network-policy=false >/dev/null
  sed "s|http://fleet-git:3000|http://$GIT_IP:3000|" "$ROOT/clusters/$c/flux-system/sync.yaml" \
    | kubectl --context "kind-$c" apply -f - >/dev/null
done
for c in $(clusters); do
  for k in operators config lifecycle; do
    wait_for 900 "$c: Flux Kustomization $k ready" \
      kubectl --context "kind-$c" -n flux-system wait "kustomization/$k" --for=condition=Ready --timeout=5s
  done
done
