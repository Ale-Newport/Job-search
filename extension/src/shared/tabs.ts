// Per-tab activation state (storage.session, so it survives a worker restart but not a browser restart) and the helpers
// that talk to a tab's frames: content-script injection, messaging, and the options-page opener.
// Nothing here uses an API that Safari lacks (docs/EXTENSION_DESIGN.md 7.1): no webNavigation, no runtime.getContexts.

import type { PendingSubmit, ToContent } from "./messages";
import { forPage } from "./connection";
import { isRestrictedUrl } from "./hosts";
import { KEYS, sessionAll, sessionGet, sessionRemove, sessionSet } from "./storage";

export interface TabState {
  active: boolean;
  /** epoch ms of the activation */
  since: number;
  /** origin where the assistant was started; navigation to the same origin (or an ATS host) keeps it on */
  origin: string | null;
  /** started without a click (always-on site or the hint pill's own follow-up) */
  auto: boolean;
  /** bridge sessions opened by this tab's content scripts, released when the tab closes */
  sessions: string[];
}

const MAX_SESSIONS_PER_TAB = 12;
const tabKey = (tabId: number) => `${KEYS.tabPrefix}${tabId}`;

function normalizeTabState(raw: unknown): TabState | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Partial<TabState>;
  return {
    active: source.active === true,
    since: typeof source.since === "number" ? source.since : 0,
    origin: typeof source.origin === "string" ? source.origin : null,
    auto: source.auto === true,
    sessions: Array.isArray(source.sessions) ? source.sessions.filter((id): id is string => typeof id === "string").slice(-MAX_SESSIONS_PER_TAB) : [],
  };
}

export async function getTabState(tabId: number): Promise<TabState | null> {
  return normalizeTabState(await sessionGet(tabKey(tabId)));
}

export async function setTabState(tabId: number, state: TabState): Promise<void> {
  await sessionSet({ [tabKey(tabId)]: state });
}

export async function clearTabState(tabId: number): Promise<void> {
  await sessionRemove(tabKey(tabId));
}

export async function listTabStates(): Promise<{ tabId: number; state: TabState }[]> {
  const all = await sessionAll();
  const found: { tabId: number; state: TabState }[] = [];
  for (const [key, value] of Object.entries(all)) {
    if (!key.startsWith(KEYS.tabPrefix)) continue;
    const tabId = Number(key.slice(KEYS.tabPrefix.length));
    const state = normalizeTabState(value);
    if (Number.isInteger(tabId) && state) found.push({ tabId, state });
  }
  return found;
}

export async function activeTabIds(): Promise<number[]> {
  return (await listTabStates()).filter((entry) => entry.state.active).map((entry) => entry.tabId);
}

export async function rememberSession(tabId: number, sessionId: string): Promise<void> {
  const state = await getTabState(tabId);
  if (!state || state.sessions.includes(sessionId)) return;
  await setTabState(tabId, { ...state, sessions: [...state.sessions, sessionId].slice(-MAX_SESSIONS_PER_TAB) });
}

export async function forgetSession(tabId: number, sessionId: string): Promise<void> {
  const state = await getTabState(tabId);
  if (!state) return;
  await setTabState(tabId, { ...state, sessions: state.sessions.filter((id) => id !== sessionId) });
}

// ---- pending submit (per tab) -------------------------------------------------------------------------------------
// "The user clicked Submit and the assistant is waiting for the confirmation page." The content script used to keep this in
// the page's sessionStorage, which belongs to ONE origin: a form that posts to another origin (the usual shape of an ATS
// hand-off) lands on a page that cannot see it. The worker keeps it per tab instead, in storage.session (trusted contexts
// only), and hands it to every frame of that tab with `ctx`. It expires 10 minutes after the click, dies with the tab and
// with the user's own "off", and is only ever a hint: the content script re-checks the page it lands on.

