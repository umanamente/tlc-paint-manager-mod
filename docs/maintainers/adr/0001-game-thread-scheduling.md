# 0001. Run Lua work cooperatively on the game thread

**Status** Accepted

## Context

Player objects, inventory slots and UMG widgets can change while a view is open. Large scans or widget builds performed in a single callback also delay input.

## Decision

Run the mod in UE4SS's Lua runtime. Schedule Unreal access on the game thread. Split collection, UI preparation and storage processing into bounded steps. Carry identifiers or plain data across steps and revalidate the active context on resumption. Prioritize work for a visible interface and cancel stale jobs.

## Consequences

The release ships Lua scripts and UI assets. Timing limits are soft because a single engine or filesystem call can run past its budget. A new job needs a progress boundary, a completion state and defined cancellation behavior. An owned live widget can retain its resources for as long as its native UI host owns it.

[Scheduler](../../../src/PaintManager/Scripts/paintmanager/background.lua) · [Work budget](../../../src/PaintManager/Scripts/paintmanager/work_budget.lua) · [Scheduler tests](../../../tests/test_background.py)
