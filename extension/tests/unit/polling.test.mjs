import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { setSettleDelays } from "../../src/background/activation.ts";
import {
  dropSession, dropTab, pollingState, resetPolling, setTabVisible, waitForEvents,
} from "../../src/background/polling.ts";
import { handleMessage } from "../../src/background/router.ts";
import { HELLO_RESERVE, MAX_IN_FLIGHT, gateStats, resetGates } from "../../src/shared/bridge-client.ts";
import { EXT_ID } from "./chrome-mock.mjs";
import { world } from "./helpers.mjs";

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
const send = (message, tabId) => handleMessage(message, sender(tabId));
const report = { url: PAGE, title: "Engineer", h1: "", lang: "en", company_hint: null, meta: {}, canonical: null, jsonld: [], description_text: "", links: [], user: {} };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const pollEntries = (w) => w.bridge.log.filter((entry) => entry.path === "/v1/poll");
/** Waits (up to `ms`) for a condition that depends on the worker's background loop; the machine may be busy. */
const eventually = async (condition, ms = 5000) => {
  const deadline = Date.now() + ms;
  while (!condition() && Date.now() < deadline) await sleep(10);
  return condition();
};
/** Resolves with the value, or with `fallback` when it has not settled after `ms`. */
const settledWithin = (promise, ms, fallback = "pending") => Promise.race([promise, sleep(ms).then(() => fallback)]);

/** A tab the worker started, with its bridge session (what one assisted page is). */
async function assistedTab(w) {
  const tabId = w.chrome.__addTab(PAGE);
  assert.equal((await send({ t: "activate-here" }, tabId)).ok, true);
  const opened = await send({ t: "session:open", report, tabKey: `tab-${tabId}` }, tabId);
  assert.equal(opened.ok, true);
  return { tabId, sid: opened.data.session_id };
}

test("ten assisted tabs share ONE long poll: the newest tab gets its first suggestion at once and the bridge never sees more than 4 requests", async () => {
  const w = await setup();
  const tabs = [];
  for (let i = 0; i < 10; i++) tabs.push(await assistedTab(w));
  // every page's content script keeps a 20 s poll pending with the worker, exactly as session.ts does
  const waits = tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, tabId));
  assert.ok(await eventually(() => pollingState().waiting === 10 && w.bridge.concurrent === 1), "ten pending waits are ONE connection to the bridge");
  assert.equal(pollingState().waiting, 10);
  assert.ok(w.bridge.log.every((entry) => !/^\/v1\/sessions\/[^/]+\/poll$/.test(entry.path)), "no per-session poll exists");
  const last = tabs[9];
  const started = Date.now();
  const suggested = await send({ t: "suggest", sessionId: last.sid, request: { elements: [], mode: "scan" } }, last.tabId);
  assert.equal(suggested.ok, true);
  assert.ok(Date.now() - started < 1000, `the 10th tab's first suggestion took ${Date.now() - started} ms`);
  // all ten pages ask for suggestions at once (each prefetches its form): still never more than the cap in flight
  const burst = await Promise.all(tabs.map(({ tabId, sid }) => send({ t: "suggest", sessionId: sid, request: { elements: [], mode: "scan" } }, tabId)));
  assert.ok(burst.every((answer) => answer.ok));
  assert.ok(w.bridge.maxConcurrent <= MAX_IN_FLIGHT + HELLO_RESERVE, `the bridge saw ${w.bridge.maxConcurrent} requests at once`);
  // an event for one page reaches that page only
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k7" }, tabs[3].sid);
  const woke = await waits[3];
  assert.equal(woke.data.events[0].knowledge_version, "k7");
  assert.equal(await settledWithin(waits[4], 150), "pending", "the other nine keep waiting");
  assert.equal(await settledWithin(waits[0], 10), "pending");
});

test("an event for one session ends only that session's wait and carries its cursor; the others stay quiet", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const waitA = send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 5000 }, a.tabId);
  const waitB = send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 5000 }, b.tabId);
  await sleep(100);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kB" }, b.sid);
  const answerB = await waitB;
  assert.deepEqual(answerB.data, { cursor: 1, events: [{ type: "knowledge", knowledge_version: "kB" }] });
  assert.equal(await settledWithin(waitA, 200), "pending");
  // B asks again from its new cursor; A is still being watched by the same shared request
  const again = send({ t: "poll", sessionId: b.sid, cursor: 1, waitMs: 5000 }, b.tabId);
  await sleep(100);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kA" }, a.sid);
  assert.equal((await waitA).data.events[0].knowledge_version, "kA");
  assert.equal(await settledWithin(again, 100), "pending");
});

