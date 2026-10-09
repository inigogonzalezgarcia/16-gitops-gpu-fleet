"""The node-by-node driver rollout for a cluster, in the plan format of repo 11 (gpu-fleet-lifecycle).

The plan covers the pools that run the cluster's newest release. A new pool joining that release, or a
new release, gives the plan a new name, which starts a new rollout in the controller.
"""

from __future__ import annotations

import json

from .config import Cluster, Fleet

POOL_LABEL = "fleet.lab/pool"
GPU_LABEL = "fleet.lab/gpu"


def rollout_pools(f: Fleet, c: Cluster) -> list[str]:
    newest = f.platform(c).name
    return sorted(p.name for p in c.pools if p.release == newest)


def plan_name(f: Fleet, c: Cluster) -> str:
    return f"{c.name}-{f.platform(c).name}-{'+'.join(rollout_pools(f, c))}"


def plan(f: Fleet, c: Cluster) -> dict:
    rel = f.platform(c)
    pools = rollout_pools(f, c)
    return {
        "name": plan_name(f, c),
        "selector": f"{POOL_LABEL} in ({','.join(pools)})",
        "component": "driver",
        "target": rel.driver,
        "canary": 1,
        "pauseAfterCanary": c.pause_after_canary,
        "batchSize": 1,
        "maxUnavailable": 1,
        "failureBudget": 0,
        "soak": "5s",
        "upgradeTimeout": "1m",
        "validationTimeout": "2m",
    }


def configmap_data(f: Fleet, c: Cluster) -> dict[str, str]:
    data = {"plan.json": json.dumps(plan(f, c), indent=2) + "\n"}
    if c.approved:
        data["approve"] = c.approved   # only counts if it names the current plan
    return data
