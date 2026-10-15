import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { setSettleDelays } from "../../src/background/activation.ts";
import { resetPolling } from "../../src/background/polling.ts";
import { handleMessage, setCtxTimings } from "../../src/background/router.ts";
import { resolveConnection, setProbeTimeouts } from "../../src/shared/connection.ts";
import { getTabState, setTabState } from "../../src/shared/tabs.ts";
import { EXT_ID } from "./chrome-mock.mjs";
import { world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  setSettleDelays([1200, 3500, 8000]);
  setCtxTimings(1500, 8000);
  setProbeTimeouts(2500, 6000);
  resetPolling();
  await current?.close();
  current = null;
});
/** Tab 1 is a tab the worker started (what every content script below speaks for); other tabs are created per test. */
const setup = async (options = { paired: {} }) => {
  current = await world(options);
  setSettleDelays([]);
  current.chrome.__addTab(PAGE, { id: 1 });
  await setTabState(1, { active: true, since: Date.now(), origin: "https://boards.greenhouse.io", auto: false, sessions: [] });
  return current;
};
const activeTab = async (w, url = PAGE) => {
  const tabId = w.chrome.__addTab(url);
  await send({ t: "activate-here" }, content(tabId, 0, url));
  return tabId;
};
/** A tab the worker started but whose connection has not been resolved yet: what the page's FIRST `ctx` finds after a click. */
const startedTab = async (w, extra = {}) => {
  const tabId = w.chrome.__addTab(PAGE, extra);
  await setTabState(tabId, { active: true, since: Date.now(), origin: "https://boards.greenhouse.io", auto: false, sessions: [] });
  for (const frame of w.chrome.__frames.get(tabId).values()) frame.onInstall(frame); // content.js is in every frame
  return tabId;
};
const PAGE = "https://boards.greenhouse.io/acme/jobs/1";
const content = (tabId, frameId = 0, url = PAGE) => ({ id: EXT_ID, tab: { id: tabId, url }, frameId, url });
const optionsPage = (tabId = 900) => ({ id: EXT_ID, tab: { id: tabId }, frameId: 0, url: `chrome-extension://${EXT_ID}/options.html` });
const send = (message, sender) => handleMessage(message, sender);
const sessionBody = { page: { url: PAGE, title: "Engineer", h1: "", lang: "en", company_hint: null, meta: {}, canonical: null, jsonld: [], description_text: "", links: [], user: {} } };

test("malformed messages and foreign senders are refused", async () => {
  const w = await setup();
  assert.deepEqual((await send(null, content(1))).error.code, "invalid");
  assert.deepEqual((await send({ t: 5 }, content(1))).error.code, "invalid");
  assert.deepEqual((await send({ t: "nope" }, content(1))).error.code, "invalid");
  const foreign = await send({ t: "ctx" }, { id: "someotherextensionidxxxxxxxxxxxx", tab: { id: 1 }, frameId: 0 });
  assert.equal(foreign.error.code, "origin");
  assert.equal(w.bridge.log.length, 0, "nothing reached the bridge");
});

test("a sender without our extension id is refused, however it presents itself (fail closed)", async () => {
  const w = await setup();
  const nameless = { tab: { id: 1, url: PAGE }, frameId: 0, url: PAGE };
  const spoofedPage = { id: EXT_ID.toUpperCase(), tab: { id: 1, url: PAGE }, frameId: 0, url: `chrome-extension://${EXT_ID}/options.html` };
  for (const sender of [nameless, spoofedPage, { id: "", tab: { id: 1 }, frameId: 0 }, {}]) {
    for (const message of [{ t: "ctx" }, { t: "status" }, { t: "diagnostics" }, { t: "disconnect" }, { t: "hint:config" }]) {
      const response = await send(message, sender);
      assert.equal(response.ok, false, `${message.t} from ${JSON.stringify(sender)}`);
      assert.equal(response.error.code, "origin");
    }
  }
  assert.equal(w.bridge.log.length, 0, "nothing reached the bridge");
  assert.ok(await w.chrome.storage.local.get("meridian.connection").then((all) => all["meridian.connection"]), "still paired");
});

