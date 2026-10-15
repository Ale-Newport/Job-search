// A browser that lacks what Safari lacked when this was written: no storage.local.setAccessLevel, no storage.session
// (older Safari), no permission events, no badge text colour, no commands, no windows API. The worker must start,
// pair, serve a page and disconnect without ever stepping on the missing pieces, and the credential must still never
// sit in plaintext where a content script reads.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { onActionClicked, setSettleDelays } from "../../src/background/activation.ts";
import { disconnectBrowser, pollPairing, setWatchInterval, startPairing } from "../../src/background/pairing.ts";
import { pollingState, resetPolling } from "../../src/background/polling.ts";
import { handleMessage } from "../../src/background/router.ts";
import { resetGates } from "../../src/shared/bridge-client.ts";
import { KEYS, credentialPlacement, resetCredentialPlacement, sessionAll, sessionRemove } from "../../src/shared/storage.ts";
import { EXT_ID, installChrome } from "./chrome-mock.mjs";
import { installFakeIndexedDB, removeFakeIndexedDB } from "./fake-idb.mjs";
import { world } from "./helpers.mjs";

function degrade(chrome) {
  delete chrome.storage.local.setAccessLevel;
  delete chrome.storage.session;
  delete chrome.permissions.onAdded;
  delete chrome.permissions.onRemoved;
  delete chrome.action.setBadgeTextColor;
  delete chrome.commands;
  delete chrome.windows;
  return chrome;
}

const boot = degrade(installChrome());
const registered = [];
for (const [name, target] of Object.entries({ "action.onClicked": boot.action.onClicked, "runtime.onMessage": boot.runtime.onMessage, "tabs.onRemoved": boot.tabs.onRemoved })) {
  const original = target.addListener;
  target.addListener = (fn) => {
    registered.push(name);
    original(fn);
  };
}

test("the worker starts where optional APIs are missing and still registers everything it needs", async () => {
  await import("../../src/background/index.ts");
  assert.deepEqual(registered.sort(), ["action.onClicked", "runtime.onMessage", "tabs.onRemoved"]);
  assert.equal(boot.__log.accessLevel, undefined, "nothing to call");
  resetCredentialPlacement();
});

afterEach(async () => {
  setSettleDelays([1200, 3500, 8000]);
  setWatchInterval(1500);
  resetPolling();
  resetGates();
  resetCredentialPlacement();
  removeFakeIndexedDB();
  await sessionRemove(...Object.keys(await sessionAll()));
});

test("pair, activate, serve a page, poll, hide, disconnect: all of it works on the degraded browser, with the credential sealed", async () => {
  const w = await world({ bridges: [{}] });
  degrade(w.chrome);
  installFakeIndexedDB();
  setSettleDelays([]);
  setWatchInterval(0);
  try {
    const { pairingId } = await startPairing();
    const issued = w.bridge.approve(pairingId);
    assert.equal((await pollPairing(pairingId)).state, "approved");
    assert.equal(await credentialPlacement(), "sealed");
    const local = JSON.stringify([...w.chrome.storage.local.data]);
    assert.equal(local.includes(issued.secret), false, "no plaintext credential where a content script can read storage.local");
    const tabId = w.chrome.__addTab("https://boards.greenhouse.io/acme/jobs/1");
    await onActionClicked({ id: tabId, url: "https://boards.greenhouse.io/acme/jobs/1" });
    const sender = { id: EXT_ID, tab: { id: tabId, url: "https://boards.greenhouse.io/acme/jobs/1" }, frameId: 0, url: "https://boards.greenhouse.io/acme/jobs/1" };
    const ctx = await handleMessage({ t: "ctx" }, sender);
    assert.equal(ctx.data.connection.phase, "ready");
    const page = { url: sender.url, title: "x", h1: "", lang: "en", company_hint: null, meta: {}, canonical: null, jsonld: [], description_text: "", links: [], user: {} };
    const opened = await handleMessage({ t: "session:open", report: page, tabKey: "k" }, sender);
    assert.equal(opened.ok, true);
    const wait = handleMessage({ t: "poll", sessionId: opened.data.session_id, cursor: 0, waitMs: 5000 }, sender);
    await new Promise((resolve) => setTimeout(resolve, 80));
    assert.equal(pollingState().inFlight, true);
    await handleMessage({ t: "visibility", visible: false }, sender);
    await new Promise((resolve) => setTimeout(resolve, 80));
    assert.equal(pollingState().inFlight, false);
    await handleMessage({ t: "visibility", visible: true }, sender);
    w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k2" }, opened.data.session_id);
    assert.equal((await wait).data.events[0].knowledge_version, "k2");
    assert.equal(w.chrome.__log.badges.get(tabId).text, "ON");
    // the pending submit works without storage.session too (it is then kept in the worker's memory, and goes with the tab)
    const record = { sid: opened.data.session_id, at: Date.now(), label: "Submit", company: "Acme", title: "Engineer", identity: "id", hops: 0 };
    assert.equal((await handleMessage({ t: "pending:set", record }, sender)).ok, true);
    assert.equal((await handleMessage({ t: "ctx" }, sender)).data.pending.sid, opened.data.session_id);
    assert.equal((await handleMessage({ t: "pending:clear" }, sender)).ok, true);
    assert.equal((await handleMessage({ t: "ctx" }, sender)).data.pending, null);
    const result = await disconnectBrowser();
    assert.equal(result.remoteRevoked, true);
    assert.equal(JSON.stringify([...w.chrome.storage.local.data]).includes(KEYS.connection), false);
  } finally {
    await w.close();
  }
});
