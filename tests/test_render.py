import contextlib
import io
import json
import unittest
from pathlib import Path

from fleet import cli, config, edit, render

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "fleet.toml").read_text()


def docs(f, path):
    return render.files(f)[path]


class Render(unittest.TestCase):
    def test_committed_files_are_current(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["--config", str(ROOT / "fleet.toml"), "--root", str(ROOT), "render", "--check"])
        self.assertEqual(rc, 0, "clusters/ is stale: run python -m fleet render")

    def test_driver_crd_follows_operator_version(self):
        f = config.parse_text(TEXT)
        old = docs(f, "clusters/prod/config/nvidiadrivers.yaml")[0]["spec"]
        self.assertNotIn("default", old)           # v26.3: the field does not exist
        f2 = config.parse_text(edit.promote(edit.start(TEXT, "2026.10"), "prod/h100")[0])
        h100, a100 = docs(f2, "clusters/prod/config/nvidiadrivers.yaml")
        self.assertEqual((h100["spec"]["version"], h100["spec"]["default"]), ("595.91.07", False))
        # a100 keeps its driver but gets the v26.7 fields, because the cluster's operator moved
        self.assertEqual((a100["spec"]["version"], a100["spec"]["default"]), ("580.126.20", False))
        self.assertEqual(a100["spec"]["nodeSelector"], {"fleet.lab/pool": "a100"})
        gpu_op = docs(f2, "clusters/prod/operators/gpu-operator.yaml")[0]
        self.assertEqual(gpu_op["spec"]["chart"]["spec"]["version"], "v26.7.1")
        nic = {d["metadata"]["name"]: d["spec"]["ofedDriver"]["version"] for d in docs(f2, "clusters/prod/config/nicnodepolicies.yaml")}
        self.assertEqual(nic, {"h100": "doca3.5.0-26.07-0.7.7.0-0", "a100": "doca3.4.1-26.04-1.1.0.0-3"})

    def test_operators(self):
        f = config.parse_text(TEXT)
        net = docs(f, "clusters/staging/operators/network-operator.yaml")[0]
        self.assertEqual(net["spec"]["values"]["nfd"]["enabled"], False)
        self.assertEqual(net["spec"]["dependsOn"], [{"name": "gpu-operator", "namespace": "gpu-operator"}])
        gpu = docs(f, "clusters/staging/operators/gpu-operator.yaml")[0]["spec"]
        self.assertEqual(gpu["values"]["driver"]["nvidiaDriverCRD"], {"enabled": True, "deployDefaultCR": False})
        self.assertEqual(gpu["upgrade"]["crds"], "CreateReplace")

    def test_lifecycle_plan_and_agent(self):
        f = config.parse_text(TEXT)
        cm = docs(f, "clusters/prod/lifecycle/rollout-plan.yaml")[0]
        p = json.loads(cm["data"]["plan.json"])
        self.assertEqual((p["name"], p["target"], p["pauseAfterCanary"]), ("prod-2026.07-a100+h100", "580.126.20", True))
        self.assertNotIn("approve", cm["data"])
        # the agent's start version never changes with a release, or every promotion would restart it
        f2 = config.parse_text(edit.start(TEXT, "2026.10"))
        a1 = docs(f, "clusters/staging/lifecycle/node-agent.yaml")
        a2 = docs(f2, "clusters/staging/lifecycle/node-agent.yaml")
        self.assertEqual(a1, a2)

    def test_kind(self):
        f = config.parse_text(TEXT)
        k = docs(f, "kind/prod.yaml")[0]
        pools = [n["labels"]["fleet.lab/pool"] for n in k["nodes"] if n["role"] == "worker"]
        self.assertEqual(pools, ["h100", "h100", "a100", "a100"])

    def test_values_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--config", str(ROOT / "fleet.toml"), "values", "staging", "network-operator"])
        v = json.loads(out.getvalue())
        self.assertEqual((v["chart"], v["version"]), ("network-operator", "26.4.2"))


if __name__ == "__main__":
    unittest.main()