export const PENDING_TTL_MS = 10 * 60 * 1000;
const pendingKey = (tabId: number) => `${KEYS.pendingPrefix}${tabId}`;
const SESSION_ID = /^[A-Za-z0-9_.-]{1,100}$/;
const MAX_HOPS = 16;

/** A record the content script sent, made safe: bounded strings, a believable click time, a bounded hop count. Null when it cannot be one. */
export function normalizePending(raw: unknown, now: number = Date.now()): PendingSubmit | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;
  const at = Number(source.at);
  if (typeof source.sid !== "string" || !SESSION_ID.test(source.sid)) return null;
  if (!Number.isFinite(at) || at <= 0 || at > now + 60_000 || now - at > PENDING_TTL_MS) return null; // from the future, or already stale
  const text = (value: unknown, max: number): string => (typeof value === "string" ? value.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, max) : "");
  const hops = Math.trunc(Number(source.hops));
  return {
    sid: source.sid, at: Math.min(at, now), label: text(source.label, 120), company: text(source.company, 200), title: text(source.title, 300),
    identity: text(source.identity, 1000), hops: Number.isFinite(hops) ? Math.max(0, Math.min(MAX_HOPS, hops)) : 0,
  };
}

/** The submit this tab is waiting to see confirmed; a record older than 10 minutes is removed and reads as none. */
export async function getPending(tabId: number): Promise<PendingSubmit | null> {
  const raw = await sessionGet<unknown>(pendingKey(tabId));
  if (raw === undefined) return null;
  const record = normalizePending(raw);
  if (!record) await sessionRemove(pendingKey(tabId));
  return record;
}

/** Stores the tab's pending submit (one per tab: a newer one replaces the older). Null, and nothing stored, for a malformed record. */
export async function setPending(tabId: number, raw: unknown): Promise<PendingSubmit | null> {
  const record = normalizePending(raw);
  if (!record) return null;
  await sessionSet({ [pendingKey(tabId)]: record });
  return record;
}

export async function clearPending(tabId: number): Promise<void> {
  await sessionRemove(pendingKey(tabId));
}

// ---- messaging ----------------------------------------------------------------------------------------------------

/** Deliver a message to one frame (or every frame) of a tab. Resolves false when nobody was listening. */
export async function sendToTab(tabId: number, message: ToContent, frameId?: number): Promise<boolean> {
  if (message.t === "conn:changed") message = { ...message, connection: forPage(message.connection) };
  try {
    if (frameId === undefined) await chrome.tabs.sendMessage(tabId, message);
    else await chrome.tabs.sendMessage(tabId, message, { frameId });
    return true;
  } catch {
    return false; // "Receiving end does not exist": no content script there (yet)
  }
}

export async function broadcastToActiveTabs(message: ToContent): Promise<number> {
  let delivered = 0;
  for (const tabId of await activeTabIds()) if (await sendToTab(tabId, message)) delivered += 1;
  return delivered;
}

// ---- injection ----------------------------------------------------------------------------------------------------

export type InjectFailure = "restricted" | "permission" | "failed";
export type InjectOutcome =
  | { ok: true; frames: number; fresh: number[] }
  | { ok: false; reason: InjectFailure; detail: string };

export function classifyInjectionError(message: string, url: string | undefined): InjectFailure {
  if (url !== undefined && isRestrictedUrl(url)) return "restricted";
  if (/must request permission|permission to access/i.test(message)) return "permission";
  if (/cannot be scripted|cannot access a (chrome|edge|about)|chrome:\/\/|edge:\/\/|chrome-error|chrome-untrusted|devtools|view-source|about:|cannot access contents of url|error page|blocked/i.test(message)) {
    return "restricted";
  }
  return "failed";
}

/** Runs inside every frame (isolated world, same as content.js): has the content script already been installed here? */
function contentIsInstalled(): boolean {
  return Boolean((globalThis as { __meridianAssist?: unknown }).__meridianAssist);
}

/**
 * Installs content.js in every accessible frame that does not have it yet (idempotent; frames the extension has no
 * access to are skipped silently by the browser). `fresh` lists the frames that received the script just now.
 */
