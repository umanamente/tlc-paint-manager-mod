"""Approved wide illustration bytes, isolated limits and package provenance."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lupa.lua54 import LuaRuntime
from PIL import Image
from test_tooling import write_player_docs

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SignalArtworkTests(unittest.TestCase):
    def setUp(self):
        self.manage = module("signal_asset_manage", ROOT / "tools/manage.py")
        self.builder = module("signal_asset_builder", ROOT / "tools/build_signal_art.py")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.art = self.root / "artwork/ui/signal-lost"
        shutil.copytree(ROOT / "artwork/ui/signal-lost", self.art)
        self.builder.build(self.root)
        self.directory = self.root / "src/PaintManager/Assets/UI/Illustrations"
        self.image = self.directory / "signal_lost.png"
        self.manifest = self.directory / "manifest.json"
        self.embedded = self.root / "src/PaintManager/Scripts/paintmanager/ui_signal_data.lua"

    def test_approved_bytes_canvas_alpha_and_generated_twin_remain_exact(self):
        files = self.manage.ui_illustrations(self.root)
        data = files["Assets/UI/Illustrations/signal_lost.png"]
        self.assertEqual(data, (self.art / self.builder.SOURCE_FILE).read_bytes())
        self.assertEqual(len(data), 118144)
        self.assertEqual(hashlib.sha256(data).hexdigest(), self.builder.APPROVED_SHA256)
        self.assertEqual(self.builder.APPROVED_SHA256, self.manage.UI_SIGNAL_SHA256)
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            self.assertEqual((image.format, image.mode, image.size), ("PNG", "RGBA", (1024, 512)))
            alpha = image.getchannel("A")
            self.assertEqual(alpha.getextrema(), (0, 255))
            self.assertEqual(sum(alpha.histogram()[1:255]), 26193)
        twin = self.manage.embedded_ui_signal(self.embedded.read_text(encoding="ascii"))
        self.assertEqual(twin["data"], data)
        self.builder.build(self.root, check=True)
        self.assertEqual(len(files), 2)

    def test_illustration_does_not_expand_square_control_caps(self):
        self.assertEqual(self.manage.UI_ICON_MAX_BYTES, 32768)
        self.assertFalse(self.manage.checked_ui_png(self.image.read_bytes()))
        self.assertTrue(self.manage.checked_ui_illustration_png(self.image.read_bytes()))
        self.assertEqual((self.manage.UI_ILLUSTRATION_MAX_BYTES, self.manage.UI_ILLUSTRATION_MAX_PIXELS), (1048576, 2000000))

    def test_unknown_files_directories_and_case_changes_are_rejected(self):
        for name, directory in (("unexpected.png", False), ("other", True)):
            with self.subTest(name=name):
                extra = self.directory / name
                if directory:
                    extra.mkdir()
                else:
                    extra.write_bytes(b"not an approved asset")
                with self.assertRaisesRegex(self.manage.ToolError, "allowlist"):
                    self.manage.ui_illustrations(self.root)
                if directory:
                    extra.rmdir()
                else:
                    extra.unlink()
        self.image.rename(self.directory / "Signal_Lost.png")
        with self.assertRaisesRegex(self.manage.ToolError, "allowlist"):
            self.manage.ui_illustrations(self.root)

    def test_symlink_file_and_parent_directory_are_rejected_even_inside_root(self):
        target = self.root / "linked-approved.png"
        self.image.rename(target)
        try:
            self.image.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Host does not permit filesystem symlinks: {exc}")
        with self.assertRaisesRegex(self.manage.ToolError, "linked deployment path"):
            self.manage.ui_illustrations(self.root)
        self.image.unlink()
        target.rename(self.image)
        actual = self.root / "actual-Illustrations"
        self.directory.rename(actual)
        self.directory.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(self.manage.ToolError, "linked deployment path"):
            self.manage.ui_illustrations(self.root)

    def test_link_rejection_guard_covers_file_directory_and_embedded_parents(self):
        original = Path.is_symlink
        for target in (self.image, self.directory, self.embedded, self.embedded.parent):
            with self.subTest(target=target.name), patch.object(
                    Path, "is_symlink", lambda path: path == target or original(path)):
                with self.assertRaisesRegex(self.manage.ToolError, "linked deployment path"):
                    self.manage.ui_illustrations(self.root)

    def test_manifest_cannot_adopt_unapproved_bytes_dimensions_or_unknown_ids(self):
        original = self.manifest.read_text(encoding="utf-8")
        for key, value in (("file", "../signal_lost.png"), ("width", 128), ("height", 512.0),
                           ("bytes", 32768), ("sha256", "0" * 64), ("alphaExtrema", [255, 255])):
            with self.subTest(key=key):
                record = json.loads(original)
                record["illustrations"]["signal_lost"][key] = value
                self.manifest.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaises(self.manage.ToolError):
                    self.manage.ui_illustrations(self.root)
        record = json.loads(original)
        record["illustrations"]["unapproved"] = record["illustrations"].pop("signal_lost")
        self.manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(self.manage.ToolError):
            self.manage.ui_illustrations(self.root)
        self.manifest.write_text(original.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'), encoding="utf-8")
        with self.assertRaises(self.manage.ToolError):
            self.manage.ui_illustrations(self.root)

    def test_corruption_trailing_bytes_and_recomputed_unapproved_digest_are_rejected(self):
        original = self.image.read_bytes()
        for value in (original[:-1], original + b"trailing", original[:40] + bytes([original[40] ^ 1]) + original[41:]):
            with self.subTest(size=len(value)):
                self.image.write_bytes(value)
                with self.assertRaises(self.manage.ToolError):
                    self.manage.ui_illustrations(self.root)
        record = json.loads(self.manifest.read_text(encoding="utf-8"))
        record["illustrations"]["signal_lost"]["sha256"] = hashlib.sha256(value).hexdigest()
        self.manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(self.manage.ToolError):
            self.manage.ui_illustrations(self.root)

    def test_actual_alpha_and_exact_rgba_canvas_are_validated(self):
        for size, mode, color in (((1024, 512), "RGBA", (0, 0, 0, 255)),
                                  ((1024, 512), "RGBA", (0, 0, 0, 0)),
                                  ((128, 128), "RGBA", (0, 0, 0, 0)),
                                  ((1024, 512), "RGB", (0, 0, 0))):
            with self.subTest(size=size, mode=mode, color=color):
                output = io.BytesIO()
                Image.new(mode, size, color).save(output, format="PNG")
                self.assertFalse(self.manage.checked_ui_illustration_png(output.getvalue()))

    def test_embedded_table_is_literal_only_and_every_byte_matches_runtime_png(self):
        original = self.embedded.read_text(encoding="ascii")
        byte_field = f"bytes = {self.builder.BYTE_LENGTH},"
        variants = [original + 'error("unexpected code")\n',
                    original.replace(byte_field, f"width = {self.builder.BYTE_LENGTH},"),
                    original.replace(byte_field, f"unapproved = {self.builder.BYTE_LENGTH},"),
                    original.replace('hex = "89504e47', 'hex = "89514e47')]
        for value in variants:
            with self.subTest(prefix=value[:50]):
                self.embedded.write_text(value, encoding="ascii")
                with self.assertRaises(self.manage.ToolError):
                    self.manage.ui_illustrations(self.root)

    def test_large_generated_literal_compiles_without_loading_runtime_ui(self):
        runtime = LuaRuntime(unpack_returned_tuples=True)
        runtime.compile(self.embedded.read_text(encoding="ascii"), name="@ui_signal_data.lua")
        self.assertIsNone(runtime.globals().PaintManagerWorker)

    def test_changed_raster_or_vector_fails_before_output(self):
        for filename in (self.builder.SOURCE_FILE, self.builder.VECTOR_FILE):
            with self.subTest(filename=filename):
                source = self.art / filename
                data = source.read_bytes()
                source.write_bytes(data[:40] + bytes([data[40] ^ 1]) + data[41:])
                with self.assertRaisesRegex(ValueError, "digest changed"):
                    self.builder.outputs(self.root)
                source.write_bytes(data)

    def test_square_control_builder_preserves_the_separate_illustration(self):
        shutil.copytree(ROOT / "artwork/ui/style-c", self.root / "artwork/ui/style-c")
        shutil.copytree(ROOT / "artwork/ui/deposit", self.root / "artwork/ui/deposit")
        controls = module("signal_control_builder", ROOT / "tools/build_ui_assets.py")
        before = {path: path.read_bytes() for path in (self.image, self.manifest, self.embedded)}
        controls.build(self.root)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(len(self.manage.ui_assets(self.root)), len(self.manage.UI_ICON_IDS) + 1)
        self.assertEqual(len(self.manage.ui_illustrations(self.root)), 2)

    def test_payload_includes_only_runtime_illustration_and_checked_twin(self):
        write_player_docs(self.root)
        (self.root / "mod.json").write_text("Fixture", encoding="utf-8")
        with patch.object(self.manage, "checked_sources", return_value=({"id": "PaintManager", "version": "0.1.0"}, [self.embedded])), \
                patch.object(self.manage, "ui_assets", return_value={}):
            _, files = self.manage.payload(self.root)
        self.assertIn("Scripts/paintmanager/ui_signal_data.lua", files)
        self.assertEqual(files["Assets/UI/Illustrations/signal_lost.png"], (self.art / self.builder.SOURCE_FILE).read_bytes())
        self.assertEqual(sum(name.startswith("Assets/UI/Illustrations/") for name in files), 2)
        self.assertFalse(any("artwork/" in name or "generation-notes" in name or "approved-source" in name for name in files))


if __name__ == "__main__":
    unittest.main()
