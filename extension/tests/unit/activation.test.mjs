import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  activateTab, deactivateTab, onActionClicked, onConnectionState, onPermissionsChanged, onTabLoaded, refreshAppSettings, refreshBadge,
  refreshFrames, setActiveFromContent, setSettingsFreshness, setSettleDelays,
} from "../../src/background/activation.ts";
import { settingsHeardAt } from "../../src/shared/storage.ts";
import { getTabState, setTabState } from "../../src/shared/tabs.ts";
import { world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  setSettleDelays([1200, 3500, 8000]);
  setSettingsFreshness(30_000, 2500);
  await current?.close();
  current = null;
});
const setup = async (options) => {
  current = await world(options);
  setSettleDelays([]);
  return current;
};
const badge = (w, tabId) => w.chrome.__log.badges.get(tabId);
const optionsTabs = (w) => w.chrome.__log.created.filter((entry) => entry.url.includes("options.html"));
const GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/1";
const COMPANY = "https://acme.example.com/careers/apply";

test("toolbar click injects content.js into the page, tells it to start and shows ON when Meridian is connected", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.deepEqual(w.chrome.__log.injected.map((entry) => entry.files), [["content.js"]]);
  assert.deepEqual(w.chrome.__messagesTo(tabId, "activate").map((entry) => entry.message), [{ t: "activate", auto: false }]);
  const state = await getTabState(tabId);
  assert.equal(state.active, true);
  assert.equal(state.origin, "https://acme.example.com");
  assert.equal(badge(w, tabId).text, "ON");
  assert.match(badge(w, tabId).title, /is on/i);
  assert.equal(optionsTabs(w).length, 0, "nothing opens when activation works");
});

test("without Meridian the assistant still starts and the badge asks for attention", async () => {
  const w = await setup({ bridges: [] });
  await w.chrome.storage.local.set({ "meridian.e2e.ports": [await w.closedPort()] });
  const tabId = w.chrome.__addTab(COMPANY);
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.equal((await getTabState(tabId)).active, true);
  assert.equal(badge(w, tabId).text, "!");
});

const topFrame = (w, tabId) => w.chrome.__frames.get(tabId).get(0);
const sentTo = (w, tabId, type) => w.chrome.__log.sent.filter((entry) => entry.tabId === tabId && entry.message.t === type);

test("a click on a running tab ASKS the page to toggle; the worker decides nothing and follows the page's own report", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await onActionClicked({ id: tabId, url: COMPANY });
  w.chrome.__log.sent.length = 0;
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.deepEqual(w.chrome.__log.sent.map((entry) => [entry.frameId, entry.message]), [[0, { t: "activate", auto: false, toggle: true }]], "to the top frame, once");
  assert.equal(sentTo(w, tabId, "deactivate").length, 0, "the worker never switches the page off by itself");
  assert.equal((await getTabState(tabId)).active, true, "its own bookkeeping is untouched by the click");
  assert.equal(badge(w, tabId).text, "ON");
  // the page decides to switch off (its panel was showing) and says so
  await setActiveFromContent(tabId, false);
  assert.equal(await getTabState(tabId), null);
  assert.equal(badge(w, tabId).text, "");
  // the next click on a tab that is off starts it, plainly
  w.chrome.__log.sent.length = 0;
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.deepEqual(w.chrome.__log.sent.map((entry) => entry.message), [{ t: "activate", auto: false }]);
  assert.equal((await getTabState(tabId)).active, true);
});

test("a click after the worker re-activated the tab by itself on a quiet page asks for the panel instead of switching off", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  // same site, a page without a form: the worker follows with an automatic (quiet) start
  w.chrome.__navigate(tabId, `${COMPANY}/thanks`);
  await onTabLoaded(tabId, `${COMPANY}/thanks`);
  assert.equal((await getTabState(tabId)).auto, true);
  w.chrome.__log.sent.length = 0;
  await onActionClicked({ id: tabId, url: `${COMPANY}/thanks` });
  assert.deepEqual(w.chrome.__log.sent.map((entry) => entry.message), [{ t: "activate", auto: false, toggle: true }]);
  assert.equal((await getTabState(tabId)).active, true, "still active: the page shows its panel and keeps going");
  assert.equal(sentTo(w, tabId, "deactivate").length, 0);
});

