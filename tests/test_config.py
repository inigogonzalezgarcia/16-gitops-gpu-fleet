import unittest
from pathlib import Path

from fleet import config

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "fleet.toml").read_text()


def variant(old: str, new: str, count: int = 1) -> str:
    assert old in TEXT, old
    return TEXT.replace(old, new, count)


class Config(unittest.TestCase):
    def test_repo_file(self):
        f = config.load(ROOT / "fleet.toml")
        self.assertEqual(f.waves, ["staging", "prod/h100", "prod/a100"])
        self.assertEqual([c.name for c in f.clusters], ["staging", "prod"])
        self.assertEqual(f.platform(f.cluster("prod")).gpu_operator, "v26.3.3")
        self.assertEqual(f.target_release("prod/h100"), "2026.07")

    def test_platform_is_newest_pool_release(self):
        f = config.parse_text(variant('name = "h100"\nnodes = 2\nrelease = "2026.07"',
                                      'name = "h100"\nnodes = 2\nrelease = "2026.10"')
                              .replace('name = "a100"\nnodes = 1\nrelease = "2026.07"', 'name = "a100"\nnodes = 1\nrelease = "2026.10"')
                              .replace('name = "h100"\nnodes = 1\nrelease = "2026.07"', 'name = "h100"\nnodes = 1\nrelease = "2026.10"'))
        prod = f.cluster("prod")
        self.assertEqual(f.platform(prod).name, "2026.10")      # h100 moved, so the operators did too
        self.assertEqual(prod.pool("a100").release, "2026.07")  # a100 keeps its driver

    def test_wave_order(self):
        # prod/h100 on a newer release than staging
        bad = variant('name = "h100"\nnodes = 2\nrelease = "2026.07"', 'name = "h100"\nnodes = 2\nrelease = "2026.10"')
        with self.assertRaisesRegex(ValueError, "newer than 2026.07 on the wave before it"):
            config.parse_text(bad)

    def test_driver_must_be_approved_with_the_operator(self):
        bad2 = variant('drivers = ["580.126.20", "595.91.07"]\n', 'drivers = ["595.91.07"]\n')
        # staging all on 2026.07, nobody uses 2026.10 yet: still valid
        config.parse_text(bad2)
        mixed = bad2.replace('name = "a100"\nnodes = 1\nrelease = "2026.07"', 'name = "a100"\nnodes = 1\nrelease = "2026.10"')
        with self.assertRaisesRegex(ValueError, "driver 580.126.20 is not approved with GPU Operator v26.7.1"):
            config.parse_text(mixed)

    def test_unknown_release_and_missing_wave(self):
        with self.assertRaisesRegex(ValueError, "unknown release 2027.01"):
            config.parse_text(variant('release = "2026.07"', 'release = "2027.01"'))
        with self.assertRaisesRegex(ValueError, "prod/a100 is in no wave"):
            config.parse_text(variant('"prod/h100", "prod/a100"]', '"prod/h100"]'))
        with self.assertRaisesRegex(ValueError, "in two waves"):
            config.parse_text(variant('waves = ["staging", "prod/h100", "prod/a100"]', 'waves = ["staging", "prod", "prod/a100"]'))

    def test_missing_key(self):
        with self.assertRaisesRegex(ValueError, "missing 'helm_repository'"):
            config.parse_text(variant('helm_repository = "https://helm.ngc.nvidia.com/nvidia"\n', ""))


if __name__ == "__main__":
    unittest.main()
