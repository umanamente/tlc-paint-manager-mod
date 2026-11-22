# Artwork

The editable UI sources live under `ui/`. The mod loads the generated PNGs and their Lua equivalents in `src/PaintManager/`.

| Source | Used for |
| --- | --- |
| `ui/style-c/` | AI-generated atlas and compact book icons for favorites, recipes, presets and refresh. The manifest selects the source images and crop rectangles. |
| `ui/deposit/paint-bucket.svg` | Recolored Lucide icon for the optional **Deposit all paints** control. |
| `ui/signal-lost/` | Editable vector illustration of a disconnected ship and its transparent 1024 × 512 export. The final composition was redrawn in SVG from an AI-generated concept. |

Run `python tools/build_ui_assets.py --check` and `python tools/build_signal_art.py --check` from the repository root to verify the packaged artwork against its sources. Run the same commands without `--check` to update the generated files. The illustration builder uses the checked-in raster export. [Third-party credits](../THIRD_PARTY_NOTICES.md)
