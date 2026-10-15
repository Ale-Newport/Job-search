// The worker's half of "Record this application?" across origins: a pending submit kept PER TAB in storage.session
// (page sessionStorage belongs to one origin, so a form that posts to another origin lost it).
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { onActionClicked, setSettleDelays } from "../../src/background/activation.ts";
import { resetPolling } from "../../src/background/polling.ts";
import { resetGates } from "../../src/shared/bridge-client.ts";
import { PENDING_TTL_MS, getPending, getTabState, normalizePending } from "../../src/shared/tabs.ts";
import { EXT_ID, installChrome } from "./chrome-mock.mjs";
import { world } from "./helpers.mjs";

const boot = installChrome();
await import("../../src/background/index.ts");
const [onMessage] = boot.runtime.onMessage.listeners;
const [onRemoved] = boot.tabs.onRemoved.listeners;
const [onUpdated] = boot.tabs.onUpdated.listeners;

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
const OTHER_ORIGIN = "https://careers.example.org/thanks";
const fromTab = (tabId, frameId = 0, url = PAGE) => ({ id: EXT_ID, tab: { id: tabId, url }, frameId, url });
const ask = (message, from) => new Promise((resolve) => onMessage(message, from, resolve));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const eventually = async (condition, ms = 3000) => {
  const deadline = Date.now() + ms;
  while (!(await condition()) && Date.now() < deadline) await sleep(10);
  return condition();
};
const record = (extra = {}) => ({
  sid: "s1xabc", at: Date.now() - 5000, label: "Submit application", company: "Acme", title: "Engineer", identity: "https://boards.greenhouse.io/acme/jobs/1", hops: 0, ...extra,
});
/** A tab the worker started: toolbar click on an ATS page, content script installed in every frame. */
async function assistedTab(w, url = PAGE, extra = {}) {
  const tabId = w.chrome.__addTab(url, extra);
  await onActionClicked({ id: tabId, url });
  return tabId;
}

test("the submit a tab waits on is stored per tab and given back to every frame of THAT tab, and to no other", async () => {
  const w = await setup();
  const a = await assistedTab(w, PAGE, { frames: [{ frameId: 7 }] });
  const b = await assistedTab(w);
  assert.deepEqual(await ask({ t: "pending:set", record: record() }, fromTab(a)), { ok: true, data: null });
  const top = await ask({ t: "ctx" }, fromTab(a));
  assert.equal(top.data.pending.sid, "s1xabc");
  assert.equal(top.data.pending.company, "Acme");
  assert.equal(top.data.pending.hops, 0);
  const frame = await ask({ t: "ctx" }, fromTab(a, 7));
  assert.deepEqual(frame.data.pending, top.data.pending, "any frame of the tab");
  assert.equal((await ask({ t: "ctx" }, fromTab(b))).data.pending, null, "another tab knows nothing of it");
  assert.equal(JSON.stringify(top).includes(w.issued.token), false);
});

test("it lives in storage.session, which the worker restricts to trusted contexts; never in storage.local", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  await ask({ t: "pending:set", record: record() }, fromTab(tabId));
  assert.equal(boot.__log.sessionAccessLevel, "TRUSTED_CONTEXTS", "set when the worker started: content scripts cannot read storage.session");
  assert.ok(w.chrome.storage.session.data.has(`meridian.pending.${tabId}`));
  assert.equal([...w.chrome.storage.local.data.keys()].some((key) => key.includes("pending")), false);
});

test("a newer record replaces the older one; hops are kept as sent (bounded) so the content script can count documents", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  await ask({ t: "pending:set", record: record({ sid: "first" }) }, fromTab(tabId));
  await ask({ t: "pending:set", record: record({ sid: "second", hops: 2 }) }, fromTab(tabId));
  const { pending } = (await ask({ t: "ctx" }, fromTab(tabId))).data;
  assert.equal(pending.sid, "second");
  assert.equal(pending.hops, 2);
  await ask({ t: "pending:set", record: record({ hops: 9999 }) }, fromTab(tabId));
  assert.equal((await getPending(tabId)).hops, 16);
});

