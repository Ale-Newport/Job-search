// Bundle the UI preview rig into a scratch directory: node build-rig.mjs --out=<dir>
// Three bundles: rig-open.js (open shadow root + e2e hooks, for DOM audits), rig-closed.js (production flags) and rig-safari.js
// (open, with the Safari flag: no toolbar shortcut exists there, so none is printed).
import { build } from "esbuild";
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(process.argv.find((a) => a.startsWith("--out="))?.split("=")[1] ?? "");
if (!out || out === "/") throw new Error("--out=<directory> is required");
mkdirSync(out, { recursive: true });

for (const [name, e2e, shadow, safari] of [["rig-open", true, "open", false], ["rig-closed", false, "closed", false], ["rig-safari", true, "open", true]]) {
  await build({
    entryPoints: [join(here, "rig.ts")], outfile: join(out, `${name}.js`), bundle: true, format: "iife", target: "chrome120",
    minifySyntax: true, sourcemap: false, logLevel: "warning", tsconfig: join(here, "tsconfig.json"),
    define: { __E2E__: String(e2e), __SHADOW__: JSON.stringify(shadow), __SAFARI__: String(safari) },
  });
}
for (const file of ["rig.html", "rig.css", "rig-loader.js", "hostile.css", "isolate.css", "frame-host.html"]) copyFileSync(join(here, file), join(out, file));
console.log(JSON.stringify({ built: out }));
