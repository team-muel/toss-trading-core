"""Independent contract examples, not expectations generated from the routing map."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_maintenance_registry.py"
spec = importlib.util.spec_from_file_location("maintenance_adversarial_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
REGISTRY = json.loads((ROOT / "config/maintenance_surfaces.json").read_text(encoding="utf-8"))


class MaintenanceAdversarialTests(unittest.TestCase):
    def test_global_wildcards_cannot_hide_unmapped_paths(self):
        for pattern in ("*", "**", "***", "?*", "[!x]*", "*/*"):
            for section in ("surfaces", "cross_surface_rules"):
                registry = copy.deepcopy(REGISTRY)
                registry[section][0]["paths"] = [pattern]
                with self.subTest(pattern=pattern, section=section):
                    with self.assertRaises(ValueError):
                        check.classify(registry, ["new_package/unowned.py"])

    def test_literal_prefix_patterns_remain_supported(self):
        for pattern in ("src/**", "requirements*.lock", "cloudbuild*.yaml", ".github/**", "src/[ab]*"):
            with self.subTest(pattern=pattern):
                self.assertTrue(check.valid_path(pattern, pattern=True))

    def test_all_provider_entry_points_include_data_schema_and_tests(self):
        # Deliberately independent of surfaces[0] and cross_surface_rules[0].
        paths = (
            "src/asset_management/toss/broker/toss.py",
            "src/asset_management/broker/toss_read.py",
            "src/asset_management/data/adapters/toss.py",
            "src/research_platform/providers.py",
            "src/research_platform/cli/research_collect_tiingo.py",
            "src/research_platform/cli/research_collect_sec.py",
            "config/toss_openapi_contract.json",
            "config/data_sources.yaml",
            "config/fred_series.csv",
            ".env.example",
        )
        for path in paths:
            with self.subTest(path=path):
                actual = set(check.classify(REGISTRY, [path])["umbrellas"])
                self.assertTrue({"AMA-135", "AMA-136", "AMA-143", "AMA-145"} <= actual, actual)

    def test_other_change_families_have_independent_contract_examples(self):
        cases = (
            ("src/asset_management/domain/economics.py", {"AMA-139", "AMA-141", "AMA-143", "AMA-146"}),
            ("schemas/migrations/deleted.sql", {"AMA-136", "AMA-140", "AMA-141", "AMA-143", "AMA-144", "AMA-145"}),
            ("src/asset_management/execution/orders.py", {"AMA-140", "AMA-141", "AMA-145"}),
            ("pyproject.toml", {"AMA-144", "AMA-145"}),
            ("src/asset_management/orchestration/research_bridge.py", {"AMA-136", "AMA-137", "AMA-138", "AMA-139", "AMA-145"}),
        )
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertTrue(expected <= set(check.classify(REGISTRY, [path])["umbrellas"]))

    def test_rule_order_and_surface_order_do_not_change_results(self):
        shuffled = copy.deepcopy(REGISTRY)
        shuffled["surfaces"].reverse()
        shuffled["cross_surface_rules"].reverse()
        paths = ["config/toss_openapi_contract.json", "schemas/migrations/deleted.sql", "README.md"]
        self.assertEqual(check.classify(REGISTRY, paths), check.classify(shuffled, paths))

    def test_combining_known_and_unknown_paths_preserves_unknowns(self):
        result = check.classify(REGISTRY, ["README.md", "new/unowned.py", "README.md"])
        self.assertEqual(result["unmapped"], ["new/unowned.py"])
        self.assertEqual(result["paths"]["new/unowned.py"], [])
        self.assertIn("AMA-146", result["umbrellas"])

    def test_cli_mixed_known_and_unknown_is_nonzero(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "README.md", "new/unowned.py"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unmapped"], ["new/unowned.py"])

    def test_nul_file_option_like_names_are_data_not_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paths"
            path.write_bytes(b'--registry\0README.md\0')
            result = subprocess.run([sys.executable, str(SCRIPT), "--paths0-from", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("--registry", json.loads(result.stdout)["unmapped"])

    def test_cli_nested_duplicate_json_keys_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "registry.json"
            path.write_text('{"version":1,"extra":{"a":1,"a":2}}', encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), "--registry", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate JSON key", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_missing_and_truncated_path_files_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paths"
            for data in (None, b"", b"README.md", b"\xff\0"):
                if data is not None:
                    path.write_bytes(data)
                result = subprocess.run([sys.executable, str(SCRIPT), "--paths0-from", str(path)], capture_output=True, text=True)
                with self.subTest(data=data):
                    self.assertEqual(result.returncode, 2)
                    self.assertNotIn("Traceback", result.stderr)

    def test_documented_diff_keeps_both_sides_of_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.run(["git", "-C", temp, *args], check=True, capture_output=True).stdout
            git("init", "-q")
            git("config", "user.name", "Offline Review")
            git("config", "user.email", "offline@example.invalid")
            old = root / "config/data_sources.yaml"
            old.parent.mkdir()
            old.write_text("example: fixture\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "before")
            base = git("rev-parse", "HEAD").decode().strip()
            new = root / "new_package/unowned.yaml"
            new.parent.mkdir()
            old.rename(new)
            git("add", "-A")
            git("commit", "-qm", "after")
            paths = check.null_paths(git("diff", "--no-renames", "--name-only", "-z", base, "HEAD", "--"))
            self.assertEqual(set(paths), {"config/data_sources.yaml", "new_package/unowned.yaml"})
            result = check.classify(REGISTRY, paths)
            self.assertEqual(result["unmapped"], ["new_package/unowned.yaml"])
            self.assertIn("AMA-135", result["umbrellas"])


if __name__ == "__main__":
    unittest.main()
