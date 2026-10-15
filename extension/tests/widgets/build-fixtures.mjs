// Builds the real-widget demo pages: src/*.jsx (bundled demos) + src/*.html|css|js (page shells, hand-rolled replicas and
// shared scripts, copied unchanged) -> fixtures/.
//
//   cd extension/tests/widgets && npm install && node build-fixtures.mjs          (dependencies from ./node_modules)
//   node build-fixtures.mjs --modules=/some/other/node_modules [--only=react_select,mui]
//
// esbuild comes from extension/node_modules (the extension's own devDependency). The output (HTML shells, one ES module per
// demo and the shared chunks under fixtures/chunks/) is committed; node_modules is not. Pages never talk to the network.
import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const arg = (name, fallback) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split("=").slice(1).join("=") ?? fallback;
const modules = resolve(arg("modules", join(here, "node_modules")));
const only = arg("only", "").split(",").filter(Boolean);
const src = join(here, "src");
const out = join(here, "fixtures");
if (!existsSync(join(modules, "react"))) throw new Error(`dependencies missing in ${modules}: run npm install in extension/tests/widgets`);

mkdirSync(out, { recursive: true });
if (!only.length) rmSync(join(out, "chunks"), { recursive: true, force: true });

const entries = readdirSync(src).filter((f) => f.endsWith(".jsx") && !f.startsWith("_")).filter((f) => !only.length || only.includes(f.replace(/\.jsx$/, "")));
await build({
  entryPoints: entries.map((f) => join(src, f)), outdir: out, bundle: true, splitting: true, format: "esm", target: "chrome120",
  entryNames: "[name]", chunkNames: "chunks/[name]-[hash]", minify: true, jsx: "automatic", logLevel: "warning", legalComments: "none",
  nodePaths: [modules], absWorkingDir: here,
  define: {
    "process.env.NODE_ENV": '"production"', __VUE_OPTIONS_API__: "true", __VUE_PROD_DEVTOOLS__: "false", __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: "false",
  },
  alias: { vue: join(modules, "vue/dist/vue.esm-bundler.js") },
  loader: { ".webp": "empty", ".png": "empty", ".svg": "empty", ".woff": "empty", ".woff2": "empty" },
});
for (const name of readdirSync(src)) {
  if (!name.startsWith("_") && /\.(html|css|js)$/.test(name) && statSync(join(src, name)).isFile() && !entries.includes(name)) cpSync(join(src, name), join(out, name));
}
console.log(JSON.stringify({ built: entries.map((f) => f.replace(/\.jsx$/, "")), out }));