test("a wait with nothing to report ends empty at its own deadline, and the request is cancelled once nobody waits", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const started = Date.now();
  const answer = await send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 300 }, tabId);
  assert.deepEqual(answer.data, { cursor: 0, events: [] });
  assert.ok(Date.now() - started >= 280 && Date.now() - started < 1500);
  assert.ok(await eventually(() => w.bridge.concurrent === 0 && !pollingState().inFlight), "the request that watched the session was cancelled, not left to run for 25 s");
});

test("a repeat wait for the same session and cursor reuses the request; a new session or cursor replaces it", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const first = send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 3000 }, a.tabId);
  assert.ok(await eventually(() => pollingState().inFlight));
  const requests = () => pollingState().stats.requests;
  const base = requests();
  // the content script's next poll for the same session from the same cursor (its previous wait ended empty)
  const second = send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 3000 }, a.tabId);
  await sleep(100);
  assert.equal(requests(), base, "nothing new to ask: the request in flight already watches this session from this cursor");
  assert.deepEqual((await first).data, { cursor: 0, events: [] }, "the older wait of the same session resolves empty at once");
  // another session joins: the request is replaced by one that covers both
  const third = send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 3000 }, b.tabId);
  assert.ok(await eventually(() => requests() === base + 1 && pollEntries(w).at(-1)?.body.sessions.length === 2));
  const latest = pollEntries(w).at(-1);
  assert.deepEqual(latest.body.sessions.map((entry) => entry.session_id).sort(), [a.sid, b.sid].sort());
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k1" }, a.sid);
  assert.equal((await second).data.events.length, 1);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k2" }, b.sid);
  assert.equal((await third).data.events.length, 1);
});

test("nothing is asked while every waiting tab is hidden; the first tab that becomes visible resumes it", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  await send({ t: "visibility", visible: false }, tabId);
  const wait = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 4000 }, tabId);
  await sleep(400);
  assert.equal(pollEntries(w).length, 0, "no request while the page is hidden");
  assert.equal(pollingState().inFlight, false);
  assert.deepEqual(pollingState().hidden, [tabId]);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k3" }, sid);
  await send({ t: "visibility", visible: true }, tabId);
  const answer = await settledWithin(wait, 1500);
  assert.equal(answer.data.events[0].knowledge_version, "k3", "it catches up from its cursor the moment it is visible again");
});

test("hiding the last visible tab cancels the request in flight; one visible tab keeps every session covered", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const waits = [send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 8000 }, a.tabId), send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 8000 }, b.tabId)];
  assert.ok(await eventually(() => w.bridge.concurrent === 1 && pollingState().waiting === 2));
  await send({ t: "visibility", visible: false }, a.tabId);
  await sleep(100);
  assert.equal(w.bridge.concurrent, 1, "B is still visible: the shared request stays");
  assert.equal(pollEntries(w).at(-1).body.sessions.length, 2, "and it still covers the hidden tab's session too (a draft may be ready when it returns)");
  await send({ t: "visibility", visible: false }, b.tabId);
  assert.ok(await eventually(() => w.bridge.concurrent === 0), "every tab is hidden: the request is cancelled");
  assert.equal(await settledWithin(waits[0], 50), "pending", "the waits themselves stay until their deadline");
  await send({ t: "visibility", visible: true }, b.tabId);
  assert.ok(await eventually(() => w.bridge.concurrent === 1), "a visible tab: asking resumes");
});

test("which tabs are hidden survives a worker restart (storage.session), and a closed tab is forgotten", async () => {
  const w = await setup();
  const { tabId } = await assistedTab(w);
  await send({ t: "visibility", visible: false }, tabId);
  assert.deepEqual((await w.chrome.storage.session.get("meridian.hidden"))["meridian.hidden"], [tabId]);
  resetPolling(); // the worker was stopped and started again: its memory is empty
  await setTabVisible(77, true);
  assert.deepEqual(pollingState().hidden, [tabId], "re-read from storage.session");
  await send({ t: "visibility", visible: true }, tabId);
  assert.deepEqual((await w.chrome.storage.session.get("meridian.hidden"))["meridian.hidden"], []);
});

