# Decisions

## Design

- **Flux, not Argo CD.** One Flux per cluster, each following its own path of the same repository, is the simplest multi-cluster layout and needs no central control plane with credentials for every cluster. HelmRelease `dependsOn` and Kustomization `dependsOn` give the ordering the operators need (CRDs before CRs, GPU Operator before Network Operator). Argo CD with an ApplicationSet per wave would work too; it is not tested here.
- **A small generator instead of Kustomize overlays.** The interesting rules are not "patch this field": a cluster runs the operators of the newest release among its pools, a driver must be approved with that operator version, and the NVIDIADriver CR has different fields in v26.3 and v26.7. Those are easier to state and test in Python than in overlays. The output is plain YAML in Git, so what Flux applies is still reviewable, and CI fails if it drifts from `fleet.toml`.
- **Versions per node pool, operators per cluster.** The GPU Operator and Network Operator are cluster-wide; drivers are not. NVIDIADriver and NicNodePolicy with a pool nodeSelector let one prod cluster upgrade h100 nodes first and keep a100 nodes on the previous driver.
- **Promotion order in the file, readiness in the clusters.** `fleet check` (and CI) enforce that no wave runs a newer release than the wave before it. `fleet gate` checks the live clusters before a promotion. Neither replaces the other: the first stops a hand edit, the second stops a release that is in Git but not healthy yet.
- **Approvals in Git.** Prod pauses after the canary of every rollout. The approval is a commit (`fleet approve prod`), so who approved what is in the history, and it names the plan, so it cannot leak into the next rollout.
- **Repo 11 for the node work.** The GPU Operator has its own driver upgrade controller; with real GPUs that would be the usual choice. Here repo 11's controller runs it, because the lab has no driver to upgrade and because it shows the link asked for: the rollout plan is generated from the same file as the versions.

## What CI taught me

- **Kustomization order matters for the rollout plan too.** The first e2e run failed at the prod canary: the rollout-plan ConfigMap (in `lifecycle`) was applied before the NVIDIADriver CRs (in `config`), so the controller started a rollout while Git's driver versions were not in the cluster yet. `lifecycle` now `dependsOn` `config`, and a unit test checks the order in every rendered `sync.yaml`.
- **The NVIDIADriver CRD is not stable across operator versions.** kubeconform against the v26.7.1 CRD rejected the v26.3 CR (missing `spec.default`), and the v26.3.3 CRD rejects `spec.upgradePolicy`. Validating each cluster against the CRDs of the exact chart version it runs caught it before any cluster did.
- **A misspelt field is not an error for the API server by default.** CI copies the rendered files, changes `nodeSelector` to `nodeSelecter`, and expects validation to fail; it does, because the generated schemas reject unknown fields.

## Lab shortcuts

- No GPUs or NICs: the operators install and reconcile, but no driver, toolkit or DOCA pod is ever scheduled. The lab tests the GitOps flow, the order of changes and the CR schemas, not what the drivers do.
- The Git server is a throwaway Gitea with a password in the script. Flux reads the repository anonymously over HTTP.
- The repo 11 image is built in CI from the pinned commit and loaded into kind, not pulled from a registry.
- Flux is installed with `flux install` and the sync objects are applied once; a real setup would use `flux bootstrap`, which commits them to the repository.

## Missing

- Image and chart signatures (cosign), and mirroring nvcr.io charts and images into a private registry.
- Notifications from Flux (alerts on failed reconciliations) and a promotion bot that opens the next wave's pull request when the gate opens.
- More than one cluster per wave, and per-cluster maintenance windows (repo 11 supports windows; they are not rendered here).
- Argo CD as an alternative, and the GPU Operator's own upgrade controller with real GPUs.
