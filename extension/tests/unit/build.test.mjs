import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { atsPatterns } from "../../src/background/activation.ts";
import { matchesAnyPattern } from "../../src/shared/hosts.ts";
import { installChrome } from "./chrome-mock.mjs";
import { STAMP, treeDigest } from "../../scripts/tree-digest.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const repo = join(root, "..");
const VARIANTS = ["chrome", "safari", "e2e", "e2e-closed", "e2e-top"];
const ats = JSON.parse(readFileSync(join(root, "ats-hosts.json"), "utf8")).matches;
const tauri = JSON.parse(readFileSync(join(repo, "src-tauri/tauri.conf.json"), "utf8"));
let tmp;
const dist = (variant) => join(tmp, variant);
const manifest = (variant) => JSON.parse(readFileSync(join(dist(variant), "manifest.json"), "utf8"));
const text = (variant, file) => readFileSync(join(dist(variant), file), "utf8");
const run = (script, args) => spawnSync(process.execPath, [join(root, script), ...args], { encoding: "utf8" });

before(() => {
  tmp = mkdtempSync(join(tmpdir(), "meridian-ext-build-"));
  for (const variant of VARIANTS) {
    execFileSync(process.execPath, [join(root, "build.mjs"), `--variant=${variant}`, `--out=${dist(variant)}`], { stdio: "pipe" });
  }
});
after(() => rmSync(tmp, { recursive: true, force: true }));

test("every variant builds with the version of the desktop app", () => {
  for (const variant of VARIANTS) assert.equal(manifest(variant).version, tauri.version, variant);
});

test("the chrome build carries a stamp with the digest of everything else in it; the other builds are never compared and carry none", () => {
  const stamp = JSON.parse(text("chrome", STAMP));
  assert.match(stamp.digest, /^[0-9a-f]{64}$/);
  assert.equal(stamp.digest, treeDigest(dist("chrome"), [STAMP]), "the stamp covers the whole folder except itself");
  assert.notEqual(stamp.digest, treeDigest(dist("chrome")), "...which is why it must be left out");
  for (const variant of ["safari", "e2e", "e2e-closed", "e2e-top"]) assert.equal(existsSync(join(dist(variant), STAMP)), false, `${variant} has no stamp`);
});

test("a rebuild with the same version number but different content gets a different stamp; the same sources give the same one", () => {
  const first = join(tmp, "stamp-1");
  const second = join(tmp, "stamp-2");
  for (const out of [first, second]) execFileSync(process.execPath, [join(root, "build.mjs"), "--variant=chrome", `--out=${out}`], { stdio: "pipe" });
  assert.equal(readFileSync(join(first, STAMP), "utf8"), readFileSync(join(second, STAMP), "utf8"), "deterministic");
  writeFileSync(join(second, "options.css"), `${readFileSync(join(second, "options.css"), "utf8")}\n/* a change */`);
  assert.equal(JSON.parse(readFileSync(join(second, "manifest.json"), "utf8")).version, JSON.parse(readFileSync(join(first, "manifest.json"), "utf8")).version);
  assert.notEqual(treeDigest(second, [STAMP]), JSON.parse(readFileSync(join(second, STAMP), "utf8")).digest, "the stale stamp no longer matches the tree");
  assert.notEqual(treeDigest(second, [STAMP]), JSON.parse(readFileSync(join(first, STAMP), "utf8")).digest);
});

test("the stamp does not break the production check", () => {
  const result = run("scripts/check-prod-build.mjs", [`--chrome=${dist("chrome")}`, "--only=chrome"]);
  assert.equal(result.status, 0, result.stderr);
});

test("production check passes on the chrome and safari bundles", () => {
  const result = run("scripts/check-prod-build.mjs", [`--chrome=${dist("chrome")}`, `--safari=${dist("safari")}`]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /passed: chrome, safari/);
});

test("production check fails on every test variant, so a test build can never be shipped by mistake", () => {
  for (const variant of ["e2e", "e2e-closed", "e2e-top"]) {
    const result = run("scripts/check-prod-build.mjs", [`--chrome=${dist(variant)}`, "--only=chrome"]);
    assert.equal(result.status, 1, variant);
    assert.match(result.stderr, /test hook|test or debug file/, variant);
  }
});