test("a content script of a tab the worker did not start cannot reach the bridge or change anything", async () => {
  const w = await setup();
  const dormant = w.chrome.__addTab(PAGE); // content.js may be there (injected earlier, or the tab was switched off), nobody started it
  const requests = [
    { t: "ctx" }, { t: "conn:state" }, { t: "status" }, { t: "session:open", report: sessionBody.page, tabKey: "k" },
    { t: "suggest", sessionId: "s1", request: {} }, { t: "poll", sessionId: "s1", cursor: 0, waitMs: 0 }, { t: "regenerate", sessionId: "s1", request: {} },
    { t: "feedback", sessionId: "s1", request: {} }, { t: "track", sessionId: "s1", request: {} }, { t: "research:refresh", sessionId: "s1" },
    { t: "documents" }, { t: "document:content", id: "d1" }, { t: "settings:patch", patch: { skip_filled: false } }, { t: "pair:start" },
    { t: "frame:relay", to: "all", payload: { k: "progress", frameKey: "top", currentKey: null } }, { t: "frames:inject" },
    { t: "site:always-on", enabled: true }, { t: "meridian:open" }, { t: "options:open" }, { t: "ui:get" }, { t: "ui:set", value: {} },
    { t: "badge", text: "9", tone: "ok" },
  ];
  for (const request of requests) {
    const response = await send(request, content(dormant));
    assert.equal(response.ok, false, request.t);
    assert.equal(response.error.code, "inactive", request.t);
  }
  assert.equal(w.bridge.log.length, 0, "nothing reached the bridge");
  assert.deepEqual(w.chrome.__log.created, [], "no tab was opened");
  assert.deepEqual((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"], undefined, "no preference changed");
  // a release or a visibility report that trails a deactivation is simply a no-op, not an error
  assert.equal((await send({ t: "session:release", sessionId: "s1" }, content(dormant))).ok, true);
  assert.equal((await send({ t: "visibility", visible: false }, content(dormant))).ok, true);
});

test("the hint script may ask what to show and offer to start, from the top frame only; nothing else", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  assert.equal((await send({ t: "hint:config" }, content(tabId))).ok, true);
  assert.equal((await send({ t: "hint", fields: 5 }, content(tabId))).ok, true);
  for (const t of ["hint", "hint:hide", "hint:config", "activate-here"]) {
    const response = await send({ t, fields: 5 }, content(tabId, 7));
    assert.equal(response.error.code, "origin", `${t} from a child frame`);
  }
  assert.equal(await getTabState(tabId), null, "nothing started from a frame");
  assert.equal((await send({ t: "documents" }, content(tabId))).error.code, "inactive", "the hint script cannot reach the bridge");
  assert.equal(w.bridge.log.length, 0);
});

test("a script reports itself ON only in a tab this worker started; reporting itself OFF always works", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  await send({ t: "activation", active: true }, content(tabId));
  assert.equal(await getTabState(tabId), null, "a foreign script cannot switch the bridge on for its tab");
  assert.equal((await send({ t: "documents" }, content(tabId))).error.code, "inactive");
  await send({ t: "activation", active: false }, content(1));
  assert.equal(await getTabState(1), null, "any script can switch itself off");
});

test("preferences, permissions, reload, disconnect and diagnostics are for the extension's own pages: never a content script", async () => {
  const w = await setup();
  for (const request of [
    { t: "prefs:patch", patch: { alwaysOn: ["evil.example"] } }, { t: "permissions:granted" }, { t: "extension:reload" },
    { t: "disconnect" }, { t: "diagnostics" },
  ]) {
    const response = await send(request, content(1)); // even from a tab the worker started
    assert.equal(response.error.code, "origin", request.t);
  }
  assert.equal(w.chrome.__log.reloaded, 0);
  assert.equal(w.bridge.log.length, 0);
  assert.equal((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"], undefined);
});

test("bodies that must be objects are checked in the worker before anything is sent", async () => {
  const w = await setup();
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(1));
  const before = w.bridge.log.length;
  assert.equal((await send({ t: "session:open", report: "not a page report", tabKey: "k" }, content(1))).error.code, "invalid");
  for (const t of ["suggest", "regenerate", "feedback", "track"]) {
    for (const bad of ["a string", null, [1, 2], 7, undefined]) {
      const response = await send({ t, sessionId: opened.data.session_id, request: bad }, content(1));
      assert.equal(response.error.code, "invalid", `${t} ${JSON.stringify(bad)}`);
    }
  }
  assert.equal(w.bridge.log.length, before, "no malformed request left the worker");
});

test("a request bigger than the bridge accepts is refused here, without a round trip", async () => {
  const w = await setup();
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(1));
  const before = w.bridge.log.length;
  const huge = { elements: [], mode: "scan", padding: "x".repeat(1_200_000) };
  const response = await send({ t: "suggest", sessionId: opened.data.session_id, request: huge }, content(1));
  assert.equal(response.ok, false);
  assert.equal(response.error.code, "too_large");
  assert.equal(w.bridge.log.length, before, "nothing was sent");
});

test("ctx gives the content script its identity, settings, prefs and connection, never a credential", async () => {
  const w = await setup();
  const tabId = await activeTab(w);
  const top = await send({ t: "ctx" }, content(tabId));
  assert.equal(top.ok, true);
  assert.equal(top.data.tabId, tabId);
  assert.equal(top.data.isTop, true);
  assert.equal(top.data.connection.phase, "ready");
  assert.equal(top.data.e2e, true);
  assert.equal(top.data.prefs.language, "auto");
  assert.equal(JSON.stringify(top).includes(w.issued.token), false);
  assert.equal(JSON.stringify(top).includes(w.issued.secret), false);
  const child = await send({ t: "ctx" }, content(tabId, 12));
  assert.equal(child.data.isTop, false);
  assert.equal(child.data.frameId, 12);
});

test("ctx does not hang when Meridian does not answer", async () => {
  const w = await setup({ bridges: [] });
  await w.chrome.storage.local.set({ "meridian.e2e.ports": [await w.closedPort()] });
  const started = Date.now();
  const response = await send({ t: "ctx" }, content(1));
  assert.equal(response.data.connection.phase, "no_bridge");
  assert.ok(Date.now() - started < 2000);
});

test("status and session:open remember the settings the app reports", async () => {
  const w = await setup();
  w.bridge.settings.skip_filled = false;
  const status = await send({ t: "status" }, content(1));
  assert.equal(status.data.profile.verified_facts, 12);
  assert.equal((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].settings.skip_filled, false);
  w.bridge.settings.skip_filled = true;
  const tabId = await activeTab(w);
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "tab-1" }, content(tabId));
  assert.equal(opened.ok, true);
  const post = w.bridge.log.find((entry) => entry.path === "/v1/sessions" && entry.method === "POST");
  assert.equal(post.body.tab_key, "tab-1");
  assert.equal(post.body.locale, "en-US");
  assert.equal(post.body.page.url, PAGE);
  assert.equal((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].settings.skip_filled, true);
});