test("only the top frame is asked to toggle (the panel lives there); child frames follow it", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY, { frames: [{ frameId: 5 }, { frameId: 6 }] });
  await onActionClicked({ id: tabId, url: COMPANY });
  w.chrome.__received.length = 0;
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.deepEqual(w.chrome.__received.map((entry) => [entry.frameId, entry.message.toggle]), [[0, true]]);
});

test("a click when the page's script is gone (reloaded) starts afresh instead of asking a script that is not there", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await onActionClicked({ id: tabId, url: COMPANY });
  w.chrome.__navigate(tabId, COMPANY); // the page reloaded: stored state says active, the script is gone
  w.chrome.__log.sent.length = 0;
  await onActionClicked({ id: tabId, url: COMPANY });
  assert.deepEqual(w.chrome.__log.sent.map((entry) => entry.message), [{ t: "activate", auto: false }], "a fresh start, not a toggle");
  assert.equal((await getTabState(tabId)).active, true);
  assert.ok(w.chrome.__log.injected.length >= 2, "content.js was injected again");
});

test("a script that is there but deaf to messages is not left with a click that does nothing", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await onActionClicked({ id: tabId, url: COMPANY });
  topFrame(w, tabId).onMessage = null; // injected, but no listener answers
  w.chrome.__log.sent.length = 0;
  await onActionClicked({ id: tabId, url: COMPANY });
  const kinds = w.chrome.__log.sent.map((entry) => entry.message.toggle === true ? "toggle" : entry.message.t);
  assert.deepEqual(kinds, ["toggle", "activate"], "the toggle found nobody, so the worker started it again");
  assert.equal((await getTabState(tabId)).active, true);
});

test("restricted pages open the options page with a clear reason instead of failing silently", async () => {
  const w = await setup({ paired: {} });
  for (const [url, message] of [
    ["chrome://extensions/", "Cannot access a chrome:// URL"],
    ["https://chromewebstore.google.com/detail/x", "The extensions gallery cannot be scripted."],
    ["https://example.com/cv.pdf", "Cannot access contents of url"],
    [undefined, "Cannot access a chrome:// URL"],
  ]) {
    const tabId = w.chrome.__addTab(url ?? "chrome://newtab/");
    w.chrome.__injectErrors.set(tabId, message);
    await onActionClicked({ id: tabId, url });
    assert.equal(await getTabState(tabId), null);
  }
  const created = optionsTabs(w);
  assert.equal(created.length >= 1, true);
  assert.ok(created.every((entry) => entry.url.includes("reason=restricted")), JSON.stringify(created));
  assert.equal(badge(w, 100)?.text ?? "", "");
});

test("a page that needs a permission sends the user to the permissions section with the site", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY, { accessible: false });
  await onActionClicked({ id: tabId, url: COMPANY });
  const [opened] = optionsTabs(w);
  assert.match(opened.url, /reason=permission/);
  assert.match(opened.url, /site=acme\.example\.com/);
  assert.match(opened.url, /#permissions$/);
});

test("the options page is reused, not duplicated", async () => {
  const w = await setup({ paired: {} });
  const first = w.chrome.__addTab("chrome://extensions/");
  w.chrome.__injectErrors.set(first, "Cannot access a chrome:// URL");
  await onActionClicked({ id: first, url: "chrome://extensions/" });
  await onActionClicked({ id: first, url: "chrome://extensions/" });
  assert.equal(optionsTabs(w).length, 1);
  assert.equal(w.chrome.__log.updated.length, 1, "second time the existing tab is focused and updated");
});

test("only a tab that really shows the options page is ever reused", async () => {
  const w = await setup({ paired: {} });
  const stranger = w.chrome.__addTab("https://news.example.org/");
  w.chrome.tabs.query = async () => [{ id: stranger, url: undefined, windowId: 1 }, { id: 999, url: "https://elsewhere.example/", windowId: 1 }];
  const tabId = w.chrome.__addTab("chrome://extensions/");
  w.chrome.__injectErrors.set(tabId, "Cannot access a chrome:// URL");
  await onActionClicked({ id: tabId, url: "chrome://extensions/" });
  assert.equal(w.chrome.__log.updated.length, 0, "no stranger's tab was navigated");
  assert.equal(optionsTabs(w).length, 1);
});

