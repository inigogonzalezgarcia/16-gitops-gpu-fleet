"""fleet.toml: releases, clusters with node pools, and the waves a release moves through."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


@dataclass
class Release:
    name: str
    gpu_operator: str        # GPU Operator chart version, e.g. v26.7.1
    network_operator: str    # Network Operator chart version, e.g. 26.7.0
    driver: str              # NVIDIA driver version for NVIDIADriver
    doca: str                # DOCA driver version for NicNodePolicy
    drivers: list[str]       # driver versions this fleet runs with this operator version


@dataclass
class Pool:
    name: str
    nodes: int
    release: str


@dataclass
class Cluster:
    name: str
    pools: list[Pool]
    pause_after_canary: bool = False
    approved: str = ""       # rollout plan name a person approved after its canary

    def pool(self, name: str) -> Pool:
        for p in self.pools:
            if p.name == name:
                return p
        raise ValueError(f"cluster {self.name} has no pool {name!r}")


@dataclass
class Lifecycle:
    repo: str
    commit: str
    image: str
    validation_image: str
    bad_versions: list[str] = field(default_factory=list)


@dataclass
class Fleet:
    waves: list[str]
    helm_repository: str
    lifecycle: Lifecycle
    releases: list[Release]
    clusters: list[Cluster]

    def release(self, name: str) -> Release:
        for r in self.releases:
            if r.name == name:
                return r
        raise ValueError(f"unknown release {name!r}")

    def rank(self, name: str) -> int:
        """Position of a release in the file: higher is newer."""
        return [r.name for r in self.releases].index(name)

    def cluster(self, name: str) -> Cluster:
        for c in self.clusters:
            if c.name == name:
                return c
        raise ValueError(f"unknown cluster {name!r}")

    def pools(self, target: str) -> tuple[Cluster, list[Pool]]:
        """'prod' -> all pools of prod; 'prod/h100' -> that pool."""
        cname, _, pname = target.partition("/")
        c = self.cluster(cname)
        return c, ([c.pool(pname)] if pname else list(c.pools))

    def target_release(self, target: str) -> str:
        """The release a wave target runs; every pool in it must agree."""
        _, pools = self.pools(target)
        names = {p.release for p in pools}
        if len(names) != 1:
            raise ValueError(f"{target}: pools run different releases {sorted(names)}")
        return names.pop()

    def platform(self, cluster: Cluster) -> Release:
        """Operators are per cluster: the newest release any of its pools runs."""
        return self.release(max((p.release for p in cluster.pools), key=self.rank))


def load(path: str | Path = "fleet.toml") -> Fleet:
    with open(path, "rb") as f:
        return parse(tomllib.load(f))


def parse(d: dict) -> Fleet:
    try:
        fl = d["fleet"]
        lc = d["lifecycle"]
        fleet = Fleet(
            waves=list(fl["waves"]),
            helm_repository=fl["helm_repository"],
            lifecycle=Lifecycle(lc["repo"], lc["commit"], lc["image"], lc["validation_image"],
                                list(lc.get("bad_versions", []))),
            releases=[Release(r["name"], r["gpu_operator"], r["network_operator"], r["driver"], r["doca"],
                              list(r["drivers"])) for r in d["release"]],
            clusters=[Cluster(c["name"], [Pool(p["name"], int(p["nodes"]), p["release"]) for p in c["pool"]],
                              bool(c.get("pause_after_canary", False)), c.get("approved", ""))
                      for c in d["cluster"]],
        )
    except KeyError as e:
        raise ValueError(f"fleet.toml: missing {e}") from None
    validate(fleet)
    return fleet


def validate(f: Fleet) -> None:
    errors: list[str] = []
    seen: set[str] = set()
    for r in f.releases:
        if r.name in seen:
            errors.append(f"release {r.name} defined twice")
        seen.add(r.name)
        if r.driver not in r.drivers:
            errors.append(f"release {r.name}: its own driver {r.driver} is not in drivers")
    for c in f.clusters:
        if not NAME.match(c.name):
            errors.append(f"cluster name {c.name!r} is not a DNS label")
        names = [p.name for p in c.pools]
        if len(set(names)) != len(names):
            errors.append(f"cluster {c.name}: duplicate pool names")
        for p in c.pools:
            if not NAME.match(p.name):
                errors.append(f"cluster {c.name}: pool name {p.name!r} is not a DNS label")
            if p.nodes < 1:
                errors.append(f"{c.name}/{p.name}: nodes must be at least 1")
            if p.release not in seen:
                errors.append(f"{c.name}/{p.name}: unknown release {p.release}")
        if errors:
            continue
        plat = f.platform(c)
        for p in c.pools:
            drv = f.release(p.release).driver
            if drv not in plat.drivers:
                errors.append(f"{c.name}/{p.name}: driver {drv} is not approved with GPU Operator "
                              f"{plat.gpu_operator} (release {plat.name})")
    # every pool belongs to exactly one wave
    covered: dict[str, str] = {}
    for w in f.waves:
        try:
            c, pools = f.pools(w)
        except ValueError as e:
            errors.append(f"wave {w}: {e}")
            continue
        for p in pools:
            key = f"{c.name}/{p.name}"
            if key in covered:
                errors.append(f"{key} is in two waves ({covered[key]}, {w})")
            covered[key] = w
    for c in f.clusters:
        for p in c.pools:
            if f"{c.name}/{p.name}" not in covered:
                errors.append(f"{c.name}/{p.name} is in no wave")
    if not errors:
        errors += order_errors(f)
    if errors:
        raise ValueError("fleet.toml:\n  " + "\n  ".join(errors))


def order_errors(f: Fleet) -> list[str]:
    """A wave never runs a newer release than the wave before it."""
    errors = []
    prev = None
    for w in f.waves:
        try:
            rel = f.target_release(w)
        except ValueError as e:
            errors.append(str(e))
            continue
        if prev and f.rank(rel) > f.rank(prev[1]):
            errors.append(f"wave {w} runs {rel}, newer than {prev[1]} on the wave before it ({prev[0]}): "
                          f"promote through the waves in order")
        prev = (w, rel)
    return errors


def parse_text(text: str) -> Fleet:
    return parse(tomllib.loads(text))