test("production check catches a tampered build", () => {
  const copy = join(tmp, "tampered");
  execFileSync(process.execPath, [join(root, "build.mjs"), "--variant=chrome", `--out=${copy}`], { stdio: "pipe" });
  const path = join(copy, "manifest.json");
  const broken = JSON.parse(readFileSync(path, "utf8"));
  broken.permissions.push("tabs");
  broken.host_permissions.push("https://*/*");
  writeFileSync(path, JSON.stringify(broken));
  const result = run("scripts/check-prod-build.mjs", [`--chrome=${copy}`, "--only=chrome"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /permissions must be exactly/);
  assert.match(result.stderr, /statically/);
});

test("production check catches page-facing scripts that carry credential code and APIs Safari lacks", () => {
  const copy = join(tmp, "credential-leak");
  execFileSync(process.execPath, [join(root, "build.mjs"), "--variant=chrome", `--out=${copy}`], { stdio: "pipe" });
  writeFileSync(join(copy, "content.js"), `${readFileSync(join(copy, "content.js"), "utf8")}\n;void ["meridian.connection", "meridian.pending.7"];`);
  writeFileSync(join(copy, "hint.js"), `${readFileSync(join(copy, "hint.js"), "utf8")}\n;chrome.storage.local.setAccessLevel;`);
  writeFileSync(join(copy, "background.js"), `${readFileSync(join(copy, "background.js"), "utf8")}\n;chrome.action.openPopup();`);
  const result = run("scripts/check-prod-build.mjs", [`--chrome=${copy}`, "--only=chrome"]);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /content\.js contains credential-handling code/);
  assert.match(result.stderr, /hint\.js contains credential-handling code/);
  assert.match(result.stderr, /content\.js reads the worker's own storage/, "the pending submit reaches the page scripts through ctx, never through storage");
  assert.match(result.stderr, /background\.js uses an API Safari lacks/);
  const worker = text("chrome", "background.js");
  assert.match(worker, /setAccessLevel/, "while the real worker is where that code belongs");
  assert.match(worker, /meridian\.pending\./, "and where the per-tab pending submit lives");
  for (const variant of ["chrome", "safari"]) assert.equal(/meridian\.pending\./.test(text(variant, "content.js")), false, `${variant}: content.js asks the worker`);
});

test("test hooks exist only in e2e builds (stripped from production by minifySyntax)", () => {
  for (const variant of ["chrome", "safari"]) {
    const background = text(variant, "background.js");
    for (const needle of ["__e2e", "e2e:seed", "meridian.e2e.ports", "seedE2e", "__meridianRoot"]) assert.equal(background.includes(needle), false, `${variant}: ${needle}`);
    assert.equal(existsSync(join(dist(variant), "e2e.html")), false);
  }
  for (const variant of ["e2e", "e2e-closed", "e2e-top"]) {
    assert.ok(text(variant, "background.js").includes("__e2e"), variant);
    assert.ok(existsSync(join(dist(variant), "e2e.html")), variant);
  }
});

test("the hint script cannot read extension storage and leaves no marker in production", () => {
  for (const variant of ["chrome", "safari"]) {
    const hint = text(variant, "hint.js");
    assert.equal(/chrome\.storage/.test(hint), false, `${variant}: the hint asks the worker`);
    assert.equal(hint.includes("data-meridian-hint"), false, `${variant}: no attribute a page script could look for`);
  }
  assert.ok(text("e2e", "hint.js").includes("data-meridian-hint"), "tests can find the pill");
});

test("the shadow root is closed in production and in the closed e2e variant, open in the others", () => {
  assert.match(text("chrome", "hint.js"), /mode: "closed"/);
  assert.match(text("safari", "hint.js"), /mode: "closed"/);
  assert.match(text("e2e-closed", "hint.js"), /mode: "closed"/);
  assert.match(text("e2e", "hint.js"), /mode: "open"/);
  assert.match(text("e2e-top", "hint.js"), /mode: "open"/);
});

test("manifests: chrome pins its id, safari drops Chrome-only keys, test builds add loopback only", () => {
  const chrome = manifest("chrome");
  const safari = manifest("safari");
  assert.equal(typeof chrome.key, "string");
  assert.equal(chrome.minimum_chrome_version, "120");
  assert.ok(chrome.commands._execute_action);
  assert.equal("key" in safari, false);
  assert.equal("commands" in safari, false);
  assert.equal(safari.browser_specific_settings.safari.strict_min_version, "18.0");
  assert.deepEqual(chrome.permissions, ["activeTab", "scripting", "storage", "alarms"]);
  assert.deepEqual(chrome.optional_host_permissions, ["https://*/*", "http://*/*"]);
  assert.ok(manifest("e2e").host_permissions.includes("http://localhost/*"));
  assert.equal(manifest("e2e-top").host_permissions.includes("http://localhost/*"), false);
  assert.equal(chrome.host_permissions.includes("http://localhost/*"), false);
  const withoutLoopback = (variant) => manifest(variant).host_permissions.filter((host) => !/127\.0\.0\.1|localhost/.test(host));
  for (const variant of VARIANTS) assert.deepEqual(withoutLoopback(variant), ats, variant);
  assert.equal(chrome.action.default_popup, undefined);
});

test("ATS hosts: one list drives host permissions, the hint script and the worker's runtime matching", () => {
  const built = manifest("chrome");
  assert.deepEqual(built.content_scripts[0].matches, ats);
  assert.ok(ats.every((pattern) => /^https:\/\/(\*\.)?[a-z0-9.-]+\/\*$/.test(pattern)), "https only, host-wide patterns only");
  assert.equal(new Set(ats).size, ats.length);
  const chrome = installChrome({ matches: built.content_scripts[0].matches });
  chrome.runtime.getManifest = () => built;
  const patterns = atsPatterns();
  assert.deepEqual(patterns, ats);
  const yes = [
    "https://boards.greenhouse.io/acme/jobs/1", "https://job-boards.greenhouse.io/acme", "https://jobs.lever.co/acme/123/apply",
    "https://jobs.ashbyhq.com/acme", "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/x", "https://careers.smartrecruiters.com/Acme",
    "https://apply.workable.com/acme/", "https://acme.teamtailor.com/jobs/1", "https://careers-acme.icims.com/jobs/1/job", "https://acme.taleo.net/careersection",
    "https://career5.successfactors.eu/career", "https://acme.bamboohr.com/careers/1", "https://jobs.jobvite.com/acme", "https://acme.recruitee.com/o/x",
    "https://acme.jobs.personio.de/job/1", "https://acme.breezy.hr/p/1", "https://www.comeet.com/jobs/acme/1", "https://acme.pinpointhq.com/postings/1",
    "https://acme.applytojob.com/apply/1", "https://ats.rippling.com/acme/jobs", "https://acme.dayforcehcm.com/CandidatePortal", "https://acme.eightfold.ai/careers",
    // regional variants of vendors that were already listed
    "https://job-boards.eu.greenhouse.io/acme/jobs/1", "https://boards.eu.greenhouse.io/acme", "https://jobs.eu.lever.co/acme/123/apply",
    // CPL-19: the systems UK and Spanish graduate schemes run on
    "https://hsbc.tal.net/vx/lang-en-GB/mobile-0/appcentre-ext/brand-4/candidate/jobboard/vacancy/1/adv/", "https://me.tal.net/idp/login",
    "https://bloomberg.avature.net/careers", "https://jpmc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1",
    "https://acme.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/X", "https://acme.csod.com/ats/careersite/search.aspx?site=1&c=acme",
    "https://recruiting.ultipro.com/ACM1000/JobBoard/abc/OpportunityDetail?opportunityId=1", "https://eyglobal.yello.co/jobs/abc",
    "https://www.jobtrain.co.uk/iomgovjobs/client", "https://jobtrain.co.uk/", "https://fusion.webrecruit.co.uk/", "https://app.beapplied.com/apply/abc",
    "https://join.com/companies/acme/123-graduate-engineer",
  ];
  const no = [
    "http://boards.greenhouse.io/acme", "https://greenhouse.io.evil.com/", "https://evilgreenhouse.io/", "https://acme.com/careers",
    "https://rippling.com/", "https://www.rippling.com/jobs", "https://lever.co.attacker.net/", "https://example.com/?u=https://boards.greenhouse.io/",
    // lookalikes and neighbours of the new vendors
    "https://tal.net.evil.com/", "https://nottal.net/", "http://acme.tal.net/", "https://oraclecloud.com.evil.org/", "https://evil-oraclecloud.com/",
    "https://cloud.oracle.com/", "https://www.oracle.com/careers", "https://acme.csod.com.attacker.net/", "https://ultipro.com.evil.io/",
    "https://yello.co.uk/", "https://jobtrain.co.uk.evil.com/", "https://beapplied.com/", "https://www.beapplied.com/", "https://evil.beapplied.com/",
    "https://www.join.com/", "https://app.join.com/", "https://joint.com/", "https://acme.webrecruit.co/", "https://webrecruit.com/",
    // job boards that were looked at and deliberately left out (they keep the toolbar click)
    "https://www.infojobs.net/", "https://www.tecnoempleo.com/", "https://www.welcometothejungle.com/es/companies/acme", "https://app.welcometothejungle.com/jobs/abc",
  ];
  for (const url of yes) assert.equal(matchesAnyPattern(patterns, url), true, url);
  for (const url of no) assert.equal(matchesAnyPattern(patterns, url), false, url);
});

test("CPL-19: the curated list holds vendor domains only, every addition is explained, and nothing broad slipped in", () => {
  const listing = JSON.parse(readFileSync(join(root, "ats-hosts.json"), "utf8"));
  const wanted = ["tal.net", "avature.net", "oraclecloud.com", "csod.com", "ultipro.com", "yello.co", "jobtrain.co.uk", "webrecruit.co.uk", "app.beapplied.com", "join.com"];
  for (const domain of wanted) assert.ok(ats.some((pattern) => pattern === `https://${domain}/*` || pattern === `https://*.${domain}/*`), `${domain} is listed`);
  // never a bare TLD, a public suffix, a shared cloud or a job board
  const hosts = ats.map((pattern) => /^https:\/\/(?:\*\.)?([a-z0-9.-]+)\/\*$/.exec(pattern)?.[1]);
  assert.ok(hosts.every((host) => typeof host === "string" && host.split(".").length >= 2), "no single-label host");
  for (const forbidden of ["co.uk", "com", "net", "io", "ai", "amazonaws.com", "azurewebsites.net", "cloudfront.net", "oracle.com", "linkedin.com",
    "indeed.com", "infojobs.net", "tecnoempleo.com", "welcometothejungle.com", "glassdoor.com", "google.com", "github.io"]) {
    assert.ok(!hosts.includes(forbidden), `${forbidden} must not be on the list`);
  }
  assert.ok(ats.every((pattern) => !pattern.startsWith("*://") && !pattern.startsWith("http://") && !pattern.includes("://*/")), "https only");
  // each addition says what it is, and each one named in `added` is really on the list (the file cannot drift from its own notes)
  for (const pattern of Object.keys(listing.added)) assert.ok(ats.includes(pattern), `${pattern} is in "added" but not in "matches"`);
  for (const pattern of ["https://*.tal.net/*", "https://*.oraclecloud.com/*", "https://join.com/*"]) assert.ok(listing.added[pattern]?.length > 20, pattern);
  assert.ok(listing.notAdded["infojobs.net, tecnoempleo.com, welcometothejungle.com"], "the job boards that were left out say why");
  assert.deepEqual(ats, [...new Set(ats)], "no duplicates");
});

test("manifest strings exist in both locales and the toolbar title is localised", () => {
  const en = JSON.parse(readFileSync(join(root, "_locales/en/messages.json"), "utf8"));
  const es = JSON.parse(readFileSync(join(root, "_locales/es/messages.json"), "utf8"));
  assert.deepEqual(Object.keys(en).sort(), Object.keys(es).sort());
  for (const key of ["extName", "extDescription", "actionTitle"]) {
    assert.ok(en[key].message.length > 3 && es[key].message.length > 3);
    assert.notEqual(en[key].message, es[key].message);
  }
  assert.ok(en.extDescription.message.length <= 132, "store limit for the description");
  assert.ok(es.extDescription.message.length <= 132);
});

test("the zip holds exactly the production files, deterministically, with nothing hidden", () => {
  const target = join(tmp, "out.zip");
  const result = run("scripts/zip.mjs", [`--source=${dist("chrome")}`, `--target=${target}`]);
  assert.equal(result.status, 0, result.stderr);
  const listing = execFileSync("unzip", ["-Z1", target], { encoding: "utf8" }).trim().split("\n");
  const expected = [];
  const walk = (dir, prefix = "") => {
    for (const name of readdirSync(dir, { withFileTypes: true })) {
      if (name.isDirectory()) walk(join(dir, name.name), `${prefix}${name.name}/`);
      else expected.push(`${prefix}${name.name}`);
    }
  };
  walk(dist("chrome"));
  assert.deepEqual(listing, expected.sort());
  assert.ok(listing.includes("manifest.json"));
  assert.ok(listing.every((name) => !name.startsWith(".") && !name.includes("/.")));
  const again = join(tmp, "again.zip");
  run("scripts/zip.mjs", [`--source=${dist("chrome")}`, `--target=${again}`]);
  assert.equal(readFileSync(again).length, readFileSync(target).length);
});