test("automatic activations never open the options page when they fail", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY, { accessible: false });
  const result = await activateTab(tabId, { auto: true, url: COMPANY });
  assert.equal(result.ok, false);
  assert.equal(optionsTabs(w).length, 0);
});

test("only frames without the script get it, and only they are told to start", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY, { frames: [{ frameId: 5 }, { frameId: 6, accessible: false }] });
  await activateTab(tabId, { url: COMPANY });
  assert.deepEqual(w.chrome.__log.injected.map((entry) => entry.frameId).sort(), [0, 5]);
  w.chrome.__log.injected.length = 0;
  w.chrome.__received.length = 0;
  w.chrome.__addFrame(tabId, 9);
  await refreshFrames(tabId);
  assert.deepEqual(w.chrome.__log.injected.map((entry) => entry.frameId), [9]);
  assert.deepEqual(w.chrome.__received.filter((entry) => entry.message.t === "activate").map((entry) => entry.frameId), [9]);
});

test("embedded forms that appear a moment after activation are picked up", async () => {
  const w = await setup({ paired: {} });
  setSettleDelays([15, 30]);
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  w.chrome.__addFrame(tabId, 7);
  await new Promise((resolve) => setTimeout(resolve, 90));
  assert.ok(w.chrome.__log.injected.some((entry) => entry.frameId === 7));
  assert.ok(w.chrome.__received.some((entry) => entry.frameId === 7 && entry.message.t === "activate"));
});

test("an active tab follows same-origin loads and curated ATS hosts, and stops for anything else", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  // same site, new document (step 2 of the form)
  w.chrome.__navigate(tabId, `${COMPANY}/step-2`);
  await onTabLoaded(tabId, `${COMPANY}/step-2`);
  assert.equal((await getTabState(tabId)).active, true);
  assert.ok(w.chrome.__messagesTo(tabId, "activate").some((entry) => entry.message.auto === true), "re-activated automatically");
  // hand-off to the applicant tracking system
  w.chrome.__navigate(tabId, GREENHOUSE);
  await onTabLoaded(tabId, GREENHOUSE);
  assert.equal((await getTabState(tabId)).active, true);
  // wandering off to an unrelated site ends the session
  w.chrome.__navigate(tabId, "https://mail.example.org/inbox");
  await onTabLoaded(tabId, "https://mail.example.org/inbox");
  assert.equal(await getTabState(tabId), null);
  assert.equal(badge(w, tabId).text, "");
});

test("when the page was not replaced the script stays and only the badge is refreshed", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  const injected = w.chrome.__log.injected.length;
  await onTabLoaded(tabId, COMPANY);
  assert.equal(w.chrome.__log.injected.length, injected);
  assert.equal(badge(w, tabId).text, "ON");
});

test("an unreadable address (no permission any more) ends the session quietly", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  await onTabLoaded(tabId, undefined);
  assert.equal(await getTabState(tabId), null);
});

test("always-on sites start by themselves when the permission exists, and not otherwise", async () => {
  const w = await setup({ paired: {} });
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["acme.example.com"] } });
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal(await getTabState(tabId), null, "no host permission yet: nothing is injected");
  assert.equal(w.chrome.__log.injected.length, 0);
  w.chrome.__granted.add("https://acme.example.com/*");
  await onTabLoaded(tabId, COMPANY);
  assert.equal((await getTabState(tabId)).auto, true);
  assert.equal(w.chrome.__messagesTo(tabId, "activate")[0].message.auto, true);
  // subdomains of a rule match too; unrelated hosts do not
  const other = w.chrome.__addTab("https://mail.example.org/");
  await onTabLoaded(other, "https://mail.example.org/");
  assert.equal(await getTabState(other), null);
});

