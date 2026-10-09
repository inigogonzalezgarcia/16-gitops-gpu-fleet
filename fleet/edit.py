"""Changes to fleet.toml: start a release in the first wave, promote it one wave, approve a rollout.

Each one rewrites a single `release = ...` or `approved = ...` line in place, so the file keeps its
comments and the Git diff of a promotion is one line.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import plan as planmod
from .config import Fleet, order_errors, parse_text


def _set_in_table(text: str, table: str, key_values: dict[str, str], match: dict[str, str]) -> str:
    """Set `key = "value"` inside the [[table]] block whose keys equal `match` (by name)."""
    lines = text.splitlines(keepends=True)
    blocks: list[tuple[int, int]] = []
    starts = [i for i, l in enumerate(lines) if re.match(r"^\s*\[\[?[\w.]+\]\]?\s*(#.*)?$", l)]
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        blocks.append((s, e))
    # walk blocks, remembering the current [[cluster]] name for [[cluster.pool]] blocks
    cluster = None
    for s, e in blocks:
        head = lines[s].strip().split("#")[0].strip()
        body = "".join(lines[s:e])
        name = re.search(r'^\s*name\s*=\s*"([^"]*)"', body, re.M)
        if head == "[[cluster]]":
            cluster = name.group(1) if name else None
        if head != f"[[{table}]]":
            continue
        ctx = {"name": name.group(1) if name else None, "cluster": cluster}
        if all(ctx.get(k) == v for k, v in match.items()):
            for key, value in key_values.items():
                pat = re.compile(rf'^(\s*{key}\s*=\s*)"[^"]*"', re.M)
                for i in range(s, e):
                    if pat.match(lines[i]):
                        lines[i] = pat.sub(lambda m: f'{m.group(1)}"{value}"', lines[i], count=1)
                        break
                else:
                    raise ValueError(f"no {key} line in [[{table}]] {match}")
            return "".join(lines)
    raise ValueError(f"no [[{table}]] block matching {match}")


def _check(text: str) -> Fleet:
    return parse_text(text)


def set_release(text: str, target: str, release: str) -> str:
    """Point every pool of a wave target at a release, then re-validate the whole file."""
    f = _check(text)
    f.release(release)
    cluster, pools = f.pools(target)
    for p in pools:
        text = _set_in_table(text, "cluster.pool", {"release": release}, {"cluster": cluster.name, "name": p.name})
    return text


def start(text: str, release: str) -> str:
    """A new (or rolled-back) release enters at the first wave."""
    f = _check(text)
    return _validated(set_release(text, f.waves[0], release))


def promote(text: str, target: str) -> tuple[str, str]:
    """Move a target to the release the wave before it runs. Returns (new text, release)."""
    f = _check(text)
    if target not in f.waves:
        raise ValueError(f"{target} is not a wave; waves are {', '.join(f.waves)}")
    i = f.waves.index(target)
    if i == 0:
        raise ValueError(f"{target} is the first wave: use `fleet start RELEASE`")
    rel = f.target_release(f.waves[i - 1])
    if f.target_release(target) == rel:
        raise ValueError(f"nothing to promote: {target} already runs {rel}, the release of the wave before it "
                         f"({f.waves[i - 1]})")
    return _validated(set_release(text, target, rel)), rel


def approve(text: str, cluster: str) -> tuple[str, str]:
    """Approve the cluster's current rollout plan after its canary (by name, so it never carries over)."""
    f = _check(text)
    c = f.cluster(cluster)
    name = planmod.plan_name(f, c)
    return _validated(_set_in_table(text, "cluster", {"approved": name}, {"name": cluster})), name


def _validated(text: str) -> str:
    f = _check(text)   # raises on any error, including order
    errs = order_errors(f)
    if errs:
        raise ValueError("\n".join(errs))
    return text


def write(path: str | Path, text: str) -> None:
    Path(path).write_text(text)
