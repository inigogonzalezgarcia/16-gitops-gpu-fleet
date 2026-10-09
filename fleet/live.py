"""What the clusters actually run, read with kubectl, compared with what Git says.

`gate TARGET` is the check before a promotion: every wave before TARGET must have applied the commit
in Git (Flux), run the operator chart versions and per-pool driver versions in fleet.toml, and finished
its node rollout (repo 11 controller).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from . import plan as planmod
from .config import Fleet

KUSTOMIZATIONS = ("operators", "config", "lifecycle")


def kubectl(context: str, *args: str) -> dict:
    out = subprocess.run(["kubectl", "--context", context, *args, "-o", "json"], capture_output=True, text=True,
                         timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} ({context}): {out.stderr.strip()}")
    return json.loads(out.stdout)


def cond(obj: dict, ctype: str = "Ready") -> dict:
    return next((c for c in obj.get("status", {}).get("conditions", []) if c.get("type") == ctype), {})


@dataclass
class ClusterState:
    """Everything the gate looks at, from one cluster (kubectl JSON, so tests can use saved files)."""
    git_revision: str = ""
    kustomizations: dict[str, dict] = field(default_factory=dict)   # name -> {ready, revision, message}
    helmreleases: dict[str, dict] = field(default_factory=dict)     # name -> {ready, chart}
    drivers: dict[str, str] = field(default_factory=dict)           # NVIDIADriver name (pool) -> version
    doca: dict[str, str] = field(default_factory=dict)              # NicNodePolicy name (pool) -> version
    rollout: dict = field(default_factory=dict)                     # repo 11 status.json
    nodes: dict[str, dict] = field(default_factory=dict)            # node -> {pool, driver}


def parse_state(gitrepo: dict, kss: dict, hrs: dict, nvds: dict, nnps: dict, status_cm: dict, nodes: dict) -> ClusterState:
    s = ClusterState()
    s.git_revision = gitrepo.get("status", {}).get("artifact", {}).get("revision", "")
    for k in kss.get("items", []):
        c = cond(k)
        s.kustomizations[k["metadata"]["name"]] = {
            "ready": c.get("status") == "True", "message": c.get("message", ""),
            "revision": k.get("status", {}).get("lastAppliedRevision", "")}
    for h in hrs.get("items", []):
        hist = h.get("status", {}).get("history") or [{}]
        s.helmreleases[h["metadata"]["name"]] = {"ready": cond(h).get("status") == "True",
                                                 "chart": hist[0].get("chartVersion", ""),
                                                 "message": cond(h).get("message", "")}
    s.drivers = {d["metadata"]["name"]: d["spec"].get("version", "") for d in nvds.get("items", [])}
    s.doca = {d["metadata"]["name"]: d["spec"].get("ofedDriver", {}).get("version", "") for d in nnps.get("items", [])}
    raw = (status_cm.get("data") or {}).get("status.json")
    s.rollout = json.loads(raw) if raw else {}
    for n in nodes.get("items", []):
        lb = n["metadata"].get("labels", {})
        if planmod.POOL_LABEL in lb:
            s.nodes[n["metadata"]["name"]] = {"pool": lb[planmod.POOL_LABEL], "driver": lb.get("lifecycle.lab/version", "")}
    return s


def read_state(context: str) -> ClusterState:
    def get(*a):
        try:
            return kubectl(context, "get", *a)
        except RuntimeError:
            return {}
    return parse_state(get("gitrepository", "fleet", "-n", "flux-system"),
                       get("kustomizations", "-n", "flux-system"),
                       get("helmreleases", "-A"), get("nvidiadrivers"), get("nicnodepolicies"),
                       get("configmap", "rollout-status", "-n", "gpu-lifecycle"), get("nodes"))


def problems(f: Fleet, cluster: str, s: ClusterState, pools: list[str] | None = None) -> list[str]:
    """Why this cluster (or these pools of it) does not match Git and is not done. Empty = good."""
    c = f.cluster(cluster)
    plat = f.platform(c)
    out = []
    if not s.git_revision:
        out.append("Flux has not fetched the Git repository")
    for k in KUSTOMIZATIONS:
        ks = s.kustomizations.get(k)
        if not ks:
            out.append(f"Kustomization {k} missing")
        elif not ks["ready"]:
            out.append(f"Kustomization {k} not ready: {ks['message']}")
        elif ks["revision"] != s.git_revision:
            out.append(f"Kustomization {k} applied {ks['revision']}, Git is at {s.git_revision}")
    for name, want in (("gpu-operator", plat.gpu_operator), ("network-operator", plat.network_operator)):
        hr = s.helmreleases.get(name)
        if not hr:
            out.append(f"HelmRelease {name} missing")
        elif not hr["ready"] or hr["chart"] != want:
            out.append(f"HelmRelease {name}: chart {hr['chart'] or '?'} ready={hr['ready']}, Git wants {want}")
    wanted = [p for p in c.pools if pools is None or p.name in pools]
    for p in wanted:
        rel = f.release(p.release)
        if s.drivers.get(p.name) != rel.driver:
            out.append(f"NVIDIADriver {p.name}: {s.drivers.get(p.name, 'missing')}, Git wants {rel.driver}")
        if s.doca.get(p.name) != rel.doca:
            out.append(f"NicNodePolicy {p.name}: {s.doca.get(p.name, 'missing')}, Git wants {rel.doca}")
    name = planmod.plan_name(f, c)
    ro = s.rollout
    if ro.get("rollout") != name:
        out.append(f"rollout {name} not started (controller shows {ro.get('rollout', 'nothing')})")
    elif ro.get("phase") != "Complete":
        out.append(f"rollout {name} is {ro.get('phase')}: {ro.get('message', '')}")
    for node, n in sorted(s.nodes.items()):
        if n["pool"] in {p.name for p in wanted}:
            want = f.release(c.pool(n["pool"]).release).driver
            if n["driver"] != want:
                out.append(f"node {node} ({n['pool']}) runs {n['driver'] or '?'}, Git wants {want}")
    return out


def gate(f: Fleet, target: str, read=read_state, context=lambda c: f"kind-{c}") -> list[str]:
    """Problems that block promoting TARGET: every earlier wave must be applied and finished."""
    if target not in f.waves:
        raise ValueError(f"{target} is not a wave; waves are {', '.join(f.waves)}")
    out = []
    states: dict[str, ClusterState] = {}
    for w in f.waves[: f.waves.index(target)]:
        cluster, pools = f.pools(w)
        if cluster.name not in states:
            states[cluster.name] = read(context(cluster.name))
        out += [f"{w}: {p}" for p in problems(f, cluster.name, states[cluster.name], [p.name for p in pools])]
    return out


def summary(f: Fleet, cluster: str, s: ClusterState) -> str:
    c = f.cluster(cluster)
    rows = [f"cluster {cluster}  git {s.git_revision[:19] or '?'}"]
    for k in KUSTOMIZATIONS:
        ks = s.kustomizations.get(k, {})
        rows.append(f"  kustomization {k:<10} ready={ks.get('ready')} applied={ks.get('revision', '?')[:19]}")
    for name, hr in sorted(s.helmreleases.items()):
        rows.append(f"  helmrelease   {name:<17} chart={hr['chart']} ready={hr['ready']}")
    for p in c.pools:
        drivers = sorted({n['driver'] for n in s.nodes.values() if n['pool'] == p.name})
        rows.append(f"  pool {p.name:<6} release={p.release:<16} NVIDIADriver={s.drivers.get(p.name, '-'):<20} "
                    f"nodes={','.join(drivers) or '-'}")
    ro = s.rollout
    rows.append(f"  rollout {ro.get('rollout', '-')} {ro.get('phase', '')} {ro.get('message', '')}".rstrip())
    return "\n".join(rows)


def to_json(s: ClusterState) -> str:
    return json.dumps(s.__dict__, indent=1, sort_keys=True)