test("sites chosen in Meridian's own settings start the assistant too, once the browser allows them", async () => {
  const w = await setup({ paired: {} });
  const settings = { enabled: true, language: "auto", auto_advance: true, skip_filled: true, tab_inserts_protected: false, save_corrections: "ask", background_research: true, background_drafts: true, web_lookup: true, hint_on_ats: true, draft_budget_usd: null, always_on_sites: ["acme.example.com"] };
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings } });
  w.bridge.settings = { ...settings }; // a page load now reads Meridian's list: Meridian really does name the site
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal(await getTabState(tabId), null, "named in Meridian, but the browser has not allowed the site: nothing is injected");
  w.chrome.__granted.add("https://acme.example.com/*");
  await onPermissionsChanged(); // what granting the permission triggers: the open tab starts without a reload
  assert.equal((await getTabState(tabId)).auto, true);
  assert.equal(w.chrome.__messagesTo(tabId, "activate")[0].message.auto, true);
  const other = w.chrome.__addTab("https://mail.example.org/");
  await onTabLoaded(other, "https://mail.example.org/");
  assert.equal(await getTabState(other), null, "other sites are not affected");
});

// ---- always_on_sites: Meridian's own list is read when a page loads, not only when the assistant happens to be in use ----------
const statusCalls = (w) => w.bridge.log.filter((entry) => entry.path === "/v1/status").length;
const appSettings = (w, sites, extra = {}) => ({ ...w.bridge.settings, always_on_sites: sites, ...extra });

test("a site added in Meridian starts the assistant on the next page load, although the browser's copy of the list was old", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://acme.example.com/*");
  // what this browser heard last: no sites. Meridian has been edited since (the person added acme.example.com in Settings).
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettings(w, []) } });
  w.bridge.settings = appSettings(w, ["acme.example.com"]);
  setSettingsFreshness(0); // the old copy is stale
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal(statusCalls(w), 1, "the page load asked Meridian once");
  assert.equal((await getTabState(tabId))?.auto, true, "and the site Meridian now names started by itself");
  assert.deepEqual((await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"].settings.always_on_sites, ["acme.example.com"]);
});

test("a site removed in Meridian stops starting, even though the browser's old copy still names it", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://acme.example.com/*");
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettings(w, ["acme.example.com"]) } });
  w.bridge.settings = appSettings(w, []);
  setSettingsFreshness(0);
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal(await getTabState(tabId), null, "the list was read first: nothing names this site any more");
  assert.equal(w.chrome.__log.injected.length, 0);
});

test("a rule of this browser keeps working when the list in Meridian is empty, and the other way round", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://*/*");
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["acme.example.com"], settings: appSettings(w, []) } });
  w.bridge.settings = appSettings(w, ["jobs.beta.org"]);
  setSettingsFreshness(0);
  const own = w.chrome.__addTab(COMPANY);
  await onTabLoaded(own, COMPANY);
  assert.equal((await getTabState(own))?.auto, true, "its own rule");
  const fromApp = w.chrome.__addTab("https://jobs.beta.org/apply");
  await onTabLoaded(fromApp, "https://jobs.beta.org/apply");
  assert.equal((await getTabState(fromApp))?.auto, true, "Meridian's rule, both honoured together");
});

test("Meridian is asked at most once per freshness window, and tabs that load together share one look", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://*/*");
  setSettingsFreshness(60_000);
  const urls = ["https://a.example.org/x", "https://b.example.org/y", "https://c.example.org/z"];
  await Promise.all(urls.map((url) => onTabLoaded(w.chrome.__addTab(url), url)));
  assert.equal(statusCalls(w), 1, "three page loads at once, one look");
  await onTabLoaded(w.chrome.__addTab("https://d.example.org/"), "https://d.example.org/");
  assert.equal(statusCalls(w), 1, "a later page inside the window does not ask again");
  setSettingsFreshness(0);
  await onTabLoaded(w.chrome.__addTab("https://e.example.org/"), "https://e.example.org/");
  assert.equal(statusCalls(w), 2, "once the copy is old a page asks again");
});

test("pages the assistant cannot work on, and tabs already running, ask nobody", async () => {
  const w = await setup({ paired: {} });
  setSettingsFreshness(0);
  await onTabLoaded(w.chrome.__addTab("https://example.org/doc.pdf"), "https://example.org/doc.pdf");
  await onTabLoaded(w.chrome.__addTab("chrome://extensions"), "chrome://extensions");
  await onTabLoaded(w.chrome.__addTab("https://example.org/"), undefined);
  assert.equal(statusCalls(w), 0, "a PDF, a browser page and an unreadable address are not worth a question");
  const running = w.chrome.__addTab(COMPANY);
  await activateTab(running, { url: COMPANY });
  const before = statusCalls(w);
  await onTabLoaded(running, COMPANY);
  assert.equal(statusCalls(w), before, "an active tab's page already talks to Meridian itself");
});

