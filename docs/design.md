# Design

## Three layers, one source

| Layer | Decides | Done by | Lives in |
|---|---|---|---|
| Fleet | which release each cluster and node pool runs, and in which order a new release reaches them | `fleet.toml` + `python -m fleet` + a person committing | Git |
| Cluster | operator chart versions and values, one driver version per node pool | Flux: HelmRelease, NVIDIADriver, NicNodePolicy | `clusters/<name>/` |
| Node | which node is upgraded when, canary, approval, rollback | the rollout controller from [repo 11](https://github.com/inigogonzalezgarcia/11-gpu-fleet-lifecycle) | `rollout-plan` ConfigMap, rendered from `fleet.toml` |

Everything a cluster runs comes from Git. Nobody runs `helm upgrade` or `kubectl apply` against a cluster; a change is a commit.

## fleet.toml

- **Releases** are sets of versions that are tested together: GPU Operator chart, Network Operator chart, NVIDIA driver, DOCA driver, and the driver versions this fleet runs with that GPU Operator version. The versions are upstream ones (each release uses the default driver of its GPU Operator version and the DOCA driver of its Network Operator version). `drivers` is the fleet's own list, not NVIDIA's support matrix.
- **Clusters** have **node pools**. Each pool points at a release. The operators run once per cluster, so a cluster runs the operator charts of the newest release among its pools. Its other pools keep their driver, as long as that driver is in the newer release's `drivers` list (`fleet check` fails otherwise).
- **Waves** are the order: `staging`, then `prod/h100`, then `prod/a100`. A wave never runs a newer release than the wave before it.

`fleet start RELEASE` puts a release on the first wave. `fleet promote TARGET` gives a wave the release of the wave before it. `fleet approve CLUSTER` approves the cluster's current rollout after its canary. Each one changes one or two lines of `fleet.toml`, so the commit says exactly what moved. Then `python -m fleet render` regenerates `clusters/`, and CI fails if anyone edits `clusters/` by hand.

## What Flux applies per cluster

```
clusters/<name>/
  flux-system/sync.yaml    GitRepository + Kustomizations operators, config (dependsOn operators), lifecycle (dependsOn config)
  operators/               HelmRepository (NGC), HelmRelease gpu-operator, HelmRelease network-operator
  config/                  NVIDIADriver and NicNodePolicy per node pool (nodeSelector fleet.lab/pool)
  lifecycle/               rollout controller and lab node agent (repo 11), rollout-plan ConfigMap
```

Choices in the operator values:

- `driver.nvidiaDriverCRD.enabled: true`, `deployDefaultCR: false`: the driver comes from one NVIDIADriver per node pool, so two pools can run two driver versions under one GPU Operator.
- Automatic driver upgrades off (`autoUpgrade: false`): node by node is the rollout controller's job here.
- One Node Feature Discovery per cluster: the GPU Operator's. The Network Operator chart is installed with `nfd.enabled: false` but keeps its rules that label NVIDIA NICs, and its HelmRelease depends on the GPU Operator one.
- CRDs are created and replaced by Flux on install and upgrade (`crds: CreateReplace`); the charts' own CRD upgrade hooks are off.
- **The NVIDIADriver CRD changed between versions.** In GPU Operator v26.7 `spec.default` is required and the upgrade policy moved into the CR; in v26.3 neither field exists. The renderer writes the fields for the operator version the cluster runs, and the `config` Kustomization waits for `operators`, so the new CRD is in place before the new CRs.

## The rollout plan from Git (repo 11)

Repo 11's controller reads its plan from the `rollout-plan` ConfigMap. Here that ConfigMap is rendered from `fleet.toml`: the pools that run the cluster's newest release, that release's driver as target, canary 1, batch 1, and in prod a pause after the canary. The plan name is `<cluster>-<release>-<pools>`, so promoting another pool or another release starts a new rollout, and an approval (`approve` key, from `approved` in `fleet.toml`) only applies to the plan it names.

In the lab there is no real driver. The controller talks to repo 11's node agent, which pretends to install versions and fails validation for the versions listed in `lifecycle.bad_versions`. With real GPUs the same plan would drive the GPU Operator's upgrade (or the operator's own upgrade controller would do the node work). The repo 11 image is built from the commit pinned in `fleet.toml`.

## The gate

`fleet gate TARGET` reads every earlier wave with kubectl and blocks the promotion unless, for each:

- Flux fetched the current commit and the three Kustomizations applied it and are Ready;
- the HelmReleases are Ready at the chart versions Git asks for;
- each pool's NVIDIADriver and NicNodePolicy have the versions Git asks for;
- the rollout controller finished the current plan (`Complete`), and every node reports the driver of its pool's release.

`fleet promote` itself only checks the order in the file. The gate is what makes the order mean "tested first".

## The lab

| Piece | How |
|---|---|
| Git server | Gitea in a container on the `kind` Docker network. The clusters pull from it over HTTP |
| Clusters | kind: `staging` (1 + 2 workers) and `prod` (1 + 4 workers), workers labelled with their pool |
| GPUs and NICs | none. The operators and their controllers run; driver and DOCA pods are never scheduled because no node has an NVIDIA GPU or NIC |
| Node rollout | repo 11's controller and lab node agent |

## Validation in CI

`scripts/validate.sh` runs `helm template` for both charts at the versions and values each cluster uses, turns the CRDs of exactly those versions into JSON schemas (`scripts/crd2schema.py`, with unknown fields rejected), and runs kubeconform on the chart output and, strictly, on `clusters/<name>/`. `scripts/promotion-path.sh` does the same for every step of a promotion before it is merged. A field that only exists in v26.7, or a misspelt field, fails in CI instead of being dropped silently by the API server.
