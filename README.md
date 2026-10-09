# GitOps GPU Fleet

A fleet of GPU clusters upgraded from Git. One file says which **GPU Operator**, **Network Operator** and driver release each cluster and node pool runs, and the order in which a new release reaches them: staging, then prod H100 nodes, then prod A100 nodes. **Flux** applies it to each cluster, the node-by-node rollout plan of [repo 11](https://github.com/inigogonzalezgarcia/11-gpu-fleet-lifecycle) is generated from the same file, and every push checks the chart output with `helm template` and kubeconform and runs the whole promotion on two kind clusters.

**fleet.toml → `python -m fleet render` → clusters/&lt;name&gt;/ in Git → Flux in each kind cluster → HelmReleases, NVIDIADriver per pool, rollout plan → gate before the next wave**

![ci](https://github.com/inigogonzalezgarcia/16-gitops-gpu-fleet/actions/workflows/ci.yml/badge.svg)

> A learning-in-public lab about upgrading GPU clusters with GitOps. I don't have production GPU fleet experience; this project is how I am learning the problem. Flux, the GPU Operator and the Network Operator are real (upstream releases and charts from NGC, on kind). The GPUs and NICs are not: the operators install and reconcile, but no driver or DOCA pod is ever scheduled, and repo 11's lab node agent pretends to install driver versions. The lab tests the GitOps flow, the order of changes and the CR schemas, not what a driver does on a node.

Eighth in a series: [09 – node remediation](https://github.com/inigogonzalezgarcia/09-gpu-node-remediation), [10 – fleet observability](https://github.com/inigogonzalezgarcia/10-gpu-fleet-observability), [11 – fleet lifecycle](https://github.com/inigogonzalezgarcia/11-gpu-fleet-lifecycle), [12 – goodput and MTBI](https://github.com/inigogonzalezgarcia/12-gpu-goodput-mtbi), [13 – cluster acceptance](https://github.com/inigogonzalezgarcia/13-cluster-acceptance-burnin), [14 – Slurm GPU operations](https://github.com/inigogonzalezgarcia/14-slurm-gpu-ops), [15 – multi-tenant GPU scheduling](https://github.com/inigogonzalezgarcia/15-multi-tenant-gpu-scheduling).

## The fleet

[fleet.toml](fleet.toml): releases (sets of versions tested together) and clusters with node pools.

| Release | GPU Operator | Network Operator | Driver | DOCA driver |
|---|---|---|---|---|
| 2026.07 | v26.3.3 | 26.4.2 | 580.126.20 | doca3.4.1-26.04-1.1.0.0-3 |
| 2026.10 | v26.7.1 | 26.7.0 | 595.91.07 | doca3.5.0-26.07-0.7.7.0-0 |
| 2026.11-lab-bad | v26.7.1 | 26.7.0 | 595.91.07-lab-bad (fails validation on purpose) | doca3.5.0-26.07-0.7.7.0-0 |

| Cluster | Node pools | Waves |
|---|---|---|
| staging | a100 x1, h100 x1 | 1: `staging` |
| prod | h100 x2, a100 x2; pauses after each canary until approved in Git | 2: `prod/h100`, 3: `prod/a100` |

The operators run once per cluster, drivers per node pool: one NVIDIADriver and one NicNodePolicy per pool, selected by a `fleet.lab/pool` label. So prod can run GPU Operator v26.7.1 with its H100 nodes on the new driver and its A100 nodes still on the old one. A wave can never run a newer release than the wave before it.

## How a release moves

```bash
python -m fleet start 2026.10          # staging gets 2026.10 (one line of fleet.toml)
python -m fleet render && git commit -am "Start 2026.10 in staging" && git push
python -m fleet gate prod/h100         # live check of staging: Flux, HelmReleases, CRs, rollout, every node
python -m fleet promote prod/h100      # same pattern for each wave
python -m fleet approve prod           # prod pauses after its canary; the approval is a commit
```

`fleet start`, `promote` and `approve` only edit `fleet.toml`, so each commit says exactly what moved. Per cluster, Flux applies three Kustomizations in order:

| Kustomization | Contents | Waits for |
|---|---|---|
| operators | HelmRepository (NGC), HelmRelease gpu-operator and network-operator, values pinned per release | — |
| config | NVIDIADriver and NicNodePolicy per node pool | operators (new CRDs first) |
| lifecycle | repo 11's rollout controller and lab node agent, `rollout-plan` ConfigMap | config |

The rollout plan is rendered from `fleet.toml`: the pools on the cluster's newest release, that release's driver as target, canary 1, batch 1, a pause after the canary in prod. Its name (`prod-2026.10-h100`) changes with every promotion, so an approval only applies to the rollout it names. [docs/design.md](docs/design.md) has the details.

## CI

Every push runs:

- **unit** (Python 3.11 to 3.13, 21 tests): `fleet check`, `fleet render --check` (CI fails if anyone edits `clusters/` by hand), shellcheck.
- **validate** (about 40 s): `helm template` of both charts at the version and values each cluster uses (GPU Operator v26.3.3: 28 objects, v26.7.1: 32; Network Operator: 15), kubeconform on the output and, strictly, on `clusters/<name>/` against schemas generated from the CRDs of exactly those chart versions. The same for every step of the promotion path to 2026.10. Then CI misspells `nodeSelector` as `nodeSelecter` in a copy and expects validation to fail: `additional properties 'nodeSelecter' not allowed`.
- **e2e** (about 20 minutes): two kind clusters, a Gitea server they pull from, Flux 2.9.6 in each, repo 11's controller built from its pinned commit. The script commits to the lab Git repository like a person would and checks each cluster after every commit.

From run 6, 29 checks:

| Step | What happened |
|---|---|
| 0. Bootstrap | both clusters on 2026.07, every node on 580.126.20 |
| 1. Order | `fleet promote prod/h100` refuses (nothing new in staging); a hand edit that skips staging fails `fleet check` |
| 2. Staging | gate for prod/h100 closed while staging upgrades; staging on v26.7.1 / 26.7.0 / 595.91.07 in 198 s; prod untouched |
| 3. prod/h100 | prod operators upgraded in 90 s; rollout waits for approval after its canary; A100 nodes still on 580.126.20; gate for prod/a100 closed; approved in Git, complete in 84 s |
| 4. prod/a100 | canary, approval, every prod node on 595.91.07 |
| 5. Drift | NVIDIADriver a100 changed by hand; Flux puts it back from Git |
| 6. Bad release | 2026.11-lab-bad halts in staging: canary fails validation and is rolled back, no other node touched, gate for prod closed; a commit puts staging back on 2026.10, complete in 61 s |

The lab Git history at the end of the run:

```
20c146e Roll staging back to 2026.10
abbea78 Start 2026.11-lab-bad in staging
363c006 Approve rollout prod-2026.10-a100+h100
fad4f2c Promote 2026.10 to prod/a100
56c61b4 Approve rollout prod-2026.10-h100
c9dad2b Promote 2026.10 to prod/h100
61451df Start 2026.10 in staging
eed73a5 Fleet as in this checkout
```

## Run it

Docker, kind, kubectl, Flux CLI, Helm and kubeconform. Python 3.11+ (standard library only).

```bash
python -m fleet show                   # what each wave runs, the rollout plan per cluster
bash scripts/validate.sh               # helm template + kubeconform
bash scripts/up.sh                     # kind clusters staging and prod, Gitea, Flux
bash scripts/e2e.sh                    # the promotion above
python -m fleet status prod            # live view of a cluster against Git
bash scripts/down.sh
python -m unittest -v
```

Tool versions are pinned in [cluster/versions.env](cluster/versions.env): kind v0.33.0, Kubernetes v1.35.8, Flux 2.9.6, kubeconform v0.8.0, Gitea 1.27.3. Chart and driver versions are in `fleet.toml`.

## Documentation

- [docs/design.md](docs/design.md): fleet, cluster and node layers; what Flux applies; operator values; the rollout plan; the gate; the lab
- [docs/decisions.md](docs/decisions.md): Flux rather than Argo CD, a generator rather than overlays, what CI taught me, lab shortcuts, what is missing

## Not tested here

- Real GPUs and NICs: no driver, toolkit or DOCA pod ever runs, and no node is drained for a real driver upgrade.
- The GPU Operator's own driver upgrade controller (the usual choice with real GPUs); repo 11's controller does the node work here.
- Argo CD, `flux bootstrap`, signed charts and images, a private registry mirror, Flux alerts.
- More than one cluster per wave, maintenance windows per cluster.

## Customisation and contact

Want to talk about GPU fleet upgrades, GitOps for clusters or a lab like this for your team? Get in touch:

- Email: [inigogonzalezgarcia@yahoo.es](mailto:inigogonzalezgarcia@yahoo.es)
- LinkedIn: [linkedin.com/in/igonzalez93](https://www.linkedin.com/in/igonzalez93)

## License

MIT
