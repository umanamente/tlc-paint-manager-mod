"""Prepare a tagged PaintManager release and recover its published artifacts."""

import argparse
from collections import Counter
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid


DIST = Path("dist")
CONFIG = Path("src/PaintManager/Scripts/paintmanager/config.lua")
STABLE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
LUA_VERSION = re.compile(r'(?m)^(\s*version = ")([^"]+)(",\s*)$')
HEADER = re.compile(r"(?m)^## (\d+\.\d+\.\d+)(?: \([^\n]*\))? *$")


def version_for(tag):
    if not re.fullmatch(f"v{STABLE}", tag):
        raise ValueError("Use a stable tag in the form vMAJOR.MINOR.PATCH")
    return tag[1:]


def output(name, value):
    if destination := os.environ.get("GITHUB_OUTPUT"):
        delimiter = uuid.uuid4().hex
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def release_notes(changelog, version):
    headers = list(HEADER.finditer(changelog))
    for index, header in enumerate(headers):
        if header[1] == version:
            end = headers[index + 1].start() if index + 1 < len(headers) else len(changelog)
            notes = changelog[header.end():end].strip()
            if notes:
                return notes
    raise ValueError(f"Missing release notes for {version}")


def nexus_notes(markdown):
    """Render the conventional-changelog preset as one plain-text change per line."""
    entries = []
    breaking = False
    for line in markdown.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^#{1,6} ", line):
            breaking = "BREAKING CHANGE" in line.upper()
            continue
        line = re.sub(r"^[-*+] ", "", line)
        line = re.sub(r"\s*\(\[[0-9a-f]{7,40}\]\(https://github\.com/[^\s)]+/commit/[0-9a-f]+\)\)$", "", line)
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"\[(#\d+)\]\(https://github\.com/[^\s)]+/(?:issues|pull)/\d+\)", r"\1", line)
        if line.strip():
            entries.append(("Breaking change: " if breaking else "") + line.strip())
    if not entries:
        raise ValueError("The release has no changelog entries for Nexus")
    return "\n".join(entries)


def write(path, content):
    path.write_text(content, encoding="utf-8", newline="\n")


