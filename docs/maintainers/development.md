# Development

Use Python 3.12 and PowerShell on Windows. The offline toolchain runs Lua 5.4 through Lupa. It reads game-facing contracts from the repository and uses fixtures for Unreal objects.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Verify a checkout

Run these commands from the repository root. The first command checks Lua syntax and generated player-language catalogs. The tests exercise module behavior and integration paths. Packaging checks the runtime images and writes the versioned ZIP and SHA-256 manifest under `dist/`.

```powershell
.\.venv\Scripts\python.exe tools/manage.py check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/manage.py package
```

For faster iteration, add a file pattern to discovery, for example `-p test_learn_recipe.py`. After an artwork change, check both generators with `tools/build_ui_assets.py --check` and `tools/build_signal_art.py --check` using the same interpreter. Run the corresponding generator without `--check` to update the checked-in outputs.

The [CI workflow](../../.github/workflows/ci.yml) defines the automatic checks. The [release workflow](../../.github/workflows/release.yml) prepares distribution artifacts.

## Translate player text

1. Add an English string under a stable key in `src/PaintManager/Scripts/paintmanager/locales/en_ui.lua` or `en_main.lua`. Precede each player-facing key with a `-- TRANSLATORS:` usage comment. Use named placeholders and describe their meaning. Declare a plural selector for counts that affect grammar.
2. Run `tools/localization.py --template` with the virtual environment's Python. In Poedit, update each affected PO from `translations/PaintManager.pot`, translate the new or changed entries and review fuzzy matches.
3. To add a language, register its culture tag and integer plural rule in `tools/localization.py`. Use the same tag in the PO filename and its `Language` header. Translate `ui.settings.language_name` into the language's own name for the Settings selector. Add regional aliases in `languages.lua` or plural rules in the compiler and `i18n.lua` when the new language needs them.
4. Run `tools/localization.py` to regenerate the Lua overlays and the language index used by Settings. Run `tools/manage.py check` and the gettext tests to verify the generated text, placeholders and plural forms. The [player guide](../gamers/guide.md#language) lists the supported languages and regional variants.

## Try a local build

For a source-managed development install, close the game. Run `tools/manage.py doctor` and then `tools/manage.py deploy --dry-run` with the same Python interpreter. Review the game directory and intended changes. Use `tools/manage.py deploy --apply` when ready to install. The tool checks ownership and backs up files under `local/deploy-backups/`.

Use `--game-root` with either command to select the game directory containing `Voyage.exe` explicitly. For a manual ZIP installation, follow the [player instructions](../gamers/installation.md). See [testing](testing.md) for real-game checks and the [release guide](releases.md) for publishing.
