"""Deterministic callback regression gates for actual guarded storage jobs.

Constant-clock tests retain the eight-unit cap without machine-speed flakiness.
They catch accidental reintroduction of microstep-per-callback scheduling and
duplicate whole-document codec passes; they do not assert native game latency.
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import benchmark_storage as benchmark


class StoragePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scripts = benchmark.DEFAULT_SCRIPTS
        cls.fixture = benchmark.fixtures(cls.scripts)
        data = benchmark.seed_document()
        data["sections"]["optional"] = {"schemaVersion": 1, "data": ""}
        # Retain the unknown-section exercise with the current optional ship
        # setting. A slightly larger 1.5 KiB fixture keeps every original
        # callback/codec bound unchanged rather than shrinking tested content.
        remainder = 1536 - len(benchmark.canonical(data).encode())
        assert 0 <= remainder <= 4096
        data["sections"]["optional"]["data"] = "p" * remainder
        cls.small = benchmark.canonical(data)
        cls.large = benchmark.canonical(benchmark.large_document(data))

    def measure(self, raw, scenario):
        result = benchmark.run(self.scripts, self.fixture, raw, scenario, "count_only")
        self.assertEqual(result["state"], "done")
        self.assertLessEqual(result["maximumUnitsPerCallback"], 8)
        return result

    def test_small_noop_and_refresh_use_one_codec_pass_and_few_callbacks(self):
        expected_hash = hashlib.sha256(self.small.encode()).hexdigest()
        for scenario in ("noop_sync", "refresh_library"):
            with self.subTest(scenario=scenario):
                result = self.measure(self.small, scenario)
                self.assertLessEqual(result["callbacks"], 16)
                self.assertEqual(result["constructors"].get("json_reader.begin"), 1)
                self.assertEqual(result["constructors"].get("json.begin"), 1)
                self.assertEqual(result["nativeAttempts"], 0)
                self.assertEqual(result["outputSha256"], expected_hash)
                self.assertEqual(result["outputPresets"], 1)

    def test_small_mutations_are_bounded_and_keep_their_distinct_outcomes(self):
        for scenario, live, deleted in (("add", 2, 0), ("delete", 0, 1), ("radius_edit", 1, 0)):
            with self.subTest(scenario=scenario):
                result = self.measure(self.small, scenario)
                self.assertLessEqual(result["callbacks"], 32)
                self.assertEqual(result["outputPresets"], live)
                self.assertEqual(result["outputTombstones"], deleted)
                self.assertEqual(result["nativeAttempts"], 1)

    def test_actual_legacy_native_with_existing_local_shape_does_not_regress(self):
        result = self.measure(self.small, "legacy_native_existing_local")
        self.assertLessEqual(result["callbacks"], 24)
        self.assertEqual(result["outputPresets"], 1)
        self.assertEqual(result["nativeAttempts"], 1)

    def test_optional_ship_radius_normalizes_once_then_returns_to_the_noop_gate(self):
        old = json.loads(self.small)
        old["sections"]["settings"]["schemaVersion"] = 3
        old["sections"]["settings"]["data"]["radiusCm"] = 10000
        old["sections"]["settings"]["data"]["updatedAt"]["radiusCm"] = 42
        old["sections"]["settings"]["data"].pop("shipRadiusCm")
        old["sections"]["settings"]["data"]["updatedAt"].pop("shipRadiusCm")
        old_raw = benchmark.canonical(old)
        expected_hash = hashlib.sha256(self.small.encode()).hexdigest()
        normalized = self.measure(old_raw, "noop_sync")
        # A genuine shape extension publishes once; it is not a steady-state
        # no-op. The current canonical seed keeps the original one-pass gates.
        self.assertLessEqual(normalized["callbacks"], 32)
        self.assertEqual(normalized["nativeAttempts"], 1)
        self.assertEqual(normalized["outputSha256"], expected_hash)
        self.assertEqual(json.loads(self.small)["revision"], old["revision"])
        current = self.measure(self.small, "noop_sync")
        self.assertLessEqual(current["callbacks"], 16)
        self.assertEqual(current["constructors"].get("json_reader.begin"), 1)
        self.assertEqual(current["constructors"].get("json.begin"), 1)
        self.assertEqual(current["nativeAttempts"], 0)
        self.assertEqual(current["outputSha256"], expected_hash)

    def test_near_capacity_library_keeps_callback_and_byte_bounds(self):
        self.assertEqual(len(self.large.encode()), 32000)
        baseline = self.measure(self.large, "noop_sync")
        self.assertLessEqual(baseline["callbacks"], 48)
        changed = self.measure(self.large, "add")
        self.assertLessEqual(changed["callbacks"], 160)
        self.assertLessEqual(changed["resultBytes"], 32768)
        self.assertEqual(changed["outputPresets"], baseline["outputPresets"] + 1)


if __name__ == "__main__":
    unittest.main()
