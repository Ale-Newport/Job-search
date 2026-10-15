// The worker as the browser drives it: listeners registered by background/index.ts, tabs closing and navigating,
// storage being cleared under a running session, messages from places that must be ignored.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { afterEach, test } from "node:test";
import { fileURLToPath } from "node:url";
import { onActionClicked, setSettleDelays } from "../../src/background/activation.ts";
import { pollingState, resetPolling } from "../../src/background/polling.ts";
import { resetGates } from "../../src/shared/bridge-client.ts";
import { resolveConnection } from "../../src/shared/connection.ts";
import { getTabState } from "../../src/shared/tabs.ts";
import { EXT_ID, installChrome } from "./chrome-mock.mjs";
import { world } from "./helpers.mjs";

// register the real listeners once, on a first mock; every test installs its own world, the closures use the global chrome
const boot = installChrome();
await import("../../src/background/index.ts");
const [onMessage] = boot.runtime.onMessage.listeners;
const [onRemoved] = boot.tabs.onRemoved.listeners;
const [onUpdated] = boot.tabs.onUpdated.listeners;
const [onClicked] = boot.action.onClicked.listeners;
assert.equal(typeof onClicked, "function", "the toolbar listener is registered");

let current = null;
afterEach(async () => {
  setSettleDelays([1200, 3500, 8000]);
  resetPolling();
  resetGates();
  await current?.close();
  current = null;
});
const setup = async (options = { paired: {} }) => {
  current = await world(options);
  setSettleDelays([]);
  return current;
};
const PAGE = "https://boards.greenhouse.io/acme/jobs/1";
const sender = (tabId) => ({ id: EXT_ID, tab: { id: tabId, url: PAGE }, frameId: 0, url: PAGE });
const ask = (message, from) => new Promise((resolve) => onMessage(message, from, resolve));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
/** Listener work is fire-and-forget (as in the browser): wait for its effects instead of for its return. */
const eventually = async (condition, ms = 3000) => {
  const deadline = Date.now() + ms;
  while (!condition() && Date.now() < deadline) await sleep(10);
  return condition();
};
const report = { url: PAGE, title: "Engineer", h1: "", lang: "en", company_hint: null, meta: {}, canonical: null, jsonld: [], description_text: "", links: [], user: {} };

async function assistedTab(w) {
  const tabId = w.chrome.__addTab(PAGE);
  await onActionClicked({ id: tabId, url: PAGE });
  const opened = await ask({ t: "session:open", report, tabKey: `k${tabId}` }, sender(tabId));
  assert.equal(opened.ok, true);
  return { tabId, sid: opened.data.session_id };
}

test("closing a tab releases its session at the bridge, ends its waits and forgets that it was hidden", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  await ask({ t: "visibility", visible: false }, sender(tabId));
  const wait = ask({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, sender(tabId));
  await sleep(100);
  assert.equal(pollingState().waiting, 1);
  await onRemoved(tabId);
  assert.deepEqual((await wait).data, { cursor: 0, events: [] });
  assert.equal(pollingState().waiting, 0);
  assert.ok(await eventually(() => w.bridge.log.some((entry) => entry.method === "DELETE" && entry.path === `/v1/sessions/${sid}`)), "the server was told, not left to time the session out");
  assert.equal(w.bridge.sessions.has(sid), false);
  assert.deepEqual(pollingState().hidden, []);
  assert.equal(await getTabState(tabId), null);
});

test("a tab that navigates away from the site releases its sessions and its waits too", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const wait = ask({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, sender(tabId));
  await sleep(100);
  w.chrome.__navigate(tabId, "https://mail.example.org/inbox");
  await onUpdated(tabId, { status: "complete" }, { url: "https://mail.example.org/inbox" });
  assert.deepEqual((await wait).data, { cursor: 0, events: [] });
  assert.equal(await getTabState(tabId), null);
  assert.ok(await eventually(() => w.bridge.log.some((entry) => entry.method === "DELETE" && entry.path === `/v1/sessions/${sid}`)));
});

test("messages from another extension are ignored: the worker listens to its own channel only", async () => {
  const w = await setup();
  const answer = await ask({ t: "diagnostics" }, { id: "abcdefghijklmnopabcdefghijklmnop", url: "chrome-extension://abcdefghijklmnopabcdefghijklmnop/x.html" });
  assert.equal(answer.ok, false);
  assert.equal(answer.error.code, "origin");
  const here = dirname(fileURLToPath(import.meta.url));
  const sources = ["../../src/background", "../../src/shared"].flatMap((folder) => readdirSync(join(here, folder)).map((name) => readFileSync(join(here, folder, name), "utf8").replace(/^\s*\/\/.*$/gm, "")));
  assert.ok(sources.every((text) => !/onMessageExternal|onConnectExternal|onConnect\b|runtime\.connect\b/.test(text)), "no external or port channel is ever opened");
  assert.equal(w.bridge.log.length, 0);
});

