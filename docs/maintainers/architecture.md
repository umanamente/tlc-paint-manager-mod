# Code map

UE4SS loads [main.lua](../../src/PaintManager/Scripts/main.lua). The entry point registers shortcuts and game hooks, observes the current player and ship, and advances work on game-thread callbacks. Modules under [Scripts/paintmanager](../../src/PaintManager/Scripts/paintmanager) own the individual jobs.

| Part | Representative modules | Responsibility |
| --- | --- | --- |
| Scheduling | `background.lua`, `work_budget.lua`, `processing.lua` | Advance bounded Lua jobs for the active view or in the background. Retire work whose context has changed. |
| Live reads | `game_adapter.lua`, `collection.lua`, `ship_scope.lua`, `paint_state.lua` | Read loaded game objects, select eligible boxes, distinguish installed colors, physical stock and recipe membership. |
| Catalog and stock | `paint_cache.lua`, `stock_cache.lua`, `paint_view.lua` | Reuse session catalog entries, maintain advisory box locations and prepare the list shown to the player. |
| Learning and transfers | `learn_recipe.lua`, `learn_all.lua`, `paint_transfer.lua`, `transfer_ledger.lua`, `deposit_similar.lua` | Prove the supply for a recipe or move, perform an explicit gameplay action and verify the outcome. Track original boxes for eligible returns. |
| Presets | `presets.lua`, `preset_workflow.lua`, `preset_supply.lua`, `preset_layout.lua` | Save a logical zone layout, compare it with the selected target, find the required cans and apply verified moves. |
| Persistence | `save_lifecycle.lua`, `save_store.lua`, `local_library.lua`, `save_bridge.lua`, `state_document.lua` | Establish the loaded account and save context, validate and merge records, publish the local library and stage the game mirror. |
| Interface | `ui.lua`, `ui_resources.lua`, `native_editor.lua`, `native_deposit_widget.lua` | Build and own the main UMG window and its resources. Attach controls to supported native editors and container menus. |
| Language | `languages.lua`, `i18n.lua`, `locales/` | Select a language, resolve stable message keys and fall back to English. |

### Follow one action

A **Learn** click starts with a row from the last completed collection. The coordinator routes the request to the learner. The learner resolves the current Vault, checks the backpack and contributing boxes for four matching cans, verifies recipe membership, asks the game to grant the recipe once, then queries membership again. Only a verified result is reported as learned.

### Repository layout

| Directory | Purpose |
| --- | --- |
| `src/PaintManager/` | Files used by the Lua mod. `Scripts/` contains the runtime and `Assets/UI/` the packaged images. |
| `artwork/` | Editable or source-quality assets and manifests used to regenerate the runtime art. |
| `translations/` | The gettext template and translated PO catalogs. |
| `tools/` | Python tooling for verification, assets, packaging and local deployment, plus the JavaScript release planner. |
| `tests/` | Offline Python tests, Lua fixtures, packaging checks and Conventional Commit tests. |
| `docs/gamers/`, `docs/ai/`, `docs/maintainers/` | Player instructions, agent workflow and developer references. |
| `.github/workflows/` | Automated checks and release jobs. |

## Active decisions

1. [Run Lua work cooperatively on the game thread](adr/0001-game-thread-scheduling.md)
2. [Verify live state before and after a gameplay change](adr/0002-live-state-before-gameplay-changes.md)
3. [Anchor stock to the ship and resolve preset targets by identity](adr/0003-ship-scope.md)
4. [Publish the account library locally and mirror it into ordinary saves](adr/0004-account-library-and-save-mirror.md)
5. [Translate mod-owned text through gettext catalogs](adr/0005-player-localization.md)
