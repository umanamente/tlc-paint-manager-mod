"""Filesystem and ownership contracts; no real game process or install is touched."""

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("paintmanager_manage", Path(__file__).resolve().parents[1] / "tools" / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


def write_player_docs(root):
    docs = root / "docs/gamers"
    docs.mkdir(parents=True)
    (root / "LICENSE").write_bytes(b"Fixture license")
    (root / "THIRD_PARTY_NOTICES.md").write_bytes(b"Fixture notices")
    links = []
    for name in ("installation.md", "guide.md", "troubleshooting.md"):
        (docs / name).write_bytes(f"Player guide {name}".encode())
        links.append(f"[Read {name}]({name})")
    (docs / "README.md").write_bytes("\n".join(links).encode())


class ToolingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.win64 = self.game / "Voyage" / "Binaries" / "Win64"
        self.win64.mkdir(parents=True)
        for path in (self.game / "Voyage.exe", self.win64 / "VoyageSteam-Win64-Shipping.exe", self.win64 / "ue4ss" / "UE4SS.dll", self.win64 / "dwmapi.dll"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        self.metadata = {"id": "PaintManager", "version": "0.1.0-dev.1"}
        self.files = {"Scripts/main.lua": b"print('fixture')", "enabled.txt": b""}
        self.target = self.win64 / "ue4ss" / "Mods" / "PaintManager"

    def install_fixture(self, files=None):
        files = files or self.files
        self.target.mkdir(parents=True)
        for name, data in files.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        marker = {"owner": "PaintManager", "schema_version": 1, "version": "0.0.1", "files": {name: manage.sha256(data) for name, data in files.items()}}
        (self.target / manage.INSTALL_MANIFEST).write_text(json.dumps(marker), encoding="utf-8")

    def test_traversal_rejected_before_archive_or_filesystem_write(self):
        for name in ("../outside", "/absolute", "C:/absolute", "Scripts/../../outside", "Scripts\\..\\outside", "a//b", "./a", "a/../b"):
            with self.subTest(name=name), self.assertRaises(manage.ToolError):
                manage.archive_bytes({name: b"bad"})
        self.assertEqual(manage.relative_path("Scripts/paintmanager/config.lua"), Path("Scripts/paintmanager/config.lua"))

    def test_zip_has_only_mod_owned_tree_and_is_deterministic(self):
        archive = manage.archive_bytes(self.files)
        self.assertEqual(archive, manage.archive_bytes(dict(reversed(list(self.files.items())))))
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            self.assertEqual(sorted(package.namelist()), sorted(manage.ZIP_PREFIX + name for name in self.files))

    def source_fixture(self):
        metadata = {**self.metadata, "referenceGameBuild": "25982825"}
        (self.root / "mod.json").write_text(json.dumps(metadata), encoding="utf-8")
        scripts = self.root / "src" / "PaintManager" / "Scripts"
        (scripts / "paintmanager").mkdir(parents=True)
        (scripts / "main.lua").write_text("return {}", encoding="utf-8")
        config = scripts / "paintmanager" / "config.lua"
        config.write_text('return {\n version = "0.1.0-dev.1",\n reference_build = "25982825",\n}\n', encoding="utf-8")
        return config

    def test_mismatched_runtime_metadata_blocks_check(self):
        config = self.source_fixture()
        config.write_text('return {\n version = "0.1.0-dev.2",\n reference_build = "25982825",\n}\n', encoding="utf-8")
        with self.assertRaisesRegex(manage.ToolError, "version.*matching mod.json"):
            manage.checked_sources(self.root)
        config.write_text('return {\n version = "0.1.0-dev.1",\n reference_build = "other-build",\n}\n', encoding="utf-8")
        with self.assertRaisesRegex(manage.ToolError, "reference_build.*matching mod.json"):
            manage.checked_sources(self.root)

    def test_package_includes_only_player_docs_and_licenses(self):
        self.source_fixture()
        write_player_docs(self.root)
        (self.root / "README.md").write_text("Source repository", encoding="utf-8")
        docs = self.root / "docs"
        (docs / "review.md").write_bytes(b"Excluded")
        (docs / "maintainers").mkdir()
        (docs / "maintainers/architecture.md").write_bytes(b"Excluded")
        (docs / "gamers/draft.md").write_bytes(b"Excluded")
        _, files = manage.payload(self.root)
        self.assertEqual(set(name for name in files if name.startswith("docs/")), {
            "docs/gamers/installation.md", "docs/gamers/guide.md", "docs/gamers/troubleshooting.md",
        })
        for name in ("installation.md", "guide.md", "troubleshooting.md"):
            self.assertIn(f"](docs/gamers/{name})".encode(), files["README.md"])
        self.assertEqual(files["LICENSE"], b"Fixture license")
        self.assertEqual(files["THIRD_PARTY_NOTICES.md"], b"Fixture notices")
        (self.root / "THIRD_PARTY_NOTICES.md").unlink()
        with self.assertRaisesRegex(manage.ToolError, "Missing release file"):
            manage.payload(self.root)

    def test_unmanaged_existing_directory_is_never_adopted(self):
        self.target.mkdir(parents=True)
        with self.assertRaisesRegex(manage.ToolError, "unmanaged"):
            manage.deployment_plan(self.game, self.metadata, self.files)

    def test_modified_owned_file_blocks_update(self):
        self.install_fixture()
        (self.target / "Scripts" / "main.lua").write_bytes(b"user edit")
        with self.assertRaisesRegex(manage.ToolError, "changed or removed"):
            manage.deployment_plan(self.game, self.metadata, self.files)

    def test_manifest_cannot_claim_paths_outside_mod(self):
        self.target.mkdir(parents=True)
        marker = {"owner": "PaintManager", "schema_version": 1, "files": {"../HumanBuilder/main.lua": "0" * 64}}
        (self.target / manage.INSTALL_MANIFEST).write_text(json.dumps(marker), encoding="utf-8")
        with self.assertRaisesRegex(manage.ToolError, "Unsafe relative"):
            manage.read_install_manifest(self.target)

    def test_invalid_manifest_shape_is_refused(self):
        self.target.mkdir(parents=True)
        (self.target / manage.INSTALL_MANIFEST).write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(manage.ToolError, "schema or owner"):
            manage.read_install_manifest(self.target)

    def test_running_game_blocks_all_install_writes(self):
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), patch.object(manage, "running_game_processes", return_value=[{"ProcessName": "Voyage", "Id": 123}]):
            with self.assertRaisesRegex(manage.ToolError, "running"):
                manage.deploy(self.game, apply=True, root=self.root)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "local").exists())

    def test_failed_process_inspection_blocks_install(self):
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), patch.object(manage, "running_game_processes", side_effect=manage.ToolError("inspection unavailable")):
            with self.assertRaisesRegex(manage.ToolError, "inspection unavailable"):
                manage.deploy(self.game, apply=True, root=self.root)
        self.assertFalse(self.target.exists())

    def test_dry_run_makes_no_game_changes(self):
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), patch.object(manage, "running_game_processes", return_value=[]):
            result = manage.deploy(self.game, root=self.root)
        self.assertFalse(result["apply"])
        self.assertFalse(self.target.exists())

    def test_update_preserves_unowned_files_and_shared_loader(self):
        self.install_fixture()
        settings = self.target / "personal-settings.json"
        settings.write_bytes(b"keep me")
        updated = {**self.files, "Scripts/main.lua": b"print('updated')"}
        with patch.object(manage, "payload", return_value=(self.metadata, updated)), patch.object(manage, "running_game_processes", return_value=[]):
            result = manage.deploy(self.game, apply=True, root=self.root)
        self.assertEqual(settings.read_bytes(), b"keep me")
        self.assertEqual((self.target / "Scripts" / "main.lua").read_bytes(), updated["Scripts/main.lua"])
        self.assertEqual((self.win64 / "ue4ss" / "UE4SS.dll").read_bytes(), b"fixture")
        backup = Path(result["backup"])
        index = json.loads((backup / "index.json").read_text(encoding="utf-8"))
        self.assertEqual((backup / index["files"]["Scripts/main.lua"]).read_bytes(), self.files["Scripts/main.lua"])

    def test_obsolete_owned_file_is_preserved_and_blocks_update(self):
        self.install_fixture({**self.files, "Scripts/old.lua": b"old"})
        with self.assertRaisesRegex(manage.ToolError, "Obsolete"):
            manage.deployment_plan(self.game, self.metadata, self.files)
        self.assertTrue((self.target / "Scripts" / "old.lua").is_file())

    def test_mid_install_failure_restores_original_bytes(self):
        self.install_fixture()
        original = manage.atomic_write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if not failed and path == self.target / "enabled.txt":
                failed = True
                raise OSError("fixture write failure")
            return original(path, data)

        updated = {**self.files, "Scripts/main.lua": b"updated"}
        with patch.object(manage, "payload", return_value=(self.metadata, updated)), patch.object(manage, "running_game_processes", return_value=[]), patch.object(manage, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(manage.ToolError, "fixture write failure"):
                manage.deploy(self.game, apply=True, root=self.root)
        self.assertEqual((self.target / "Scripts" / "main.lua").read_bytes(), self.files["Scripts/main.lua"])
        self.assertIsNotNone(manage.read_install_manifest(self.target))


if __name__ == "__main__":
    unittest.main()
