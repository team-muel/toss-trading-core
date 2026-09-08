"""Routing regression tests deliberately need no trading runtime or credentials."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/check_maintenance_registry.py'
spec = importlib.util.spec_from_file_location('maintenance_registry_check', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
REGISTRY = json.loads((ROOT / 'config/maintenance_surfaces.json').read_text())


class MaintenanceRegistryTests(unittest.TestCase):
    def test_registry_and_exact_umbrellas(self):
        check.validate(REGISTRY)
        self.assertEqual({s['issue'] for s in REGISTRY['surfaces']}, check.EXPECTED)

    def test_every_surface_has_a_routing_example(self):
        for surface in REGISTRY['surfaces']:
            path = surface['paths'][0].replace('**', 'nested/example.py').replace('*', 'example')
            with self.subTest(issue=surface['issue']):
                self.assertIn(surface['issue'], check.classify(REGISTRY, [path])['umbrellas'])

    def test_cross_surface_rules_are_additive(self):
        for rule in REGISTRY['cross_surface_rules']:
            path = rule['paths'][0].replace('**', 'nested/example.py').replace('*', 'example')
            with self.subTest(reason=rule['reason']):
                result = check.classify(REGISTRY, [path])
                self.assertTrue(set(rule['issues']) <= set(result['umbrellas']))
                self.assertTrue(result['cross_surface'])

    def test_provider_change_includes_data_schema_and_tests(self):
        result = check.classify(REGISTRY, ['config/toss_openapi_contract.json'])
        self.assertTrue({'AMA-135', 'AMA-136', 'AMA-143', 'AMA-145'} <= set(result['umbrellas']))

    def test_unknown_path_is_not_silently_classified(self):
        result = check.classify(REGISTRY, ['new_package/unknown.py'])
        self.assertEqual(result['unmapped'], ['new_package/unknown.py'])

    def test_path_results_are_deterministic_and_deduplicated(self):
        paths = ['README.md', 'tests/test_example.py', 'README.md']
        self.assertEqual(check.classify(REGISTRY, paths), check.classify(REGISTRY, list(reversed(paths))))
        self.assertEqual(len(check.classify(REGISTRY, paths)['paths']), 2)

    def test_deleted_paths_need_not_exist(self):
        self.assertIn('AMA-143', check.classify(REGISTRY, ['schemas/migrations/deleted.sql'])['umbrellas'])

    def test_invalid_paths_rejected(self):
        for path in ['', '/etc/passwd', '../README.md', 'a/../b', './README.md', 'a//b', 'a\\b', 'C:/b', 'a\nb', 'a\x00b']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                check.classify(REGISTRY, [path])

    def test_null_paths_preserve_spaces_and_unicode(self):
        paths = ['docs/two words.md', 'docs/\ud55c\uae00.md']
        self.assertEqual(check.null_paths(('\0'.join(paths) + '\0').encode()), paths)

    def test_empty_or_truncated_null_input_rejected(self):
        for data in [b'', b'README.md', b'\0', b'README.md\0\0', b'\xff\0']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                check.null_paths(data)

    def test_missing_or_duplicate_umbrella_rejected(self):
        for mutate in [lambda r: r['surfaces'].pop(), lambda r: r['surfaces'][1].update(issue='AMA-135')]:
            registry = copy.deepcopy(REGISTRY)
            mutate(registry)
            with self.assertRaises(ValueError):
                check.validate(registry)

    def test_malformed_surfaces_rejected(self):
        for value in [None, [], 1, True, {}, {'issue': []}]:
            registry = copy.deepcopy(REGISTRY)
            registry['surfaces'][0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                check.validate(registry)

    def test_invalid_versions_rejected(self):
        for value in [True, 0, 2, '1', None]:
            registry = copy.deepcopy(REGISTRY)
            registry['version'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                check.validate(registry)

    def test_invalid_baseline_or_sot_rejected(self):
        for key, value in [('baseline_sha', '5d6e28b'), ('baseline_sha', 1), ('linear_register', 'https://example.com')]:
            registry = copy.deepcopy(REGISTRY)
            registry[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                check.validate(registry)

    def test_missing_evidence_or_invalid_patterns_rejected(self):
        for key, value in [('evidence', []), ('paths', ['*']), ('paths', ['../*']), ('paths', ['src/{a,b}']), ('paths', [{}])]:
            registry = copy.deepcopy(REGISTRY)
            registry['surfaces'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                check.validate(registry)

    def test_unknown_cross_surface_reference_rejected(self):
        registry = copy.deepcopy(REGISTRY)
        registry['cross_surface_rules'][0]['issues'] = ['AMA-135', 'AMA-999']
        with self.assertRaises(ValueError):
            check.validate(registry)

    def test_missing_or_malformed_cross_surface_rules_rejected(self):
        for value in [None, [], [None], [{'paths': ['src/**'], 'issues': []}]]:
            registry = copy.deepcopy(REGISTRY)
            registry['cross_surface_rules'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                check.validate(registry)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"version":1,"version":1}', object_pairs_hook=check.unique_object)

    def test_cli_modes_and_exit_codes(self):
        for paths, code, mode in [([], 0, 'registry-validation-only'), (['README.md'], 0, 'path-classification'), (['new/unknown.py'], 1, 'path-classification')]:
            result = subprocess.run([sys.executable, str(SCRIPT), *paths], capture_output=True, text=True)
            with self.subTest(paths=paths):
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)['mode'], mode)

    def test_cli_paths_file_and_invalid_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'paths'
            path.write_bytes(b'README.md\0schemas/deleted.schema.json\0')
            result = subprocess.run([sys.executable, str(SCRIPT), '--paths0-from', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['cross_surface'])
            path.write_text('{"version":true}')
            result = subprocess.run([sys.executable, str(SCRIPT), '--registry', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('maintenance registry error', result.stderr)


if __name__ == '__main__':
    unittest.main()