test("malformed or hostile records are refused and nothing is stored", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  const bad = [
    null, "x", 5, [], {}, record({ sid: "" }), record({ sid: "has space" }), record({ sid: "x".repeat(101) }), record({ sid: 7 }),
    record({ at: "soon" }), record({ at: 0 }), record({ at: -5 }), record({ at: Date.now() + 3_600_000 }), record({ at: Date.now() - PENDING_TTL_MS - 1000 }),
  ];
  for (const candidate of bad) {
    const answer = await ask({ t: "pending:set", record: candidate }, fromTab(tabId));
    assert.equal(answer.ok, false, JSON.stringify(candidate));
    assert.equal(answer.error.code, "invalid");
  }
  assert.equal(await getPending(tabId), null);
  assert.equal(w.chrome.storage.session.data.has(`meridian.pending.${tabId}`), false);
});

test("strings are bounded and stripped of control characters; a click time in the future is not believed", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  const huge = record({ label: "Sub\u0000mit\n".repeat(100), company: "C".repeat(5000), title: "T".repeat(5000), identity: "I".repeat(5000), at: Date.now() + 30_000 });
  assert.equal((await ask({ t: "pending:set", record: huge }, fromTab(tabId))).ok, true);
  const stored = await getPending(tabId);
  assert.equal(stored.label.length, 120);
  assert.equal(/[\u0000-\u001f]/.test(stored.label), false);
  assert.equal(stored.company.length, 200);
  assert.equal(stored.title.length, 300);
  assert.equal(stored.identity.length, 1000);
  assert.ok(stored.at <= Date.now(), "clamped to now");
  assert.equal(normalizePending({ ...record(), extra: "ignored", hops: "2" }).hops, 2);
  assert.deepEqual(Object.keys(normalizePending({ ...record(), extra: "ignored" })).sort(), ["at", "company", "hops", "identity", "label", "sid", "title"]);
});

test("a script in a tab nobody started cannot plant or read a record; clearing is allowed from anywhere and only touches its own tab", async () => {
  const w = await setup();
  const started = await assistedTab(w);
  const dormant = w.chrome.__addTab(PAGE);
  const refused = await ask({ t: "pending:set", record: record() }, fromTab(dormant));
  assert.equal(refused.ok, false);
  assert.equal(refused.error.code, "inactive");
  assert.equal((await ask({ t: "ctx" }, fromTab(dormant))).error.code, "inactive");
  await ask({ t: "pending:set", record: record() }, fromTab(started));
  assert.deepEqual(await ask({ t: "pending:clear" }, fromTab(dormant)), { ok: true, data: null });
  assert.ok(await getPending(started), "another tab's clear left it alone");
  assert.deepEqual(await ask({ t: "pending:clear" }, fromTab(started)), { ok: true, data: null });
  assert.equal(await getPending(started), null);
  assert.equal((await ask({ t: "ctx" }, fromTab(started))).data.pending, null);
});

test("it expires 10 minutes after the click, and an expired record reads as none and is removed", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  const key = `meridian.pending.${tabId}`;
  await w.chrome.storage.session.set({ [key]: record({ at: Date.now() - PENDING_TTL_MS + 5000 }) });
  assert.ok(await getPending(tabId), "9 min 55 s: still there");
  assert.ok((await ask({ t: "ctx" }, fromTab(tabId))).data.pending);
  await w.chrome.storage.session.set({ [key]: record({ at: Date.now() - PENDING_TTL_MS - 1000 }) });
  assert.equal((await ask({ t: "ctx" }, fromTab(tabId))).data.pending, null);
  assert.equal(w.chrome.storage.session.data.has(key), false, "removed when found stale");
  await w.chrome.storage.session.set({ [key]: { not: "a record" } });
  assert.equal(await getPending(tabId), null);
  assert.equal(w.chrome.storage.session.data.has(key), false);
});