test("sessions are remembered per tab and released explicitly; ids are validated before they touch a path", async () => {
  const w = await setup();
  const tabId = await activeTab(w);
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(tabId));
  assert.deepEqual((await getTabState(tabId)).sessions, [opened.data.session_id]);
  const released = await send({ t: "session:release", sessionId: opened.data.session_id }, content(tabId));
  assert.equal(released.ok, true);
  assert.deepEqual((await getTabState(tabId)).sessions, []);
  assert.equal(w.bridge.sessions.size, 0);
  const again = await send({ t: "session:release", sessionId: opened.data.session_id }, content(tabId));
  assert.equal(again.ok, true, "releasing twice is fine");
  for (const bad of ["../x", "a/b", "", "x".repeat(200), 7, null]) {
    const response = await send({ t: "suggest", sessionId: bad, request: {} }, content(tabId));
    assert.equal(response.ok, false);
    assert.equal(response.error.code, "invalid");
  }
  assert.ok(w.bridge.log.every((entry) => !entry.path.includes("..")));
});

test("suggest, regenerate, feedback, track and the research corrections go to the right routes with the right bodies", async () => {
  const w = await setup();
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(1));
  const sid = opened.data.session_id;
  const cases = [
    [{ t: "suggest", sessionId: sid, request: { elements: [], mode: "scan" } }, "suggest", { elements: [], mode: "scan" }],
    [{ t: "regenerate", sessionId: sid, request: { question_id: "q", instruction: "shorter" } }, "regenerate", { question_id: "q", instruction: "shorter" }],
    [{ t: "feedback", sessionId: sid, request: { question_id: "q", outcome: "edited", remember: "none" } }, "feedback", { question_id: "q", outcome: "edited", remember: "none" }],
    [{ t: "track", sessionId: sid, request: { event: "record", evidence: "", notes: "" } }, "track", { event: "record", evidence: "", notes: "" }],
    [{ t: "research:site", sessionId: sid, domain: "acme.com" }, "research/site", { domain: "acme.com" }],
    [{ t: "research:company", sessionId: sid, company: "Acme Corp" }, "research/company", { company: "Acme Corp" }],
    [{ t: "research:refresh", sessionId: sid }, "research/refresh", {}],
  ];
  for (const [message, route, body] of cases) {
    const response = await send(message, content(1));
    assert.equal(response.ok, true, route);
    const entry = w.bridge.log.filter((item) => item.path === `/v1/sessions/${sid}/${route}`).pop();
    assert.equal(entry.method, "POST", route);
    assert.deepEqual(entry.body, body, route);
    assert.match(entry.auth, /^Bearer mrd1\./);
  }
});

test("poll returns promptly with events and after waitMs without them, and clamps the wait", async () => {
  const w = await setup();
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(1));
  const sid = opened.data.session_id;
  const started = Date.now();
  const quiet = await send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 150 }, content(1));
  assert.equal(quiet.ok, true);
  assert.deepEqual(quiet.data, { cursor: 0, events: [] });
  assert.ok(Date.now() - started >= 140 && Date.now() - started < 1500);
  const pending = send({ t: "poll", sessionId: sid, cursor: 0, waitMs: 5000 }, content(1));
  setTimeout(() => w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k2" }, sid), 40);
  const woke = await pending;
  assert.equal(woke.data.events[0].knowledge_version, "k2");
  assert.equal(woke.data.cursor, 1);
  assert.ok(Date.now() - started < 2500, "an event ends the wait immediately");
});

test("poll goes to the ONE multiplexed endpoint, with sanitised cursor and a wait the bridge allows", async () => {
  const w = await setup();
  const opened = await send({ t: "session:open", report: sessionBody.page, tabKey: "k" }, content(1));
  const sid = opened.data.session_id;
  w.bridge.pushEvent({ type: "knowledge", knowledge_version: "k9" }, sid);
  const answer = await send({ t: "poll", sessionId: sid, cursor: -3, waitMs: 99999 }, content(1));
  assert.equal(answer.data.events[0].knowledge_version, "k9");
  const entry = w.bridge.log.filter((item) => item.path === "/v1/poll").pop();
  assert.deepEqual(entry.body.sessions, [{ session_id: sid, cursor: 0 }]);
  assert.ok(entry.body.wait_ms > 24_000 && entry.body.wait_ms <= 25_000, `asked the bridge to wait ${entry.body.wait_ms} ms: the 99999 was clamped to 25 s`);
  assert.ok(w.bridge.log.every((item) => !item.path.endsWith("/poll") || item.path === "/v1/poll"), "no per-session poll");
  for (const bad of ["../x", "", 7, null]) {
    assert.equal((await send({ t: "poll", sessionId: bad, cursor: 0, waitMs: 10 }, content(1))).error.code, "invalid");
  }
});

test("documents are listed and fetched by validated id", async () => {
  const w = await setup();
  assert.deepEqual((await send({ t: "documents" }, content(1))).data, []);
  const doc = await send({ t: "document:content", id: "doc-1.v2" }, content(1));
  assert.equal(doc.data.filename, "cv.pdf");
  assert.equal((await send({ t: "document:content", id: "../../x" }, content(1))).error.code, "invalid");
  assert.ok(w.bridge.log.some((entry) => entry.path === "/v1/documents/doc-1.v2/content"));
});

