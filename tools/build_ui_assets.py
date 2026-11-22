"""Build the geometric, style C and Lucide paint-bucket control images.

PNG resources and the bounded embedded Lua buffers share checked source artwork.
Runtime loading uses the embedded buffers, avoiding synchronous mod-file reads.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import zlib

from PIL import Image
from resvg_py import svg_to_bytes

ROOT = Path(__file__).resolve().parents[1]
SIZE, SAMPLE = 128, 2
# Geometry uses a 32-unit canvas. All shapes are original, simple UI symbols.
ICONS = {
    "refresh": [("arc", 16, 16, 10, 40, 320), ("poly", [(23, 3), (28, 11), (18, 10)])],
    "close": [("line", [(8, 8), (24, 24)]), ("line", [(24, 8), (8, 24)])],
    "clear": [("circle", 16, 16, 11), ("line", [(12, 12), (20, 20)]), ("line", [(20, 12), (12, 20)])],
    "star": [("line", [(16, 3), (20, 11), (29, 12), (22, 19), (24, 28), (16, 23), (8, 28), (10, 19), (3, 12), (12, 11), (16, 3)])],
    "star_filled": [("poly", [(16, 3), (20, 11), (29, 12), (22, 19), (24, 28), (16, 23), (8, 28), (10, 19), (3, 12), (12, 11)])],
    "learned": [("line", [(4, 6), (13, 6), (16, 9), (19, 6), (28, 6), (28, 25), (19, 25), (16, 28), (13, 25), (4, 25), (4, 6)]), ("line", [(16, 9), (16, 23)])],
    "ready": [("circle", 16, 16, 12), ("line", [(9, 16), (14, 21), (23, 11)])],
    "missing": [("line", [(7, 14), (7, 27), (25, 27), (25, 14), (7, 14)]), ("arc", 16, 12, 6, 180, 360)],
    "settings": [("circle", 16, 16, 5), ("circle", 16, 16, 11), ("line", [(16, 2), (16, 6)]), ("line", [(16, 26), (16, 30)]), ("line", [(2, 16), (6, 16)]), ("line", [(26, 16), (30, 16)])],
    "debug": [("circle", 16, 18, 8), ("line", [(11, 6), (16, 10), (21, 6)]), ("line", [(3, 12), (8, 15)]), ("line", [(24, 15), (29, 12)]), ("line", [(2, 21), (8, 21)]), ("line", [(24, 21), (30, 21)]), ("line", [(16, 11), (16, 25)])],
    "search": [("circle", 13, 13, 8), ("line", [(19, 19), (28, 28)])],
    "sort": [("line", [(9, 5), (9, 27), (4, 22)]), ("line", [(9, 27), (14, 22)]), ("line", [(19, 7), (29, 7)]), ("line", [(19, 15), (26, 15)]), ("line", [(19, 23), (23, 23)])],
    "learn": [("line", [(5, 8), (14, 8), (16, 10), (18, 8), (27, 8), (27, 27), (18, 27), (16, 29), (14, 27), (5, 27), (5, 8)]), ("line", [(16, 11), (16, 24)]), ("line", [(23, 2), (23, 8)]), ("line", [(20, 5), (26, 5)])],
    "presets": [("line", [(4, 5), (4, 27), (28, 27), (28, 5), (4, 5)]), ("line", [(10, 5), (10, 27)]), ("line", [(4, 13), (28, 13)]), ("line", [(19, 13), (19, 27)])],
    "save": [("line", [(5, 4), (23, 4), (28, 9), (28, 28), (5, 28), (5, 4)]), ("line", [(11, 4), (11, 12), (22, 12), (22, 4)]), ("line", [(10, 28), (10, 19), (23, 19), (23, 28)])],
    "apply": [("line", [(5, 17), (12, 24), (27, 8)])],
    "delete": [("line", [(8, 10), (10, 28), (23, 28), (25, 10)]), ("line", [(5, 9), (28, 9)]), ("line", [(12, 9), (12, 4), (21, 4), (21, 9)]), ("line", [(14, 14), (14, 23)]), ("line", [(20, 14), (20, 23)])],
    "import": [("line", [(4, 17), (4, 28), (28, 28), (28, 17)]), ("line", [(16, 3), (16, 21), (10, 15)]), ("line", [(16, 21), (22, 15)])],
    "back": [("line", [(26, 16), (6, 16), (14, 8)]), ("line", [(6, 16), (14, 24)])],
    "to_backpack": [
        ("line", [(14, 8), (14, 5), (16, 3), (20, 3), (22, 5), (22, 8)]),
        ("line", [(9, 11), (12, 8), (24, 8), (27, 12), (28, 25), (25, 28), (11, 28), (8, 25)]),
        ("line", [(3, 18), (18, 18)]), ("line", [(13, 13), (18, 18), (13, 23)]),
        ("line", [(21, 21), (24, 21)]),
    ],
    "to_box": [
        ("line", [(3, 16), (28, 16), (28, 20), (3, 20), (3, 16)]),
        ("line", [(5, 20), (5, 28), (26, 28), (26, 20)]),
        ("line", [(29, 14), (29, 10), (26, 7), (13, 7)]),
        ("line", [(18, 2), (13, 7), (18, 12)]),
        ("line", [(10, 24), (16, 24)]),
    ],
}


def png(shapes: list) -> bytes:
    high = SIZE * SAMPLE
    scale = high / 32
    mask = bytearray(high * high)

    def stroke(a, b, width=2):
        ax, ay, bx, by = a[0]*scale, a[1]*scale, b[0]*scale, b[1]*scale
        radius = width*scale/2
        length = (bx-ax)**2+(by-ay)**2
        for y in range(max(0, int(min(ay, by)-radius)), min(high, math.ceil(max(ay, by)+radius))):
            for x in range(max(0, int(min(ax, bx)-radius)), min(high, math.ceil(max(ax, bx)+radius))):
                t = max(0, min(1, ((x+.5-ax)*(bx-ax)+(y+.5-ay)*(by-ay))/length)) if length else 0
                if (x+.5-ax-t*(bx-ax))**2+(y+.5-ay-t*(by-ay))**2 <= radius**2:
                    mask[y*high+x] = 255

    for shape in shapes:
        if shape[0] == "line":
            for a, b in zip(shape[1], shape[1][1:]):
                stroke(a, b)
        elif shape[0] in ("circle", "arc"):
            _, cx, cy, radius, *angles = shape
            start, end = angles if angles else (0, 360)
            points = [(cx+radius*math.cos(math.radians(deg)), cy+radius*math.sin(math.radians(deg)))
                      for deg in range(start, end+1, 2)]
            for a, b in zip(points, points[1:]):
                stroke(a, b)
        else:
            points = [(x*scale, y*scale) for x, y in shape[1]]
            edges = list(zip(points, points[1:]+points[:1]))
            for y in range(high):
                for x in range(high):
                    hits = sum((ay > y+.5) != (by > y+.5)
                               and x+.5 < (bx-ax)*(y+.5-ay)/(by-ay)+ax
                               for (ax, ay), (bx, by) in edges)
                    if hits % 2:
                        mask[y*high+x] = 255
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            alpha = sum(mask[(y*SAMPLE+dy)*high+x*SAMPLE+dx]
                        for dy in range(SAMPLE) for dx in range(SAMPLE)) // SAMPLE**2
            rows.extend((255, 255, 255, alpha))

    def chunk(kind, value):
        return struct.pack(">I", len(value))+kind+value+struct.pack(">I", zlib.crc32(kind+value))

    return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
            +chunk(b"IDAT", zlib.compress(rows, 9))+chunk(b"IEND", b""))


def approved_overrides(root: Path) -> tuple[dict[str, bytes], str]:
    directory = root / "artwork/ui/style-c"
    manifest_path = directory / "source-manifest.json"
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Approved artwork directory escapes the project")
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
        raise ValueError("Invalid approved artwork source manifest path or size")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schemaVersion") != 1 or set(manifest.get("sources", {})) != {"atlas", "compact_learned", "compact_ready"}:
        raise ValueError("Invalid approved style C source manifest")
    expected = {"star", "star_filled", "presets", "refresh", "learned", "ready", "learn", "learned_detailed", "ready_detailed"}
    if set(manifest.get("overrides", {})) != expected:
        raise ValueError("Approved artwork override IDs differ from the selected family")
    sources = {}
    for identity, record in manifest["sources"].items():
        filename = record["file"]
        if Path(filename).name != filename or not filename.endswith(".png") or ":" in filename or "\\" in filename:
            raise ValueError("Unsafe approved artwork filename")
        path = directory / filename
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Approved artwork source escapes its directory")
        data = path.read_bytes()
        if len(data) > 8 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("Approved artwork source digest or size mismatch")
        image = Image.open(io.BytesIO(data))
        if image.format != "PNG" or image.width > 2048 or image.height > 2048 or image.mode != "RGBA":
            raise ValueError("Approved artwork must be bounded RGBA PNG data")
        image.load()
        sources[identity] = image
    result = {}
    for identity, record in manifest["overrides"].items():
        image = sources[record["source"]]
        if "region" in record:
            region = record["region"]
            if len(region) != 4 or any(type(value) is not int for value in region):
                raise ValueError("Invalid approved artwork crop")
            left, top, right, bottom = region
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError("Approved artwork crop exceeds source")
            image = image.crop(region)
        alpha = image.getchannel("A")
        # Ignore almost invisible extraction specks when locating the silhouette.
        bounds = alpha.point(lambda value: 255 if value >= 16 else 0).getbbox()
        if not bounds or alpha.getextrema()[0] != 0:
            raise ValueError("Approved artwork needs an isolated transparent silhouette")
        image = image.crop(bounds)
        image.thumbnail((116, 116), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((SIZE - image.width) // 2, (SIZE - image.height) // 2))
        # A fixed palette bounds bytes while preserving multicolor enamel and
        # antialiased alpha. Convert back to RGBA8 for the pinned native importer.
        canvas = canvas.quantize(colors=128, method=Image.Quantize.FASTOCTREE,
                                 dither=Image.Dither.NONE).convert("RGBA")
        pixels = list(canvas.get_flattened_data())
        canvas.putdata([(r, g, b, a) if a else (0, 0, 0, 0) for r, g, b, a in pixels])
        output = io.BytesIO()
        canvas.save(output, format="PNG", compress_level=9, optimize=False)
        data = output.getvalue()
        if len(data) > 32768:
            raise ValueError("Approved runtime artwork exceeds its bounded PNG cap")
        result[identity] = data
    return result, hashlib.sha256(raw).hexdigest()


def outputs(root: Path) -> dict[Path, bytes]:
    directory = root / "src/PaintManager/Assets/UI"
    result = {directory / f"{name}.png": png(shapes) for name, shapes in sorted(ICONS.items())}
    overrides, source_hash = approved_overrides(root)
    for name, data in overrides.items():
        result[directory / f"{name}.png"] = data
    bucket = (root / "artwork/ui/deposit/paint-bucket.svg").read_bytes()
    result[directory / "deposit_bucket.png"] = bytes(svg_to_bytes(svg_string=bucket.decode("utf-8")))
    manifest = {"schemaVersion": 1, "source": "Original geometric controls, approved style C artwork and the Lucide paint bucket",
                "artworkSourceSha256": source_hash,
                "depositBucketSourceSha256": hashlib.sha256(bucket).hexdigest(),
                "width": SIZE, "height": SIZE,
                "icons": {path.stem: {"file": path.name, "sha256": hashlib.sha256(data).hexdigest()}
                          for path, data in result.items()}}
    result[directory / "manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True)+"\n").encode()
    lines = ["-- Generated by tools/build_ui_assets.py from the checked control artwork.",
             "-- Hex keeps source ASCII; ui_icons decodes only bounded byte slices.", "return {"]
    for path, data in list(result.items()):
        if path.suffix == ".png":
            lines.append(f'    ["{path.stem}"] = "{data.hex()}",')
    lines.append("}")
    result[root / "src/PaintManager/Scripts/paintmanager/ui_icon_data.lua"] = ("\n".join(lines)+"\n").encode()
    return result


def build(root: Path = ROOT, check: bool = False):
    for path, data in outputs(root).items():
        if check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"UI asset differs from its generated source: {path.relative_to(root)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    build(check=parser.parse_args().check)
