import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeUiPrefs, STORAGE_VERSION, defaultPrefs, getConnection, getPrefs, migrateStorage, normalizeConnection, normalizePrefs, normalizeSettings, patchPrefs } from "../../src/shared/storage.ts";
import { installChrome } from "./chrome-mock.mjs";

const ID = "5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11";
const TOKEN = `mrd1.${ID}.Zm9yLXRlc3RzLW9ubHktc2VjcmV0LXZhbHVlLTEyMzQ`;
const GOOD = { port: 47615, clientId: ID, token: TOKEN, clientName: "Google Chrome", pairedAt: "2026-01-01T00:00:00.000Z" };

test("a credential is trusted only when its parts agree", () => {
  assert.deepEqual(normalizeConnection(GOOD), GOOD);
  for (const bad of [
    null, "x", {}, { ...GOOD, port: 0 }, { ...GOOD, port: 70000 }, { ...GOOD, port: 1.5 }, { ...GOOD, token: "mrd1.bad" },
    { ...GOOD, clientId: "00000000-0000-0000-0000-000000000000" }, { ...GOOD, token: undefined },
  ]) assert.equal(normalizeConnection(bad), null, JSON.stringify(bad));
  assert.equal(normalizeConnection({ ...GOOD, clientName: "x".repeat(200) }).clientName.length, 60);
  assert.equal(normalizeConnection({ ...GOOD, extra: "dropped" }).extra, undefined);
});

test("preferences are normalised defensively", () => {
  assert.deepEqual(normalizePrefs(undefined), defaultPrefs());
  assert.deepEqual(normalizePrefs("garbage"), defaultPrefs());
  const prefs = normalizePrefs({
    language: "ES", alwaysOn: ["Jobs.Acme.com", "https://x.org/path", "bad host", 5, "jobs.acme.com"], hintOff: "greenhouse.io", settings: { hint_on_ats: false, language: "fr" }, welcomed: "yes",
  });
  assert.equal(prefs.language, "es");
  assert.deepEqual(prefs.alwaysOn, ["jobs.acme.com", "x.org"]);
  assert.deepEqual(prefs.hintOff, ["greenhouse.io"]);
  assert.equal(prefs.settings.hint_on_ats, false);
  assert.equal(prefs.settings.language, "auto", "an unknown language falls back to auto");
  assert.equal(prefs.settings.skip_filled, true, "missing keys take their defaults");
  assert.equal(prefs.welcomed, false, "only a real boolean true counts");
  assert.equal(normalizeSettings([]), null);
  assert.equal(normalizeSettings({ draft_budget_usd: -3 }).draft_budget_usd, null);
  assert.equal(normalizeSettings({ draft_budget_usd: 2.5 }).draft_budget_usd, 2.5);
  assert.equal(normalizePrefs({ alwaysOn: Array.from({ length: 500 }, (_, i) => `h${i}.example.com`) }).alwaysOn.length, 200);
});

test("patchPrefs validates and serialises concurrent updates", async () => {
  installChrome();
  await Promise.all(Array.from({ length: 20 }, (_, i) => patchPrefs({ alwaysOn: [`a${i}.example.com`] })));
  assert.equal((await getPrefs()).alwaysOn.length, 1);
  await patchPrefs({ language: "xx", welcomed: true, bogus: 1 });
  const prefs = await getPrefs();
  assert.equal(prefs.language, "auto");
  assert.equal(prefs.welcomed, true);
  assert.equal("bogus" in prefs, false);
  await patchPrefs({ settings: { skip_filled: false } });
  assert.equal((await getPrefs()).settings.skip_filled, false);
  await patchPrefs({ settings: "not settings" });
  assert.equal((await getPrefs()).settings.skip_filled, false, "an invalid settings object keeps the previous one");
  await patchPrefs({ settings: null });
  assert.equal((await getPrefs()).settings, null);
});

