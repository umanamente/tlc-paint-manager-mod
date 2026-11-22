# Working on PaintManager

- Write code, comments and project documentation in English. Keep translated player text in the language catalogs. When wording or behavior changes, update the player guide and its relevant tests.
- Work from the installed entry point `src/PaintManager/Scripts/main.lua`. Preserve the `paintmanager.*` module names and the release layout when moving files.
- Run Unreal object access on the UE4SS game thread. Reacquire objects after yielding. Keep multi-step work bounded and carry only plain identifiers and observed data between steps.
- Treat a prior scan as a hint about where to look. Before changing a recipe, inventory slot or paint zone, verify the current context, source and destination. Check the result before marking the operation successful.
- Keep existing account files readable and respect the loaded save's restoration state. Cover migration, partial completion and retry behavior when changing persistence or inventory work.
- Keep saves, dumps, logs, account paths, credentials and generated output out of source. Use focused fixtures for regressions. Changes that need real game behavior should state the manual checks still required.

## Commit and pull request titles

Use `type(optional-scope): short description` for each commit subject and PR title. Write the player-visible outcome in plain English, for example `fix(ui): keep long settings labels visible` or `feat(presets): show the matching paint scheme in the editor`. Mark incompatible changes with `!` before the colon, as in `feat(storage)!: require the new preset import format`. Describe the migration in a `BREAKING CHANGE: ...` footer. Preserve the Conventional Commit subject on a squash merge. [Versioning and releases](docs/maintainers/releases.md)

Read the [agent working guide](docs/ai/working-guide.md) for task-specific entry points and the [development guide](docs/maintainers/development.md) for setup and checks.