test("a tab that stops or closes releases its waits; a released session stops being watched", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const waitA = send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 20_000 }, a.tabId);
  const waitB = send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 20_000 }, b.tabId);
  assert.ok(await eventually(() => pollingState().waiting === 2 && w.bridge.concurrent === 1));
  dropTab(a.tabId);
  assert.deepEqual((await waitA).data, { cursor: 0, events: [] });
  assert.equal(pollingState().waiting, 1);
  const released = await send({ t: "session:release", sessionId: b.sid }, b.tabId);
  assert.equal(released.ok, true);
  assert.deepEqual((await waitB).data, { cursor: 0, events: [] });
  assert.ok(await eventually(() => w.bridge.concurrent === 0));
  assert.equal(pollingState().waiting, 0);
  dropSession("not-waiting"); // harmless
});

test("a session the bridge no longer has fails only its own wait", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const ghost = send({ t: "poll", sessionId: "ghostsession", cursor: 0, waitMs: 5000 }, tabId);
  const real = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 5000 }, tabId);
  const failed = await ghost;
  assert.equal(failed.ok, false);
  assert.equal(failed.error.code, "not_found");
  assert.equal(await settledWithin(real, 200), "pending", "the healthy session keeps waiting");
});

test("a credential that Meridian revoked fails every wait with 'revoked'; the event that was already on its way still arrives", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const waits = [send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 10_000 }, a.tabId), send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 10_000 }, b.tabId)];
  assert.ok(await eventually(() => pollingState().waiting === 2 && w.bridge.concurrent === 1));
  w.bridge.revoke(w.issued.clientId);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kx" }, a.sid); // ends the held request (authorised before the revocation)
  const first = await waits[0];
  assert.equal(first.ok, true, "the event itself still arrives");
  const next = await send({ t: "poll", sessionId: a.sid, cursor: 1, waitMs: 2000 }, a.tabId); // the next request is refused
  assert.equal(next.ok, false);
  assert.equal(next.error.connection.phase, "revoked");
  const other = await settledWithin(waits[1], 3000);
  assert.equal(other.ok, false, "the other page's wait failed with it");
  assert.equal(other.error.connection.phase, "revoked");
});

test("when Meridian is gone every wait fails with that, and the page backs off instead of spinning", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const waits = [send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 10_000 }, a.tabId), send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 10_000 }, b.tabId)];
  assert.ok(await eventually(() => pollingState().waiting === 2 && w.bridge.concurrent === 1));
  await w.bridge.stop();
  const answers = await Promise.all(waits.map((wait) => settledWithin(wait, 4000)));
  for (const answer of answers) {
    assert.equal(answer.ok, false);
    assert.equal(answer.error.connection.phase, "no_bridge");
  }
});

test("a Meridian without the multiplexed endpoint is served per session, two at a time, and every session still gets its events", async () => {
  const w = await setup({ bridges: [{ manyPoll: false }], paired: {} });
  const tabs = [];
  for (let i = 0; i < 5; i++) tabs.push(await assistedTab(w));
  const waits = tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 15_000 }, tabId));
  await sleep(300);
  assert.equal(pollingState().mode, "single");
  assert.equal(w.bridge.log.filter((entry) => entry.path === "/v1/poll").length, 1, "the endpoint was tried once, got 404, and is left alone for a while");
  assert.ok(gateStats(w.bridge.port).peak <= 2, `${gateStats(w.bridge.port).peak} per-session polls at once`);
  assert.ok(w.bridge.maxConcurrent <= 3, `${w.bridge.maxConcurrent} per-session polls (and a hello) at once`);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k5" }, tabs[4].sid);
  const answer = await settledWithin(waits[4], 6000);
  assert.equal(answer.data.events[0].knowledge_version, "k5", "the fifth session is reached in rotation");
  assert.ok(gateStats(w.bridge.port).peak <= 2);
});

test("waitMs 0 is a plain check: asked at once, nothing is kept waiting", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k0" }, sid);
  const answer = await send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 0 }, tabId);
  assert.equal(answer.data.events[0].knowledge_version, "k0");
  assert.equal(pollingState().waiting, 0);
  const entry = pollEntries(w).pop();
  assert.equal(entry.body.wait_ms, 0);
});

