# 0004. Publish an account library and mirror it into ordinary saves

**Status** Accepted

## Context

Presets, favorites and settings should be available across the current account's local saves. Each loaded save has its own player progress, and the game controls when that progress reaches disk.

## Decision

Resolve the native account and save location after the loaded context is ready. Reconcile versioned records from the account-local `PaintManager.library.json` and the vanilla HUD key `PaintManager.State`. Publish and verify the local file before staging the same document in the active game state. Use stable preset IDs, per-record timestamps and persistent deletion markers during reconciliation.

## Consequences

The ordinary game save persists its staged copy. Local success with a pending or uncertain mirror is a distinct result. Preserve an invalid or newer-format file for diagnosis, and check the source again before replacing it. A save on another machine can carry only the records it actually contains. Filesystem publication currently requires supported Windows paths.

[State schema](../../../src/PaintManager/Scripts/paintmanager/state_document.lua) · [Local publication](../../../src/PaintManager/Scripts/paintmanager/local_library.lua) · [Save coordinator tests](../../../tests/test_save_store.py)