test("prefs:patch keeps only known keys with valid values; new always-on sites ask for the permission", async () => {
  const w = await setup();
  const response = await send({
    t: "prefs:patch",
    patch: { language: "es", alwaysOn: ["https://Jobs.Acme.com/apply", "not a host", "*.evil.com"], hintOff: "greenhouse.io", welcomed: true, evil: 1, token: "x" },
  }, optionsPage());
  assert.equal(response.ok, true);
  const prefs = (await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"];
  assert.deepEqual(prefs, { language: "es", alwaysOn: ["jobs.acme.com"], hintOff: ["greenhouse.io"], settings: null, welcomed: true });
  assert.ok(w.chrome.__granted.has("https://jobs.acme.com/*"), "the worker asked for the new site");
  w.chrome.permissions.__decision = false;
  await send({ t: "prefs:patch", patch: { alwaysOn: ["jobs.acme.com", "careers.beta.org"] } }, optionsPage());
  const opened = w.chrome.__log.created.find((entry) => entry.url.includes("site=careers.beta.org"));
  assert.ok(opened, "when the browser will not prompt from here, the options page offers the one-click allow");
  assert.match(opened.url, /reason=permission/);
});

test("settings:patch sends only the editable keys and stores the answer", async () => {
  const w = await setup();
  const response = await send({
    t: "settings:patch", patch: { skip_filled: false, language: "es", enabled: false, always_on_sites: ["x.com"], draft_budget_usd: 99, tab_inserts_protected: true },
  }, content(1));
  assert.equal(response.ok, true);
  const patch = w.bridge.log.find((entry) => entry.method === "PATCH");
  assert.deepEqual(patch.body, { language: "es", skip_filled: false, tab_inserts_protected: true });
  assert.equal(response.data.skip_filled, false);
  assert.equal((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].settings.language, "es");
});

test("meridian:open launches the app scheme and never closes a tab it cannot vouch for", async () => {
  const w = await setup();
  await send({ t: "meridian:open" }, content(1));
  assert.deepEqual(w.chrome.__log.created.map((entry) => entry.url), ["meridian://open"]);
  assert.equal(w.chrome.__log.alarms.size, 0);
  assert.deepEqual(w.chrome.__log.removed, []);
});

test("options:open and permissions:granted", async () => {
  const w = await setup();
  await send({ t: "options:open", anchor: "permissions" }, content(1));
  assert.match(w.chrome.__log.created[0].url, /options\.html#permissions$/);
  assert.equal((await send({ t: "permissions:granted" }, optionsPage())).ok, true);
});

test("frame relay forwards focus hand-offs inside the sender's tab only", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE, { frames: [{ frameId: 3 }] });
  const other = w.chrome.__addTab("https://other.example.org/");
  await send({ t: "activate-here" }, content(tabId));
  await w.chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content.js"] });
  await w.chrome.scripting.executeScript({ target: { tabId: other, allFrames: true }, files: ["content.js"] });
  w.chrome.__received.length = 0;
  const payload = { k: "focus-after-frame", frameKey: "f3" };
  await send({ t: "frame:relay", to: "top", payload }, content(tabId, 3));
  assert.deepEqual(w.chrome.__received.map((entry) => [entry.tabId, entry.frameId, entry.message]), [[tabId, 0, { t: "frame", payload, fromFrameId: 3 }]]);
  w.chrome.__received.length = 0;
  await send({ t: "frame:relay", to: { frameId: 3 }, payload: { k: "focus-first", frameKey: "f3" } }, content(tabId, 0));
  assert.deepEqual(w.chrome.__received.map((entry) => entry.frameId), [3]);
  w.chrome.__received.length = 0;
  await send({ t: "frame:relay", to: "all", payload: { k: "progress", frameKey: "top", currentKey: null } }, content(tabId, 0));
  assert.deepEqual(w.chrome.__received.map((entry) => entry.frameId).sort(), [0, 3]);
  assert.ok(w.chrome.__received.every((entry) => entry.tabId === tabId), "never leaks into another tab");
  w.chrome.__received.length = 0;
  for (const bad of [{ to: "everyone", payload }, { to: { frameId: -1 }, payload }, { to: "top", payload: "x" }, { to: "top", payload: {} }]) {
    await send({ t: "frame:relay", ...bad }, content(tabId, 0));
  }
  assert.equal(w.chrome.__received.length, 0);
});

test("badge: text from an active tab is shown, tone off means the assistant stopped, inactive tabs are ignored", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  assert.equal((await send({ t: "badge", text: "3", tone: "ok" }, content(tabId))).error.code, "inactive");
  assert.equal(w.chrome.__log.badges.get(tabId), undefined, "ignored while the assistant is not active in this tab");
  await send({ t: "activate-here" }, content(tabId));
  w.chrome.__log.badges.clear();
  await send({ t: "badge", text: "12345678", tone: "warn" }, content(tabId));
  assert.equal(w.chrome.__log.badges.get(tabId).text, "1234", "four characters at most");
  await send({ t: "badge", text: "", tone: "off" }, content(tabId));
  assert.equal(await getTabState(tabId), null);
  assert.equal(w.chrome.__log.badges.get(tabId).text, "");
});

