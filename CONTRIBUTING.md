# Contributing

Start with the [development setup](docs/maintainers/development.md). The project uses Lua for the in-game mod and Python for verification and packaging. You can run the offline checks without owning a local game installation.

For a **bug report**, include the mod, game and UE4SS versions, exact steps, expected behavior and actual result. For storage or inventory problems, include the message shown in the window and which moves the game already completed. See the [player troubleshooting guide](docs/gamers/troubleshooting.md) for useful log details.

For a **code change**, describe the player-facing effect or the developer problem. Locate the owning module in the [code map](docs/maintainers/architecture.md), run the relevant tests, then follow the [verification checklist](docs/maintainers/testing.md). Mention any in-game checks you performed and those still pending in the pull request.

For a **translation**, edit the PO catalog and regenerate its runtime table. Give ambiguous labels an English usage note. The [development guide](docs/maintainers/development.md#translate-player-text) includes the commands.

For changes to persistent formats, native calls or release layout, document compatibility with existing data and installations. A change that introduces a lasting architectural choice belongs in the [decision index](docs/maintainers/architecture.md#active-decisions).

## Commit and pull request titles

Use Conventional Commits for **commit subjects and pull request titles**. Write `type(optional-scope): short description`. Describe the visible effect so the title can serve as a release note. Keep the PR title in this form when squash-merging.

| Example title | What it conveys |
| --- | --- |
| `fix(ui): keep long settings labels visible` | Corrects behavior a player can observe. |
| `feat(presets): show the matching paint scheme in the editor` | Adds a capability. |
| `feat(storage)!: require the new preset import format` | The exclamation mark flags a breaking change. |

The release process selects a patch for `fix`, `perf` or `revert`, a minor version for `feat`, and a major version for a breaking change. Explain a breaking change's impact and migration in a `BREAKING CHANGE: ...` footer in the commit body. Use `docs:`, `test:`, `ci:` or another appropriate type for maintenance work. See [versioning and releases](docs/maintainers/releases.md).
