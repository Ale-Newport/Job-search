import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const widgets = join(root, "tests", "widgets");
const notice = join(widgets, "THIRD_PARTY.md");

test("REPO-01: the widget fixtures' third-party notice exists and matches the lock file", () => {
  assert.ok(existsSync(notice), "extension/tests/widgets/THIRD_PARTY.md is missing: run node extension/scripts/third-party.mjs");
  const checked = spawnSync(process.execPath, [join(root, "scripts", "third-party.mjs"), "--check"], { encoding: "utf8" });
  assert.equal(checked.status, 0, checked.stderr);
});

test("REPO-01: every pinned library is listed with a version and a licence, and the vendored React keeps its own notice", () => {
  const text = readFileSync(notice, "utf8");
  const pkg = JSON.parse(readFileSync(join(widgets, "package.json"), "utf8"));
  const lock = JSON.parse(readFileSync(join(widgets, "package-lock.json"), "utf8"));
  for (const [name, version] of Object.entries(pkg.devDependencies)) {
    assert.match(text, new RegExp(`\\| \\[${name.replace(/[/@.]/g, "\\$&")}\\]\\(https://www\\.npmjs\\.com/package/[^)]+/v/${version.replace(/\./g, "\\.")}\\) \\| ${version.replace(/\./g, "\\.")} \\| [A-Za-z0-9.-]+ \\|`), `${name}@${version}`);
  }
  const packages = Object.keys(lock.packages).filter((key) => key !== "");
  assert.ok(packages.length > 100);
  assert.doesNotMatch(text, /not stated in the lock file|missing from the lock file/);
  // not one copyleft or unknown licence slipped in: the table of licences only has permissive ones
  const licences = [...text.matchAll(/^\| ([A-Za-z0-9.+-]+) \| \d+ \|$/gm)].map((match) => match[1]);
  assert.ok(licences.length >= 3);
  for (const licence of licences) assert.match(licence, /^(MIT|Apache-2\.0|BSD-[23]-Clause|ISC|0BSD)$/, licence);
  assert.match(text, /not part\s+of the Meridian Assistant extension/);
  assert.ok(existsSync(join(root, "tests", "fixtures", "vendor", "LICENSE-react.txt")));
  assert.match(text, /LICENSE-react\.txt/);
});

test("REPO-01: no test fixture or notice is part of a production build", () => {
  const build = readFileSync(join(root, "build.mjs"), "utf8");
  assert.doesNotMatch(build, /tests\/(widgets|fixtures)/, "build.mjs must never copy anything from tests/");
});
