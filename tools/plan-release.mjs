// Only plan here. The checked release payload must pass on Windows before Git refs move.
import { execFileSync } from "node:child_process";
import { appendFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import semanticRelease from "semantic-release";

execFileSync("git", ["merge-base", "--is-ancestor", "v0.2.0", "HEAD"]);
const metadata = JSON.parse(readFileSync("mod.json", "utf8"));
rmSync("dist/release-plan.json", { force: true });
const result = await semanticRelease({ dryRun: true, ci: false });
if (result) {
  const { lastRelease, nextRelease } = result;
  if (lastRelease.version !== metadata.version) {
    throw new Error("The last stable tag and the current mod version differ");
  }
  mkdirSync("dist", { recursive: true });
  writeFileSync("dist/release-plan.json", JSON.stringify({
    version: nextRelease.version,
    notes: nextRelease.notes,
    base: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  }));
}
if (process.env.GITHUB_OUTPUT) {
  appendFileSync(process.env.GITHUB_OUTPUT, `tag=${result ? result.nextRelease.gitTag : ""}\n`);
}
