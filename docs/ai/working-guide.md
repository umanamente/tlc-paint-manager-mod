# Agent working guide

Read the root [AGENTS.md](../../AGENTS.md) before changing the project. Use the [code map](../maintainers/architecture.md) to trace each action from its UI entry point to the module that owns the result.

| Change | Read first | Verify |
| --- | --- | --- |
| Collection and ship range | `collection.lua`, `game_adapter.lua`, `ship_scope.lua` | Exercise unknown counts, unloading boxes, changing ships and moving across the boundary. |
| Recipes, paint moves or presets | `learn_recipe.lua`, `paint_transfer.lua` or `preset_workflow.lua`, plus their callers in `main.lua` | Check inventory identity before a write, the result after it and cancellation between operations. |
| Account library | `save_lifecycle.lua`, `save_store.lua`, `local_library.lua`, `state_document.lua` | Exercise old schema input, interrupted publication, stale account scope and reload. |
| Visible controls and text | `ui.lua`, the affected editor or deposit widget, and `locales/en_*.lua` | Check the English source, gettext output, placeholder names and a narrow viewport with longer translations. |
| Native call signature | [Native call inventory](../maintainers/native-call-contracts.json), owning Lua module and matching fixture | Check receiver, argument size and result semantics for the pinned build. Record what an actual game test still needs to establish. |

After a change, run the [offline checks](../maintainers/development.md#verify-a-checkout). When an operation has more than one native step, test what remains after the first step succeeds and the next fails. For a data migration, test the same input twice. The final report should distinguish observed game behavior, fixture results and pending manual checks.