test("migration: a fresh install is stamped, nothing is invented", async () => {
  const chrome = installChrome();
  assert.deepEqual(await migrateStorage(), { from: 0, to: STORAGE_VERSION });
  const all = await chrome.storage.local.get(null);
  assert.deepEqual(Object.keys(all), ["meridian.schema"]);
  assert.deepEqual(await migrateStorage(), { from: STORAGE_VERSION, to: STORAGE_VERSION }, "idempotent");
});

test("migration: legacy keys disappear, damaged values are repaired or dropped, good ones survive", async () => {
  const chrome = installChrome();
  await chrome.storage.local.set({
    apiBase: "http://127.0.0.1:8765", token: "meridian-development-token", "meridian.token": "x",
    "meridian.prefs": { language: "EN", alwaysOn: "acme.com", hintOff: ["", "x y"], settings: 7, welcomed: 1 },
    "meridian.connection": { port: 47615, clientId: "x", token: "broken" },
    unrelated: "kept",
  });
  await migrateStorage();
  const all = await chrome.storage.local.get(null);
  assert.equal("apiBase" in all, false);
  assert.equal("token" in all, false);
  assert.equal("meridian.token" in all, false);
  assert.equal("meridian.connection" in all, false, "an unusable credential is removed");
  assert.equal(all.unrelated, "kept");
  assert.deepEqual(all["meridian.prefs"], { language: "en", alwaysOn: ["acme.com"], hintOff: [], settings: null, welcomed: false });
  await chrome.storage.local.set({ "meridian.connection": GOOD });
  await chrome.storage.local.set({ "meridian.schema": 0 });
  await migrateStorage();
  assert.deepEqual(await getConnection(), GOOD, "a valid credential survives a migration");
});

test("migration never touches data written by a newer version", async () => {
  const chrome = installChrome();
  await chrome.storage.local.set({ "meridian.schema": 99, "meridian.prefs": { future: true }, apiBase: "x" });
  assert.deepEqual(await migrateStorage(), { from: 99, to: 99 });
  const all = await chrome.storage.local.get(null);
  assert.deepEqual(all["meridian.prefs"], { future: true });
  assert.equal(all.apiBase, "x");
  assert.equal(normalizePrefs(all["meridian.prefs"]).language, "auto", "readers still cope");
});

test("session storage falls back to memory where the API is missing (older Safari)", async () => {
  const chrome = installChrome();
  chrome.storage.session = undefined;
  const { sessionGet, sessionSet, sessionRemove } = await import("../../src/shared/storage.ts");
  await sessionSet({ k: 1 });
  assert.equal(await sessionGet("k"), 1);
  await sessionRemove("k");
  assert.equal(await sessionGet("k"), undefined);
});

test("the panel look is normalised: known dock sides, bounded dismissed map", () => {
  assert.deepEqual(normalizeUiPrefs(undefined), { dock: "right", dismissed: {} });
  assert.deepEqual(normalizeUiPrefs({ dock: "left", dismissed: { a: 1, "b c": 2, d: "x", e: Infinity } }), { dock: "left", dismissed: { a: 1 } });
  assert.equal(normalizeUiPrefs({ dock: "middle" }).dock, "right");
  assert.deepEqual(normalizeUiPrefs({ dismissed: [1, 2] }), { dock: "right", dismissed: {} });
});

test("migration keeps a credential that was sealed (it is not a plain one, and not damage either)", async () => {
  const chrome = installChrome();
  const sealed = {
    port: 47615, clientId: "5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11", clientName: "Safari", pairedAt: "2026-01-01T00:00:00.000Z",
    sealed: { v: 1, iv: "AAAAAAAAAAAAAAAA", ct: "Zm9yLXRlc3RzLW9ubHk" },
  };
  await chrome.storage.local.set({ "meridian.connection": sealed });
  await migrateStorage();
  assert.deepEqual((await chrome.storage.local.get("meridian.connection"))["meridian.connection"], sealed);
  await chrome.storage.local.set({ "meridian.schema": 0, "meridian.connection": { ...sealed, sealed: { v: 1, iv: "not base64!", ct: "x" } } });
  await migrateStorage();
  assert.equal((await chrome.storage.local.get("meridian.connection"))["meridian.connection"], undefined, "a damaged one is dropped, as before");
});
