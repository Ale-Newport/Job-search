import assert from "node:assert/strict";
import { test } from "node:test";
import { STRINGS, t } from "../../src/shared/i18n-core.ts";
import { defaultStartShortcut, isMacPlatform, startKeyRow } from "../../src/shared/platform.ts";

test("CPL-18: the start line of the key list follows what the browser really offers", () => {
  // Chrome with a shortcut: print the one the browser reports (the person may have changed it)
  assert.deepEqual(startKeyRow(false, "⌘⇧Y"), { key: "shortcut", text: "help.d.start" });
  assert.deepEqual(startKeyRow(false, "Ctrl+Shift+K"), { key: "shortcut", text: "help.d.start" });
  // Chrome with none set (removed, or another extension owns it): name the icon, and say what the default was
  assert.deepEqual(startKeyRow(false, ""), { key: "icon", text: "help.d.start.unset" });
  // Safari: there is no such thing, so no key is printed and the page says so, whatever the browser reports
  assert.deepEqual(startKeyRow(true, ""), { key: "icon", text: "help.d.start.safari" });
  assert.deepEqual(startKeyRow(true, "⌘⇧Y"), { key: "icon", text: "help.d.start.safari" });
});

test("CPL-18: the Safari caveat and the unset-shortcut text read well in both languages", () => {
  for (const lang of ["en", "es"]) {
    const safari = t(lang, startKeyRow(true, "").text, { default: "⌘⇧Y" });
    assert.match(safari, /Safari/, lang);
    assert.doesNotMatch(safari, /\{|chrome:\/\//, `${lang}: nothing unfilled, no Chrome page`);
    const unset = t(lang, startKeyRow(false, "").text, { default: defaultStartShortcut(true) });
    assert.match(unset, /⌘⇧Y/, `${lang}: says what the default was on a Mac`);
    assert.match(t(lang, "help.d.start.unset", { default: defaultStartShortcut(false) }), /Ctrl\+Shift\+Y/, `${lang}: and elsewhere`);
    assert.ok(STRINGS[lang]["help.k.start"].length > 3, lang);
  }
});

test("the default start shortcut is the one the manifest declares", async () => {
  const { readFileSync } = await import("node:fs");
  const manifest = JSON.parse(readFileSync(new URL("../../manifest.template.json", import.meta.url), "utf8"));
  const suggested = manifest.commands._execute_action.suggested_key;
  assert.equal(suggested.mac, "Command+Shift+Y");
  assert.equal(suggested.default, "Ctrl+Shift+Y");
  assert.equal(defaultStartShortcut(true), "⌘⇧Y");
  assert.equal(defaultStartShortcut(false), "Ctrl+Shift+Y");
});

test("isMacPlatform reads the platform without throwing where there is no navigator", () => {
  assert.equal(typeof isMacPlatform(), "boolean");
  const saved = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  try {
    Object.defineProperty(globalThis, "navigator", { value: { platform: "MacIntel", userAgent: "x" }, configurable: true });
    assert.equal(isMacPlatform(), true);
    Object.defineProperty(globalThis, "navigator", { value: { userAgentData: { platform: "macOS" } }, configurable: true });
    assert.equal(isMacPlatform(), true);
    Object.defineProperty(globalThis, "navigator", { value: { platform: "Linux x86_64", userAgent: "x11" }, configurable: true });
    assert.equal(isMacPlatform(), false);
    Object.defineProperty(globalThis, "navigator", { value: undefined, configurable: true });
    assert.equal(isMacPlatform(), false);
  } finally {
    if (saved) Object.defineProperty(globalThis, "navigator", saved);
    else delete globalThis.navigator;
  }
});