test("the worker keeps no cursor of its own: a restarted worker simply serves the content script's next poll", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const pending = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 10_000 }, tabId);
  await sleep(100);
  resetPolling(); // the worker was killed in the middle of the wait
  assert.deepEqual((await pending).data, { cursor: 0, events: [] }, "the old wait ends; the page asks again");
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k1" }, sid);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k2" }, sid);
  const again = await send({ t: "poll", sessionId: sid, cursor: 1, waitMs: 1000 }, tabId);
  assert.deepEqual(again.data.events.map((event) => event.knowledge_version), ["k2"], "from the cursor the page sent, nothing lost");
  assert.equal(again.data.cursor, 2);
});

test("polling never holds more than the one slot: with the gate saturated by slow calls the poll still works and calls queue", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const wait = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 10_000 }, tabId);
  await sleep(100);
  const slow = Array.from({ length: 8 }, () => send({ t: "document:content", id: "doc-1" }, tabId));
  const results = await Promise.all(slow);
  assert.ok(results.every((answer) => answer.ok));
  assert.ok(w.bridge.maxConcurrent <= MAX_IN_FLIGHT + HELLO_RESERVE, `${w.bridge.maxConcurrent} in flight`);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k8" }, sid);
  assert.equal((await wait).data.events.length, 1);
});

test("releasing a session ends its wait even when Meridian cannot be reached", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const wait = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, tabId);
  await sleep(100);
  await w.bridge.stop();
  const released = await send({ t: "session:release", sessionId: sid }, tabId);
  assert.equal(released.ok, false, "the bridge could not be told");
  assert.deepEqual((await wait).data, { cursor: 0, events: [] }, "but nobody waits for a session that is being released");
  assert.equal(pollingState().waiting, 0);
});

test("what the bridge says about a page decides what its wait does: gone pages are re-opened, a taken-over page keeps waiting, a released one is left alone", async () => {
  const w = await setup();
  const tabs = [];
  for (let i = 0; i < 4; i++) tabs.push(await assistedTab(w));
  const [expired, unknown, released, superseded] = tabs;
  w.bridge.expired = new Set([expired.sid]); // the page's server state is gone (Meridian restarted)
  w.bridge.released = new Set([released.sid]);
  w.bridge.pollSuperseded = new Set([superseded.sid]);
  w.bridge.sessions.delete(unknown.sid); // a page this browser never had, as far as the bridge can tell
  const waits = tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 1800 }, tabId));
  const [a, b] = await Promise.all(waits.slice(0, 2));
  assert.equal(a.ok, false);
  assert.equal(a.error.code, "expired", "expired: open the page again");
  assert.equal(b.ok, false);
  assert.equal(b.error.code, "not_found", "unknown: the page is not this browser's; the content script opens it again");
  assert.equal(await settledWithin(waits[2], 300), "pending", "released is no error and no 'expired': the wait stays until its own deadline");
  assert.equal(await settledWithin(waits[3], 10), "pending", "superseded is not an answer either: the wait stays, the loop asks again");
  w.bridge.pollSuperseded.clear();
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kS" }, superseded.sid);
  assert.deepEqual((await waits[3]).data, { cursor: 1, events: [{ type: "knowledge", knowledge_version: "kS" }] });
  const late = await waits[2];
  assert.deepEqual(late, { ok: true, data: { cursor: 0, events: [] } }, "released: no events, cursor unchanged, no error");
  assert.equal(pollingState().mode, "many");
});

