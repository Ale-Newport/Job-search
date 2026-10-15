// Build the Meridian Assistant extension.
//   node build.mjs --variant=chrome|safari|e2e|e2e-closed|e2e-top [--out=dist/<variant>]
// The page scanner is backend/jobagent/automation/observation.js bundled verbatim (single source of truth).
import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { STAMP, treeDigest } from "./scripts/tree-digest.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "..");
const arg = (name, fallback) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split("=")[1] ?? fallback;
const variant = arg("variant", "chrome");
const VARIANTS = {
  chrome: { e2e: false, shadow: "closed", loopback: ["http://127.0.0.1/*"], key: true, safari: false },
  safari: { e2e: false, shadow: "closed", loopback: ["http://127.0.0.1/*"], key: false, safari: true },
  e2e: { e2e: true, shadow: "open", loopback: ["http://127.0.0.1/*", "http://localhost/*"], key: true, safari: false },
  "e2e-closed": { e2e: true, shadow: "closed", loopback: ["http://127.0.0.1/*", "http://localhost/*"], key: true, safari: false },
  "e2e-top": { e2e: true, shadow: "open", loopback: ["http://127.0.0.1/*"], key: true, safari: false },
};
const cfg = VARIANTS[variant];
if (!cfg) throw new Error(`unknown variant ${variant}`);
const out = resolve(here, arg("out", `dist/${variant === "chrome" ? "chrome" : variant}`));
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const observation = {
  name: "meridian-observation",
  setup(b) {
    b.onResolve({ filter: /^meridian-observation$/ }, () => ({ path: "observation", namespace: "meridian-observation" }));
    b.onLoad({ filter: /.*/, namespace: "meridian-observation" }, () => ({
      contents: `export default (${readFileSync(join(repo, "backend/jobagent/automation/observation.js"), "utf8").trim()});`,
      loader: "js",
    }));
  },
};

const entries = [
  ["background", "src/background/index.ts"],
  ["content", "src/content/index.ts"],
  ["hint", "src/hint/index.ts"],
  ["options", "src/options/index.ts"],
];
for (const [name, file] of entries) {
  if (!existsSync(join(here, file))) { console.warn(`skip ${name}: ${file} missing`); continue; }
  await build({
    entryPoints: [join(here, file)], outfile: join(out, `${name}.js`), bundle: true, format: "iife", target: "chrome120",
    minifySyntax: true, sourcemap: false, logLevel: "warning", plugins: [observation],
    define: { __E2E__: String(cfg.e2e), __SHADOW__: JSON.stringify(cfg.shadow), __SAFARI__: String(cfg.safari) },
  });
}

for (const size of [16, 32, 48, 128]) {
  if (!existsSync(join(here, `icons/icon-${size}.png`))) {
    throw new Error(`icons/icon-${size}.png is missing: run .venv/bin/python extension/scripts/render_icons.py`);
  }
}

const tauri = JSON.parse(readFileSync(join(repo, "src-tauri/tauri.conf.json"), "utf8"));
const ats = JSON.parse(readFileSync(join(here, "ats-hosts.json"), "utf8")).matches;
const manifest = JSON.parse(readFileSync(join(here, "manifest.template.json"), "utf8"));
manifest.version = tauri.version;
manifest.host_permissions = [...new Set([...cfg.loopback, ...ats])];
manifest.content_scripts[0].matches = ats;
if (cfg.key) manifest.key = readFileSync(join(here, "manifest-key.txt"), "utf8").trim();
if (cfg.safari) {
  // Safari ignores commands before 26 and users cannot rebind them there: the toolbar button is the one entry point.
  delete manifest.commands;
  manifest.browser_specific_settings = { safari: { strict_min_version: "18.0" } };
} else {
  manifest.minimum_chrome_version = "120";
}
writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
for (const dir of ["_locales", "icons"]) if (existsSync(join(here, dir))) cpSync(join(here, dir), join(out, dir), { recursive: true });
for (const file of ["options.html", "options.css"]) {
  if (existsSync(join(here, "src/options", file))) cpSync(join(here, "src/options", file), join(out, file));
}
if (cfg.e2e) {
  writeFileSync(join(out, "e2e.html"), '<!doctype html><meta charset="utf-8"><title>meridian e2e</title><script src="e2e.js"></script>');
  writeFileSync(join(out, "e2e.js"), "window.send = (m) => chrome.runtime.sendMessage(m);\n");
}
if (variant === "chrome") {
  // The copy a browser has loaded compares this with the one Meridian advertises, so a rebuild whose version number did not
  // change is still noticed. Written last: it covers everything else in the folder. Only the chrome build is installed by
  // Meridian, so the Safari and test builds carry no stamp and are never compared.
  writeFileSync(join(out, STAMP), JSON.stringify({ digest: treeDigest(out, [STAMP]) }) + "\n");
}
console.log(JSON.stringify({ built: variant, out, version: manifest.version }));
