// Release gate for the production bundles: no test hooks, no test hosts, an exact permission set, every file present.
//   node scripts/check-prod-build.mjs [--chrome=dist/chrome] [--safari=dist/safari] [--only=chrome|safari]
// Exits 1 and lists every violation. The unit tests run it against fresh builds, and against an e2e build to prove it fails.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const repo = resolve(root, "..");
const arg = (name, fallback) => process.argv.find((a) => a.startsWith(`--${name}=`))?.split("=").slice(1).join("=") ?? fallback;

const ATS = JSON.parse(readFileSync(join(root, "ats-hosts.json"), "utf8")).matches;
const TAURI_VERSION = JSON.parse(readFileSync(join(repo, "src-tauri/tauri.conf.json"), "utf8")).version;
const PERMISSIONS = ["activeTab", "scripting", "storage", "alarms"];
const OPTIONAL_HOSTS = ["https://*/*", "http://*/*"];
const BUNDLES = ["background.js", "content.js", "hint.js", "options.js"];
const FILES = [...BUNDLES, "manifest.json", "options.html", "options.css", "icons/icon-16.png", "icons/icon-32.png", "icons/icon-48.png", "icons/icon-128.png",
  "_locales/en/messages.json", "_locales/es/messages.json"];
/** Anything that exists only for tests. Matched against every shipped script. */
const TEST_HOOKS = [/__e2e/, /e2e:seed/, /e2e\.(html|js)/, /__E2E__/, /__SHADOW__/, /__SAFARI__/, /__meridianRoot/, /meridian\.e2e/, /seedE2e/];
/** Code patterns that must never ship in the extension's own scripts. */
const DANGEROUS = [/\beval\s*\(/, /new Function\s*\(/, /document\.write\s*\(/];
/** APIs Safari lacks (docs/EXTENSION_DESIGN.md 7.1): the shared sources must not depend on them anywhere. */
const UNSUPPORTED = [/chrome\.(webNavigation|sidePanel|downloads|idle|notifications|offscreen)\b/, /getContexts\b/, /sendNativeMessage|connectNative/, /openPopup\b/];
/** The scripts that run next to hostile pages must not even contain the code or the storage keys that handle the credential. */
const CREDENTIAL_CODE = [/meridian\.connection/, /meridian-vault/, /setAccessLevel/, /markProven|credential without a verified proof/];
/** The worker's per-tab store (the pending submit) is read by the worker only: content scripts get it through `ctx`. */
const WORKER_STORE_KEYS = [/meridian\.pending\./, /meridian\.tab\./];
const SECRETS = [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, /mrd1\.[0-9a-f]{8}-[0-9a-f-]{27}\.[A-Za-z0-9_-]{20,}/];

export function checkBuild(dir, variant) {
  const problems = [];
  const fail = (message) => problems.push(`${variant}: ${message}`);
  if (!existsSync(dir)) return [`${variant}: ${dir} does not exist (run npm run build)`];
  for (const file of FILES) if (!existsSync(join(dir, file))) fail(`missing ${file}`);
  const extras = readdirSync(dir).filter((name) => /^e2e\./.test(name) || name.endsWith(".map"));
  for (const name of extras) fail(`test or debug file shipped: ${name}`);

  let manifest = null;
  try {
    manifest = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
  } catch {
    fail("manifest.json is not valid JSON");
  }
  if (manifest) {
    const is = (condition, message) => condition || fail(message);
    is(manifest.manifest_version === 3, "manifest_version must be 3");
    is(manifest.version === TAURI_VERSION, `version ${manifest.version} must equal src-tauri/tauri.conf.json (${TAURI_VERSION})`);
    is(JSON.stringify(manifest.permissions) === JSON.stringify(PERMISSIONS), `permissions must be exactly ${PERMISSIONS.join(", ")} (got ${manifest.permissions})`);
    is(JSON.stringify(manifest.optional_host_permissions) === JSON.stringify(OPTIONAL_HOSTS), "optional_host_permissions must be the two all-sites patterns");
    const hosts = manifest.host_permissions ?? [];
    is(hosts.includes("http://127.0.0.1/*"), "host_permissions must include loopback http://127.0.0.1/*");
    is(!hosts.some((host) => /localhost/.test(host)), "host_permissions must not include localhost (test builds only)");
    is(!hosts.some((host) => /^(\*|https?):\/\/\*\/\*$|<all_urls>/.test(host)), "host_permissions must not grant all sites statically");
    const expected = ["http://127.0.0.1/*", ...ATS].sort();
    is(JSON.stringify([...hosts].sort()) === JSON.stringify(expected), "host_permissions must be loopback plus exactly the curated ATS hosts");
    const scripts = manifest.content_scripts ?? [];
    is(scripts.length === 1 && scripts[0].js?.length === 1 && scripts[0].js[0] === "hint.js", "exactly one manifest content script: hint.js");
    is(JSON.stringify(scripts[0]?.matches ?? []) === JSON.stringify(ATS), "hint.js matches must equal ats-hosts.json");
    is(scripts[0]?.all_frames === false, "hint.js must run in the top frame only");
    is(manifest.background?.service_worker === "background.js", "service worker must be background.js");
    is(!manifest.action?.default_popup, "no popup: the toolbar click is the entry point");
    is(manifest.options_ui?.page === "options.html" && manifest.options_ui?.open_in_tab === true, "options page opens in a tab");
    is(manifest.default_locale === "en", "default_locale en");
    is(!("externally_connectable" in manifest) && !("web_accessible_resources" in manifest), "nothing may be exposed to web pages");
    is(!("content_security_policy" in manifest), "keep the strict default content security policy");
    if (variant === "chrome") {
      is(typeof manifest.key === "string" && manifest.key.length > 100, "chrome build pins the extension id with `key`");
      is(manifest.minimum_chrome_version === "120", "chrome build declares minimum_chrome_version");
      is(Boolean(manifest.commands?._execute_action), "chrome build offers the keyboard shortcut");
      is(!manifest.browser_specific_settings, "chrome build has no Safari settings");
    } else {
      is(!("key" in manifest) && !("minimum_chrome_version" in manifest), "safari build has no Chrome-only keys");
      is(!("commands" in manifest), "safari build has no commands (not supported before Safari 26)");
      is(manifest.browser_specific_settings?.safari?.strict_min_version === "18.0", "safari build declares browser_specific_settings.safari");
    }
    const messages = {};
    for (const lang of ["en", "es"]) {
      try {
        messages[lang] = JSON.parse(readFileSync(join(dir, `_locales/${lang}/messages.json`), "utf8"));
      } catch {
        fail(`_locales/${lang}/messages.json unreadable`);
      }
    }
    for (const text of JSON.stringify(manifest).match(/__MSG_\w+__/g) ?? []) {
      const key = text.slice(6, -2);
      for (const lang of Object.keys(messages)) is(Boolean(messages[lang][key]?.message), `_locales/${lang} lacks ${key}`);
    }
    for (const size of [16, 32, 48, 128]) is(manifest.icons?.[String(size)] === `icons/icon-${size}.png`, `icon ${size} declared`);
  }
  for (const file of BUNDLES) {
    const path = join(dir, file);
    if (!existsSync(path)) continue;
    const text = readFileSync(path, "utf8");
    for (const pattern of TEST_HOOKS) if (pattern.test(text)) fail(`${file} contains a test hook (${pattern})`);
    for (const pattern of DANGEROUS) if (pattern.test(text)) fail(`${file} contains ${pattern}`);
    for (const pattern of UNSUPPORTED) if (pattern.test(text)) fail(`${file} uses an API Safari lacks (${pattern})`);
    for (const pattern of SECRETS) if (pattern.test(text)) fail(`${file} contains something that looks like a secret (${pattern})`);
    if (file === "content.js" || file === "hint.js") {
      for (const pattern of CREDENTIAL_CODE) if (pattern.test(text)) fail(`${file} contains credential-handling code (${pattern}): page-facing scripts never touch the credential`);
      for (const pattern of WORKER_STORE_KEYS) if (pattern.test(text)) fail(`${file} reads the worker's own storage (${pattern}): it gets that state through ctx`);
    }
    if (/sourceMappingURL/.test(text)) fail(`${file} references a source map`);
    if (file !== "content.js" && /\.innerHTML\s*=|insertAdjacentHTML|outerHTML\s*=/.test(text)) fail(`${file} assigns HTML (use text nodes)`);
    if (file === "hint.js" && /chrome\.storage|data-meridian-hint/.test(text)) fail("hint.js must not read extension storage nor mark its host element");
    if (file === "hint.js" && statSync(path).size > 16_000) fail(`hint.js is ${statSync(path).size} bytes: it must stay tiny`);
  }
  return problems;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const only = arg("only", "");
  const targets = [];
  if (only !== "safari") targets.push([resolve(root, arg("chrome", "dist/chrome")), "chrome"]);
  if (only !== "chrome") targets.push([resolve(root, arg("safari", "dist/safari")), "safari"]);
  const problems = targets.flatMap(([dir, variant]) => checkBuild(dir, variant));
  if (problems.length) {
    console.error(`production build check FAILED (${problems.length}):\n- ${problems.join("\n- ")}`);
    process.exit(1);
  }
  console.log(`production build check passed: ${targets.map(([, variant]) => variant).join(", ")}`);
}
