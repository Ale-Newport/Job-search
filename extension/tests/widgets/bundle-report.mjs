// What the widget adapters cost in the PRODUCTION content script, and proof that nothing of the test suite ships.
//
//   node extension/tests/widgets/bundle-report.mjs [--json]      (from any directory)
//
// Bundles src/content/index.ts exactly like build.mjs does for the `chrome` variant (same esbuild options and defines, a metafile
// on) into memory and reports: the size of content.js, the bytes contributed by fill.ts and by src/content/widgets/, every input
// outside extension/src (only the shared observation.js may appear), and any marker of a test hook, fixture or rig in the output.
// Exit code 1 on a violation. esbuild names inputs relative to its working directory (and writes those names into the bundle as
// comments), so the working directory is pinned to extension/, which is also where `npm --prefix extension run build` runs it:
// the report is the same from every caller's directory.
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const repo = resolve(root, "..");

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

const result = await build({
  absWorkingDir: root, entryPoints: [join(root, "src/content/index.ts")], bundle: true, format: "iife", target: "chrome120", minifySyntax: true, write: false,
  metafile: true, outfile: "content.js", logLevel: "silent", plugins: [observation],
  define: { __E2E__: "false", __SHADOW__: JSON.stringify("closed"), __SAFARI__: "false" },
});
const output = result.outputFiles[0].text;
const meta = result.metafile.outputs["content.js"];
const bytesOf = (predicate) => Object.entries(meta.inputs).filter(([path]) => predicate(path)).reduce((sum, [, info]) => sum + info.bytesInOutput, 0);

const report = {
  content_js_bytes: Buffer.byteLength(output),
  fill_ts_bytes: bytesOf((p) => p.endsWith("src/content/fill.ts")),
  widgets_dir_bytes: bytesOf((p) => p.includes("src/content/widgets/")),
  widget_adapters: Object.keys(meta.inputs).filter((p) => p.includes("src/content/widgets/")).map((p) => relative(root, resolve(root, p))),
  outside_src: Object.keys(meta.inputs).filter((p) => !p.startsWith("src/") && !p.startsWith("meridian-observation:") && !p.includes("observation")),
  test_markers: [],
};
const MARKERS = [/__mk_(?:req|res|deliver|call)/, /__meridianShim/, /widgets-e2e/, /tests\/widgets/, /react_select\.html/, /__meridianUiLog/, /__meridianUiEmit/, /__MERIDIAN_HEADLESS_UI__/, /__e2e/, /e2e:seed/];
for (const marker of MARKERS) if (marker.test(output)) report.test_markers.push(String(marker));
report.adapters_used_at_runtime = /react-select/i.test(output) || /aria-autocomplete/.test(output);
const problems = [];
if (report.outside_src.length) problems.push(`inputs outside extension/src: ${report.outside_src.join(", ")}`);
if (report.test_markers.length) problems.push(`test markers in the production content script: ${report.test_markers.join(", ")}`);
if (report.widget_adapters.some((p) => /fixtures|tests/.test(p))) problems.push("a fixture or test file is bundled as an adapter");
report.problems = problems;
console.log(process.argv.includes("--json") ? JSON.stringify(report) : JSON.stringify(report, null, 2));
process.exit(problems.length ? 1 : 0);
