"""Build and deploy PaintManager's own Lua payload; never manage the shared loader."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
MOD_ID = "PaintManager"
INSTALL_MANIFEST = ".paintmanager-install.json"
ZIP_PREFIX = f"ue4ss/Mods/{MOD_ID}/"
PLAYER_GUIDES = ("installation.md", "guide.md", "troubleshooting.md")
GAME_PROCESSES = ("Voyage", "VoyageSteam-Win64-Shipping")
NATIVE_SUFFIXES = frozenset((".dll", ".so", ".dylib", ".exe"))
LEGACY_COMPANION_FILES = frozenset(("dlls/main.dll", "dlls/worker-provenance.json"))
# Files eligible for removal only when the previous install manifest owns their exact bytes.
RETIRED_FILES = frozenset((
    "Scripts/paintmanager/background_wire.lua",
    "Scripts/paintmanager/background_reader.lua",
    "Scripts/paintmanager/ui_input.lua",
    "Scripts/paintmanager/worker_paint_cache.lua",
    "Assets/UI/deposit_bucket.png",
    "THIRD_PARTY_NOTICES.md",
))


class ToolError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(value: str) -> Path:
    """Reject both Windows and POSIX traversal before any filesystem operation."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value:
        raise ToolError(f"Unsafe relative path: {value!r}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or PurePosixPath(value).is_absolute():
        raise ToolError(f"Unsafe relative path: {value!r}")
    return Path(*parts)


def owned_path(base: Path, relative: str) -> Path:
    path = base / relative_path(relative)
    resolved_base = base.resolve()
    if not path.resolve().is_relative_to(resolved_base):
        raise ToolError(f"Path escapes owned directory: {relative}")
    current = path
    while current != base.parent:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ToolError(f"Refusing linked deployment path: {current}")
        if current == base:
            break
        current = current.parent
    return path


def load_metadata(root: Path = ROOT) -> dict:
    try:
        data = json.loads((root / "mod.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"Cannot read mod.json: {exc}") from exc
    if not isinstance(data, dict) or data.get("id") != MOD_ID:
        raise ToolError("mod.json id must be PaintManager")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", str(data.get("version", ""))):
        raise ToolError("mod.json requires a safe semantic version")
    return data


def checked_sources(root: Path = ROOT) -> tuple[dict, list[Path]]:
    metadata = load_metadata(root)
    scripts = root / "src" / MOD_ID / "Scripts"
    if (scripts / "paintmanager" / "i18n.lua").is_file():
        try:
            if __name__ == "__main__":
                from localization import generate
            else:
                from tools.localization import generate
            generate(root, check=True)
        except (ImportError, OSError, ValueError) as exc:
            raise ToolError(f"Localization check failed: {exc}") from exc
    files = sorted(scripts.rglob("*.lua"))
    if not (scripts / "main.lua").is_file() or not files:
        raise ToolError("Missing src/PaintManager/Scripts/main.lua")
    for path in (root / "src" / MOD_ID).rglob("*"):
        if path.suffix.lower() in NATIVE_SUFFIXES or "dlls" in path.relative_to(root / "src" / MOD_ID).parts:
            raise ToolError(f"Lua-only runtime cannot contain native artifacts: {path}")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(scripts.resolve()):
            raise ToolError(f"Source link escapes Scripts: {path}")
        if not path.read_bytes():
            raise ToolError(f"Empty Lua source: {path}")
        source = path.read_text(encoding="utf-8-sig")
        if re.search(r"\bpackage\s*\.\s*(?:loadlib\s*\(|cpath\s*=)|\bffi\s*\.\s*load\s*\(|\brequire\s*\(?\s*['\"]ffi['\"]", source):
            raise ToolError(f"Lua-only runtime cannot load native modules: {path}")
        if re.search(r"\bPaintManagerWorker\s*[.:\[]|\bPMWorkerNative[A-Za-z_0-9]*\s*\(|\brawget\s*\(\s*_G\s*,\s*['\"]PaintManagerWorker['\"]", source):
            raise ToolError(f"Lua-only runtime cannot route to the retired native companion: {path}")
    config = scripts / "paintmanager" / "config.lua"
    if not config.is_file():
        raise ToolError("Missing Scripts/paintmanager/config.lua")
    config_text = config.read_text(encoding="utf-8-sig")
    # These two literal metadata fields deliberately do not execute Lua at build time.
    for field, expected in (("version", metadata["version"]), ("reference_build", metadata.get("referenceGameBuild"))):
        matches = re.findall(r"^\s*" + field + r"\s*=\s*[\"']([^\"']+)[\"']\s*,?\s*$", config_text, re.MULTILINE)
        if len(matches) != 1 or matches[0] != expected:
            raise ToolError(f"config.lua {field} must be a single string literal matching mod.json ({expected!r})")
    try:
        from lupa.lua54 import LuaRuntime
    except ImportError as exc:
        raise ToolError("Install development dependencies: python -m pip install -r requirements-dev.txt") from exc
    runtime = LuaRuntime(unpack_returned_tuples=True)
    for path in files:
        try:
            runtime.compile(path.read_text(encoding="utf-8-sig"), name="@" + path.relative_to(root).as_posix())
        except Exception as exc:
            raise ToolError(f"Lua 5.4 syntax error in {path.relative_to(root)}: {exc}") from exc
    return metadata, files


UI_ICON_IDS = frozenset(("refresh", "close", "clear", "star", "star_filled", "learned", "ready",
                         "missing", "settings", "debug", "search", "sort", "learn", "presets",
                         "save", "apply", "delete", "import", "back", "learned_detailed", "ready_detailed",
                         "to_backpack", "to_box", "deposit_paint_can"))
UI_ICON_MAX_BYTES = 32768
UI_SIGNAL_WIDTH, UI_SIGNAL_HEIGHT = 1024, 512
UI_SIGNAL_BYTES = 118144
UI_ILLUSTRATION_MAX_BYTES, UI_ILLUSTRATION_MAX_PIXELS = 1048576, 2000000
UI_SIGNAL_SHA256 = "9c01e023000726d7b828fbc165fc1e13497795c073984260629391237e55ddbe"


def checked_ui_png(image: bytes) -> bool:
    """Validate bounded native import bytes without trusting only their header."""
    return checked_rgba_png(image, width=128, height=128, maximum=UI_ICON_MAX_BYTES)


def checked_rgba_png(image: bytes, *, width: int, height: int, maximum: int) -> bool:
    """Validate an exact bounded noninterlaced RGBA8 PNG, including all chunks."""
    if not 57 <= len(image) <= maximum or image[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    offset, header, ended_data = 8, False, False
    compressed = bytearray()
    try:
        while offset < len(image):
            if len(image) - offset < 12:
                return False
            length = struct.unpack_from(">I", image, offset)[0]
            end = offset + 12 + length
            if end > len(image):
                return False
            kind, value = image[offset + 4:offset + 8], image[offset + 8:offset + 8 + length]
            if (not re.fullmatch(b"[A-Za-z]{4}", kind)
                    or zlib.crc32(kind + value) != struct.unpack_from(">I", image, end - 4)[0]):
                return False
            if not header:
                if kind != b"IHDR" or value != struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0):
                    return False
                header = True
            elif kind == b"IHDR":
                return False
            elif kind == b"IDAT":
                if ended_data:
                    return False
                compressed.extend(value)
            elif kind == b"IEND":
                if value or end != len(image) or not compressed:
                    return False
                # The decompression output is capped before accepting any data.
                # Cap decompression before accepting any decoded row bytes.
                stride = width * 4 + 1
                expected = height * stride
                decoder = zlib.decompressobj()
                pixels = decoder.decompress(bytes(compressed), expected + 1)
                return (len(pixels) == expected and decoder.eof and not decoder.unused_data
                        and not decoder.unconsumed_tail
                        and all(pixels[row * stride] <= 4 for row in range(height)))
            else:
                if compressed:
                    ended_data = True
                if kind[0] & 32 == 0 and kind != b"PLTE":
                    return False
            offset = end
    except (ValueError, struct.error, zlib.error):
        return False
    return False


def embedded_ui_icons(source: str) -> dict[str, bytes]:
    """Accept one literal generated table so duplicate keys cannot mask a twin."""
    lines = [line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith("--")]
    if len(lines) < 2 or lines[0] != "return {" or lines[-1] != "}":
        raise ToolError("Invalid embedded UI icon table")
    result = {}
    for line in lines[1:-1]:
        match = re.fullmatch(r'\["([A-Za-z_]+)"\]\s*=\s*"([0-9a-f]+)"\s*,?', line)
        if not match or match[1] not in UI_ICON_IDS or match[1] in result or len(match[2]) > UI_ICON_MAX_BYTES * 2:
            raise ToolError("Invalid or duplicate embedded UI icon entry")
        try:
            result[match[1]] = bytes.fromhex(match[2])
        except ValueError as exc:
            raise ToolError("Invalid embedded UI icon bytes") from exc
    if set(result) != UI_ICON_IDS:
        raise ToolError("Embedded UI icon IDs do not match the allowlist")
    return result


def checked_ui_illustration_png(image: bytes) -> bool:
    """Keep the approved prerendered canvas and real alpha separate from icon limits."""
    if UI_SIGNAL_WIDTH * UI_SIGNAL_HEIGHT > UI_ILLUSTRATION_MAX_PIXELS or not checked_rgba_png(
            image, width=UI_SIGNAL_WIDTH, height=UI_SIGNAL_HEIGHT, maximum=UI_ILLUSTRATION_MAX_BYTES):
        return False
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image)) as decoded:
            decoded.load()
            if decoded.format != "PNG" or decoded.mode != "RGBA" or decoded.size != (UI_SIGNAL_WIDTH, UI_SIGNAL_HEIGHT):
                return False
            alpha = decoded.getchannel("A")
            return alpha.getextrema() == (0, 255) and sum(alpha.histogram()[1:255]) > 0
    except (ImportError, OSError, ValueError):
        return False


def embedded_ui_signal(source: str) -> dict:
    """Read one generated literal table without executing its large Lua source."""
    lines = [line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith("--")]
    if len(lines) != 7 or lines[0] != "return {" or lines[-1] != "}":
        raise ToolError("Invalid embedded signal illustration table")
    result = {}
    for line in lines[1:-1]:
        number = re.fullmatch(r"(width|height|bytes)\s*=\s*([0-9]+),", line)
        literal = re.fullmatch(r'(sha256|hex)\s*=\s*"([0-9a-f]+)",', line)
        if number:
            key, value = number[1], int(number[2])
        elif literal:
            key, value = literal[1], literal[2]
            if len(value) > UI_ILLUSTRATION_MAX_BYTES * 2:
                raise ToolError("Embedded signal illustration exceeds its byte cap")
        else:
            raise ToolError("Invalid embedded signal illustration field")
        if key in result:
            raise ToolError("Duplicate embedded signal illustration field")
        result[key] = value
    if set(result) != {"width", "height", "bytes", "sha256", "hex"}:
        raise ToolError("Embedded signal illustration fields differ from the allowlist")
    if (result["width"] != UI_SIGNAL_WIDTH or result["height"] != UI_SIGNAL_HEIGHT
            or result["bytes"] != UI_SIGNAL_BYTES or result["sha256"] != UI_SIGNAL_SHA256
            or len(result["hex"]) != result["bytes"] * 2):
        raise ToolError("Embedded signal illustration metadata differs from approved artwork")
    try:
        result["data"] = bytes.fromhex(result.pop("hex"))
    except ValueError as exc:
        raise ToolError("Invalid embedded signal illustration bytes") from exc
    return result


def ui_illustrations(root: Path = ROOT) -> dict[str, bytes]:
    """Package only the separately approved illustration and its exact twin."""
    directory = owned_path(root, "src/PaintManager/Assets/UI/Illustrations")
    embedded = owned_path(root, "src/PaintManager/Scripts/paintmanager/ui_signal_data.lua")
    if not directory.exists():
        if embedded.exists():
            raise ToolError("Embedded signal illustration requires its runtime PNG")
        return {}
    if not directory.is_dir() or {path.name for path in directory.iterdir()} != {"manifest.json", "signal_lost.png"}:
        raise ToolError("Signal illustration directory differs from the exact allowlist")
    manifest = owned_path(root, "src/PaintManager/Assets/UI/Illustrations/manifest.json")
    path = owned_path(root, "src/PaintManager/Assets/UI/Illustrations/signal_lost.png")
    if not manifest.is_file() or manifest.stat().st_size > 16384:
        raise ToolError("Signal illustration requires a bounded manifest")
    if (not embedded.is_file() or embedded.stat().st_size > UI_ILLUSTRATION_MAX_BYTES * 2 + 1024
            or not path.is_file() or path.stat().st_size != UI_SIGNAL_BYTES):
        raise ToolError("Invalid signal illustration PNG or embedded source size")
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate signal illustration manifest key")
            value[key] = item
        return value
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if (not isinstance(record, dict) or set(record) != {"schemaVersion", "artworkSourceSha256", "illustrations"}
                or type(record["schemaVersion"]) is not int or record["schemaVersion"] != 1
                or not isinstance(record["artworkSourceSha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["artworkSourceSha256"])
                or not isinstance(record["illustrations"], dict) or set(record["illustrations"]) != {"signal_lost"}):
            raise ValueError("Signal illustration manifest/schema mismatch")
        entry = record["illustrations"]["signal_lost"]
        expected = {"file": "signal_lost.png", "sha256": UI_SIGNAL_SHA256,
                    "width": UI_SIGNAL_WIDTH, "height": UI_SIGNAL_HEIGHT,
                    "bytes": UI_SIGNAL_BYTES, "alphaExtrema": [0, 255]}
        if (not isinstance(entry, dict) or entry != expected
                or any(type(entry[key]) is not int for key in ("width", "height", "bytes"))):
            raise ValueError("Signal illustration is not the approved artifact")
        twin = embedded_ui_signal(embedded.read_text(encoding="ascii"))
    except (OSError, ValueError, KeyError, AssertionError, TypeError) as exc:
        raise ToolError("Invalid signal illustration manifest or embedded source") from exc
    image = path.read_bytes()
    if sha256(image) != UI_SIGNAL_SHA256 or not checked_ui_illustration_png(image):
        raise ToolError("Signal illustration PNG digest, dimensions or alpha mismatch")
    if twin["data"] != image:
        raise ToolError("Embedded signal illustration differs from the packaged PNG")
    return {"Assets/UI/Illustrations/manifest.json": manifest.read_bytes(),
            "Assets/UI/Illustrations/signal_lost.png": image}


def ui_assets(root: Path = ROOT) -> dict[str, bytes]:
    """Include only approved bounded PNGs with exact manifest and embedded twins."""
    directory = root / "src" / MOD_ID / "Assets" / "UI"
    embedded = root / "src" / MOD_ID / "Scripts" / "paintmanager" / "ui_icon_data.lua"
    if not directory.exists():
        if embedded.exists():
            raise ToolError("Embedded UI icons require their source assets")
        return {}
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise ToolError("Invalid UI asset directory")
    if (embedded.is_symlink() or not embedded.is_file()
            or not embedded.resolve().is_relative_to(root.resolve())
            or embedded.stat().st_size > len(UI_ICON_IDS) * (UI_ICON_MAX_BYTES * 2 + 128) + 1024):
        raise ToolError("Invalid embedded UI icon source")
    manifest = directory / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 65536:
        raise ToolError("UI assets require a regular bounded manifest")
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if (not isinstance(record, dict)
                or type(record.get("schemaVersion")) is not int or record["schemaVersion"] != 1
                or type(record.get("width")) is not int or record["width"] != 128
                or type(record.get("height")) is not int or record["height"] != 128
                or not isinstance(record.get("icons"), dict) or set(record["icons"]) != UI_ICON_IDS):
            raise ValueError("UI icon schema, dimensions or allowlist mismatch")
        twins = embedded_ui_icons(embedded.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, AssertionError, TypeError) as exc:
        raise ToolError("Invalid UI icon manifest or embedded data") from exc
    result = {"Assets/UI/manifest.json": manifest.read_bytes()}
    for name in sorted(UI_ICON_IDS):
        entry = record["icons"][name]
        if not isinstance(entry, dict) or entry.get("file") != name + ".png":
            raise ToolError("UI icon filename is not allowlisted")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
            raise ToolError("UI icon digest is invalid")
        path = directory / (name + ".png")
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            raise ToolError("Invalid UI icon source")
        if path.stat().st_size > UI_ICON_MAX_BYTES:
            raise ToolError("UI icon PNG dimensions, format, size or digest mismatch")
        image = path.read_bytes()
        if not checked_ui_png(image) or sha256(image) != entry.get("sha256"):
            raise ToolError("UI icon PNG dimensions, format, size or digest mismatch")
        if twins[name] != image:
            raise ToolError("Embedded UI icon does not match the packaged PNG")
        result["Assets/UI/" + name + ".png"] = image
    return result


def payload(root: Path = ROOT) -> tuple[dict, dict[str, bytes]]:
    metadata, files = checked_sources(root)
    scripts = root / "src" / MOD_ID / "Scripts"
    result = {"Scripts/" + file.relative_to(scripts).as_posix(): file.read_bytes() for file in files}
    gamer_docs = "docs/gamers"
    release_paths = ["LICENSE", "mod.json"]
    release_paths.extend(f"{gamer_docs}/{name}" for name in ("README.md", *PLAYER_GUIDES))
    for name in release_paths:
        path = owned_path(root, name)
        if not path.is_file():
            raise ToolError(f"Missing release file: {name}")
        result[name] = path.read_bytes()
    # Put the player index beside the mod and keep its links usable offline.
    readme = result.pop(f"{gamer_docs}/README.md").decode("utf-8")
    banner = "Assets/Branding/readme-banner.png"
    banner_source = f"src/{MOD_ID}/{banner}"
    result[banner] = owned_path(root, banner_source).read_bytes()
    readme = readme.replace(f'src="../../{banner_source}"', f'src="{banner}"')
    for guide in PLAYER_GUIDES:
        readme = readme.replace(f"]({guide})", f"]({gamer_docs}/{guide})")
    result["README.md"] = readme.encode("utf-8")
    result["enabled.txt"] = b""
    result.update(ui_assets(root))
    result.update(ui_illustrations(root))
    for name in result:
        relative_path(name)
    return metadata, result


def archive_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            relative_path(name)
            if PurePosixPath(name).suffix.lower() in NATIVE_SUFFIXES or "dlls" in PurePosixPath(name).parts:
                raise ToolError(f"Lua-only package cannot contain native artifacts: {name}")
            info = zipfile.ZipInfo(ZIP_PREFIX + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return stream.getvalue()


def package(root: Path = ROOT) -> dict:
    metadata, files = payload(root)
    data = archive_bytes(files)
    output = root / "dist" / f"PaintManager-{metadata['version']}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(output, data)
    report = {"version": metadata["version"], "archive": output.name, "sha256": sha256(data),
              "report_backend": "lua_cooperative",
              "files": {ZIP_PREFIX + name: sha256(content) for name, content in sorted(files.items())}}
    atomic_write(output.with_suffix(".manifest.json"), json_bytes(report))
    return report


def valid_game_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    for relative in ("Voyage.exe", "Voyage/Binaries/Win64/VoyageSteam-Win64-Shipping.exe"):
        if not (path / relative).is_file():
            raise ToolError(f"Not a Voyage installation: {path} (missing {relative})")
    return path


def find_game_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return valid_game_root(Path(explicit))
    if os.environ.get("PAINTMANAGER_GAME_ROOT"):
        return valid_game_root(Path(os.environ["PAINTMANAGER_GAME_ROOT"]))
    steam_roots = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
    steam_roots.append(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam")
    libraries = set(steam_roots)
    for steam in steam_roots:
        folders = steam / "steamapps" / "libraryfolders.vdf"
        if folders.is_file():
            text = folders.read_text(encoding="utf-8-sig", errors="replace")
            libraries.update(Path(value.replace("\\\\", "\\")) for value in re.findall(r'"path"\s*"([^"\r\n]+)"', text))
    candidates = []
    for library in sorted(libraries):
        manifest = library / "steamapps" / "appmanifest_1783560.acf"
        if manifest.is_file():
            match = re.search(r'"installdir"\s*"([^"\r\n]+)"', manifest.read_text(encoding="utf-8-sig"))
            if match:
                candidates.append(valid_game_root(library / "steamapps" / "common" / relative_path(match[1])))
    if len(set(candidates)) != 1:
        raise ToolError("Could not select exactly one installed game; pass --game-root or PAINTMANAGER_GAME_ROOT")
    return candidates[0]


def running_game_processes() -> list[dict]:
    if os.name != "nt":
        raise ToolError("Live deployment requires Windows process inspection")
    command = ("$ErrorActionPreference='Stop'; "
               "@(Get-Process -ErrorAction Stop | Where-Object { $_.ProcessName -eq 'Voyage' -or "
               "$_.ProcessName -eq 'VoyageSteam-Win64-Shipping' } | "
               "Select-Object ProcessName,Id) | ConvertTo-Json -Compress")
    try:
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                   capture_output=True, text=True, timeout=20, check=True)
        result = json.loads(completed.stdout.strip() or "[]")
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list) or any(not isinstance(row, dict) or row.get("ProcessName") not in GAME_PROCESSES for row in result):
            raise ValueError("Unexpected process-inspection output")
        return result
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ToolError(f"Cannot establish that the game is closed: {exc}") from exc


def require_closed_game() -> None:
    processes = running_game_processes()
    if processes:
        raise ToolError("The Last Caretaker is running; save and close it normally before deployment")


def doctor(game_root: Path) -> dict:
    win64 = game_root / "Voyage" / "Binaries" / "Win64"
    manifest = game_root.parent.parent / "appmanifest_1783560.acf"
    match = re.search(r'"buildid"\s*"(\d+)"', manifest.read_text(encoding="utf-8-sig")) if manifest.is_file() else None
    result = {"game_root": str(game_root), "steam_build": match[1] if match else None,
              "files": {}, "running_processes": None}
    for label, file in {"game_executable": win64 / "VoyageSteam-Win64-Shipping.exe",
                        "loader": win64 / "ue4ss" / "UE4SS.dll", "proxy": win64 / "dwmapi.dll"}.items():
        result["files"][label] = {"present": file.is_file(), "sha256": file_hash(file) if file.is_file() else None}
    try:
        result["running_processes"] = running_game_processes()
    except ToolError as exc:
        result["process_check_error"] = str(exc)
    return result


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".paintmanager-", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def read_install_manifest(target: Path) -> dict | None:
    if not target.exists():
        return None
    marker = owned_path(target, INSTALL_MANIFEST)
    if not marker.is_file():
        raise ToolError(f"Refusing unmanaged existing folder: {target}")
    try:
        if marker.stat().st_size > 1024 * 1024:
            raise ValueError("Manifest is too large")
        data = json.loads(marker.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("owner") != MOD_ID or data.get("schema_version") != 1 or not isinstance(data.get("files"), dict):
            raise ValueError("Wrong manifest schema or owner")
        for name, digest in data["files"].items():
            path = owned_path(target, name)
            if name == INSTALL_MANIFEST or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid ownership record")
            if not path.is_file() or file_hash(path) != digest:
                raise ValueError(f"Managed file was changed or removed: {name}")
    except (OSError, ValueError, TypeError) as exc:
        raise ToolError(f"Cannot trust install manifest: {exc}") from exc
    return data


@contextmanager
def deployment_lock(root: Path):
    lock = root / "local" / "deploy.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ToolError(f"Deployment lock exists; inspect its owner before removing: {lock}") from exc
    try:
        with stream:
            stream.write(json.dumps({"pid": os.getpid(), "created_utc": datetime.now(timezone.utc).isoformat()}))
        yield
    finally:
        lock.unlink()


def installed_native_files(target: Path) -> set[str]:
    """Inspect only the owned mod tree, refusing any linked directory before descent."""
    result = set()
    if not target.exists():
        return result
    for current, directories, filenames in os.walk(target, followlinks=False):
        directory = Path(current)
        for name in directories:
            owned_path(target, (directory / name).relative_to(target).as_posix())
        for name in filenames:
            relative = (directory / name).relative_to(target).as_posix()
            path = owned_path(target, relative)
            if path.suffix.lower() in NATIVE_SUFFIXES:
                result.add(relative)
    return result


def verified_legacy_removals(target: Path, previous: dict | None, metadata: dict,
                            files: dict[str, bytes]) -> set[str]:
    """Authorize only manifest/hash-verified files explicitly retired by PaintManager."""
    old_files = previous["files"] if previous else {}
    obsolete = set(old_files) - set(files)
    allowed = LEGACY_COMPANION_FILES | RETIRED_FILES
    if obsolete - allowed:
        raise ToolError("Obsolete owned files require explicit review, no files deleted: " +
                        ", ".join(sorted(obsolete - allowed)))
    legacy = obsolete & LEGACY_COMPANION_FILES
    if legacy:
        if legacy != LEGACY_COMPANION_FILES:
            raise ToolError("Legacy companion removal requires both owned DLL and provenance")
        try:
            record = json.loads(owned_path(target, "dlls/worker-provenance.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ToolError(f"Cannot verify legacy companion provenance: {exc}") from exc
        expected = {"schemaVersion": 1, "loaderCommit": metadata.get("referenceLoaderCommit"),
                    "protocol": 1, "reportWire": 1, "arch": "x86_64", "abi": "MSVC",
                    "configuration": "Game__Shipping__Win64", "dllSha256": old_files["dlls/main.dll"]}
        if (not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()) or
                not re.fullmatch(r"[a-f0-9]{64}", str(record.get("sourceSha256", "")))):
            raise ToolError("Legacy companion provenance does not match its owned DLL and pinned loader")
    unexpected = installed_native_files(target) - legacy
    if unexpected:
        raise ToolError("Unowned or unexpected native runtime files prevent Lua-only deployment; preserved: " +
                        ", ".join(sorted(unexpected)))
    return obsolete


def retired_directory_removals(target: Path, removed_files: set[str]) -> list[str]:
    """UE4SS treats even an empty mod-root dlls directory as a C++ mod."""
    directory = owned_path(target, "dlls")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ToolError("Refusing unexpected non-directory dlls path; preserved")
    for entry in directory.iterdir():
        name = entry.relative_to(target).as_posix()
        path = owned_path(target, name)
        if not path.is_file() or name not in removed_files:
            raise ToolError(f"Unexpected dlls directory content prevents Lua-only deployment; preserved: {name}")
    return ["dlls"]


def deployment_plan(game_root: Path, metadata: dict, files: dict[str, bytes]) -> tuple[Path, dict]:
    win64 = game_root / "Voyage" / "Binaries" / "Win64"
    if not (win64 / "ue4ss" / "UE4SS.dll").is_file() or not (win64 / "dwmapi.dll").is_file():
        raise ToolError("Existing UE4SS loader and proxy are required; this tool does not install them")
    target = owned_path(game_root, f"Voyage/Binaries/Win64/ue4ss/Mods/{MOD_ID}")
    previous = read_install_manifest(target)
    old_files = previous["files"] if previous else {}
    obsolete = verified_legacy_removals(target, previous, metadata, files)
    directories = retired_directory_removals(target, obsolete)
    for name in files:
        path = owned_path(target, name)
        if path.exists() and name not in old_files:
            raise ToolError(f"Refusing to overwrite an unowned file: {path}")
    manifest = {"schema_version": 1, "owner": MOD_ID, "version": metadata["version"],
                "files": {name: sha256(data) for name, data in sorted(files.items())}}
    if obsolete or directories:
        manifest["migration"] = {"from_version": previous.get("version"), "removed_files": sorted(obsolete),
                                 "removed_directories": directories}
    return target, manifest


def deploy(game_root: Path, *, apply: bool = False, root: Path = ROOT) -> dict:
    metadata, files = payload(root)
    target, manifest = deployment_plan(game_root, metadata, files)
    plan = {"apply": apply, "target": str(target), "version": metadata["version"], "files": sorted(files),
            "shared_loader_changed": False,
            "report_backend": "lua_cooperative",
            "removed_files": manifest.get("migration", {}).get("removed_files", []),
            "removed_directories": manifest.get("migration", {}).get("removed_directories", [])}
    if not apply:
        try:
            plan["running_processes"] = running_game_processes()
        except ToolError as exc:
            plan["process_check_error"] = str(exc)
        return plan
    require_closed_game()
    with deployment_lock(root):
        # Re-read ownership and process state immediately before changing the game.
        target, manifest = deployment_plan(game_root, metadata, files)
        require_closed_game()
        changes = dict(files)
        removals = manifest.get("migration", {}).get("removed_files", [])
        directories = manifest.get("migration", {}).get("removed_directories", [])
        plan["removed_files"], plan["removed_directories"] = removals, directories
        prior = read_install_manifest(target)
        changes.update({name: None for name in removals})
        changes[INSTALL_MANIFEST] = json_bytes(manifest)
        before = {name: owned_path(target, name).read_bytes() if owned_path(target, name).is_file() else None for name in changes}
        backup = root / "local" / "deploy-backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True, exist_ok=False)
        index = {"target": str(target), "files": {}, "removed_directories": directories}
        for number, (name, data) in enumerate(before.items()):
            if data is not None:
                file = f"{number:04d}.bin"
                atomic_write(backup / file, data)
                index["files"][name] = file
            else:
                index["files"][name] = None
        atomic_write(backup / "index.json", json_bytes(index))
        written, removed_directories = [], []
        try:
            for name, data in changes.items():
                path = owned_path(target, name)
                if data is None:
                    if prior is None or file_hash(path) != prior["files"].get(name):
                        raise ToolError(f"Owned obsolete file changed before removal: {name}")
                    path.unlink()
                else:
                    atomic_write(path, data)
                written.append(name)
                if data is not None and file_hash(path) != sha256(data):
                    raise ToolError(f"Installed hash mismatch: {name}")
            for name in directories:
                directory = owned_path(target, name)
                # Recheck under the deployment lock. rmdir refuses any content
                # introduced since preflight; never recursively delete here.
                if not directory.is_dir() or any(directory.iterdir()):
                    raise ToolError(f"Retired directory is no longer empty; preserved: {name}")
                directory.rmdir()
                removed_directories.append(name)
            read_install_manifest(target)
            if installed_native_files(target):
                raise ToolError("Native runtime files remain after Lua-only deployment")
            if owned_path(target, "dlls").exists():
                raise ToolError("C++ mod discovery directory remains after Lua-only deployment: dlls")
        except Exception as exc:
            recovery_errors = []
            for name in reversed(written):
                try:
                    path = owned_path(target, name)
                    if before[name] is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, before[name])
                except Exception as recovery:
                    recovery_errors.append(f"{name}: {recovery}")
            for name in reversed(removed_directories):
                try:
                    owned_path(target, name).mkdir(exist_ok=True)
                except Exception as recovery:
                    recovery_errors.append(f"{name}: {recovery}")
            raise ToolError(f"Deployment failed: {exc}; recovery errors={recovery_errors}; backup={backup}") from exc
        plan["backup"] = str(backup)
        return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Compile Lua 5.4 source without executing it")
    package_parser = subparsers.add_parser("package", help="Check and create a mod-only deterministic ZIP")
    for name in ("doctor", "deploy"):
        command = subparsers.add_parser(name)
        command.add_argument("--game-root")
        if name == "deploy":
            mode = command.add_mutually_exclusive_group()
            mode.add_argument("--apply", action="store_true", help="Write only owned files, with the game closed")
            mode.add_argument("--dry-run", action="store_true", help="Default: report the deployment without changing the game")
    arguments = parser.parse_args()
    try:
        if arguments.command == "check":
            metadata, files = checked_sources()
            result = {"version": metadata["version"], "lua_runtime": "Lua 5.4 (lupa.lua54)", "lua_files_checked": len(files)}
        elif arguments.command == "package":
            result = package()
        else:
            game_root = find_game_root(arguments.game_root)
            result = doctor(game_root) if arguments.command == "doctor" else deploy(
                game_root, apply=arguments.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ToolError, OSError) as exc:
        print(f"PaintManager tooling: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
