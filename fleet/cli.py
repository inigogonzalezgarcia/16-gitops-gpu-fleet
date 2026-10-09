"""fleet: GPU/Network Operator versions for a fleet of clusters, promoted through waves with GitOps.

    fleet check                      validate fleet.toml (releases, pools, wave order)
    fleet render [--check]           write clusters/ and kind/ (or fail if they are out of date)
    fleet show                       what each wave runs, and the rollout plan per cluster
    fleet values CLUSTER CHART       Helm values a HelmRelease uses, as JSON (CI: helm template)
    fleet start RELEASE              put a release on the first wave
    fleet promote TARGET             give TARGET the release of the wave before it
    fleet approve CLUSTER            approve the cluster's rollout after its canary
    fleet gate TARGET                live check: may TARGET be promoted now? (kubectl, kind-<cluster>)
    fleet status CLUSTER [--json] [--strict]   live view of one cluster against Git

start/promote/approve only edit fleet.toml (one line each); render and commit, and Flux does the rest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, config, edit, live, plan, render, yamlout


def rendered(f: config.Fleet) -> dict[str, str]:
    return {path: yamlout.dump_all(docs, render.HEADER.rstrip("\n")) for path, docs in render.files(f).items()}


def show(f: config.Fleet) -> str:
    rows = [f"{'wave':<12} {'release':<16} {'GPU Operator':<13} {'Network Op.':<12} driver"]
    for w in f.waves:
        r = f.release(f.target_release(w))
        rows.append(f"{w:<12} {r.name:<16} {r.gpu_operator:<13} {r.network_operator:<12} {r.driver}")
    for c in f.clusters:
        p = plan.plan(f, c)
        appr = " (approved)" if c.approved == p["name"] else (" (needs approval after canary)" if p["pauseAfterCanary"] else "")
        rows.append(f"rollout {c.name}: {p['name']} -> {p['target']} on {p['selector']}{appr}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fleet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config", default="fleet.toml")
    ap.add_argument("--root", default=".", help="where clusters/ and kind/ are written")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("render")
    r.add_argument("--check", action="store_true")
    sub.add_parser("show")
    v = sub.add_parser("values")
    v.add_argument("cluster")
    v.add_argument("chart", choices=("gpu-operator", "network-operator"))
    s = sub.add_parser("start")
    s.add_argument("release")
    p = sub.add_parser("promote")
    p.add_argument("target")
    a = sub.add_parser("approve")
    a.add_argument("cluster")
    g = sub.add_parser("gate")
    g.add_argument("target")
    st = sub.add_parser("status")
    st.add_argument("cluster")
    st.add_argument("--json", action="store_true")
    st.add_argument("--strict", action="store_true", help="exit 1 if the cluster differs from Git or is not done")
    args = ap.parse_args(argv)

    try:
        if args.cmd in ("start", "promote", "approve"):
            text = Path(args.config).read_text()
            if args.cmd == "start":
                new, msg = edit.start(text, args.release), f"{config.parse_text(text).waves[0]} -> {args.release}"
            elif args.cmd == "promote":
                new, rel = edit.promote(text, args.target)
                msg = f"{args.target} -> {rel}"
            else:
                new, name = edit.approve(text, args.cluster)
                msg = f"approved rollout {name}"
            edit.write(args.config, new)
            print(f"fleet.toml: {msg}. Now: python -m fleet render, commit, push.")
            return 0

        f = config.load(args.config)
        if args.cmd == "check":
            print(show(f))
            return 0
        if args.cmd == "render":
            root = Path(args.root)
            files = rendered(f)
            if args.check:
                stale = [p for p, t in files.items() if not (root / p).exists() or (root / p).read_text() != t]
                extra = [str(p.relative_to(root)) for d in ("clusters", "kind") if (root / d).exists()
                         for p in (root / d).rglob("*.yaml") if str(p.relative_to(root)) not in files]
                if stale or extra:
                    print("out of date: " + ", ".join(stale + [f"{e} (not generated)" for e in extra])
                          + "\nrun: python -m fleet render", file=sys.stderr)
                    return 1
                print(f"{len(files)} files up to date")
                return 0
            for path, text in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text)
            print(f"wrote {len(files)} files under clusters/ and kind/")
            return 0
        if args.cmd == "values":
            c = f.cluster(args.cluster)
            rel = f.platform(c)
            vals = render.gpu_operator_values(rel) if args.chart == "gpu-operator" else render.network_operator_values(rel)
            ver = rel.gpu_operator if args.chart == "gpu-operator" else rel.network_operator
            print(json.dumps({"chart": args.chart, "version": ver, "values": vals}))
            return 0
        if args.cmd == "gate":
            probs = live.gate(f, args.target)
            if probs:
                print(f"promotion to {args.target} blocked:\n  " + "\n  ".join(probs))
                return 1
            print(f"{args.target} may be promoted: every earlier wave runs what Git says and finished its rollout")
            return 0
        if args.cmd == "show":
            print(show(f))
            return 0
        if args.cmd == "status":
            s = live.read_state(f"kind-{args.cluster}")
            print(live.to_json(s) if args.json else live.summary(f, args.cluster, s))
            probs = live.problems(f, args.cluster, s)
            if probs and not args.json:
                print("  differs from Git / not done:\n    " + "\n    ".join(probs))
            return 1 if probs and args.strict else 0
    except (ValueError, OSError, RuntimeError) as e:
        print(f"fleet: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
