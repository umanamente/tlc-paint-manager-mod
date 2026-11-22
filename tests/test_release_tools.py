import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(sandbox.cleanup)
        self.enterContext(contextlib.chdir(sandbox.name))
        Path("dist").mkdir()
        Path("mod.json").write_text('{"version":"0.2.0"}', encoding="utf-8")
        release.CONFIG.parent.mkdir(parents=True)
        release.CONFIG.write_text('return {\n    version = "0.2.0",\n}\n', encoding="utf-8")
        Path("CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\nInitial release.\n", encoding="utf-8")

    def test_prepare_updates_both_versions_and_keeps_the_previous_notes(self):
        plan = {"base": "abc", "version": "0.2.1", "notes": "## [0.2.1](compare)\n\n### Fixes\n\n* Restore focus."}
        Path("dist/release-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        with patch.object(release, "git", return_value="abc"):
            release.prepare("v0.2.1")
        self.assertEqual(json.loads(Path("mod.json").read_text())["version"], "0.2.1")
        self.assertIn('version = "0.2.1"', release.CONFIG.read_text())
        changes = Path("CHANGELOG.md").read_text()
        self.assertEqual(release.release_notes(changes, "0.2.1"), "### Fixes\n\n* Restore focus.")
        self.assertEqual(release.release_notes(changes, "0.2.0"), "Initial release.")

    def test_preparation_rejects_a_plan_for_another_commit_before_writing(self):
        plan = {"base": "old", "version": "0.3.0", "notes": "New feature."}
        Path("dist/release-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        with patch.object(release, "git", return_value="new"), self.assertRaisesRegex(ValueError, "Commit"):
            release.prepare("v0.3.0")
        self.assertIn('"0.2.0"', release.CONFIG.read_text())

    def test_accepts_only_stable_canonical_tags(self):
        for bad in ["main", "v01.2.0", "v0.2.0-dev.1", "v1.2.3/../x", "v0.2.0\n"]:
            with self.subTest(tag=bad), self.assertRaises(ValueError):
                release.version_for(bad)

    def test_nexus_duplicate_and_downgrade_checks(self):
        history = [{"version": "0.2.0"}, {"version": "0.2.10"}]
        self.assertEqual(release.nexus_status(history, "0.2.10"), "already present")
        with self.assertRaisesRegex(ValueError, "newer"):
            release.nexus_status(history, "0.2.9")
        self.assertEqual(release.nexus_status(history, "0.3.0"), "ready")
        with self.assertRaisesRegex(ValueError, "existing version"):
            release.nexus_status([], "0.3.0")

    def make_assets(self, directory):
        names = release.asset_names("0.2.0")
        (directory / names[0]).write_bytes(b"verified payload")
        record = {"version": "0.2.0", "archive": names[0], "sha256": hashlib.sha256(b"verified payload").hexdigest()}
        (directory / names[1]).write_text(json.dumps(record), encoding="utf-8")
        return names

    def test_mismatched_download_is_rejected(self):
        names = self.make_assets(Path("dist"))
        Path("dist", names[0]).write_bytes(b"unexpected bytes")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            release.verify(Path("dist"), "0.2.0")

    def fake_github(self, directory, *, draft=False):
        """Model gh's draft/assets operations without obtaining credentials or using a remote."""
        calls = []
        state = {"isDraft": draft, "assets": [{"name": path.name} for path in directory.iterdir()]}
        view = subprocess.CompletedProcess(["gh", "release", "view"], 0, stdout=json.dumps(state))
        self.enterContext(patch.object(release.subprocess, "run", return_value=view))
        self.enterContext(patch.object(release, "git", return_value="tagged-commit"))

        def gh(*args):
            calls.append(args)
            if args[1] == "download":
                destination = Path(args[args.index("--dir") + 1])
                for index, arg in enumerate(args):
                    if arg == "--pattern":
                        shutil.copy2(directory / args[index + 1], destination)
            elif args[1] == "upload":
                for name in args[3:]:
                    shutil.copy2(name, directory)
        self.enterContext(patch.object(release, "gh", side_effect=gh))
        return calls

    def test_completed_github_release_only_downloads_and_verifies_existing_assets(self):
        remote = Path("published")
        remote.mkdir()
        self.make_assets(remote)
        calls = self.fake_github(remote)
        release.github("v0.2.0")
        self.assertEqual([args[1] for args in calls], ["download"])

    def test_interrupted_github_upload_completes_only_the_missing_asset(self):
        remote = Path("published")
        remote.mkdir()
        archive, manifest = self.make_assets(Path("dist"))
        shutil.copy2(Path("dist", archive), remote)
        calls = self.fake_github(remote, draft=True)
        release.github("v0.2.0")
        self.assertEqual([args[1] for args in calls], ["download", "upload", "download", "edit"])
        self.assertEqual(calls[1][3:], (str(Path("dist", manifest)),))
        self.assertTrue((remote / manifest).is_file())

    def test_recovery_will_not_replace_a_conflicting_asset(self):
        remote = Path("published")
        remote.mkdir()
        archive, _ = self.make_assets(Path("dist"))
        (remote / archive).write_bytes(b"conflict")
        calls = self.fake_github(remote, draft=True)
        with self.assertRaisesRegex(ValueError, "differs"):
            release.github("v0.2.0")
        self.assertEqual([args[1] for args in calls], ["download"])
