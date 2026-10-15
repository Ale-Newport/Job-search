// The content digest of a build: the same value as `tree_digest()` in backend/jobagent/extension/assets.py (a test compares the two).
//   sha256 over, for every shipped file in sorted order: its relative POSIX path, a NUL, and the sha256 of its bytes.
// Not shipped (so not hashed): dotfiles, *.map, __pycache__ and symlinks, exactly as the app's installer skips them. Entries are
// ordered like Python orders paths: component by component, not as one string ("a/b" comes before "a.txt").
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

/** File the chrome build writes last: `{"digest": treeDigest(dir, [STAMP])}`. The worker reads its own copy, the app advertises the bundled one. */
export const STAMP = "build.json";

function skipped(name, path) {
  return name.startsWith(".") || name.endsWith(".map") || name === "__pycache__" || lstatSync(path).isSymbolicLink();
}

function collect(root, parts, ignore, found) {
  const here = join(root, ...parts);
  for (const name of readdirSync(here)) {
    const path = join(here, name);
    if (skipped(name, path) || (parts.length === 0 && ignore.includes(name))) continue;
    if (lstatSync(path).isDirectory()) collect(root, [...parts, name], ignore, found);
    else found.push([...parts, name]);
  }
}

function compareParts(a, b) {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length - b.length;
}

/** `ignore` names top-level entries that are not part of the build (the stamp itself). */
export function treeDigest(root, ignore = []) {
  const found = [];
  collect(root, [], ignore, found);
  found.sort(compareParts);
  const digest = createHash("sha256");
  for (const parts of found) {
    digest.update(Buffer.concat([Buffer.from(parts.join("/")), Buffer.from([0])]));
    digest.update(createHash("sha256").update(readFileSync(join(root, ...parts))).digest());
  }
  return digest.digest("hex");
}
