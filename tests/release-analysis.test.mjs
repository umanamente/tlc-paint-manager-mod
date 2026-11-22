import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { analyzeCommits } from "@semantic-release/commit-analyzer";
import { generateNotes } from "@semantic-release/release-notes-generator";

const config = JSON.parse(readFileSync(new URL("../.releaserc.json", import.meta.url)));
const options = Object.fromEntries(config.plugins);
const cwd = process.cwd();
const logger = { log() {} };
const types = [
  ["fix(ui): restore the selected row", "patch"],
  ["perf: reduce inventory queries", "patch"],
  ["revert: restore the previous paint order", "patch"],
  ["revert!: retire the old export format", "major"],
  ["feat(presets): export a preset", "minor"],
  ["feat!: replace the preset layout", "major"],
  ["refactor: update storage\n\nBREAKING CHANGE: existing presets need migration", "major"],
  ["docs: improve installation steps", null],
  ["chore(release): 0.2.0", null],
  ["test: add paint fixtures", null],
  ["ci: update test runner", null],
  ["refactor: rename a helper", null],
];

for (const [message, expected] of types) {
  test(message.split("\n")[0], async () => {
    const release = await analyzeCommits(options["@semantic-release/commit-analyzer"], {
      cwd, logger, commits: [{ hash: "1234567", message }],
    });
    assert.equal(release, expected);
  });
}

test("release notes cover user-visible features", async () => {
  const notes = await generateNotes(options["@semantic-release/release-notes-generator"], {
    cwd, logger,
    options: { repositoryUrl: "https://github.com/example/paintmanager" },
    commits: [{ hash: "12345678901234567890", message: "feat: export a preset" }],
    lastRelease: { gitTag: "v0.2.0", version: "0.2.0" },
    nextRelease: { gitTag: "v0.3.0", version: "0.3.0" },
  });
  assert.match(notes, /export a preset/);
});
