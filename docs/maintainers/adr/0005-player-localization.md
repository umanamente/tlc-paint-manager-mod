# 0005. Translate mod-owned text through gettext catalogs

**Status** Accepted

## Context

The mod owns its controls, progress messages and tooltips. The game already supplies localized item names. Players also create their own preset names.

## Decision

Give player-facing mod text a stable key, English source, translator note and named arguments when needed. Export the gettext template and compile translated PO entries into Lua overlays. Use the game's language by default, allow an explicit preference and fall back to English for missing messages. Preserve game-provided labels and player-entered names verbatim.

## Consequences

Players get the selected language without installing translation tools. Developer diagnostics can use English. New locale work must cover plural rules, placeholders and layout at realistic caption lengths. Editing a PO requires regenerating its checked-in Lua overlay.

[Resolver](../../../src/PaintManager/Scripts/paintmanager/i18n.lua) · [Catalogs](../../../translations) · [Compiler tests](../../../tests/test_gettext_catalog.py)