test("a page the bridge says was released is not asked about any more, until it is opened again", async () => {
  const w = await setup({ bridges: [{ shareSessions: true }], paired: {} }); // POST /sessions of the same page answers the same id, as the real bridge does
  const pageTab = async (suffix) => {
    const tabId = w.chrome.__addTab(PAGE);
    await send({ t: "activate-here" }, tabId);
    const opened = await send({ t: "session:open", report: { ...report, url: `${PAGE}?${suffix}` }, tabKey: suffix }, tabId);
    assert.equal(opened.ok, true);
    return { tabId, sid: opened.data.session_id };
  };
  const [gone, other] = [await pageTab("gone"), await pageTab("other")];
  assert.notEqual(gone.sid, other.sid);
  w.bridge.released = new Set([gone.sid]);
  const waitGone = send({ t: "poll", sessionId: gone.sid, cursor: 0, waitMs: 2500 }, gone.tabId);
  const waitOther = send({ t: "poll", sessionId: other.sid, cursor: 0, waitMs: 2500 }, other.tabId);
  assert.ok(await eventually(() => pollEntries(w).some((entry) => entry.body.sessions.some((target) => target.session_id === gone.sid))));
  await sleep(200);
  const asked = () => pollEntries(w).filter((entry) => entry.body.sessions.some((target) => target.session_id === gone.sid)).length;
  const base = asked();
  assert.equal(base, 1, "it was asked once, the bridge said released");
  // the healthy page keeps being served, new waits for the released one ask nothing (not even a quick check)
  const restarts = pollingState().stats.restarts;
  const again = send({ t: "poll", sessionId: gone.sid, cursor: 0, waitMs: 400 }, gone.tabId);
  await sleep(60);
  assert.equal(pollingState().stats.restarts, restarts, "a wait for a released page does not restart the request that serves the healthy ones");
  const quick = await send({ t: "poll", sessionId: gone.sid, cursor: 0, waitMs: 0 }, gone.tabId);
  assert.deepEqual(quick, { ok: true, data: { cursor: 0, events: [] } });
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kO" }, other.sid);
  assert.equal((await waitOther).data.events[0].knowledge_version, "kO");
  assert.deepEqual((await again).data, { cursor: 0, events: [] });
  assert.equal(asked(), base, "never asked about the released page again");
  // the same page opened again (its id comes back from POST /sessions): it is heard from again
  w.bridge.released = new Set();
  const reopened = await send({ t: "session:open", report: { ...report, url: `${PAGE}?gone` }, tabKey: "gone" }, gone.tabId);
  assert.equal(reopened.data.session_id, gone.sid, "the bridge hands the same page the same id");
  const afterOpen = send({ t: "poll", sessionId: gone.sid, cursor: 0, waitMs: 3000 }, gone.tabId);
  assert.ok(await eventually(() => asked() > base), "asking resumes once the page is opened again");
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kG" }, gone.sid);
  assert.equal((await afterOpen).data.events[0].knowledge_version, "kG");
  await waitGone;
});

test("a superseded page does not make the loop spin: it is asked again at a calm pace, and nothing is reported as an error", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  w.bridge.pollSuperseded = new Set([sid]);
  const wait = send({ t: "poll", sessionId: sid, cursor: 7, waitMs: 2000 }, tabId);
  await sleep(1500);
  assert.ok(pollEntries(w).length <= 5, `${pollEntries(w).length} requests in 1.5 s for one page that is taken over`);
  assert.ok(pollEntries(w).length >= 1);
  assert.deepEqual(await wait, { ok: true, data: { cursor: 7, events: [] } }, "no events, the cursor it asked with, no error");
});

test("a wait that has only a moment left still asks the bridge for a real long poll (more than 250 ms), never for a quick check", async () => {
  const w = await setup();
  const { tabId, sid } = await assistedTab(w);
  const answer = await send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 150 }, tabId);
  assert.deepEqual(answer.data, { cursor: 0, events: [] });
  assert.ok(await eventually(() => pollEntries(w).length >= 1));
  assert.ok(pollEntries(w).every((entry) => entry.body.wait_ms > 250), JSON.stringify(pollEntries(w).map((entry) => entry.body.wait_ms)));
});

test("a real long poll always asks to wait MORE than 250 ms (the bridge answers anything up to 250 at once and never lets it take a page over)", async () => {
  const w = await setup();
  const [a, b] = [await assistedTab(w), await assistedTab(w)];
  const long = send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 20_000 }, a.tabId);
  const short = send({ t: "poll", sessionId: b.sid, cursor: 0, waitMs: 120 }, b.tabId); // only 120 ms left to wait: still a long poll request
  assert.ok(await eventually(() => pollEntries(w).length >= 1));
  assert.deepEqual((await short).data, { cursor: 0, events: [] });
  const quick = await send({ t: "poll", sessionId: a.sid, cursor: 0, waitMs: 0 }, a.tabId);
  assert.equal(quick.ok, true);
  const waits = pollEntries(w).map((entry) => entry.body.wait_ms);
  const quickOnes = waits.filter((wait) => wait <= 250);
  assert.equal(quickOnes.length, 1, `only the explicit quick check is quick: ${JSON.stringify(waits)}`);
  assert.equal(quickOnes[0], 0);
  assert.ok(waits.filter((wait) => wait > 250).length >= 1);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kL" }, a.sid);
  await long;
});

