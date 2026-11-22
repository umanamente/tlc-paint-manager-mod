"""Prepare a tagged PaintManager release and recover its published artifacts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
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
    request = Request("https://api.nexusmods.com/v3/mod-files/" + quote(file_id, safe="") + "/versions",
                      headers={"apikey": key, "User-Agent": "PaintManager-Release", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        status = nexus_status(json.load(response)["data"]["versions"], version)
    print(f"Nexus file version {version}: {status}")
    output("upload", str(status == "ready").lower())
    output("archive", (DIST / names[0]).as_posix())
    output("version", version)
    if os.environ.get("NEXUSMODS_MOD_ID"):
        output("notes", release_notes(Path("CHANGELOG.md").read_text(encoding="utf-8"), version))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "github", "nexus"])
    parser.add_argument("tag")
    args = parser.parse_args()
    {"prepare": prepare, "github": github, "nexus": nexus}[args.operation](args.tag)
