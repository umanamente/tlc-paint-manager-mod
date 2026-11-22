"""Approved resource provenance and bounded native icon-lifetime contracts."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
import zlib

from lupa.lua54 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class UiAssetValidationTests(unittest.TestCase):
    """Package checks remain strict for the approved colored raster expansion."""

    def setUp(self):
        self.manage = module("ui_validation_manage", ROOT / "tools/manage.py")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "src/PaintManager/Assets/UI"
        self.directory.mkdir(parents=True)
        self.embedded = self.root / "src/PaintManager/Scripts/paintmanager/ui_icon_data.lua"
        self.embedded.parent.mkdir(parents=True)
        self.images = {name: self.png() for name in self.manage.UI_ICON_IDS}
        self.manifest = {"schemaVersion": 1, "width": 128, "height": 128,
                         "source": "Approved source fixture", "artworkSourceSha256": "a" * 64,
                         "icons": {name: {"file": name + ".png", "sha256": self.manage.sha256(data)}
                                   for name, data in self.images.items()}}
        self.write_fixture()

    @staticmethod
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))

    @classmethod
    def png(cls, *, width=128, height=128, depth=8, color=6, interlace=0, pixels=None, length=None):
        header = cls.chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace))
        data = cls.chunk(b"IDAT", zlib.compress(pixels if pixels is not None else bytes(128 * 513), 9))
        end = cls.chunk(b"IEND", b"")
        image = b"\x89PNG\r\n\x1a\n" + header + data + end
        if length is not None:
            filler = length - len(image) - 12
            if filler < 0:
                raise ValueError("Fixture PNG length is too small")
            image = b"\x89PNG\r\n\x1a\n" + header + cls.chunk(b"pmWt", bytes(filler)) + data + end
        return image

    def write_fixture(self):
        for name, data in self.images.items():
            (self.directory / (name + ".png")).write_bytes(data)
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.embedded.write_text("return {\n" + "\n".join(
            f'    ["{name}"] = "{data.hex()}",' for name, data in sorted(self.images.items())) + "\n}\n",
            encoding="utf-8")

    def replace_icon(self, data, name="ready_detailed"):
        self.images[name] = data
        self.manifest["icons"][name]["sha256"] = self.manage.sha256(data)
        self.write_fixture()

    def test_control_family_and_inclusive_32k_png_limit_are_accepted(self):
        self.assertEqual(len(self.manage.UI_ICON_IDS), 24)
        self.assertTrue({"to_backpack", "to_box", "deposit_bucket"}.issubset(self.manage.UI_ICON_IDS))
        self.assertIn("learned_detailed", self.manage.UI_ICON_IDS)
        self.assertIn("ready_detailed", self.manage.UI_ICON_IDS)
        self.replace_icon(self.png(length=32768))
        files = self.manage.ui_assets(self.root)
        self.assertEqual(len(files), 25)
        self.assertEqual(len(files["Assets/UI/ready_detailed.png"]), 32768)
        self.assertFalse(any("artwork" in name for name in files))

    def test_oversize_png_rejected_even_with_matching_manifest_and_embedded_bytes(self):
        self.replace_icon(self.png(length=32769))
        with self.assertRaisesRegex(self.manage.ToolError, "embedded UI icon entry|size or digest mismatch"):
            self.manage.ui_assets(self.root)

    def test_wrong_rgba8_dimensions_depth_and_interlace_rejected(self):
        for change in ({"width": 64}, {"height": 64}, {"depth": 16}, {"color": 2}, {"interlace": 1}):
            with self.subTest(change=change):
                self.replace_icon(self.png(**change))
                with self.assertRaisesRegex(self.manage.ToolError, "format, size or digest mismatch"):
                    self.manage.ui_assets(self.root)

    def test_corrupt_crc_trailing_bytes_and_bounded_decompression_are_rejected(self):
        corrupt = bytearray(self.png())
        corrupt[40] ^= 1
        invalid_filter = bytearray(128 * 513)
        invalid_filter[0] = 5
        for data in (bytes(corrupt), self.png() + b"trailing", self.png()[:-1],
                     self.png(pixels=bytes(128 * 513 + 1)), self.png(pixels=bytes(invalid_filter))):
            with self.subTest(length=len(data)):
                self.replace_icon(data)
                with self.assertRaises(self.manage.ToolError):
                    self.manage.ui_assets(self.root)

    def test_unknown_missing_or_case_changed_icon_ids_are_rejected(self):
        for mutation in ("unknown", "missing", "case"):
            with self.subTest(mutation=mutation):
                self.setUp()
                entry = self.manifest["icons"].pop("back")
                if mutation == "unknown":
                    self.manifest["icons"]["not_allowlisted"] = entry
                elif mutation == "case":
                    self.manifest["icons"]["Back"] = entry
                self.write_fixture()
                with self.assertRaisesRegex(self.manage.ToolError, "Invalid UI icon manifest"):
                    self.manage.ui_assets(self.root)

    def test_manifest_schema_dimensions_and_icon_table_types_are_strict(self):
        for key, value in (("schemaVersion", True), ("schemaVersion", 2), ("width", 128.0),
                           ("height", 64), ("icons", []), ("icons", None)):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.manifest[key] = value
                self.write_fixture()
                with self.assertRaisesRegex(self.manage.ToolError, "Invalid UI icon manifest"):
                    self.manage.ui_assets(self.root)

    def test_filename_paths_digest_types_and_metadata_shapes_are_rejected(self):
        for value in ("../back.png", "back.PNG", "other.png", "/back.png"):
            with self.subTest(filename=value):
                self.manifest["icons"]["back"]["file"] = value
                self.write_fixture()
                with self.assertRaisesRegex(self.manage.ToolError, "filename is not allowlisted"):
                    self.manage.ui_assets(self.root)
        self.manifest["icons"]["back"]["file"] = "back.png"
        for value in (None, True, [], "f" * 63, "X" * 64):
            with self.subTest(digest=value):
                self.manifest["icons"]["back"]["sha256"] = value
                self.write_fixture()
                with self.assertRaisesRegex(self.manage.ToolError, "digest is invalid"):
                    self.manage.ui_assets(self.root)
        for value in (None, [], "invalid"):
            with self.subTest(entry=value):
                self.manifest["icons"]["back"] = value
                self.write_fixture()
                with self.assertRaises(self.manage.ToolError):
                    self.manage.ui_assets(self.root)

    def test_duplicate_unknown_missing_or_executable_embedded_entries_are_rejected(self):
        original = self.embedded.read_text(encoding="utf-8")
        line = f'["back"] = "{self.images["back"].hex()}",'
        variants = [original.replace("\n}", "\n" + line + "\n}"),
                    original.replace('["back"]', '["not_allowlisted"]'),
                    original.replace(next(row for row in original.splitlines() if '["back"]' in row) + "\n", ""),
                    original + "error(\"unexpected code\")\n"]
        for value in variants:
            with self.subTest(prefix=value[:50]):
                self.embedded.write_text(value, encoding="utf-8")
                with self.assertRaisesRegex(self.manage.ToolError, "embedded UI icon|Embedded UI icon"):
                    self.manage.ui_assets(self.root)

    def test_duplicate_asset_bytes_are_allowed_but_every_twin_must_match(self):
        self.replace_icon(self.images["ready"], "learn")
        self.assertEqual(self.manage.ui_assets(self.root)["Assets/UI/learn.png"], self.images["ready"])
        self.embedded.write_text(self.embedded.read_text(encoding="utf-8").replace(
            f'["learn"] = "{self.images["learn"].hex()}"',
            f'["learn"] = "{self.png(length=4096).hex()}"'), encoding="utf-8")
        with self.assertRaisesRegex(self.manage.ToolError, "Embedded UI icon does not match"):
            self.manage.ui_assets(self.root)

    def test_package_includes_only_runtime_icons_and_their_checked_manifest(self):
        from unittest.mock import patch
        from test_tooling import write_player_docs
        write_player_docs(self.root)
        (self.root / "mod.json").write_text('{"id":"PaintManager","version":"0.1.0"}', encoding="utf-8")
        artwork = self.root / "artwork/ui/style-c"
        artwork.mkdir(parents=True)
        (artwork / "source-manifest.json").write_text("private source fixture", encoding="utf-8")
        (artwork / "ready.png").write_bytes(b"not a runtime icon")
        with patch.object(self.manage, "checked_sources", return_value=({"id": "PaintManager", "version": "0.1.0"}, [self.embedded])):
            _, files = self.manage.payload(self.root)
        self.assertEqual(sum(name.startswith("Assets/UI/") for name in files), len(self.manage.UI_ICON_IDS) + 1)
        self.assertIn("Scripts/paintmanager/ui_icon_data.lua", files)
        self.assertFalse(any(name.startswith("artwork/") for name in files))
        self.assertEqual(files["Assets/UI/manifest.json"], (self.directory / "manifest.json").read_bytes())


class UiApprovedArtworkTests(unittest.TestCase):
    def setUp(self):
        self.builder = module("ui_approved_builder", ROOT / "tools/build_ui_assets.py")
        self.manage = module("ui_approved_manage", ROOT / "tools/manage.py")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "artwork/ui/style-c"
        shutil.copytree(ROOT / "artwork/ui/style-c", self.directory)
        self.manifest_path = self.directory / "source-manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_approved_sources_bind_output_provenance_and_all_runtime_bytes_validate(self):
        overrides, digest = self.builder.approved_overrides(self.root)
        self.assertEqual(digest, self.manage.sha256(self.manifest_path.read_bytes()))
        self.assertEqual(set(overrides), {"star", "star_filled", "presets", "refresh", "learned", "ready",
                                          "learn", "learned_detailed", "ready_detailed"})
        self.assertTrue(all(self.manage.checked_ui_png(data) for data in overrides.values()))
        self.assertEqual(overrides["learn"], overrides["ready"])
        self.assertNotEqual(overrides["learned"], overrides["learned_detailed"])
        self.assertNotEqual(overrides["ready"], overrides["ready_detailed"])

    def test_changed_approved_source_bytes_are_rejected_before_build_outputs_exist(self):
        path = self.directory / self.manifest["sources"]["atlas"]["file"]
        data = bytearray(path.read_bytes())
        data[80] ^= 1
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "digest or size mismatch"):
            self.builder.outputs(self.root)
        self.assertFalse((self.root / "src").exists())

    def test_unknown_override_and_traversing_source_are_rejected(self):
        self.manifest["overrides"]["not_approved"] = {"source": "atlas"}
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "override IDs"):
            self.builder.approved_overrides(self.root)
        del self.manifest["overrides"]["not_approved"]
        self.manifest["sources"]["atlas"]["file"] = "../outside.png"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "Unsafe approved artwork filename"):
            self.builder.approved_overrides(self.root)

    def test_crop_outside_approved_source_is_rejected(self):
        self.manifest["overrides"]["star"]["region"] = [0, 0, 9000, 9000]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "crop exceeds source"):
            self.builder.approved_overrides(self.root)


class UiIconTests(unittest.TestCase):
    def test_approved_assets_reproduce_and_match_packaged_embedded_bytes(self):
        builder = module("ui_asset_builder", ROOT / "tools/build_ui_assets.py")
        builder.build(check=True)
        manage = module("ui_asset_package", ROOT / "tools/manage.py")
        files = manage.ui_assets(ROOT)
        self.assertEqual(len(files), len(manage.UI_ICON_IDS) + 1)
        self.assertEqual(files, manage.ui_assets(ROOT))

    def test_tampered_png_and_embedded_twin_are_rejected(self):
        manage = module("ui_asset_checks", ROOT / "tools/manage.py")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            shutil.copytree(ROOT / "src/PaintManager/Assets", target / "src/PaintManager/Assets")
            embedded = target / "src/PaintManager/Scripts/paintmanager/ui_icon_data.lua"
            embedded.parent.mkdir(parents=True)
            original = (ROOT / "src/PaintManager/Scripts/paintmanager/ui_icon_data.lua").read_bytes()
            embedded.write_bytes(original)
            embedded.write_bytes(original.replace(b"89504e47", b"00504e47", 1))
            with self.assertRaisesRegex(manage.ToolError, "Embedded UI icon"):
                manage.ui_assets(target)
            embedded.write_bytes(original)
            icon = target / "src/PaintManager/Assets/UI/close.png"
            icon.write_bytes(icon.read_bytes() + b"tamper")
            with self.assertRaisesRegex(manage.ToolError, "digest mismatch"):
                manage.ui_assets(target)

    def runtime(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().scripts_path = (ROOT / "src/PaintManager/Scripts").as_posix()
        lua.execute(r'''
        package.path=scripts_path.."/?.lua;"..package.path
        function FText(value) return value end
        calls,imports,attached=0,0,false
        function make(kind)
            local value={kind=kind,Font={},children={},alive=true}
            function value:IsValid() return self.alive end
            function value:SetText(text) self.text=text end
            function value:SetAutoWrapText() end
            function value:SetJustification() end
            function value:SetFont() end
            function value:SetVisibility(v) self.visibility=v end
            function value:AddChild(child) self.children[#self.children+1]=child end
            function value:SetBrushFromTexture(texture)
                assert(texture.alive);calls=calls+1;self.texture=texture;attached=true
            end
            return value
        end
        tree=make("tree")
        library=make("library")
        function library:ImportBufferAsTexture2D(context,bytes)
            imports=imports+1;assert(context==tree)
            assert(bytes[1]==137 and bytes[2]==80 and bytes[3]==78 and bytes[4]==71)
            if badImport then return nil end
            texture=make("Texture2D")
            function texture:IsA(class) return class=="/Script/Engine.Texture2D" end
            return texture
        end
        function StaticFindObject(path) return library end
        icons=require("paintmanager.ui_icons").new(tree,{},make)
        budget={used=0,expired=function() return false end,consume=function(self,n) self.used=self.used+n end}
        function drain_icons()
            for _=1,10000 do
                if not icons:has_work() then return end
                icons:pump(budget)
            end
            error("Bounded icon preparation did not finish")
        end
        ''')
        return lua

    def test_import_is_deferred_bounded_and_brush_owns_texture_before_yield(self):
        lua = self.runtime()
        lua.execute('''
        a=icons:image("star");b=icons:image("star")
        assert(imports==0 and a.children[2].visibility==1)
        for i=1,10000 do
            local before=budget.used
            icons:pump(budget);assert(budget.used-before<=8)
            if imports>0 then assert(attached) end
            if not icons:has_work() then break end
        end
        assert(not icons:has_work())
        assert(imports==1 and calls==3)
        assert(a.children[1].texture==b.children[1].texture)
        assert(a.children[2].visibility==1)
        assert(#icons.entries.star.targets==0)
        c=icons:image("star")
        assert(imports==1 and c.kind=="Image" and c.texture==texture)
        icons:close();for i=1,20 do icons:pump(budget) end
        assert(imports==1 and icons:image("close")==nil)
        ''')

    def test_failed_import_keeps_visible_fallback_and_unknown_id_is_safe(self):
        lua = self.runtime()
        lua.execute('''
        badImport=true;a=icons:image("close")
        drain_icons()
        assert(imports==1 and calls==0 and a.children[2].visibility~=1)
        assert(icons:image("not_allowlisted")==nil)
        ''')

    def test_transfer_icons_import_on_demand_and_keep_direction_on_failure(self):
        lua = self.runtime()
        lua.execute('''
        icons:preload_all();drain_icons()
        assert(not icons.entries.to_backpack and not icons.entries.to_box)
        local before=imports
        local take=icons:image("to_backpack",22);drain_icons()
        assert(imports==before+1 and take.children[1].texture==icons.entries.to_backpack.texture)
        badImport=true
        local back=icons:image("to_box",22);drain_icons()
        assert(imports==before+2 and back.children[2].text=="<" and back.children[2].visibility~=1)
        ''')

    def test_rebinding_pending_status_ignores_the_old_entry_and_reuses_widgets(self):
        lua = self.runtime()
        lua.execute('''
        local image,target=icons:image("ready")
        local first,second=image.children[1],image.children[2]
        assert(icons:rebind(target,"learned"))
        drain_icons()
        assert(image.children[1]==first and image.children[2]==second)
        assert(first.texture==icons.entries.learned.texture and first.texture~=icons.entries.ready.texture)
        assert(second.visibility==1 and imports==2)
        ''')

    def test_ready_rebinding_performs_no_import_or_widget_construction(self):
        lua = self.runtime()
        lua.execute('''
        icons:preload_all();drain_icons()
        local image,target=icons:image("ready");local before=imports
        icons.make=function() error("Rebinding created a widget") end
        assert(icons:rebind(target,"learned"))
        assert(image.texture==icons.entries.learned.texture and imports==before)
        ''')



if __name__ == "__main__":
    unittest.main()
