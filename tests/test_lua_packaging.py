"""Lua-only payload admission and owned legacy-companion migration."""
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_tooling import write_player_docs


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("paintmanager_lua_manage", ROOT / "tools/manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


class LuaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.metadata = {"id": "PaintManager", "version": "0.2.0-dev.38",
                         "referenceGameBuild": "25982825",
                         "referenceLoaderCommit": "527a483b63b4dd0104fe1ca1a3934a06b87fcfb2"}
        (self.root / "mod.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        write_player_docs(self.root)
        self.scripts = self.root / "src/PaintManager/Scripts"
        (self.scripts / "paintmanager").mkdir(parents=True)
        (self.scripts / "main.lua").write_text("return {}", encoding="utf-8")
        (self.scripts / "paintmanager/config.lua").write_text(
            'return {\n version = "0.2.0-dev.38",\n reference_build = "25982825",\n}\n', encoding="utf-8")

    def test_package_has_one_lua_backend_and_no_native_artifact_entry_point(self):
        report = manage.package(self.root)
        self.assertEqual(report["report_backend"], "lua_cooperative")
        self.assertFalse(any("/dlls/" in name for name in report["files"]))
        for operation in (manage.payload, manage.package, manage.deploy):
            self.assertNotIn("native_artifact", inspect.signature(operation).parameters)
        self.assertFalse(hasattr(manage, "native_payload"))
        self.assertFalse(hasattr(manage, "native_source_hash"))

    def test_source_check_rejects_native_runtime_files_in_any_location(self):
        for relative in ("dlls/main.dll", "Scripts/native.DLL", "Assets/helper.so", "helper.dylib", "runner.exe"):
            with self.subTest(path=relative):
                path = self.root / "src/PaintManager" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"not a runtime dependency")
                with self.assertRaisesRegex(manage.ToolError, "native artifacts"):
                    manage.checked_sources(self.root)
                path.unlink()
                # An empty legacy dlls directory also must not ship.
                if relative.startswith("dlls/"):
                    path.parent.rmdir()

    def test_source_check_rejects_native_module_loading(self):
        path = self.scripts / "main.lua"
        for source in ('package.loadlib("helper.dll", "open")',
                       'local ffi = require("ffi")',
                       'ffi.load("helper")', 'package.cpath = "./?.dll"'):
            with self.subTest(source=source):
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(manage.ToolError, "native modules"):
                    manage.checked_sources(self.root)

    def test_archive_refuses_native_files_even_if_a_caller_bypasses_payload(self):
        for name in ("dlls/main.dll", "Scripts/helper.DLL", "helper.so", "dlls/provenance.json"):
            with self.subTest(name=name), self.assertRaisesRegex(manage.ToolError, "native artifacts"):
                manage.archive_bytes({name: b"fixture"})

    def test_source_check_rejects_optional_old_companion_routing(self):
        path = self.scripts / "main.lua"
        for source in ('PaintManagerWorker.capabilities()',
                       'local bridge=rawget(_G,"PaintManagerWorker")',
                       'PMWorkerNativeBegin(1)'):
            with self.subTest(source=source):
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(manage.ToolError, "retired native companion"):
                    manage.checked_sources(self.root)


class LegacyCompanionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "game"
        self.win64 = self.game / "Voyage/Binaries/Win64"
        self.target = self.win64 / "ue4ss/Mods/PaintManager"
        for relative in ("Voyage.exe", "Voyage/Binaries/Win64/VoyageSteam-Win64-Shipping.exe",
                         "Voyage/Binaries/Win64/ue4ss/UE4SS.dll", "Voyage/Binaries/Win64/dwmapi.dll"):
            path = self.game / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"shared fixture")
        self.metadata = {"id": "PaintManager", "version": "0.2.0-dev.38",
                         "referenceLoaderCommit": "527a483b63b4dd0104fe1ca1a3934a06b87fcfb2"}
        self.files = {"Scripts/main.lua": b"return {}", "enabled.txt": b""}
        self.old_dll = b"MZ legacy owned fixture"
        self.provenance = {"schemaVersion": 1, "loaderCommit": self.metadata["referenceLoaderCommit"],
                           "protocol": 1, "reportWire": 1, "arch": "x86_64", "abi": "MSVC",
                           "configuration": "Game__Shipping__Win64", "sourceSha256": "a" * 64,
                           "dllSha256": manage.sha256(self.old_dll)}
        self.old_files = {"Scripts/main.lua": b"old Lua", "enabled.txt": b"",
                          "dlls/main.dll": self.old_dll,
                          "dlls/worker-provenance.json": manage.json_bytes(self.provenance),
                          "Scripts/paintmanager/background_wire.lua": b"return {}"}
        self.install(self.old_files)

    def install(self, files):
        self.target.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        marker = {"schema_version": 1, "owner": "PaintManager", "version": "0.2.0-dev.37",
                  "files": {name: manage.sha256(data) for name, data in files.items()}}
        (self.target / manage.INSTALL_MANIFEST).write_bytes(manage.json_bytes(marker))

    def run_deploy(self, apply):
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), \
                patch.object(manage, "running_game_processes", return_value=[]):
            return manage.deploy(self.game, apply=apply, root=self.root)

    def install_lua_with_empty_dlls(self):
        """Reproduce dev.38's already-migrated files and stale empty folder."""
        for name in set(self.old_files) - set(self.files):
            (self.target / name).unlink()
        self.install(self.files)
        marker = json.loads((self.target / manage.INSTALL_MANIFEST).read_text(encoding="utf-8"))
        marker["version"] = self.metadata["version"]
        (self.target / manage.INSTALL_MANIFEST).write_bytes(manage.json_bytes(marker))
        self.assertTrue((self.target / "dlls").is_dir())
        self.assertEqual(list((self.target / "dlls").iterdir()), [])

    def test_dry_run_lists_exact_owned_retirements_without_removing(self):
        result = self.run_deploy(False)
        self.assertEqual(set(result["removed_files"]), set(self.old_files) - set(self.files))
        self.assertEqual(result["removed_directories"], ["dlls"])
        self.assertEqual((self.target / "dlls/main.dll").read_bytes(), self.old_dll)
        self.assertFalse((self.root / "local").exists())

    def test_closed_game_migration_backs_up_and_removes_only_owned_legacy_files(self):
        personal = self.target / "PaintManager.paint-cache.json"
        personal.write_bytes(b"unowned derived file remains")
        result = self.run_deploy(True)
        self.assertEqual(result["report_backend"], "lua_cooperative")
        self.assertFalse((self.target / "dlls/main.dll").exists())
        self.assertFalse((self.target / "dlls/worker-provenance.json").exists())
        self.assertFalse((self.target / "dlls").exists())
        self.assertEqual(result["removed_directories"], ["dlls"])
        self.assertFalse((self.target / "Scripts/paintmanager/background_wire.lua").exists())
        self.assertEqual(personal.read_bytes(), b"unowned derived file remains")
        marker = manage.read_install_manifest(self.target)
        self.assertEqual(marker["files"], {name: manage.sha256(data) for name, data in self.files.items()})
        backup = Path(result["backup"])
        index = json.loads((backup / "index.json").read_text(encoding="utf-8"))
        self.assertEqual((backup / index["files"]["dlls/main.dll"]).read_bytes(), self.old_dll)
        self.assertEqual((self.win64 / "ue4ss/UE4SS.dll").read_bytes(), b"shared fixture")

    def test_already_lua_install_removes_empty_dlls_and_repeat_is_idempotent(self):
        self.install_lua_with_empty_dlls()
        planned = self.run_deploy(False)
        self.assertEqual(planned["removed_files"], [])
        self.assertEqual(planned["removed_directories"], ["dlls"])
        self.assertTrue((self.target / "dlls").is_dir())
        self.assertFalse((self.root / "local").exists())
        result = self.run_deploy(True)
        self.assertEqual(result["removed_directories"], ["dlls"])
        self.assertFalse((self.target / "dlls").exists())
        index = json.loads((Path(result["backup"]) / "index.json").read_text(encoding="utf-8"))
        self.assertIn("dlls", index["removed_directories"])
        repeated = self.run_deploy(True)
        self.assertEqual(repeated["removed_directories"], [])
        self.assertFalse((self.target / "dlls").exists())

    def test_retires_the_owned_umg_escape_reader_from_an_existing_lua_install(self):
        self.run_deploy(True)
        retired = "Scripts/paintmanager/ui_input.lua"
        reader = b"return {} -- old reader"
        self.install({**self.files, retired: reader})
        result = self.run_deploy(True)
        self.assertEqual(result["removed_files"], [retired])
        self.assertFalse((self.target / retired).exists())
        backup = Path(result["backup"])
        index = json.loads((backup / "index.json").read_text(encoding="utf-8"))
        self.assertEqual((backup / index["files"][retired]).read_bytes(), reader)

    def test_nonempty_dlls_is_preserved_and_refused_before_writes(self):
        for migrated in (False, True):
            for name, directory in (("notes.txt", False), (".keep", False), ("nested", True)):
                with self.subTest(migrated=migrated, name=name):
                    self.setUp()
                    if migrated:
                        self.install_lua_with_empty_dlls()
                    foreign = self.target / "dlls" / name
                    if directory:
                        foreign.mkdir()
                    else:
                        foreign.write_bytes(b"unknown contents must survive")
                    before = (self.target / "Scripts/main.lua").read_bytes()
                    with self.assertRaisesRegex(manage.ToolError, "dlls"):
                        self.run_deploy(True)
                    self.assertTrue(foreign.exists())
                    if not directory:
                        self.assertEqual(foreign.read_bytes(), b"unknown contents must survive")
                    self.assertEqual((self.target / "Scripts/main.lua").read_bytes(), before)
                    self.assertFalse((self.root / "local").exists())

    def test_dlls_file_is_not_removed_or_treated_as_a_directory(self):
        self.install_lua_with_empty_dlls()
        directory = self.target / "dlls"
        directory.rmdir()
        directory.write_bytes(b"unknown file must survive")
        with self.assertRaisesRegex(manage.ToolError, "dlls"):
            self.run_deploy(True)
        self.assertEqual(directory.read_bytes(), b"unknown file must survive")
        self.assertFalse((self.root / "local").exists())

    def test_dlls_removal_failure_rolls_back_files_and_manifest(self):
        original = Path.rmdir

        def fail_owned_directory(path):
            if path == self.target / "dlls":
                raise OSError("fixture dlls removal failure")
            return original(path)

        with patch.object(Path, "rmdir", fail_owned_directory):
            with self.assertRaisesRegex(manage.ToolError, "dlls"):
                self.run_deploy(True)
        for name, data in self.old_files.items():
            self.assertEqual((self.target / name).read_bytes(), data)
        self.assertEqual(manage.read_install_manifest(self.target)["version"], "0.2.0-dev.37")

    def test_failure_after_directory_removal_restores_legacy_files(self):
        original = manage.installed_native_files
        failed = False

        def fail_after_removal(target):
            nonlocal failed
            if not failed and target == self.target and not (target / "dlls").exists():
                failed = True
                raise OSError("fixture failure after dlls removal")
            return original(target)

        with patch.object(manage, "installed_native_files", side_effect=fail_after_removal):
            with self.assertRaisesRegex(manage.ToolError, "after dlls removal"):
                self.run_deploy(True)
        self.assertTrue(failed)
        for name, data in self.old_files.items():
            self.assertEqual((self.target / name).read_bytes(), data)
        self.assertEqual(manage.read_install_manifest(self.target)["version"], "0.2.0-dev.37")

    def test_failure_after_empty_directory_removal_restores_its_empty_state(self):
        self.install_lua_with_empty_dlls()
        original = manage.installed_native_files
        failed = False

        def fail_after_removal(target):
            nonlocal failed
            if not failed and target == self.target and not (target / "dlls").exists():
                failed = True
                raise OSError("fixture failure after empty dlls removal")
            return original(target)

        with patch.object(manage, "installed_native_files", side_effect=fail_after_removal):
            with self.assertRaisesRegex(manage.ToolError, "after empty dlls removal"):
                self.run_deploy(True)
        self.assertTrue(failed)
        self.assertTrue((self.target / "dlls").is_dir())
        self.assertEqual(list((self.target / "dlls").iterdir()), [])
        self.assertIsNotNone(manage.read_install_manifest(self.target))

    def test_running_game_blocks_empty_directory_cleanup(self):
        self.install_lua_with_empty_dlls()
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), \
                patch.object(manage, "running_game_processes", return_value=[{"ProcessName": "Voyage", "Id": 1}]):
            with self.assertRaisesRegex(manage.ToolError, "running"):
                manage.deploy(self.game, apply=True, root=self.root)
        self.assertTrue((self.target / "dlls").is_dir())
        self.assertFalse((self.root / "local").exists())

    def test_modified_owned_dll_is_preserved_and_refused(self):
        (self.target / "dlls/main.dll").write_bytes(b"modified")
        with self.assertRaisesRegex(manage.ToolError, "changed or removed"):
            self.run_deploy(True)
        self.assertEqual((self.target / "dlls/main.dll").read_bytes(), b"modified")
        self.assertFalse((self.root / "local").exists())

    def test_unknown_native_file_is_preserved_and_blocks_migration(self):
        foreign = self.target / "Scripts/third-party.dll"
        foreign.write_bytes(b"unknown")
        with self.assertRaisesRegex(manage.ToolError, "unexpected native"):
            self.run_deploy(True)
        self.assertEqual(foreign.read_bytes(), b"unknown")
        self.assertTrue((self.target / "dlls/main.dll").exists())

    def test_wrong_provenance_is_refused_even_if_manifest_hashes_match(self):
        files = dict(self.old_files)
        files["dlls/worker-provenance.json"] = manage.json_bytes({**self.provenance, "dllSha256": "0" * 64})
        self.install(files)
        with self.assertRaisesRegex(manage.ToolError, "provenance does not match"):
            self.run_deploy(True)
        self.assertEqual((self.target / "dlls/main.dll").read_bytes(), self.old_dll)

    def test_unpaired_owned_dll_is_not_deleted(self):
        files = dict(self.old_files)
        files.pop("dlls/worker-provenance.json")
        (self.target / "dlls/worker-provenance.json").unlink()
        self.install(files)
        with self.assertRaisesRegex(manage.ToolError, "both owned"):
            self.run_deploy(True)
        self.assertTrue((self.target / "dlls/main.dll").exists())

    def test_failure_after_removal_rolls_back_dll_provenance_and_manifest(self):
        original = manage.atomic_write
        failed = False

        def fail_once(path, data):
            nonlocal failed
            if not failed and path == self.target / manage.INSTALL_MANIFEST:
                failed = True
                raise OSError("fixture manifest write failure after removal")
            return original(path, data)

        with patch.object(manage, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(manage.ToolError, "fixture manifest write failure"):
                self.run_deploy(True)
        self.assertEqual((self.target / "dlls/main.dll").read_bytes(), self.old_dll)
        self.assertEqual((self.target / "dlls/worker-provenance.json").read_bytes(), self.old_files["dlls/worker-provenance.json"])
        self.assertEqual(manage.read_install_manifest(self.target)["version"], "0.2.0-dev.37")

    def test_running_game_blocks_legacy_removal_and_all_writes(self):
        with patch.object(manage, "payload", return_value=(self.metadata, self.files)), \
                patch.object(manage, "running_game_processes", return_value=[{"ProcessName": "Voyage", "Id": 1}]):
            with self.assertRaisesRegex(manage.ToolError, "running"):
                manage.deploy(self.game, apply=True, root=self.root)
        self.assertEqual((self.target / "dlls/main.dll").read_bytes(), self.old_dll)
        self.assertFalse((self.root / "local").exists())


if __name__ == "__main__":
    unittest.main()
