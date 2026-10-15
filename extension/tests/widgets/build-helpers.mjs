// Bundles the helpers the widget scenarios call DIRECTLY inside a page (src/content/visibility.ts, sensitive.ts and the Scanner of scan.ts)
// into one IIFE that sets window.__vis / window.__sens / window.__scan. Test-only: nothing here ships.
//   node tests/widgets/build-helpers.mjs <out-file> [<extension dir>]
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(process.argv[2]);
const ext = resolve(process.argv[3] ?? resolve(here, "../.."));
// the page observer is bundled verbatim, like in build.mjs
const observation = {
  name: "meridian-observation",
  setup(b) {
    b.onResolve({ filter: /^meridian-observation$/ }, () => ({ path: "observation", namespace: "meridian-observation" }));
    b.onLoad({ filter: /.*/, namespace: "meridian-observation" }, () => ({
      contents: `export default (${readFileSync(join(ext, "..", "backend/jobagent/automation/observation.js"), "utf8").trim()});`,
      loader: "js",
    }));
  },
};
await build({
  stdin: {
    contents: [
      'import * as v from "./src/content/visibility.ts"; import * as s from "./src/content/sensitive.ts";',
      'import { Scanner } from "./src/content/scan.ts"; import { RootRegistry } from "./src/content/roots.ts";',
      "window.__vis = v; window.__sens = s; window.__scan = { Scanner, RootRegistry };",
    ].join("\n"),
    resolveDir: ext, loader: "ts", sourcefile: "helpers.ts",
  },
  bundle: true, format: "iife", target: "chrome120", outfile: out, logLevel: "warning", plugins: [observation],
  define: { __E2E__: "true", __SAFARI__: "false", __SHADOW__: '"open"' },
});
console.log(JSON.stringify({ built: out }));