def prepare(tag):
    version = version_for(tag)
    plan = json.loads((DIST / "release-plan.json").read_text(encoding="utf-8"))
    if plan["version"] != version or plan["base"] != git("rev-parse", "HEAD"):
        raise ValueError("Commit or version changed after release analysis")
    metadata_path = Path("mod.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    changelog = Path("CHANGELOG.md")
    original = changelog.read_text(encoding="utf-8")
    previous = metadata["version"]
    release_notes(original, previous)
    if not original.startswith("# Changelog\n") or any(m[1] == version for m in HEADER.finditer(original)):
        raise ValueError("Unexpected changelog title or version already present")
    lua = CONFIG.read_text(encoding="utf-8")
    if [m[2] for m in LUA_VERSION.finditer(lua)] != [previous]:
        raise ValueError("Runtime and mod.json must have one matching version")
    # The preset supplies its own version heading and compare link. Keep one standard heading.
    notes = plan["notes"].strip()
    headline, separator, body = notes.partition("\n")
    if re.match(r"^#{1,3} ", headline) and version in headline:
        notes = body.strip() if separator else ""
    if not notes:
        raise ValueError("Release notes are empty")
    metadata["version"] = version
    write(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    write(CONFIG, LUA_VERSION.sub(lambda m: m[1] + version + m[3], lua))
    previous_sections = original.removeprefix("# Changelog\n").lstrip()
    write(changelog, f"# Changelog\n\n## {version}\n\n{notes}\n\n{previous_sections}")


def asset_names(version):
    prefix = f"PaintManager-{version}"
    return (prefix + ".zip", prefix + ".manifest.json")


def verify(directory, version):
    archive, manifest = (directory / name for name in asset_names(version))
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if (record.get("version"), record.get("archive"), record.get("sha256")) != (
        version, archive.name, hashlib.sha256(archive.read_bytes()).hexdigest()
    ):
        raise ValueError(f"{archive.name} does not match the version, filename and SHA-256 in its manifest")


def gh(*args):
    subprocess.run(["gh", *args], check=True)


def github(tag):
    version = version_for(tag)
    if git("rev-parse", "HEAD") != git("rev-parse", f"refs/tags/{tag}^{{commit}}"):
        raise ValueError("The checked-out source must match the release tag")
    git("merge-base", "--is-ancestor", "HEAD", "origin/main")
    if json.loads(Path("mod.json").read_text(encoding="utf-8"))["version"] != version:
        raise ValueError("Tag and mod.json versions differ")
    DIST.mkdir(exist_ok=True)
    notes = release_notes(Path("CHANGELOG.md").read_text(encoding="utf-8"), version)
    notes_file = DIST / "release-notes.md"
    write(notes_file, notes + "\n")
    result = subprocess.run(["gh", "release", "view", tag, "--json", "isDraft,assets"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        if "release not found" not in result.stderr.lower():
            raise RuntimeError(result.stderr)
        gh("release", "create", tag, "--draft", "--verify-tag", "--title", f"PaintManager {version}",
           "--notes-file", str(notes_file))
        release = {"assets": [], "isDraft": True}
    else:
        release = json.loads(result.stdout)
    names = asset_names(version)
    # A failed GitHub upload can leave an empty "starter" placeholder on a draft.
    for asset in release["assets"].copy():
        if (release["isDraft"] and asset["name"] in names
                and asset.get("state", "").lower() == "starter" and asset.get("size") == 0):
            gh("release", "delete-asset", tag, asset["name"], "--yes")
            release["assets"].remove(asset)
    present = {asset["name"] for asset in release["assets"]}
    missing = [name for name in names if name not in present]
    if missing:
        if not all((DIST / name).exists() for name in names):
            subprocess.run([sys.executable, "tools/manage.py", "package"], check=True)
        verify(DIST, version)
        # Reconcile a release interrupted between uploads. Refuse to replace a different asset.
        with TemporaryDirectory() as temp:
            for name in names:
                if name in present:
                    gh("release", "download", tag, "--pattern", name, "--dir", temp)
                    if (Path(temp) / name).read_bytes() != (DIST / name).read_bytes():
                        raise ValueError(f"The existing GitHub asset {name} differs from this build")
        gh("release", "upload", tag, *(str(DIST / name) for name in missing))
    with TemporaryDirectory() as temp:
        gh("release", "download", tag, "--pattern", names[0], "--pattern", names[1], "--dir", temp)
        verify(Path(temp), version)
    if release["isDraft"]:
        gh("release", "edit", tag, "--draft=false")


def nexus_status(records, version):
    if not isinstance(records, list) or not records:
        raise ValueError("Expected a Nexus file with at least one existing version")
    versions = [record["version"] for record in records]
    if version in versions:
        return "already present"
    target = tuple(map(int, version.split(".")))
    stable_versions = (value for value in versions if re.fullmatch(STABLE, value))
    if any(tuple(map(int, value.split("."))) > target for value in stable_versions):
        raise ValueError("A newer Nexus version already exists on this file")
    return "ready"


def nexus_request(path, key, body=None):
    headers = {"apikey": key, "User-Agent": "PaintManager-Release", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = Request("https://api.nexusmods.com/v3" + path, headers=headers, data=data)
    with urlopen(request, timeout=30) as response:
        if response.status != (200 if body is None else 201):
            raise ValueError(f"Unexpected Nexus response status {response.status}")
        return json.load(response)


def find_nexus_changelog(mod_id, version, key):
    path = f"/mods/{quote(mod_id, safe='')}/changelogs"
    page = 1
    total = None
    page_size = 100
    seen = set()
    matching_entries = None
    while True:
        result = nexus_request(f"{path}?page={page}&page_size={page_size}", key)
        meta = result.get("meta") if isinstance(result, dict) else None
        if not isinstance(meta, dict) or not isinstance(result.get("data"), list):
            raise ValueError("Invalid Nexus changelog page")
        size, count = meta.get("page_size"), meta.get("total_count")
        if (type(meta.get("page")) is not int or meta["page"] != page
                or type(size) is not int or not 1 <= size <= page_size
                or type(count) is not int or count < 0):
            raise ValueError("Invalid Nexus changelog pagination")
        if page > 1 and (total != count or page_size != size):
            raise ValueError("Nexus changelog pagination changed during the read")
        total, page_size = count, size
        if len(result["data"]) != min(size, total - len(seen)):
            raise ValueError("Incomplete Nexus changelog page")
        for item in result["data"]:
            if not isinstance(item, dict):
                raise ValueError("Invalid Nexus changelog record")
            name, entries = item.get("version"), item.get("entries")
            if (not isinstance(name, str) or not name.strip() or name in seen
                    or not isinstance(entries, list) or not all(isinstance(text, str) for text in entries)):
                raise ValueError("Invalid or duplicate Nexus changelog record")
            seen.add(name)
            if name == version:
                matching_entries = entries if any(text.strip() for text in entries) else None
        if len(seen) == total:
            return matching_entries
        page += 1


def ensure_nexus_changelog(mod_id, version, key, notes, *, before_new_file=False):
    if find_nexus_changelog(mod_id, version, key):
        print(f"Keeping the existing Nexus changelog for {version}.")
        return True

    expected = Counter(line.strip() for line in notes.splitlines() if line.strip())
    if not expected or len(notes) > 65535:
        raise ValueError("Nexus changelog must contain text and fit within 65535 characters")
    payload = {"version": version, "changelog": notes}
    last_error = None
    try:
        reply = nexus_request(f"/mods/{quote(mod_id, safe='')}/changelogs", key, payload)
        receipt = reply.get("data") if isinstance(reply, dict) else None
        if not isinstance(receipt, dict) or any(receipt.get(field) != value for field, value in payload.items()):
            raise ValueError("Nexus changelog receipt did not match the submitted version and text")
    except (OSError, HTTPException, ValueError) as error:
        if isinstance(error, HTTPError) and error.code == 422 and before_new_file:
            # An explicit validation rejection made no change. Fall back to the vendor's order.
            print(f"Nexus rejected the changelog before upload (HTTP 422). Will send it after publishing {version}.")
            return False
        if isinstance(error, HTTPError) and error.code in (400, 401, 403, 404, 422):
            raise
        # A lost response can follow a successful append. Retry only the reads.
        last_error = error
        print(f"Checking whether Nexus stored the changelog after an uncertain response: {error}")

    for delay in (0, 2, 5, 10):
        if delay:
            time.sleep(delay)
        try:
            entries = find_nexus_changelog(mod_id, version, key) or []
            stored = Counter(line.strip() for entry in entries for line in entry.splitlines() if line.strip())
            if expected <= stored:
                print(f"Verified the Nexus changelog for {version}.")
                return True
        except (OSError, HTTPException, ValueError) as error:
            last_error = error
    raise RuntimeError(f"Could not verify the Nexus changelog for {version} after one append. "
                       "Check the entry on Nexus before retrying the release.") from last_error


def sync_tag_changelog(tag, *, before_new_file=False):
    version = version_for(tag)
    mod_id = os.environ["NEXUSMODS_MOD_ID"].strip()
    key = os.environ["NEXUSMODS_API_KEY"].strip()
    markdown = release_notes(git("show", f"refs/tags/{tag}:CHANGELOG.md"), version)
    return ensure_nexus_changelog(mod_id, version, key, nexus_notes(markdown), before_new_file=before_new_file)


def nexus(tag):
    version = version_for(tag)
    names = asset_names(version)
    DIST.mkdir(exist_ok=True)
    gh("release", "download", tag, "--pattern", names[0], "--pattern", names[1], "--dir", str(DIST))
    verify(DIST, version)
    file_id = os.environ["NEXUSMODS_FILE_ID"].strip()
    key = os.environ["NEXUSMODS_API_KEY"].strip()
    if not file_id or not key:
        raise ValueError("Set NEXUSMODS_FILE_ID and NEXUSMODS_API_KEY for Nexus publication")
    history = nexus_request(f"/mod-files/{quote(file_id, safe='')}/versions", key)
    status = nexus_status(history["data"]["versions"], version)
    print(f"Nexus file version {version}: {status}")
    if os.environ.get("NEXUSMODS_MOD_ID", "").strip():
        if not sync_tag_changelog(tag, before_new_file=status == "ready"):
            output("changelog_after_upload", "true")
    else:
        print("NEXUSMODS_MOD_ID is unset. Skipping the Nexus changelog.")
    output("upload", str(status == "ready").lower())
    output("archive", (DIST / names[0]).as_posix())
    output("version", version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    operations = {"prepare": prepare, "github": github, "nexus": nexus, "nexus-changelog": sync_tag_changelog}
    parser.add_argument("operation", choices=operations)
    parser.add_argument("tag")
    args = parser.parse_args()
    operations[args.operation](args.tag)