test("storage.local cleared under a running session: the pages hear that Meridian is not connected, waits fail cleanly, nothing crashes", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const wait = ask({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, sender(tabId));
  await sleep(100);
  w.chrome.__received.length = 0;
  await w.chrome.storage.local.clear(); // the credential, the preferences, everything
  const state = (await resolveConnection({ force: true })).state;
  assert.equal(state.phase, "unpaired");
  await sleep(50);
  const pushed = w.chrome.__messagesTo(tabId, "conn:changed").map((entry) => entry.message.connection.phase);
  assert.ok(pushed.includes("unpaired"), `the active tab was told: ${pushed}`);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k1" }, sid);
  const first = await wait; // the request that was already on its way finishes normally
  assert.equal(first.ok, true);
  const next = await ask({ t: "poll", sessionId: sid, cursor: 1, waitMs: 2000 }, sender(tabId));
  assert.equal(next.ok, false);
  assert.equal(next.error.connection.phase, "unpaired");
  const hint = await ask({ t: "hint:config" }, sender(tabId));
  assert.deepEqual(hint.data, { show: true, language: "en", connected: false });
  assert.equal((await ask({ t: "ctx" }, sender(tabId))).data.prefs.language, "auto", "preferences fall back to defaults");
});

test("storage.session cleared under a running session: the old script is refused, not trusted, and one click starts everything again", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  await w.chrome.storage.session.clear(); // the worker lost its picture of the tabs and of the connection
  const refused = await ask({ t: "suggest", sessionId: sid, request: { elements: [], mode: "scan" } }, sender(tabId));
  assert.equal(refused.ok, false);
  assert.equal(refused.error.code, "inactive");
  assert.equal(w.bridge.log.filter((entry) => entry.path.endsWith("/suggest")).length, 0);
  await onActionClicked({ id: tabId, url: PAGE });
  assert.equal((await getTabState(tabId)).active, true);
  const again = await ask({ t: "ctx" }, sender(tabId));
  assert.equal(again.data.connection.phase, "ready", "the credential is in storage.local: no need to connect again");
});

test("a same-site page load ends the old document's waits before the worker starts the assistant in the new one", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const wait = ask({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, sender(tabId));
  await sleep(100);
  assert.equal(pollingState().waiting, 1);
  w.chrome.__navigate(tabId, "https://boards.greenhouse.io/acme/jobs/2"); // a new document: the old script and its pending message are gone
  await onUpdated(tabId, { status: "complete" }, { url: "https://boards.greenhouse.io/acme/jobs/2" });
  const ended = await Promise.race([wait, sleep(2000).then(() => "still waiting")]);
  assert.notEqual(ended, "still waiting", "the old document's wait ended with the load, not 20 s later");
  assert.deepEqual(ended.data, { cursor: 0, events: [] });
  assert.equal(pollingState().waiting, 0);
  assert.equal((await getTabState(tabId)).active, true, "and the assistant carried on in the new page");
});

test("two tabs on the same page share one bridge session: the first to close or let go does not take it away from the other", async () => {
  const w = await setup({ bridges: [{ shareSessions: true }], paired: {} });
  const first = await assistedTab(w);
  const secondTab = w.chrome.__addTab(PAGE);
  await onActionClicked({ id: secondTab, url: PAGE });
  const opened = await ask({ t: "session:open", report, tabKey: `k${secondTab}` }, sender(secondTab));
  assert.equal(opened.data.session_id, first.sid, "the bridge hands out ONE session for the page");
  const deletes = () => w.bridge.log.filter((entry) => entry.method === "DELETE").map((entry) => entry.path);
  const waitB = ask({ t: "poll", sessionId: first.sid, cursor: 0, waitMs: 20_000 }, sender(secondTab));
  await sleep(100);
  // tab 1 lets go of the session (its assistant was switched off)
  assert.equal((await ask({ t: "session:release", sessionId: first.sid }, sender(first.tabId))).ok, true);
  assert.deepEqual(deletes(), [], "the session is still in use by the other tab");
  assert.equal(w.bridge.sessions.has(first.sid), true);
  assert.equal(await Promise.race([waitB, sleep(150).then(() => "pending")]), "pending", "and the other tab's wait went on");
  // tab 1 closes for good: still not released
  await onRemoved(first.tabId);
  await sleep(150);
  assert.deepEqual(deletes(), []);
  // the last tab on the page releases it
  assert.equal((await ask({ t: "session:release", sessionId: first.sid }, sender(secondTab))).ok, true);
  assert.deepEqual(deletes(), [`/v1/sessions/${first.sid}`]);
  assert.deepEqual((await waitB).data, { cursor: 0, events: [] });
});

test("closing the last of two tabs that share a session does release it", async () => {
  const w = await setup({ bridges: [{ shareSessions: true }], paired: {} });
  const first = await assistedTab(w);
  const secondTab = w.chrome.__addTab(PAGE);
  await onActionClicked({ id: secondTab, url: PAGE });
  await ask({ t: "session:open", report, tabKey: `k${secondTab}` }, sender(secondTab));
  await onRemoved(first.tabId);
  await sleep(150);
  assert.equal(w.bridge.sessions.has(first.sid), true, "one tab left on the page");
  await onRemoved(secondTab);
  assert.ok(await eventually(() => !w.bridge.sessions.has(first.sid)), "the last one out releases it");
});
