import assert from "node:assert/strict";
import { test } from "node:test";
import { EXT_ID, installChrome } from "./chrome-mock.mjs";

test("the worker registers every listener synchronously and keeps no state of its own", async () => {
  const chrome = installChrome();
  const registered = [];
  for (const [name, target] of Object.entries({
    "action.onClicked": chrome.action.onClicked, "runtime.onMessage": chrome.runtime.onMessage, "runtime.onInstalled": chrome.runtime.onInstalled,
    "tabs.onUpdated": chrome.tabs.onUpdated, "tabs.onRemoved": chrome.tabs.onRemoved, "alarms.onAlarm": chrome.alarms.onAlarm,
    "permissions.onAdded": chrome.permissions.onAdded, "permissions.onRemoved": chrome.permissions.onRemoved,
  })) {
    const original = target.addListener;
    target.addListener = (fn) => {
      registered.push(name);
      original(fn);
    };
  }
  await import("../../src/background/index.ts");
  assert.deepEqual(registered.sort(), [
    "action.onClicked", "alarms.onAlarm", "permissions.onAdded", "permissions.onRemoved", "runtime.onInstalled", "runtime.onMessage",
    "tabs.onRemoved", "tabs.onUpdated",
  ]);
  assert.equal(globalThis.__e2e, undefined, "no test hook in a production-flavoured load");
  assert.equal(chrome.__log.accessLevel, "TRUSTED_CONTEXTS", "content scripts cannot read the credential from storage.local");
  chrome.runtime.onMessage.listeners.length = 1;
});

test("onMessage answers asynchronously and ignores nothing silently", async () => {
  const chrome = globalThis.chrome;
  const [listener] = chrome.runtime.onMessage.listeners;
  await chrome.storage.session.set({ "meridian.tab.1": { active: true, since: Date.now(), origin: "https://x.example", auto: false, sessions: [] } });
  const answer = await new Promise((resolve) => {
    const keepOpen = listener({ t: "nope" }, { id: EXT_ID, tab: { id: 1 }, frameId: 0 }, resolve);
    assert.equal(keepOpen, true, "the channel stays open for the async answer");
  });
  assert.equal(answer.ok, false);
  assert.equal(answer.error.code, "invalid");
});

test("installing opens the welcome page once; updating only migrates storage", async () => {
  const chrome = globalThis.chrome;
  const [onInstalled] = chrome.runtime.onInstalled.listeners;
  await onInstalled({ reason: "update" });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(chrome.__log.created.length, 0);
  assert.equal((await chrome.storage.local.get("meridian.schema"))["meridian.schema"], 1);
  onInstalled({ reason: "install" });
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(chrome.__log.created.length, 1);
  assert.match(chrome.__log.created[0].url, /options\.html\?welcome=1$/);
});

test("tab events: loading finishes activation rules, closing a tab clears its state", async () => {
  const chrome = globalThis.chrome;
  const [onUpdated] = chrome.tabs.onUpdated.listeners;
  const [onRemoved] = chrome.tabs.onRemoved.listeners;
  const tabId = chrome.__addTab("https://acme.example.com/apply");
  await chrome.storage.session.set({ [`meridian.tab.${tabId}`]: { active: true, since: Date.now(), origin: "https://acme.example.com", auto: false, sessions: [] } });
  onUpdated(tabId, { status: "loading" }, { url: "https://acme.example.com/apply" });
  onUpdated(tabId, { status: "complete" }, { url: "https://other.example.net/" });
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal((await chrome.storage.session.get(`meridian.tab.${tabId}`))[`meridian.tab.${tabId}`], undefined, "left the site: assistant off");
  await chrome.storage.session.set({ [`meridian.tab.${tabId}`]: { active: true, since: Date.now(), origin: "x", auto: false, sessions: [] } });
  onRemoved(tabId);
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal((await chrome.storage.session.get(`meridian.tab.${tabId}`))[`meridian.tab.${tabId}`], undefined);
});
