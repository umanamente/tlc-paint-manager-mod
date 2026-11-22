import contextlib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from tools import release


def nexus_changelog_page(entries, *, page=1, total=None):
    total_count = len(entries) if total is None else total
    return {"data": entries, "meta": {"page": page, "page_size": 100, "total_count": total_count}}


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

    def test_nexus_formats_one_plain_text_change_per_line(self):
        notes = (
            "### ⚠ BREAKING CHANGES\n\n* **presets:** import old schemes first\n\n"
            "### Bug Fixes\n\n* **ui:** show the paint can "
            "([abcdef1](https://github.com/example/mod/commit/abcdef1234567))\n"
            "* Track [#42](https://github.com/example/mod/issues/42)\n"
        )
        self.assertEqual(release.nexus_notes(notes), "Breaking change: presets: import old schemes first\n"
                         "ui: show the paint can\nTrack #42")
        with self.assertRaisesRegex(ValueError, "no changelog entries"):
            release.nexus_notes("### Bug Fixes\n")

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

    def make_assets(self, directory, version="0.2.0"):
        names = release.asset_names(version)
        (directory / names[0]).write_bytes(b"verified payload")
        record = {"version": version, "archive": names[0], "sha256": hashlib.sha256(b"verified payload").hexdigest()}
        (directory / names[1]).write_text(json.dumps(record), encoding="utf-8")
        return names

    def test_mismatched_download_is_rejected(self):
        names = self.make_assets(Path("dist"))
        Path("dist", names[0]).write_bytes(b"unexpected bytes")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            release.verify(Path("dist"), "0.2.0")

    def test_failed_zip_upload_can_retry_without_appending_the_changelog_again(self):
        self.make_assets(Path("dist"), version="0.2.1")
        tagged_changelog = "# Changelog\n\n## 0.2.1\n\nRestore focus.\n"
        git = self.enterContext(patch.object(release, "git", return_value=tagged_changelog))
        self.enterContext(patch.dict(release.os.environ, {
            "NEXUSMODS_MOD_ID": "123", "NEXUSMODS_FILE_ID": "456", "NEXUSMODS_API_KEY": "test-key",
        }))
        self.enterContext(patch.object(release, "gh"))
        self.enterContext(patch.object(release.time, "sleep"))
        history = {"data": {"versions": [{"version": "0.2.0"}]}}

        written = []
        requests = []

        def api(path, key, body=None):
            self.assertEqual(key, "test-key")
            if path == "/mod-files/456/versions" and body is None:
                return history
            self.assertEqual(urlsplit(path).path, "/mods/123/changelogs")
            if body is not None:
                requests.append("POST")
                self.assertEqual(body, {"version": "0.2.1", "changelog": "Restore focus."})
                written.append({"version": body["version"], "entries": body["changelog"].splitlines()})
                return {"data": body}
            requests.append("GET")
            return nexus_changelog_page(written)

        self.enterContext(patch.object(release, "nexus_request", side_effect=api))
        publish = self.enterContext(patch.object(release, "output"))
        release.nexus("v0.2.1")
        git.assert_called_with("show", "refs/tags/v0.2.1:CHANGELOG.md")
        self.assertEqual(requests, ["GET", "POST", "GET"])
        publish.assert_any_call("upload", "true")

        # The action failed to upload its ZIP. Only the changelog is on Nexus.
        publish.reset_mock()
        release.nexus("v0.2.1")
        self.assertEqual(requests, ["GET", "POST", "GET", "GET"])
        publish.assert_any_call("upload", "true")

        history["data"]["versions"].append({"version": "0.2.1"})
        publish.reset_mock()
        release.nexus("v0.2.1")
        self.assertEqual(requests.count("POST"), 1)
        publish.assert_any_call("upload", "false")

    def test_authored_nexus_entries_on_a_later_page_are_preserved(self):
        rows = [{"version": f"1.0.{i}", "entries": ["Another release."]} for i in range(100)]
        pages = [
            nexus_changelog_page(rows, total=101),
            nexus_changelog_page([{"version": "0.2.1", "entries": ["Edited by the author."]}], page=2, total=101),
        ]
        with patch.object(release, "nexus_request", side_effect=pages) as request:
            release.ensure_nexus_changelog("123", "0.2.1", "key", "Generated note.")
        self.assertEqual(request.call_count, 2)
        page_numbers = [parse_qs(urlsplit(call.args[0]).query).get("page", ["1"]) for call in request.call_args_list]
        self.assertEqual(page_numbers, [["1"], ["2"]])

    def test_explicit_validation_rejection_defers_only_a_pre_upload_append(self):
        posted = {"version": "0.2.2", "changelog": "Restore focus."}
        rejection = HTTPError("https://api.nexusmods.com", 422, "Unprocessable Content", {}, None)
        empty = nexus_changelog_page([])
        saved = nexus_changelog_page([{"version": "0.2.2", "entries": ["Restore focus."]}])
        with patch.object(release, "nexus_request", side_effect=[empty, rejection, empty, {"data": posted}, saved]):
            self.assertFalse(release.ensure_nexus_changelog("123", "0.2.2", "key", "Restore focus.", before_new_file=True))
            self.assertTrue(release.ensure_nexus_changelog("123", "0.2.2", "key", "Restore focus."))
        with patch.object(release, "nexus_request", side_effect=[empty, rejection]), self.assertRaises(HTTPError):
            release.ensure_nexus_changelog("123", "0.2.2", "key", "Restore focus.")

    def test_unclear_changelog_listing_cannot_authorize_an_append(self):
        for payload in (
            {"data": []},
            nexus_changelog_page([], total=10),
            nexus_changelog_page([{"version": "0.2.1", "entries": "Malformed string"}]),
        ):
            with self.subTest(payload=payload), patch.object(release, "nexus_request", return_value=payload) as request:
                with self.assertRaises((ValueError, RuntimeError)):
                    release.ensure_nexus_changelog("123", "0.2.1", "key", "Restore focus.")
                self.assertEqual(request.call_count, 1)

    def test_ambiguous_changelog_write_is_resolved_by_reading_without_a_second_post(self):
        for visible in (True, False):
            with self.subTest(visible=visible):
                methods = []

                def changelogs(path, key, body=None):
                    if body is not None:
                        methods.append("POST")
                        raise URLError("Response lost after sending")
                    methods.append("GET")
                    entries = [{"version": "0.2.1", "entries": ["Restore focus."]}] if visible and "POST" in methods else []
                    return nexus_changelog_page(entries)

                with patch.object(release, "nexus_request", side_effect=changelogs), patch.object(release.time, "sleep"):
                    if visible:
                        release.ensure_nexus_changelog("123", "0.2.1", "key", "Restore focus.")
                    else:
                        with self.assertRaises((ValueError, RuntimeError, URLError)):
                            release.ensure_nexus_changelog("123", "0.2.1", "key", "Restore focus.")
                self.assertEqual(methods[:3], ["GET", "POST", "GET"])
                self.assertEqual(methods.count("POST"), 1)

    def test_nexus_receipt_alone_cannot_confirm_all_submitted_changes(self):
        submitted = "Restore focus.\nKeep the selected paint."
        post_bodies = []
        reads = []

        def changelogs(path, key, body=None):
            if body is not None:
                post_bodies.append(body)
                return {"data": body}
            reads.append(path)
            if not post_bodies:
                return nexus_changelog_page([])
            return nexus_changelog_page([{"version": "0.2.1", "entries": ["Restore focus."]}])

        with patch.object(release, "nexus_request", side_effect=changelogs), patch.object(release.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "verify"):
                release.ensure_nexus_changelog("123", "0.2.1", "key", submitted)
        self.assertEqual(post_bodies, [{"version": "0.2.1", "changelog": submitted}])
        self.assertGreaterEqual(len(reads), 2)

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