test("hint lights a dot on inactive tabs only; activate-here starts the tab like a toolbar click", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  await send({ t: "hint", fields: 6 }, content(tabId));
  assert.equal(w.chrome.__log.badges.get(tabId).text, "●");
  await send({ t: "hint", fields: 0 }, content(tabId));
  assert.equal(w.chrome.__log.badges.get(tabId).text, "");
  await send({ t: "activate-here" }, content(tabId));
  assert.equal((await getTabState(tabId)).active, true);
  assert.equal(w.chrome.__log.badges.get(tabId).text, "ON");
  await send({ t: "hint", fields: 5 }, content(tabId));
  assert.equal(w.chrome.__log.badges.get(tabId).text, "ON", "an active tab keeps its own badge");
});

test("site rules come from the sender, never from the payload", async () => {
  const w = await setup();
  w.chrome.__granted.add("https://*/*");
  const tabId = await activeTab(w);
  await send({ t: "site:always-on", enabled: true, host: "evil.com" }, content(tabId));
  assert.deepEqual((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].alwaysOn, ["boards.greenhouse.io"]);
  await send({ t: "site:always-on", enabled: false }, content(tabId));
  assert.deepEqual((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].alwaysOn, []);
  await send({ t: "hint:hide", host: "evil.com" }, content(tabId));
  await send({ t: "hint:hide" }, content(tabId));
  assert.deepEqual((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].hintOff, ["boards.greenhouse.io"]);
});

// ---- UX-13: "Start automatically on this site" says ON only when it is true, and asks in context ----
const prefsOf = async (w) => (await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"] ?? {};

test("UX-13: switching 'start automatically' on without the permission stores nothing, says so, and asks in a small window", async () => {
  const w = await setup();
  w.chrome.permissions.__decision = false; // a worker has no click to ask with
  const tabId = await activeTab(w);
  const response = await send({ t: "site:always-on", enabled: true }, content(tabId));
  assert.equal(response.ok, false);
  assert.equal(response.error.code, "permission", "the panel is told the rule is NOT in effect, so its switch stays off");
  assert.deepEqual((await prefsOf(w)).alwaysOn ?? [], [], "a rule without the permission is never stored");
  assert.equal(w.chrome.__log.windows.length, 1, "the question opens in its own small window");
  const [popup] = w.chrome.__log.windows;
  assert.equal(popup.type, "popup");
  assert.equal(popup.focused, true);
  const url = new URL(popup.url);
  assert.equal(url.pathname, "/options.html");
  assert.equal(url.searchParams.get("reason"), "permission");
  assert.equal(url.searchParams.get("site"), "boards.greenhouse.io");
  assert.equal(url.searchParams.get("intent"), "always-on");
  assert.equal(url.searchParams.get("popup"), "1");
  assert.equal(w.chrome.__log.created.length, 0, "no tab is opened: the application tab keeps the user's attention");
  assert.equal(w.chrome.__log.updated.filter((entry) => entry.active === true && entry.id === tabId).length, 0, "and it is not switched away from");
});

test("UX-13: with the permission the rule is stored, and a browser that lets the worker ask is asked", async () => {
  const w = await setup();
  const tabId = await activeTab(w);
  assert.equal((await send({ t: "site:always-on", enabled: true }, content(tabId))).ok, true);
  assert.ok(w.chrome.__granted.has("https://boards.greenhouse.io/*"), "the worker asked, the browser agreed");
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["boards.greenhouse.io"]);
  assert.equal(w.chrome.__log.windows.length + w.chrome.__log.created.length, 0, "nothing had to be opened");
  // already allowed (all sites): no question at all
  const again = await send({ t: "site:always-on", enabled: true }, content(tabId));
  assert.equal(again.ok, true);
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["boards.greenhouse.io"], "no duplicate");
});

test("UX-13: where the browser has no popup windows the options tab asks instead, and an open options tab is reused", async () => {
  const w = await setup();
  w.chrome.permissions.__decision = false;
  w.chrome.windows.__fail = true;
  const tabId = await activeTab(w);
  assert.equal((await send({ t: "site:always-on", enabled: true }, content(tabId))).ok, false);
  assert.equal(w.chrome.__log.windows.length, 0);
  assert.equal(w.chrome.__log.created.length, 1);
  assert.match(w.chrome.__log.created[0].url, /intent=always-on/);
  assert.match(w.chrome.__log.created[0].url, /reason=permission/);
  assert.doesNotMatch(w.chrome.__log.created[0].url, /popup=1/, "the full page, not the one-question layout, in a tab");
  // the options tab is now open: the next question reuses it instead of piling up windows
  w.chrome.windows.__fail = false;
  const other = w.chrome.__addTab("https://careers.beta.org/apply");
  await setTabState(other, { active: true, since: Date.now(), origin: "https://careers.beta.org", auto: false, sessions: [] });
  assert.equal((await send({ t: "site:always-on", enabled: true }, content(other, 0, "https://careers.beta.org/apply"))).ok, false);
  assert.equal(w.chrome.__log.windows.length, 0, "no second window");
  assert.ok(w.chrome.__log.updated.some((entry) => String(entry.url).includes("site=careers.beta.org")), "the open options tab was pointed at the new question");
});

test("UX-13: switching off always works and needs no permission", async () => {
  const w = await setup();
  w.chrome.permissions.__decision = false;
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["boards.greenhouse.io", "other.example.org"] } });
  const tabId = await activeTab(w);
  assert.equal((await send({ t: "site:always-on", enabled: false }, content(tabId))).ok, true);
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["other.example.org"]);
  assert.equal(w.chrome.__log.windows.length + w.chrome.__log.created.length, 0);
});