test("a Meridian without the multiplexed endpoint is also asked for long polls only (more than 250 ms per page)", async () => {
  const w = await setup({ bridges: [{ manyPoll: false }], paired: {} });
  const { tabId, sid } = await assistedTab(w);
  const wait = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 150 }, tabId);
  assert.deepEqual((await wait).data, { cursor: 0, events: [] });
  const perSession = w.bridge.log.filter((entry) => /\/poll$/.test(entry.path) && entry.path !== "/v1/poll");
  assert.ok(perSession.length >= 1);
  assert.ok(perSession.every((entry) => entry.body.wait_ms > 250), JSON.stringify(perSession.map((entry) => entry.body.wait_ms)));
});

test("more pages than one request can carry take turns: every page is covered within seconds, and the bridge's 16-page limit never looks like 'no such endpoint'", async () => {
  const w = await setup();
  const tabs = [];
  for (let i = 0; i < 20; i++) tabs.push(await assistedTab(w));
  const waits = tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, tabId));
  await sleep(4500);
  const requests = pollEntries(w);
  assert.ok(requests.every((entry) => entry.body.sessions.length <= 16), "never more than the bridge accepts");
  assert.ok(requests.every((entry) => entry.body.wait_ms <= 3000), "short while pages are waiting for their turn");
  const covered = new Set(requests.flatMap((entry) => entry.body.sessions.map((target) => target.session_id)));
  assert.equal(covered.size, 20, "all twenty pages were asked about");
  assert.equal(pollingState().mode, "many", "no 422, no downgrade");
  const last = tabs[19];
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k20" }, last.sid);
  const answer = await settledWithin(waits[19], 4500);
  assert.equal(answer.data.events[0].knowledge_version, "k20");
  assert.ok(w.bridge.maxConcurrent <= MAX_IN_FLIGHT + HELLO_RESERVE);
});

for (const count of [16, 17, 33]) {
  test(`${count} waiting pages: never more than 16 in one request, every page covered in turn, the cap never looks like 'no such endpoint'`, async () => {
    const w = await setup();
    const tabs = [];
    for (let i = 0; i < count; i++) tabs.push(await assistedTab(w));
    const waits = tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, tabId));
    const everyPageAsked = () => new Set(pollEntries(w).flatMap((entry) => entry.body.sessions.map((target) => target.session_id))).size === count;
    assert.ok(await eventually(everyPageAsked, count > 16 ? 12_000 : 3000), `all ${count} pages were asked about`);
    const requests = pollEntries(w);
    assert.ok(requests.every((entry) => entry.body.sessions.length <= 16), `sizes ${requests.map((entry) => entry.body.sessions.length)}`);
    assert.equal(requests[0].body.sessions.length, Math.min(count, 16), "the first request is as full as the bridge allows");
    assert.ok(requests.every((entry) => entry.body.wait_ms > 250), "always a real long poll");
    if (count <= 16) assert.ok(requests.every((entry) => entry.body.wait_ms > 3000), "everyone fits in one request: it waits properly, no rotation");
    else assert.ok(requests.every((entry) => entry.body.wait_ms <= 3000), "rotation requests are short so every page has its turn within seconds");
    assert.equal(pollingState().mode, "many", "no 422, no downgrade to per-session polling");
    assert.equal(w.bridge.log.filter((entry) => /^\/v1\/sessions\/[^/]+\/poll$/.test(entry.path)).length, 0);
    // the page that had to wait for its turn still gets its event
    const last = tabs[count - 1];
    w.bridge.pushEvent({ type: "knowledge", knowledge_version: "kLast" }, last.sid);
    assert.equal((await settledWithin(waits[count - 1], 8000)).data.events[0].knowledge_version, "kLast");
    for (const wait of waits.slice(0, count - 1)) assert.equal(await settledWithin(wait, 0), "pending");
  });
}

