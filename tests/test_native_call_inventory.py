"""Same-build native parameter envelopes plus source-inventory drift gate.

This does not emulate ProcessEvent, native struct lifetime or Slate. A newly
introduced method requires inspection of the pinned metadata/bridge, rather
than being silently accepted by a permissive fixture.
"""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeCallInventoryTests(unittest.TestCase):
    def test_all_native_method_names_have_reviewed_binding_or_parameter_envelopes(self):
        contract = json.loads((ROOT / "docs/maintainers/native-call-contracts.json").read_text(encoding="utf-8"))
        observed = set()
        for path in (ROOT / "src/PaintManager/Scripts").rglob("*.lua"):
            source = path.read_text(encoding="utf-8-sig")
            observed.update(re.findall(r":([A-Z][A-Za-z_0-9]*)\s*\(", source))
            observed.update(re.findall(r'\bcall\([^,]+,\s*"([A-Z][A-Za-z_0-9]*)"', source))
        known = set(contract["loaderMethods"]) | set(contract["reflectedMethods"])
        self.assertEqual(observed - known, set(), "Inspect new native calls against the pinned loader before adding them")
        self.assertNotIn("SetStyle", observed)
        self.assertEqual(contract["parameterCapacity"], 0x200)
        for name, signatures in contract["reflectedMethods"].items():
            self.assertTrue(signatures, name)
            for signature in signatures:
                with self.subTest(method=name, path=signature["path"]):
                    size = signature["size"]
                    self.assertGreaterEqual(size, 0)
                    self.assertLessEqual(size, 0x200)
                    for parameter in signature["properties"]:
                        self.assertGreaterEqual(parameter["offset"], 0)
                        self.assertLessEqual(parameter["offset"] + parameter["size"], size)
                        self.assertIn("CPF_Parm", parameter["flags"])
