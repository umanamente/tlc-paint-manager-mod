# 0002. Verify live state before and after a gameplay change

**Status** Accepted

## Context

A stock scan spans callbacks. A player can move a can, close a box, change ship or learn a recipe between a scan and a click. A native transfer can complete one part of a requested layout before a later part fails.

## Decision

Use cached stock and descriptors to find candidates. At the time of an explicit action, recheck current identity, membership, source items and destination capacity as appropriate. Grant a recipe once after verifying the four-can threshold and a locked recipe, then query membership again. Move real cans through the game's inventory operations and verify the resulting inventories and item identity.

## Consequences

Learning keeps the cans. A preset replacement returns the old can before equipping the new one. The receipt can therefore report a completed removal and an empty zone. Ambiguous native writes block automatic retries in that context. Tests need both success and partial outcomes.

[Recipe learner](../../../src/PaintManager/Scripts/paintmanager/learn_recipe.lua) · [Paint transfers](../../../src/PaintManager/Scripts/paintmanager/paint_transfer.lua) · [Learner tests](../../../tests/test_learn_recipe.py)