test("with more pages than fit, the least recently covered page goes first and none is starved", async () => {
  const w = await setup();
  const tabs = [];
  for (let i = 0; i < 20; i++) tabs.push(await assistedTab(w));
  tabs.map(({ tabId, sid }) => send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 20_000 }, tabId));
  assert.ok(await eventually(() => pollEntries(w).length >= 2, 8000));
  const [first, second] = pollEntries(w).map((entry) => entry.body.sessions.map((target) => target.session_id));
  assert.equal(first.length, 16);
  const left = tabs.map((tab) => tab.sid).filter((sid) => !first.includes(sid));
  assert.equal(left.length, 4);
  assert.deepEqual(second.slice(0, 4).sort(), left.sort(), "the four pages the first request left out are the first ones of the second");
  assert.equal(second.length, 16);
});

test("two tabs on the same page share one bridge session without taking turns to cancel each other", async () => {
  const w = await setup();
  const first = await assistedTab(w);
  const second = { tabId: w.chrome.__addTab(PAGE), sid: first.sid }; // the same job page open twice: the bridge hands out the SAME session
  await send({ t: "activate-here" }, second.tabId);
  const base = pollingState().stats.requests;
  const waitA = send({ t: "poll", sessionId: first.sid, cursor: 0, waitMs: 10_000 }, first.tabId);
  const waitB = send({ t: "poll", sessionId: first.sid, cursor: 0, waitMs: 10_000 }, second.tabId);
  await sleep(800);
  assert.equal(await settledWithin(waitA, 10), "pending", "the second tab did not end the first tab's wait");
  assert.equal(await settledWithin(waitB, 10), "pending");
  assert.ok(pollingState().stats.requests - base <= 2, `${pollingState().stats.requests - base} requests for two waits on one page`);
  assert.equal(pollingState().waiting, 2);
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k1" }, first.sid);
  const [a, b] = await Promise.all([waitA, waitB]);
  assert.deepEqual([a.data.cursor, a.data.events.length, b.data.cursor, b.data.events.length], [1, 1, 1, 1], "both tabs got the event, once");
});

test("tabs on the same page at different cursors each get exactly the events they have not seen", async () => {
  const w = await setup();
  const first = await assistedTab(w);
  const second = { tabId: w.chrome.__addTab(PAGE), sid: first.sid };
  await send({ t: "activate-here" }, second.tabId);
  for (const version of ["k1", "k2", "k3"]) w.bridge.pushEvent({ type: "knowledge", knowledge_version: version }, first.sid);
  const behind = send({ t: "poll", sessionId: first.sid, cursor: 1, waitMs: 5000 }, first.tabId); // has seen k1
  const ahead = send({ t: "poll", sessionId: first.sid, cursor: 3, waitMs: 5000 }, second.tabId); // has seen everything
  const answerBehind = await behind;
  assert.deepEqual(answerBehind.data.events.map((event) => event.knowledge_version), ["k2", "k3"]);
  assert.equal(await settledWithin(ahead, 300), "pending", "the tab that is up to date keeps waiting; it is not handed what it already has");
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k4" }, first.sid);
  const answerAhead = await ahead;
  assert.deepEqual(answerAhead.data.events.map((event) => event.knowledge_version), ["k4"]);
  assert.equal(answerAhead.data.cursor, 4);
});

test("closing one of two tabs on the same page leaves the other one's wait alone; releasing the page ends both", async () => {
  const w = await setup();
  const first = await assistedTab(w);
  const second = { tabId: w.chrome.__addTab(PAGE), sid: first.sid };
  await send({ t: "activate-here" }, second.tabId);
  const waitA = send({ t: "poll", sessionId: first.sid, cursor: 0, waitMs: 20_000 }, first.tabId);
  const waitB = send({ t: "poll", sessionId: first.sid, cursor: 0, waitMs: 20_000 }, second.tabId);
  await sleep(100);
  dropTab(first.tabId);
  assert.deepEqual((await waitA).data, { cursor: 0, events: [] });
  assert.equal(await settledWithin(waitB, 150), "pending");
  assert.equal(pollingState().inFlight, true, "the page is still watched for the tab that stays");
  dropSession(first.sid);
  assert.deepEqual((await waitB).data, { cursor: 0, events: [] });
});
