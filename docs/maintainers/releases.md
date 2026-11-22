# Releasing PaintManager

Each stable version has a Git tag, a GitHub Release, a Windows-built mod ZIP and a JSON manifest with SHA-256 hashes. Nexus distributes the ZIP attached to that GitHub Release.

## First release

1. After reviewing the public folder, create the empty GitHub repository, use `main` as its default branch and add its URL as `origin`. Push the reviewed snapshot and its existing local tag together with `git push --atomic origin main refs/tags/v0.2.0`.
2. Allow Actions to write release contents. If branch or tag rules govern releases, configure them to permit the release workflow to push its release commit to `main` and create stable tags. The workflow uses GitHub's built-in token for these operations.
3. In **Actions → Release → Run workflow**, choose branch `main` and enter `v0.2.0`. The runner tests that tag, builds `PaintManager-0.2.0.zip` and its `.manifest.json` on Windows, and publishes both assets after checking the download. Download the named mod ZIP from the GitHub Release.
4. Create the PaintManager page in the **The Last Caretaker** section of Nexus Mods. Add gameplay screenshots, the installation instructions, the tested UE4SS dependency, credits, permissions and the applicable AI disclosures. Upload the GitHub ZIP as the first **Main File**, with version `0.2.0`. Link the mod page and the public repository to each other. [Nexus upload action](https://github.com/Nexus-Mods/upload-action/blob/main/README.md) · [Nexus disclosure guidance](https://help.nexusmods.com/article/180-ai-policy-changes-2026)
5. Create a GitHub Actions environment named `nexus` for subsequent releases. Restrict its deployment branches to `main`. Put the author API key in the environment secret `NEXUSMODS_API_KEY`. Set the variable `NEXUSMODS_FILE_ID` to the v3 ID for that Main File. To send release notes to the Nexus changelog, also set `NEXUSMODS_MOD_ID`. Copy these IDs from **Files → Advanced** or **Manage Files → Edit** on Nexus. [Nexus input reference](https://github.com/Nexus-Mods/upload-action/blob/main/README.md#inputs)

## Following releases

Use Conventional Commits for changes on `main`. For squash merges, use the PR title as the commit title and preserve any breaking-change footer. The CI job checks the PR title and runs on Windows. After a successful push check on `main`, the release workflow analyses commits from the last stable tag.

| Change in the merged commits | Version change |
| --- | --- |
| `fix`, `perf`, `revert` | Patch |
| `feat` | Minor |
| `!` before the subject colon or a `BREAKING CHANGE:` footer | Major |
| Routine documentation, tests, CI, builds, style, chores and refactoring | No release |

The highest applicable change wins. The analyzer accounts for matching revert pairs. Keep `mod.json`, the Lua version, and the release headings in `CHANGELOG.md` under release automation. New player-facing behavior belongs in meaningful commit subjects.

1. The runner checks out the exact commit that passed CI. A newer `main` commit receives its own run.
2. The workflow uses semantic-release to calculate the next version and generate release notes. It writes the version to `mod.json` and the Lua config, adds a changelog section, checks translations and Lua, runs the tests, then builds the package.
3. A single atomic Git push advances `main` to the release commit and creates the tag. Concurrent changes to `main` cause the push to fail safely. GitHub receives a draft, the ZIP and its manifest. The workflow downloads and verifies the files before publishing the draft.
4. The Nexus job downloads the two GitHub assets, checks the ZIP against the manifest and reads the versions of the configured Nexus file. When `NEXUSMODS_MOD_ID` is set, the job reads the mod's changelog and preserves an existing entry for this version. For a missing entry, it sends the tagged release notes as one plain-text change per line and reads them back. The official upload action then publishes the ZIP and updates the mod version. [Nexus changelog API](https://api-docs.nexusmods.com/#tag/mods/operation/getModChangelogs)

   This order puts the notes on Nexus before the mod page receives the new file version. If Nexus explicitly rejects the early write with HTTP 422, the job publishes the file first and then reconciles the changelog. In 0.2.1 the upload finished before Nexus accepted the notes, and the website showed the previous changelog until an editor save. The API confirms the stored text. Nexus does not document when the website refreshes **Description → Changelogs**.

## Retry an existing tag

Choose **Actions → Release → Run workflow** on `main` and enter the existing stable tag. Use this when the tag exists but a GitHub or Nexus step failed. The runner verifies that the tag belongs to `main` and matches the mod version.

- A complete GitHub release reuses its uploaded assets. A missing asset is rebuilt from the tag on Windows, compared against any existing asset and uploaded. Retrying will also finish a verified draft. A hash mismatch requires inspection of the conflicting assets.
- When the Nexus version already exists, the workflow skips uploading it again. Nexus lists the version string in this check. To investigate conflicting payloads, download the Nexus file and compare its SHA-256 to the GitHub manifest. An existing newer stable version blocks an older upload.
- A retry uses the current workflow's tools and the selected tag's release notes. If only the changelog was saved, the retry keeps it and finishes the file upload. If only the file was saved, the retry can add the missing changelog. An uncertain response to a changelog write triggers read-only checks. If verification times out, inspect the version in **Manage → Documentation → Changelog** before retrying. Repeating the changelog POST appends text. [Nexus write semantics](https://api-docs.nexusmods.com/#tag/mods/operation/addModChangelogEntries)
- To add a missing GitHub release for the initial version, supply `v0.2.0`. Nexus setup for that tag is always manual.

## Verify release tooling locally

With the project Python environment active and Node.js 24.10 or later in the 24.x family, run `npm ci --ignore-scripts`, `npm test` and `python -m unittest discover -s tests`. `python tools/manage.py package` creates a local ZIP and manifest. Check the downloaded artifact on Windows with `Get-FileHash -Algorithm SHA256 .\PaintManager-0.2.0.zip`, substituting the selected release version.

On a configured clone tracking `origin/main`, `npm run release:plan` reads repository tags and checks push access using Git's dry-run mode. If a release is due, it writes `dist/release-plan.json`. The live publication steps run in the release workflow.