export async function injectContent(tabId: number, url?: string): Promise<InjectOutcome> {
  try {
    const probe = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: contentIsInstalled });
    const frames = probe.length;
    if (frames === 0) return { ok: false, reason: "failed", detail: "no accessible frame" };
    const missing = probe.filter((entry) => entry.result !== true).map((entry) => entry.frameId).filter((id): id is number => typeof id === "number");
    if (missing.length === 0) return { ok: true, frames, fresh: [] };
    try {
      await chrome.scripting.executeScript({ target: { tabId, frameIds: missing }, files: ["content.js"] });
    } catch {
      // Some engines refuse frameIds on files: fall back to allFrames, which is idempotent thanks to the content guard.
      await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content.js"] });
    }
    return { ok: true, frames, fresh: missing };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, reason: classifyInjectionError(message, url), detail: message.slice(0, 200) };
  }
}

// ---- options page -------------------------------------------------------------------------------------------------

export type OptionsReason = "restricted" | "permission" | "failed";
export type OptionsAnchor = "permissions" | "connection" | "behaviour" | "help";
export interface OptionsTarget {
  anchor?: OptionsAnchor; reason?: OptionsReason; site?: string; welcome?: boolean;
  /** the permission is being asked for so that a site rule can be stored ("Start automatically on this site") */
  intent?: "always-on";
  /** the page is shown alone in a small window: only the question and its one button */
  popup?: boolean;
}

export function optionsUrl(target: OptionsTarget = {}): string {
  const params = new URLSearchParams();
  if (target.reason) params.set("reason", target.reason);
  if (target.site) params.set("site", target.site);
  if (target.intent) params.set("intent", target.intent);
  if (target.popup) params.set("popup", "1");
  if (target.welcome) params.set("welcome", "1");
  const query = params.toString();
  const hash = target.anchor ? `#${target.anchor}` : "";
  return `${chrome.runtime.getURL("options.html")}${query ? `?${query}` : ""}${hash}`;
}

/** An open tab that really shows the options page (the address is verified here, never trusted from the query). */
async function findOptionsTab(): Promise<chrome.tabs.Tab | null> {
  try {
    const base = chrome.runtime.getURL("options.html");
    const existing = await chrome.tabs.query({ url: `${base}*` });
    return existing.find((candidate) => typeof candidate.url === "string" && candidate.url.startsWith(base) && typeof candidate.id === "number") ?? null;
  } catch {
    return null;
  }
}

/** Opens (or refocuses) the options page in a tab, optionally explaining why. Never throws. */
export async function openOptions(target: OptionsTarget = {}): Promise<void> {
  const url = optionsUrl(target);
  try {
    const tab = await findOptionsTab();
    if (tab && typeof tab.id === "number") {
      await chrome.tabs.update(tab.id, { url, active: true });
      if (typeof tab.windowId === "number") await chrome.windows.update(tab.windowId, { focused: true }).catch(() => undefined);
      return;
    }
  } catch {
    // fall through to a new tab
  }
  try {
    await chrome.tabs.create({ url, active: true });
  } catch {
    // no window to open it in: nothing more can be done
  }
}

/**
 * Asks for the permission a page needs, in context. `permissions.request` needs a click on an extension page, which a content
 * script cannot give, so the question is shown by the options page: in a small window of its own, so the application tab
 * stays where it is (the user closes nothing and loses nothing in the form). Falls back to the options tab where the browser
 * has no popup windows, and reuses the options tab when one is already open. Never throws.
 */
export async function openPermissionPrompt(site: string, intent?: "always-on"): Promise<void> {
  const target: OptionsTarget = { reason: "permission", site, anchor: "permissions", intent };
  if (await findOptionsTab()) return openOptions(target);
  try {
    await chrome.windows.create({ url: optionsUrl({ ...target, anchor: undefined, popup: true }), type: "popup", width: 520, height: 440, focused: true });
  } catch {
    await openOptions(target);
  }
}
