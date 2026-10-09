#!/usr/bin/env bash
# A release goes through the waves of fleet.toml with Git as the only way in: staging, then the h100
# pool of prod, then its a100 pool. Every change is a commit to the lab Git server; Flux applies it and
# repo 11's controller rolls the driver node by node. Run scripts/up.sh first.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
: > "$OUT/e2e-report.md"
NEW=2026.10
BAD=2026.11-lab-bad

step "0. Both clusters run what Git says (release $(lab show | sed -n 2p | awk '{print $2}'))"
for c in $(clusters); do
  wait_for 600 "$c matches Git and its rollout is complete" clean "$c"
done
expect "prod runs GPU Operator v26.3.3 and Network Operator 26.4.2" \
  is prod '(d["helmreleases"]["gpu-operator"]["chart"], d["helmreleases"]["network-operator"]["chart"])' "('v26.3.3', '26.4.2')"
expect "every node of prod on driver 580.126.20" is prod 'sorted({n["driver"] for n in d["nodes"].values()})' "['580.126.20']"
snapshot "0. Start"

step "1. A release can only enter at the first wave"
refuses "fleet promote prod/h100: nothing to promote yet" lab promote prod/h100
cp "$WORK/fleet.toml" "$OUT/fleet.toml.bak"
python3 - "$WORK/fleet.toml" <<'PY'
import re, sys
p = sys.argv[1]; t = open(p).read()
# hand edit: prod's h100 pool straight to the new release, skipping staging
i = t.index('name = "prod"'); j = t.index('name = "h100"', i)
t = t[:j] + re.sub(r'release = "[^"]*"', 'release = "2026.10"', t[j:], count=1)
open(p, "w").write(t)
PY
refuses "fleet check rejects a hand edit that skips staging (the CI check does the same)" lab check
cp "$OUT/fleet.toml.bak" "$WORK/fleet.toml"

step "2. Release $NEW to staging"
lab start "$NEW" >/dev/null
publish "Start $NEW in staging"
refuses "gate for prod/h100 closed while staging is still on its way" lab gate prod/h100
wait_for 900 "staging upgraded: operators, drivers per pool, rollout complete" clean staging
expect "staging runs GPU Operator v26.7.1 and Network Operator 26.7.0" \
  is staging '(d["helmreleases"]["gpu-operator"]["chart"], d["helmreleases"]["network-operator"]["chart"])' "('v26.7.1', '26.7.0')"
expect "every staging node on 595.91.07" is staging 'sorted({n["driver"] for n in d["nodes"].values()})' "['595.91.07']"
expect "prod untouched: still v26.3.3" is prod 'd["helmreleases"]["gpu-operator"]["chart"]' "v26.3.3"
snapshot "2. $NEW in staging"

step "3. Promote to prod/h100; the canary waits for an approval in Git"
expect "gate for prod/h100 open" lab gate prod/h100
lab promote prod/h100 >/dev/null
publish "Promote $NEW to prod/h100"
wait_for 900 "prod operators upgraded to v26.7.1" is prod 'd["helmreleases"]["gpu-operator"]["chart"] if d["helmreleases"]["gpu-operator"]["ready"] else 0' "v26.7.1"
wait_for 300 "prod rollout prod-$NEW-h100 waits for approval after its canary" \
  is prod '(d["rollout"].get("rollout"), d["rollout"].get("phase"))' "('prod-$NEW-h100', 'AwaitingApproval')"
wait_for 120 "prod NVIDIADriver: h100 on 595.91.07, a100 still on 580.126.20" \
  is prod '(d["drivers"]["h100"], d["drivers"]["a100"])' "('595.91.07', '580.126.20')"
refuses "gate for prod/a100 closed until prod/h100 has finished" lab gate prod/a100
snapshot "3. prod/h100 canary done, waiting for approval"
lab approve prod >/dev/null
publish "Approve rollout prod-$NEW-h100"
wait_for 600 "prod/h100 rollout complete after the approval" clean prod
expect "prod nodes: h100 on 595.91.07, a100 on 580.126.20" \
  is prod 'sorted({(n["pool"], n["driver"]) for n in d["nodes"].values()})' "[('a100', '580.126.20'), ('h100', '595.91.07')]"

step "4. Promote to prod/a100, the last wave"
expect "gate for prod/a100 open" lab gate prod/a100
lab promote prod/a100 >/dev/null
publish "Promote $NEW to prod/a100"
wait_for 300 "prod rollout prod-$NEW-a100+h100 waits for approval after its canary" \
  is prod '(d["rollout"].get("rollout"), d["rollout"].get("phase"))' "('prod-$NEW-a100+h100', 'AwaitingApproval')"
lab approve prod >/dev/null
publish "Approve rollout prod-$NEW-a100+h100"
wait_for 600 "prod complete" clean prod
expect "every prod node on 595.91.07" is prod 'sorted({n["driver"] for n in d["nodes"].values()})' "['595.91.07']"
snapshot "4. $NEW everywhere"

step "5. Drift: a hand change in the cluster is put back from Git"
kubectl --context kind-prod patch nvidiadriver a100 --type merge -p '{"spec":{"version":"580.126.20"}}' >/dev/null
expect "NVIDIADriver a100 changed by hand" is prod 'd["drivers"]["a100"]' "580.126.20"
flux --context kind-prod -n flux-system reconcile kustomization config >/dev/null
wait_for 120 "Flux restored NVIDIADriver a100 to 595.91.07" is prod 'd["drivers"]["a100"]' "595.91.07"

step "6. A bad release stops in staging and never reaches prod"
lab start "$BAD" >/dev/null
publish "Start $BAD in staging"
wait_for 600 "staging rollout halted: the canary failed validation and was rolled back" \
  is staging 'd["rollout"].get("phase")' "Halted"
refuses "gate for prod/h100 closed: staging halted" lab gate prod/h100
expect "prod still on $NEW" is prod 'd["drivers"]["h100"]' "595.91.07"
snapshot "6. $BAD halted in staging"
lab start "$NEW" >/dev/null
publish "Roll staging back to $NEW"
wait_for 600 "staging back on $NEW and complete" clean staging
expect "gate for prod/h100 open again (prod already runs $NEW)" lab gate prod/h100

echo
echo "e2e: $PASS checks passed; $(git -C "$WORK" rev-list --count HEAD) commits in the lab Git repository"
git -C "$WORK" log --format='  %h %s' | head -n 12 | tee -a "$OUT/e2e-commits.txt"
