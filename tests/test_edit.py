import difflib
import unittest
from pathlib import Path

from fleet import config, edit, plan

TEXT = (Path(__file__).resolve().parent.parent / "fleet.toml").read_text()


def changed(a: str, b: str) -> list[str]:
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and l[:3] not in ("+++", "---")]


class Edit(unittest.TestCase):
    def test_promotion_path(self):
        t1 = edit.start(TEXT, "2026.10")
        self.assertEqual(len(changed(TEXT, t1)), 4)          # two pools of staging: 2 lines out, 2 in
        self.assertIn("# The fleet in one file", t1)           # comments survive

        with self.assertRaisesRegex(ValueError, "nothing to promote: prod/a100 already runs 2026.07"):
            edit.promote(t1, "prod/a100")                     # the wave before it has not moved yet

        t2, rel = edit.promote(t1, "prod/h100")
        self.assertEqual(rel, "2026.10")
        self.assertEqual(changed(t1, t2), ['-release = "2026.07"', '+release = "2026.10"'])
        f = config.parse_text(t2)
        self.assertEqual(plan.plan_name(f, f.cluster("prod")), "prod-2026.10-h100")

        t3, name = edit.approve(t2, "prod")
        self.assertEqual(name, "prod-2026.10-h100")
        self.assertEqual(changed(t2, t3), ['-approved = ""', '+approved = "prod-2026.10-h100"'])
        self.assertEqual(plan.configmap_data(config.parse_text(t3), config.parse_text(t3).cluster("prod"))["approve"],
                         "prod-2026.10-h100")

        t4, _ = edit.promote(t3, "prod/a100")
        f = config.parse_text(t4)
        self.assertEqual({w: f.target_release(w) for w in f.waves},
                         {"staging": "2026.10", "prod/h100": "2026.10", "prod/a100": "2026.10"})
        # a new plan name: the old approval does not carry over
        self.assertNotEqual(f.cluster("prod").approved, plan.plan_name(f, f.cluster("prod")))

    def test_first_wave_and_unknown(self):
        with self.assertRaisesRegex(ValueError, "first wave"):
            edit.promote(TEXT, "staging")
        with self.assertRaisesRegex(ValueError, "not a wave"):
            edit.promote(TEXT, "prod")
        with self.assertRaisesRegex(ValueError, "unknown release"):
            edit.start(TEXT, "2099.01")

    def test_start_cannot_go_behind_later_waves(self):
        t = edit.promote(edit.start(TEXT, "2026.10"), "prod/h100")[0]
        with self.assertRaisesRegex(ValueError, "newer than 2026.07"):
            edit.start(t, "2026.07")


if __name__ == "__main__":
    unittest.main()