test("UX-13: a page is told only about the rules the browser can honour (the panel's switch shows the real state)", async () => {
  const w = await setup();
  w.chrome.permissions.__decision = false;
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["boards.greenhouse.io", "careers.beta.org"] } });
  const tabId = await activeTab(w);
  let info = (await send({ t: "ctx" }, content(tabId))).data;
  assert.deepEqual(info.prefs.alwaysOn, [], "stored, but neither site is allowed: nothing is 'on'");
  w.chrome.__granted.add("https://careers.beta.org/*");
  info = (await send({ t: "ctx" }, content(tabId))).data;
  assert.deepEqual(info.prefs.alwaysOn, ["careers.beta.org"], "only the allowed one");
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["boards.greenhouse.io", "careers.beta.org"], "storage is untouched: the options page still lists both");
  w.chrome.__granted.add("https://*/*");
  info = (await send({ t: "ctx" }, content(tabId))).data;
  assert.deepEqual(info.prefs.alwaysOn, ["boards.greenhouse.io", "careers.beta.org"], "all sites allowed: both are real");
});

const appSettingsOf = (w, sites, extra = {}) => ({ ...w.bridge.settings, always_on_sites: sites, ...extra });

test("always_on_sites: the panel's switch reads ON where the site starts by itself for a rule that is not its own entry", async () => {
  const w = await setup();
  w.chrome.permissions.__decision = false;
  const tabId = await activeTab(w);
  const ask = async () => (await send({ t: "ctx" }, content(tabId))).data.prefs.alwaysOn;
  // a rule of this browser for the PARENT site, and a rule set in Meridian for another one: neither allowed yet
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["greenhouse.io"], settings: appSettingsOf(w, []) } });
  assert.deepEqual(await ask(), [], "no permission: it does not start, so it is not 'on'");
  w.chrome.__granted.add("https://boards.greenhouse.io/*");
  assert.deepEqual(await ask(), ["boards.greenhouse.io"], "allowed, and the parent rule covers this site: the switch is on");
  // Meridian's list names this very site (and the browser has no rule of its own)
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["boards.greenhouse.io"]) } });
  assert.deepEqual(await ask(), ["boards.greenhouse.io"], "a rule set in Meridian counts");
  // ... unless Meridian has the assistant switched off: then nothing starts anywhere
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["boards.greenhouse.io"], { enabled: false }) } });
  assert.deepEqual(await ask(), [], "switched off in Meridian: not 'on'");
  // another site's rule never says anything about this page
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["careers.beta.org"]) } });
  w.chrome.__granted.add("https://careers.beta.org/*");
  assert.deepEqual(await ask(), []);
  // the other frames of the page are told about their own rules only (the switch is the top frame's)
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["boards.greenhouse.io"]) } });
  assert.deepEqual((await send({ t: "ctx" }, content(tabId, 5))).data.prefs.alwaysOn, []);
});

test("always_on_sites: switching off removes the site's own entry and says so when another rule still starts the assistant here", async () => {
  const w = await setup();
  const tabId = await activeTab(w);
  const off = () => send({ t: "site:always-on", enabled: false }, content(tabId));
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["boards.greenhouse.io", "other.example.org"], settings: appSettingsOf(w, []) } });
  assert.equal((await off()).ok, true);
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["other.example.org"]);
  // a rule for the parent site is not this switch's to remove: the page is told (its switch stays on), and the rule stays
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["boards.greenhouse.io", "greenhouse.io"], settings: appSettingsOf(w, []) } });
  const parent = await off();
  assert.equal(parent.ok, false);
  assert.equal(parent.error.code, "rule");
  assert.match(parent.error.detail, /greenhouse\.io/);
  assert.deepEqual((await prefsOf(w)).alwaysOn, ["greenhouse.io"], "its own entry went, the parent rule stays");
  // so does a rule set in Meridian
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["boards.greenhouse.io"]) } });
  const app = await off();
  assert.equal(app.ok, false);
  assert.equal(app.error.code, "rule");
  assert.match(app.error.detail, /Meridian/);
  // with Meridian's assistant switched off nothing starts here, so nothing is left to explain
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettingsOf(w, ["boards.greenhouse.io"], { enabled: false }) } });
  assert.equal((await off()).ok, true);
});

test("UX-13: the options page stores the rule, then reports the permission: the open tabs of that site start at once", async () => {
  const w = await setup();
  const beta = "https://careers.beta.org/apply";
  const tabId = w.chrome.__addTab(beta);
  w.chrome.__granted.add("https://careers.beta.org/*"); // what the click on "Allow" gave
  await send({ t: "prefs:patch", patch: { alwaysOn: ["careers.beta.org"] } }, optionsPage());
  assert.equal(await getTabState(tabId), null, "the rule alone starts nothing");
  assert.equal(w.chrome.__log.windows.length + w.chrome.__log.created.length, 0, "and the permission is there, so nobody is asked again");
  await send({ t: "permissions:granted" }, optionsPage());
  assert.equal((await getTabState(tabId)).auto, true, "reporting the permission starts the tab, without a reload");
});

test("options:open passes only the sections the options page has", async () => {
  const w = await setup();
  const tabId = await activeTab(w);
  const optionsUrl = () => [...w.chrome.__tabs.values()].filter((tab) => tab.url.includes("options.html")).map((tab) => tab.url).at(-1);
  for (const anchor of ["help", "permissions", "connection", "behaviour"]) {
    await send({ t: "options:open", anchor }, content(tabId));
    assert.ok(optionsUrl().endsWith(`#${anchor}`), anchor);
  }
  await send({ t: "options:open", anchor: "../evil" }, content(tabId));
  assert.ok(!optionsUrl().includes("evil") && !optionsUrl().includes("#"), "an unknown anchor is dropped");
});