test("a browser that is not connected to Meridian asks nobody, and a Meridian that is closed leaves the last copy in place", async () => {
  const unpaired = await setup();
  unpaired.chrome.__granted.add("https://*/*");
  await unpaired.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettings(unpaired, ["acme.example.com"]) } });
  setSettingsFreshness(0);
  await onTabLoaded(unpaired.chrome.__addTab("https://elsewhere.example.org/"), "https://elsewhere.example.org/");
  assert.equal(unpaired.bridge.log.length, 0, "a page that starts nothing, in a browser with no credential: Meridian is not even looked for");
  const first = unpaired.chrome.__addTab(COMPANY);
  await onTabLoaded(first, COMPANY);
  assert.equal(statusCalls(unpaired), 0, "no credential: nothing is asked of Meridian");
  assert.ok(unpaired.bridge.log.every((entry) => entry.auth === null), "and no request carries a credential");
  assert.equal((await getTabState(first))?.auto, true, "the copy it has still works");
  await current.close();
  current = null;

  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://*/*");
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettings(w, ["acme.example.com"]) } });
  await w.bridge.stop();
  setSettingsFreshness(0);
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal((await getTabState(tabId))?.auto, true, "Meridian closed: the browser keeps honouring what it last heard");
});

test("a Meridian that is not running is not probed again by every page that loads", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://*/*");
  await w.bridge.stop();
  setSettingsFreshness(60_000);
  const realFetch = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = (...args) => {
    attempts += 1;
    return realFetch(...args);
  };
  try {
    await onTabLoaded(w.chrome.__addTab("https://a.example.org/"), "https://a.example.org/");
    const first = attempts;
    assert.ok(first > 0, "the first page tried to reach Meridian");
    await onTabLoaded(w.chrome.__addTab("https://b.example.org/"), "https://b.example.org/");
    await onTabLoaded(w.chrome.__addTab("https://c.example.org/"), "https://c.example.org/");
    assert.equal(attempts, first, "and the failure counts as a look: the next pages inside the window do not try again");
    assert.ok((await settingsHeardAt()) > 0, "the attempt itself is stamped, answered or not, so a slow or hung Meridian is not asked by every page either");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("a slow Meridian does not hold a page back: the rules already stored decide, and the answer is kept for the next page", async () => {
  const w = await setup({ paired: {}, bridges: [{ helloDelayMs: 600 }] });
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: appSettings(w, []) } });
  w.bridge.settings = appSettings(w, ["jobs.beta.org"]);
  setSettingsFreshness(0, 30); // wait at most 30 ms for the look
  const began = Date.now();
  await onTabLoaded(w.chrome.__addTab("https://other.example.org/"), "https://other.example.org/");
  assert.ok(Date.now() - began < 500, `the page did not wait for the slow answer (${Date.now() - began} ms)`);
  await refreshAppSettings(); // the look that was running (one at a time) finishes
  const prefs = (await w.chrome.storage.local.get("meridian.prefs"))["meridian.prefs"];
  assert.deepEqual(prefs.settings.always_on_sites, ["jobs.beta.org"], "and the answer was stored for the next page");
  // the next page then honours it
  w.chrome.__granted.add("https://jobs.beta.org/*");
  setSettingsFreshness(60_000);
  const next = w.chrome.__addTab("https://jobs.beta.org/apply");
  await onTabLoaded(next, "https://jobs.beta.org/apply");
  assert.equal((await getTabState(next))?.auto, true);
});

test("always-on does nothing while the app has the assistant switched off", async () => {
  const w = await setup({ paired: {} });
  w.chrome.__granted.add("https://*/*");
  const settings = { enabled: false, language: "auto", auto_advance: true, skip_filled: true, tab_inserts_protected: false, save_corrections: "ask", background_research: true, background_drafts: true, web_lookup: true, hint_on_ats: true, draft_budget_usd: null, always_on_sites: [] };
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: ["acme.example.com"], settings } });
  w.bridge.settings = { ...settings }; // Meridian really has it off (a page load reads Meridian's settings first)
  const tabId = w.chrome.__addTab(COMPANY);
  await onTabLoaded(tabId, COMPANY);
  assert.equal(await getTabState(tabId), null);
  // and switched on again in Meridian: the very next page load notices, without anyone using the assistant in between
  w.bridge.settings = { ...settings, enabled: true };
  setSettingsFreshness(0);
  await onTabLoaded(tabId, COMPANY);
  assert.equal((await getTabState(tabId))?.auto, true);
});