test("closing the tab, switching the assistant off, or an activation that cannot happen all forget it", async () => {
  const w = await setup();
  const closing = await assistedTab(w);
  await ask({ t: "pending:set", record: record() }, fromTab(closing));
  onRemoved(closing); // fire-and-forget, as in the browser
  assert.ok(await eventually(async () => (await getPending(closing)) === null), "tab closed");
  const off = await assistedTab(w);
  await ask({ t: "pending:set", record: record() }, fromTab(off));
  await ask({ t: "activation", active: false }, fromTab(off)); // the page reports the user switched the assistant off
  assert.equal(await getPending(off), null, "switched off");
  assert.equal(await getTabState(off), null);
});

test("a form that posts to ANOTHER origin keeps the assistant on there, started automatically, with the record in ctx", async () => {
  const w = await setup();
  await w.chrome.permissions.request({ origins: ["https://careers.example.org/*"] }); // the browser lets the assistant run there
  const tabId = await assistedTab(w);
  await ask({ t: "pending:set", record: record() }, fromTab(tabId));
  w.chrome.__navigate(tabId, OTHER_ORIGIN);
  await onUpdated(tabId, { status: "complete" }, { url: OTHER_ORIGIN });
  assert.ok(await eventually(() => w.chrome.__messagesTo(tabId, "activate").some((entry) => entry.message.auto === true)), "re-activated with auto=true");
  const state = await getTabState(tabId);
  assert.equal(state.active, true);
  assert.equal(state.origin, "https://careers.example.org", "the origin the assistant now follows");
  assert.equal(state.auto, true);
  const ctx = await ask({ t: "ctx" }, fromTab(tabId, 0, OTHER_ORIGIN));
  assert.equal(ctx.data.pending.sid, "s1xabc", "the new document, another origin, reads what the old one stored");
  assert.equal(ctx.data.pending.company, "Acme");
});

test("without a pending submit, leaving the origin still switches the assistant off (nothing about plain navigation changed)", async () => {
  const w = await setup();
  await w.chrome.permissions.request({ origins: ["https://careers.example.org/*"] });
  const tabId = await assistedTab(w);
  w.chrome.__navigate(tabId, OTHER_ORIGIN);
  await onUpdated(tabId, { status: "complete" }, { url: OTHER_ORIGIN });
  assert.ok(await eventually(async () => (await getTabState(tabId)) === null), "switched off");
  assert.equal(w.chrome.__messagesTo(tabId, "activate").filter((entry) => entry.message.auto === true).length, 0);
});

test("an expired record no longer carries the assistant to another origin", async () => {
  const w = await setup();
  await w.chrome.permissions.request({ origins: ["https://careers.example.org/*"] });
  const tabId = await assistedTab(w);
  await w.chrome.storage.session.set({ [`meridian.pending.${tabId}`]: record({ at: Date.now() - PENDING_TTL_MS - 1000 }) });
  w.chrome.__navigate(tabId, OTHER_ORIGIN);
  await onUpdated(tabId, { status: "complete" }, { url: OTHER_ORIGIN });
  assert.ok(await eventually(async () => (await getTabState(tabId)) === null));
});

test("where the browser does not let the assistant run (no access to that origin), the record goes with the failed start", async () => {
  const w = await setup();
  const tabId = await assistedTab(w);
  await ask({ t: "pending:set", record: record() }, fromTab(tabId));
  w.chrome.__navigate(tabId, OTHER_ORIGIN);
  w.chrome.__injectErrors.set(tabId, "Cannot access contents of the page. Extension manifest must request permission to access the respective host.");
  await onUpdated(tabId, { status: "complete" }, { url: OTHER_ORIGIN });
  assert.ok(await eventually(async () => (await getTabState(tabId)) === null));
  assert.equal(await getPending(tabId), null, "nothing could pick it up there");
  assert.equal(w.chrome.__log.created.length, 0, "an automatic start never opens the options page");
});
