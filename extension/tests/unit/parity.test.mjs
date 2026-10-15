// Safari / Chrome parity of the worker (docs/EXTENSION_DESIGN.md 7.1, 7.7): nothing the code does may depend on an API
// that one of the two browsers lacks, and what differs between them (badge colours, popups, access levels) degrades silently.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { PAIRING_ALARM, setWatchInterval, startPairing } from "../../src/background/pairing.ts";
import { applyBadge } from "../../src/shared/badge.ts";
import { world } from "./helpers.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "../../src");
function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (/\.(ts|html)$/.test(name)) yield path;
  }
}
/** The worker's, the shared and the extension pages' sources, comments removed (a comment may MENTION an API it avoids). */
const sources = ["background", "shared", "options", "hint"].flatMap((folder) => [...files(join(root, folder))])
  .map((path) => ({ path, text: readFileSync(path, "utf8").replace(/^\s*\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "") }));

test("no source of the worker, the shared code, the options page or the hint script uses an API Safari lacks", () => {
  const unsupported = /chrome\.(webNavigation|sidePanel|downloads|idle|notifications|offscreen|declarativeNetRequest|identity|debugger|tabCapture|system)\b|getContexts\b|sendNativeMessage|connectNative|openPopup\b|setUninstallURL|chrome\.runtime\.requestUpdateCheck|storage\.managed|chrome\.storage\.sync/;
  for (const { path, text } of sources) assert.doesNotMatch(text, unsupported, path);
});

test("every alarm fires no more often than every 30 seconds (the shortest period Chrome 120 and Safari accept)", () => {
  const found = [];
  for (const { path, text } of sources) {
    for (const call of text.matchAll(/alarms\.create\([^)]*\{([^}]*)\}/g)) {
      const minutes = [...call[1].matchAll(/(?:delayInMinutes|periodInMinutes)\s*:\s*([0-9.]+)/g)].map((match) => Number(match[1]));
      found.push({ path, minutes });
      assert.ok(minutes.length > 0 && minutes.every((value) => value >= 0.5), `${path}: ${minutes}`);
    }
    assert.doesNotMatch(text, /alarms\.create\([^,)]*\)/, `${path}: an alarm without a period is a one-off at an arbitrary time`);
  }
  assert.ok(found.length >= 1, "the pairing alarm exists");
});

test("the pairing alarm really is created at 30 seconds or more", async () => {
  const w = await world();
  setWatchInterval(0);
  try {
    await startPairing();
    const info = w.chrome.__log.alarms.get(PAIRING_ALARM);
    assert.ok(info.delayInMinutes >= 0.5 && info.periodInMinutes >= 0.5, JSON.stringify(info));
  } finally {
    setWatchInterval(1500);
    await w.close();
  }
});

test("badge colours, text colours and titles are cosmetic: a browser that lacks them, or refuses them, never breaks the click", async () => {
  const w = await world();
  try {
    const tab = w.chrome.__addTab("https://acme.example.com/apply");
    delete w.chrome.action.setBadgeTextColor; // Safari
    w.chrome.action.setBadgeBackgroundColor = () => {
      throw new Error("not implemented in this browser");
    };
    w.chrome.action.setTitle = async () => {
      throw new Error("no such tab");
    };
    await applyBadge(tab, "on");
    await applyBadge(tab, "attention");
    await applyBadge(tab, "hint");
    await applyBadge(tab, "off");
    assert.equal(w.chrome.__log.badges.get(tab).text, "", "the text itself still went through");
    delete w.chrome.action.setBadgeText;
    await applyBadge(tab, "on"); // not even a badge: still silent
    await applyBadge(-1, "on");
  } finally {
    await w.close();
  }
});

test("the worker never calls an optional API without testing for it first", () => {
  const optional = [/chrome\.permissions\.onAdded(?!\?)/, /chrome\.permissions\.onRemoved(?!\?)/];
  for (const { path, text } of sources) for (const pattern of optional) assert.doesNotMatch(text, pattern, path);
  const storage = sources.find((entry) => entry.path.endsWith("shared/storage.ts")).text;
  assert.match(storage, /typeof chrome\.storage\.local\.setAccessLevel === "function"/, "feature-detected, then awaited inside try");
});

test("nothing in the worker, the shared code or the pages logs: a credential can never reach a console by accident", () => {
  // shared/trace.ts is the one sanctioned console writer: it prints only booleans, numbers and short plain words (see its header)
  for (const { path, text } of sources) if (!/shared[\\/]trace\.ts$/.test(path)) assert.doesNotMatch(text, /\bconsole\.(log|info|warn|error|debug|trace|dir)\b/, path);
});
