import json
import unittest
from pathlib import Path

from fleet import config, edit, live, plan

TEXT = (Path(__file__).resolve().parent.parent / "fleet.toml").read_text()
REV = "main@sha1:0123456789abcdef"


def state(f, cluster, phase="Complete", chart=None, driver_override=None, node_driver=None, rev=REV):
    """kubectl-style JSON for a cluster that runs exactly what fleet.toml says, unless told otherwise."""
    c = f.cluster(cluster)
    plat = f.platform(c)
    ks = {"items": [{"metadata": {"name": n}, "status": {"lastAppliedRevision": rev,
                     "conditions": [{"type": "Ready", "status": "True"}]}} for n in live.KUSTOMIZATIONS]}
    hrs = {"items": [{"metadata": {"name": n}, "status": {"history": [{"chartVersion": chart or v}],
                      "conditions": [{"type": "Ready", "status": "True"}]}}
                     for n, v in (("gpu-operator", plat.gpu_operator), ("network-operator", plat.network_operator))]}
    nvds = {"items": [{"metadata": {"name": p.name}, "spec": {"version": (driver_override or {}).get(p.name, f.release(p.release).driver)}}
                      for p in c.pools]}
    nnps = {"items": [{"metadata": {"name": p.name}, "spec": {"ofedDriver": {"version": f.release(p.release).doca}}} for p in c.pools]}
    status = {"data": {"status.json": json.dumps({"rollout": plan.plan_name(f, c), "phase": phase, "message": "m"})}}
    nodes = {"items": []}
    for p in c.pools:
        for i in range(p.nodes):
            d = (node_driver or {}).get(p.name, f.release(p.release).driver)
            nodes["items"].append({"metadata": {"name": f"{cluster}-{p.name}-{i}",
                                                "labels": {"fleet.lab/pool": p.name, "lifecycle.lab/version": d}}})
    return live.parse_state({"status": {"artifact": {"revision": rev}}}, ks, hrs, nvds, nnps, status, nodes)


class Live(unittest.TestCase):
    def setUp(self):
        self.f = config.parse_text(edit.start(TEXT, "2026.10"))

    def test_good_staging_opens_the_gate(self):
        s = state(self.f, "staging")
        self.assertEqual(live.problems(self.f, "staging", s), [])
        self.assertEqual(live.gate(self.f, "prod/h100", read=lambda ctx: s), [])

    def test_gate_blocks(self):
        cases = {
            "rollout staging-2026.10-a100+h100 is Halted": state(self.f, "staging", phase="Halted"),
            "HelmRelease gpu-operator: chart v26.3.3": state(self.f, "staging", chart="v26.3.3"),
            "NVIDIADriver h100: 580.126.20, Git wants 595.91.07": state(self.f, "staging", driver_override={"h100": "580.126.20"}),
            "node staging-a100-0 (a100) runs 580.126.20": state(self.f, "staging", node_driver={"a100": "580.126.20"}),
        }
        for want, s in cases.items():
            with self.subTest(want):
                probs = live.gate(self.f, "prod/h100", read=lambda ctx, s=s: s)
                self.assertTrue(any(want in p for p in probs), probs)

    def test_stale_kustomization(self):
        s = state(self.f, "staging")
        s.git_revision = "main@sha1:fedcba"
        self.assertTrue(any("Git is at main@sha1:fedcba" in p for p in live.problems(self.f, "staging", s)))

    def test_later_wave_checks_only_its_pools(self):
        f = config.parse_text(edit.promote(edit.start(TEXT, "2026.10"), "prod/h100")[0])
        calls = []

        def read(ctx):
            calls.append(ctx)
            return state(f, "staging") if ctx == "kind-staging" else state(f, "prod")
        self.assertEqual(live.gate(f, "prod/a100", read=read), [])
        self.assertEqual(calls, ["kind-staging", "kind-prod"])

    def test_summary(self):
        text = live.summary(self.f, "staging", state(self.f, "staging"))
        self.assertIn("helmrelease   gpu-operator      chart=v26.7.1 ready=True", text)
        self.assertIn("pool h100   release=2026.10", text)


if __name__ == "__main__":
    unittest.main()
