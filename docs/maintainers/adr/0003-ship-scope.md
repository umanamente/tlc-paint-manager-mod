# 0003. Anchor stock to the ship and resolve preset targets by identity

**Status** Accepted

## Context

Paint access and stock search need a predictable geographical boundary. A saved paint layout also needs to target the same ship or character throughout its application.

## Decision

Use the selected ship's native actor position as the anchor for both the window connection and eligible player boxes. The shared radius defaults to 35 m and can reach 50 m. Resolve a preset target using the current world and the identity of the selected ship or character. Once connected, reading, saving and applying a preset require no additional target-distance threshold.

## Consequences

A switch of ship or world invalidates the previous view. Applying a preset may still need physical cans from within the ship radius. A return to an original box can use a loaded, accessible box outside this radius when its identity still matches the recorded transfer.

[Ship scope](../../../src/PaintManager/Scripts/paintmanager/ship_scope.lua) · [Preset supply](../../../src/PaintManager/Scripts/paintmanager/preset_supply.lua) · [Scope tests](../../../tests/test_ship_scope.py)