test("frames:inject reaches frames added after activation", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  await send({ t: "activate-here" }, content(tabId));
  w.chrome.__addFrame(tabId, 8);
  await send({ t: "frames:inject" }, content(tabId));
  assert.ok(w.chrome.__log.injected.some((entry) => entry.frameId === 8));
});

test("reload, disconnect and diagnostics are for the extension's own pages only", async () => {
  const w = await setup();
  for (const t of ["extension:reload", "disconnect", "diagnostics"]) {
    assert.equal((await send({ t }, content(1))).error.code, "origin", t);
  }
  assert.equal(w.chrome.__log.reloaded, 0);
  assert.ok(await w.chrome.storage.local.get("meridian.connection").then((all) => all["meridian.connection"]), "still paired");
  assert.equal((await send({ t: "extension:reload" }, optionsPage())).ok, true);
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.equal(w.chrome.__log.reloaded, 1);
});

test("diagnostics describe the connection without any secret", async () => {
  const w = await setup();
  const response = await send({ t: "diagnostics" }, optionsPage());
  assert.equal(response.ok, true);
  const text = JSON.stringify(response.data);
  assert.equal(text.includes(w.issued.token), false);
  assert.equal(text.includes(w.issued.secret), false);
  assert.equal(response.data.phase, "ready");
  assert.equal(response.data.paired, true);
  assert.equal(response.data.protocol, 1);
  assert.equal(response.data.extensionVersion, "0.1.0");
  assert.deepEqual(response.data.candidatePorts, w.ports);
  assert.equal(response.data.permissions.allSites, false);
});

test("disconnect from the options page revokes this browser in Meridian and then forgets the credential", async () => {
  const w = await setup();
  const response = await send({ t: "disconnect" }, optionsPage());
  assert.equal(response.data.phase, "unpaired");
  assert.equal(response.data.remoteRevoked, true, "the options page can tell the user Meridian dropped this browser too");
  assert.deepEqual(w.bridge.disconnects, [w.issued.clientId]);
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], undefined);
  const again = await send({ t: "disconnect" }, optionsPage());
  assert.equal(again.data.remoteRevoked, false, "nothing left to revoke");
  assert.equal(w.bridge.disconnects.length, 1, "and nobody was asked twice");
});

test("bridge failures come back as errors with the connection state, never as thrown exceptions", async () => {
  const w = await setup();
  w.bridge.revoke(w.issued.clientId);
  const response = await send({ t: "status" }, content(1));
  assert.equal(response.ok, false);
  assert.equal(response.error.connection.phase, "revoked");
  await w.bridge.stop();
  const gone = await send({ t: "documents" }, content(1));
  assert.equal(gone.ok, false);
  assert.ok(["no_bridge", "revoked"].includes(gone.error.code));
});

test("the hint script is told what to show without touching storage or the network", async () => {
  const w = await setup({ bridges: [] });
  const tabId = w.chrome.__addTab(PAGE);
  const sender = content(tabId);
  const first = await send({ t: "hint:config" }, sender);
  assert.deepEqual(first.data, { show: true, language: "en", connected: false });
  await w.chrome.storage.local.set({
    "meridian.prefs": { language: "es", hintOff: ["greenhouse.io"], settings: null },
    "meridian.connection": { port: 47615, clientId: "5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11", token: "mrd1.5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11.Zm9yLXRlc3RzLW9ubHktc2VjcmV0LXZhbHVlLTEyMzQ" },
  });
  const hidden = await send({ t: "hint:config" }, sender);
  assert.deepEqual(hidden.data, { show: false, language: "es", connected: true }, "hidden for the site and its subdomains");
  assert.equal(JSON.stringify(hidden).includes("mrd1."), false);
  await w.chrome.storage.local.set({ "meridian.prefs": { settings: { hint_on_ats: false } } });
  assert.equal((await send({ t: "hint:config" }, sender)).data.show, false, "the app's own switch wins");
  await w.chrome.storage.local.set({ "meridian.prefs": { settings: { enabled: false } } });
  assert.equal((await send({ t: "hint:config" }, sender)).data.show, false);
});

test("the hint does not call a browser connected once Meridian stopped recognising its credential (which is kept, not forgotten)", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  assert.equal((await send({ t: "hint:config" }, content(tabId))).data.connected, true);
  w.bridge.revoke(w.issued.clientId);
  assert.equal((await resolveConnection({ force: true })).state.phase, "revoked");
  assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], "the credential itself is still stored");
  assert.equal((await send({ t: "hint:config" }, content(tabId))).data.connected, false, "so the pill offers to connect, not 'ready'");
});

test("the hint does not call a browser connected while the listener cannot be recognised either", async () => {
  const w = await setup();
  const tabId = w.chrome.__addTab(PAGE);
  w.bridge.clients.clear(); // a Meridian that does not know this browser (reinstalled, restored backup): no proof, not even a revoked one
  assert.equal((await resolveConnection({ force: true })).state.phase, "unknown_listener");
  assert.equal((await send({ t: "hint:config" }, content(tabId))).data.connected, false);
});