test("granting a site's permission starts its open tabs, unless Meridian has the assistant switched off", async () => {
  const w = await setup({ paired: {} });
  const off = { ...w.bridge.settings, enabled: false, always_on_sites: ["acme.example.com"] };
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: off } });
  const tabId = w.chrome.__addTab(COMPANY);
  w.chrome.__granted.add("https://acme.example.com/*");
  await onPermissionsChanged();
  assert.equal(await getTabState(tabId), null, "switched off in Meridian: the new permission starts nothing");
  await w.chrome.storage.local.set({ "meridian.prefs": { alwaysOn: [], settings: { ...off, enabled: true } } });
  await onPermissionsChanged();
  assert.equal((await getTabState(tabId))?.auto, true, "switched on: it does");
});

test("the content script switching itself off clears the tab and the badge; switching on records it", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await setActiveFromContent(tabId, true);
  assert.equal((await getTabState(tabId)).active, true);
  assert.equal(badge(w, tabId).text, "ON");
  await setActiveFromContent(tabId, false);
  assert.equal(await getTabState(tabId), null);
  assert.equal(badge(w, tabId).text, "");
  assert.equal(w.chrome.__messagesTo(tabId, "deactivate").length, 0, "the script already knows: no echo");
});

test("deactivating releases the bridge sessions the tab opened", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  const created = await (await fetch(`http://127.0.0.1:${w.bridge.port}/v1/sessions`, {
    method: "POST", headers: { authorization: `Bearer ${w.issued.token}`, "content-type": "application/json" }, body: JSON.stringify({ page: {} }),
  })).json();
  await setTabState(tabId, { ...(await getTabState(tabId)), sessions: [created.session_id] });
  await deactivateTab(tabId);
  assert.ok(w.bridge.log.some((entry) => entry.method === "DELETE" && entry.path === `/v1/sessions/${created.session_id}`));
  assert.equal(w.bridge.sessions.size, 0);
});

test("new permissions reach frames that were out of reach before", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY, { frames: [{ frameId: 4, accessible: false }] });
  await activateTab(tabId, { url: COMPANY });
  assert.deepEqual(w.chrome.__log.injected.map((entry) => entry.frameId), [0]);
  w.chrome.__frames.get(tabId).get(4).accessible = true; // the user just granted access to the frame's site
  await onPermissionsChanged();
  assert.deepEqual(w.chrome.__log.injected.map((entry) => entry.frameId).sort(), [0, 4]);
  assert.ok(w.chrome.__received.some((entry) => entry.frameId === 4 && entry.message.t === "activate"));
});

test("connection changes are pushed to active tabs and update their badges", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  await onConnectionState({ phase: "no_bridge" });
  assert.deepEqual(w.chrome.__messagesTo(tabId, "conn:changed").pop().message, { t: "conn:changed", connection: { phase: "no_bridge" } });
  assert.equal(badge(w, tabId).text, "!");
  await onConnectionState({ phase: "ready" });
  assert.equal(badge(w, tabId).text, "ON");
});

test("a badge refresh that was slow can never overwrite the newer fact that the page switched itself off", async () => {
  const w = await setup({ paired: {} });
  const tabId = w.chrome.__addTab(COMPANY);
  await activateTab(tabId, { url: COMPANY });
  const record = w.chrome.action.setBadgeText;
  let interrupted = false;
  w.chrome.action.setBadgeText = async (details) => {
    if (!interrupted && details.text === "ON") {
      interrupted = true;
      await setActiveFromContent(tabId, false); // the page's own report lands while the refresh is mid-way
    }
    return record(details);
  };
  await refreshBadge(tabId);
  assert.equal(interrupted, true);
  assert.equal(await getTabState(tabId), null);
  assert.equal(badge(w, tabId).text, "", "the refresh looked again and applied the newer state");
});
