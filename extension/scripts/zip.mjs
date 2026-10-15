// Packs dist/chrome into dist/meridian-assistant-chrome.zip, the file the Chrome Web Store (or "Load unpacked" users) take.
//   node scripts/zip.mjs [--source=dist/chrome] [--target=dist/meridian-assistant-chrome.zip]   (`npm run zip` builds and checks first)
// Deterministic: sorted entries, no extra attributes, no directory entries, nothing hidden.
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name, fallback) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split("=").slice(1).join("=") ?? fallback;
const source = resolve(root, arg("source", "dist/chrome"));
const target = resolve(root, arg("target", "dist/meridian-assistant-chrome.zip"));

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (name.startsWith(".")) return [];
    return statSync(path).isDirectory() ? walk(path) : [relative(source, path)];
  });
}

if (!existsSync(join(source, "manifest.json"))) throw new Error(`${relative(root, source)} is missing: run npm run build`);
rmSync(target, { force: true });
const files = walk(source).sort();
execFileSync("zip", ["-X", "-D", "-q", "-9", target, "-@"], { cwd: source, input: `${files.join("\n")}\n` });
console.log(JSON.stringify({ zip: relative(root, target), files: files.length, bytes: statSync(target).size }));