test("the panel's remembered look is stored by the worker, validated and bounded", async () => {
  const w = await setup();
  assert.deepEqual((await send({ t: "ui:get" }, content(1))).data, { dock: "right", dismissed: {} });
  const saved = await send({ t: "ui:set", value: { dock: "left", dismissed: { research: 1700000000000, "bad key!": 1, nan: "x" }, extra: "dropped" } }, content(1));
  assert.deepEqual(saved.data, { dock: "left", dismissed: { research: 1700000000000 } });
  assert.deepEqual((await send({ t: "ui:get" }, content(1, 4))).data, saved.data);
  const many = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`n${i}`, i]));
  assert.equal(Object.keys((await send({ t: "ui:set", value: { dock: "sideways", dismissed: many } }, content(1))).data.dismissed).length, 24);
  assert.equal((await send({ t: "ui:get" }, content(1))).data.dock, "right");
  assert.equal(w.bridge.log.length, 0, "purely local");
});

// ---- wave 2: a patient first connection check ----------------------------------------------------------------------------

const lateStates = (w, tabId, frameId = 0) => w.chrome.__received
  .filter((entry) => entry.tabId === tabId && entry.frameId === frameId && entry.message.t === "conn:changed").map((entry) => entry.message.connection);
const waitFor = async (condition, ms = 4000) => {
  const deadline = Date.now() + ms;
  while (!(await condition()) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 20));
};

test("a slow first connection check shows a calm 'connecting' at once and the real state follows to that frame", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 700 }], paired: {} });
  setProbeTimeouts(150, 3000);
  setCtxTimings(120, 4000);
  const tabId = await startedTab(w);
  const started = Date.now();
  const reply = await send({ t: "ctx" }, content(tabId));
  assert.ok(Date.now() - started < 500, `ctx took ${Date.now() - started} ms`);
  assert.equal(reply.ok, true);
  assert.equal(reply.data.connection.phase, "connecting", "not an error: Meridian simply has not answered yet");
  assert.equal(reply.data.connection.retryAfterMs, undefined);
  await waitFor(() => lateStates(w, tabId).length > 0);
  const [late] = lateStates(w, tabId);
  assert.equal(late.phase, "ready", "the answer is pushed to the page that asked");
  assert.equal(late.port, w.bridge.port);
  assert.equal(lateStates(w, tabId, 3).length, 0, "other frames of the tab are not told about it");
});

test("each frame that asked gets its own late answer", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 500 }], paired: {} });
  setProbeTimeouts(150, 3000);
  setCtxTimings(100, 4000);
  const tabId = await startedTab(w, { frames: [{ frameId: 4 }] });
  const replies = await Promise.all([send({ t: "ctx" }, content(tabId, 0)), send({ t: "ctx" }, content(tabId, 4))]);
  assert.deepEqual(replies.map((reply) => reply.data.connection.phase), ["connecting", "connecting"]);
  await waitFor(() => lateStates(w, tabId, 0).length > 0 && lateStates(w, tabId, 4).length > 0);
  assert.equal(lateStates(w, tabId, 0)[0].phase, "ready");
  assert.equal(lateStates(w, tabId, 4)[0].phase, "ready");
});

test("a check that is still running after the budget is reported as a transient error with a QUICK retry, and the answer still follows", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 900 }], paired: {} });
  setProbeTimeouts(150, 3000);
  setCtxTimings(80, 300);
  const tabId = await startedTab(w);
  const reply = await send({ t: "ctx" }, content(tabId));
  assert.equal(reply.data.connection.phase, "connecting");
  await waitFor(() => lateStates(w, tabId).length >= 2);
  const [giveUp, final] = lateStates(w, tabId);
  assert.equal(giveUp.phase, "error");
  assert.equal(giveUp.retryAfterMs, 1500, "first retry after 1.5 s, not the page's own 8 s timer");
  assert.equal(final.phase, "ready");
});

test("a fast check answers in the reply itself and nothing is pushed afterwards", async () => {
  const w = await setup();
  const tabId = await startedTab(w);
  const reply = await send({ t: "ctx" }, content(tabId));
  assert.equal(reply.data.connection.phase, "ready");
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.deepEqual(lateStates(w, tabId), []);
});

test("while a slow re-check runs, a connection that was ready a moment ago stays ready for the page", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 600 }], paired: {} });
  setProbeTimeouts(150, 3000);
  setCtxTimings(100, 4000);
  const tabId = await startedTab(w);
  await resolveConnection({ force: true });
  const cache = (await w.chrome.storage.session.get("meridian.conn"))["meridian.conn"];
  await w.chrome.storage.session.set({ "meridian.conn": { ...cache, verifiedAt: Date.now() - 60_000, at: 0 } }); // known, but long unverified
  const reply = await send({ t: "ctx" }, content(tabId));
  assert.equal(reply.data.connection.phase, "ready", "the last known state, not a flash of 'connecting'");
  await new Promise((resolve) => setTimeout(resolve, 900));
  assert.deepEqual(lateStates(w, tabId), [], "it was still true: nothing to correct");
});

test("with no Meridian at all the answer is immediate (nothing to wait for) and carries the quick first retry", async () => {
  const w = await setup({ bridges: [] });
  await w.chrome.storage.local.set({ "meridian.e2e.ports": [await w.closedPort()] });
  const tabId = await startedTab(w);
  const started = Date.now();
  const reply = await send({ t: "ctx" }, content(tabId));
  assert.ok(Date.now() - started < 700);
  assert.equal(reply.data.connection.phase, "no_bridge");
  assert.equal(reply.data.connection.retryAfterMs, 1500);
});
