// Message router of the service worker: every BgRequest of shared/messages.ts, validated and answered as a BgResponse.
// Content scripts never reach the bridge; they come through here. Nothing here logs request or page content.

import type {
  ExtensionSettings, PageReport, ResearchCompanyRequest, ResearchSiteRequest, SessionCreateRequest,
  SessionCreateResponse, StatusResponse, SuggestResponse,
} from "../shared/protocol";
import type { BgRequest, BgResponse, ConnectionState, CtxInfo, Diagnostics, HintConfig, StoredPrefs } from "../shared/messages";
import { PROTOCOL } from "../shared/protocol";
import { trace } from "../shared/trace";
import { applyBadge } from "../shared/badge";
import type { BadgeKind } from "../shared/badge";
import { candidatePorts } from "../shared/bridge-client";
import {
  BridgeCallError, bridgeCall, cachedConnection, forPage, ownVersion, resolveConnection,
} from "../shared/connection";
import { relayFrame } from "../shared/frames-relay";
import { hostMatchesRules, hostOf, normalizeHost } from "../shared/hosts";
import { browserLanguage, languageFromPrefs } from "../shared/i18n-core";
import { hasHost, permissionSnapshot, requestHost } from "../shared/permissions";
import {
  KEYS, alwaysOnRules, getConnection, getPrefs, getUiPrefs, lastError, localGet, patchPrefs, rememberSettings, sessionGet, setUiPrefs,
} from "../shared/storage";
import {
  activeTabIds, clearPending, forgetSession, getPending, getTabState, openOptions, openPermissionPrompt, rememberSession, sendToTab,
  setPending,
} from "../shared/tabs";
import type { OptionsAnchor } from "../shared/tabs";
import { activateTab, onPermissionsChanged, refreshFrames, sessionsUsedByOtherTabs, setActiveFromContent } from "./activation";
import { cancelPairing, disconnectBrowser, pollPairing, startPairing } from "./pairing";
import { dropSession, dropTabSession, sessionOpened, setTabVisible, waitForEvents } from "./polling";

const ID = /^[A-Za-z0-9_.-]{1,100}$/;
const SETTINGS_PATCH_KEYS = ["language", "auto_advance", "skip_filled", "tab_inserts_protected", "save_corrections", "hint_on_ats"] as const;
/**
 * The first connection check a content script asks for. It is given this long to answer before the page is told
 * `connecting` (a calm "checking" state, not an error) and the answer is pushed when it is ready. A slow machine
 * therefore shows a patient assistant instead of an error followed by a retry timer.
 */
export let CTX_GRACE_MS = 1500;
/** Past this the check is reported as a (transient) error with a quick retry; the real answer is still pushed when it lands. */
export let CTX_BUDGET_MS = 8000;
/** First retry after a failed first check (the later ones follow the connection backoff: 3 s, 6 s, 10 s). */
const FIRST_RETRY_MS = 1500;
/** The late answer is never pushed sooner than this after the reply, so it cannot overtake the page's own start-up. */
const LATE_PUSH_MIN_MS = 200;
export function setCtxTimings(graceMs: number, budgetMs: number): void {
  CTX_GRACE_MS = graceMs;
  CTX_BUDGET_MS = budgetMs;
}

type Sender = chrome.runtime.MessageSender;

export function isExtensionPage(sender: Sender): boolean {
  try {
    return typeof sender.url === "string" && sender.url.startsWith(chrome.runtime.getURL(""));
  } catch {
    return false;
  }
}

function fail(code: string, detail: string): BgResponse<never> {
  return { ok: false, error: { code, detail } };
}

function ok<T>(data: T): BgResponse<T> {
  return { ok: true, data };
}

function sessionPath(sessionId: unknown, suffix = ""): string {
  if (typeof sessionId !== "string" || !ID.test(sessionId)) throw new BridgeCallError({ code: "invalid", detail: "Bad session id." });
  return `/v1/sessions/${encodeURIComponent(sessionId)}${suffix}`;
}

function senderTab(sender: Sender): number | null {
  return typeof sender.tab?.id === "number" && sender.tab.id >= 0 ? sender.tab.id : null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A request body that must be a JSON object (never a string, array or nothing) before it is passed to the bridge. */
function bodyObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new BridgeCallError({ code: "invalid", detail: "Malformed request." });
  return value as Record<string, unknown>;
}

/**
 * The connection as the content script needs it on start. Cached-and-fresh or quickly resolved answers come back at once;
 * a check that is still running after CTX_GRACE_MS is reported as `connecting` (or as the last known `ready`), and the
 * real answer follows as `conn:changed` to exactly this frame.
 */
async function quickConnection(tabId: number | null, frameId: number): Promise<ConnectionState> {
  const known = await cachedConnection();
  const resolving = resolveConnection().then((resolved) => resolved.state);
  const early = await Promise.race([resolving.catch(() => null), sleep(CTX_GRACE_MS).then(() => undefined)]);
  if (early) return early;
  if (early === null) return { phase: "error", detail: "Could not check for Meridian.", retryAfterMs: FIRST_RETRY_MS };
  const waiting: ConnectionState = known?.phase === "ready"
    ? known
    : { phase: "connecting", detail: "Checking for Meridian.", ...(known?.port !== undefined ? { port: known.port } : {}) };
  void deliverLate(tabId, frameId, resolving, waiting);
  return waiting;
}

async function deliverLate(tabId: number | null, frameId: number, resolving: Promise<ConnectionState>, returned: ConnectionState): Promise<void> {
  if (tabId === null) return;
  const gaveUp = Symbol("budget");
  const first = await Promise.race([
    resolving.catch(() => null), sleep(Math.max(0, CTX_BUDGET_MS - CTX_GRACE_MS)).then(() => gaveUp),
  ]);
  await sleep(LATE_PUSH_MIN_MS);
  if (first === gaveUp) {
    // still nothing: say so (and when to look again) instead of leaving the page on `connecting`; the answer still follows
    await sendToTab(tabId, { t: "conn:changed", connection: { phase: "error", detail: "Meridian did not answer in time.", retryAfterMs: FIRST_RETRY_MS } }, frameId);
    const final = await resolving.catch(() => null);
    if (final) await sendToTab(tabId, { t: "conn:changed", connection: final }, frameId);
    return;
  }
  const state = first as ConnectionState | null;
  if (!state) {
    await sendToTab(tabId, { t: "conn:changed", connection: { phase: "error", detail: "Could not check for Meridian.", retryAfterMs: FIRST_RETRY_MS } }, frameId);
    return;
  }
  if (state.phase !== returned.phase || state.port !== returned.port || state.pairing?.code !== returned.pairing?.code) {
    await sendToTab(tabId, { t: "conn:changed", connection: state }, frameId);
  }
}

export function badgeKindForTone(tone: "ok" | "warn" | "off" | undefined): BadgeKind {
  return tone === "warn" ? "attention" : tone === "off" ? "off" : "on";
}

function pickSettingsPatch(patch: unknown): Partial<ExtensionSettings> {
  const source = patch && typeof patch === "object" ? (patch as Record<string, unknown>) : {};
  const picked: Record<string, unknown> = {};
  for (const key of SETTINGS_PATCH_KEYS) if (key in source) picked[key] = source[key];
  return picked as Partial<ExtensionSettings>;
}

function pickPrefsPatch(patch: unknown): Partial<StoredPrefs> {
  const source = patch && typeof patch === "object" ? (patch as Record<string, unknown>) : {};
  const picked: Record<string, unknown> = {};
  for (const key of ["language", "alwaysOn", "hintOff", "settings", "welcomed"]) if (key in source) picked[key] = source[key];
  return picked as Partial<StoredPrefs>;
}

async function diagnostics(): Promise<Diagnostics> {
  const resolved = await resolveConnection();
  const [stored, error, permissions, tabs] = await Promise.all([getConnection(), lastError(), permissionSnapshot(), activeTabIds()]);
  const override = __E2E__ ? await localGet<unknown>("meridian.e2e.ports") : null;
  return {
    extensionVersion: ownVersion(), browser: __SAFARI__ ? "safari" : "chrome", protocol: PROTOCOL, phase: resolved.state.phase,
    port: resolved.state.port, meridianPorts: resolved.state.meridianPorts ?? (resolved.state.phase === "ready" && resolved.state.port !== undefined ? [resolved.state.port] : undefined), candidatePorts: candidatePorts([], Array.isArray(override) ? (override as number[]) : null),
    appVersion: resolved.state.appVersion, latestExtensionVersion: resolved.state.latestExtensionVersion,
    extensionOutdated: resolved.state.extensionOutdated === true, paired: stored !== null, clientName: stored?.clientName || undefined,
    pairedAt: stored?.pairedAt || undefined, checkedAt: await checkedAt(), lastError: error, activeTabs: tabs.length,
    permissions, e2e: __E2E__,
  };
}

async function checkedAt(): Promise<string | null> {
  const cache = await sessionGet<{ at?: number }>(KEYS.conn);
  return cache && typeof cache.at === "number" && cache.at > 0 ? new Date(cache.at).toISOString() : null;
}

/** Hosts for "start automatically on this site" and "hide the hint here": always derived from the sender, never from a payload. */
function senderHost(sender: Sender): string | null {
  return normalizeHost(hostOf(sender.tab?.url ?? sender.url));
}

const OPTIONS_ANCHORS: readonly OptionsAnchor[] = ["permissions", "connection", "behaviour", "help"];

/**
 * What a page is told about the "start automatically" rules: only the ones this browser can honour. A site that is stored but
 * has no permission (the question was ignored, or the permission was taken away later) is not "on", and the panel's switch
 * must not say it is. The options page lists the other ones with a way to allow them.
 *
 * The panel's switch is about the page it is on, so that site is also listed when it starts by itself for a reason that is not
 * an entry of its own: a rule of this browser for a parent site (acme.com covers jobs.acme.com) or one set in Meridian. It is
 * listed only where that is true: the browser allows the site and Meridian has the assistant switched on.
 */
async function honouredPrefs(prefs: StoredPrefs, host: string | null): Promise<StoredPrefs> {
  const alwaysOn: string[] = [];
  for (const rule of prefs.alwaysOn) if (await hasHost(rule)) alwaysOn.push(rule);
  const covered = host !== null && !alwaysOn.includes(host) && prefs.settings?.enabled !== false
    && hostMatchesRules(host, alwaysOnRules(prefs)) && (await hasHost(host));
  if (covered) alwaysOn.push(host);
  return alwaysOn.length === prefs.alwaysOn.length && !covered ? prefs : { ...prefs, alwaysOn };
}

/**
 * Launches Meridian through its registered `meridian://` scheme. The scheme is fixed (no page-controlled text reaches it).
 * The browser may leave the throw-away tab it opened for the launch behind; closing a tab the user may have started using
 * is a worse failure than a blank tab, so it is never closed from here.
 */
async function openMeridianApp(): Promise<void> {
  try {
    await chrome.tabs.create({ url: "meridian://open", active: true });
  } catch {
    // the scheme is not registered (Meridian is not installed in Applications): the caller keeps showing "open Meridian"
  }
}

/** The extension's own address without scheme or slashes (an id, not a secret): lets the console show what the bridge is looking at. */
function ownHost(): string {
  try {
    return chrome.runtime.getURL("").replace(/^[a-z-]+:\/\//i, "").replace(/\/+$/, "");
  } catch {
    return "";
  }
}

export async function handleMessage(message: unknown, sender: Sender): Promise<BgResponse> {
  let response: BgResponse;
  try {
    response = await dispatch(message, sender);
  } catch (error) {
    const request = (message as { t?: unknown } | null)?.t;
    if (error instanceof BridgeCallError) {
      trace("request failed", { t: typeof request === "string" ? request : "?", code: String((error.detail as { code?: unknown }).code ?? ""), why: String((error.detail as { detail?: unknown }).detail ?? ""), ext: ownHost() });
      response = { ok: false, error: error.detail };
    } else {
      trace("request crashed", { t: typeof request === "string" ? request : "?", kind: error instanceof Error ? error.name : "unknown" });
      return fail("internal", "Unexpected error in the extension.");
    }
  }
  return isExtensionPage(sender) ? response : pageResponse(response);
}

/** The connection states inside an answer, as a script next to a web page may see them (see `forPage`). */
function pageResponse(response: BgResponse): BgResponse {
  if (!response.ok) {
    const connection = response.error.connection;
    return connection ? { ok: false, error: { ...response.error, connection: forPage(connection) } } : response;
  }
  const data = response.data as { phase?: unknown; connection?: ConnectionState } | null;
  if (!data || typeof data !== "object") return response;
  if (typeof data.phase === "string") return { ok: true, data: forPage(data as unknown as ConnectionState) };
  if (data.connection) return { ok: true, data: { ...data, connection: forPage(data.connection) } };
  return response;
}

/**
 * Who may send what. Content scripts run next to hostile pages, so they get the least the assistant needs:
 *  - "any"      the tiny ATS hint script (never activated) may only ask what to show and offer to start the assistant;
 *  - "active"   everything else a content script sends needs a tab THIS worker activated (toolbar click, always-on site,
 *               hint pill): a dormant or foreign script in a tab nobody started cannot reach the bridge;
 *  - "internal" the options page (and test pages in e2e builds): settings, permissions, disconnect, diagnostics, reload.
 */
const FROM_ANY_CONTENT = new Set<BgRequest["t"]>(["hint", "hint:hide", "hint:config", "activate-here", "activation", "pending:clear"]);
const TOP_FRAME_ONLY = new Set<BgRequest["t"]>(["hint", "hint:hide", "hint:config", "activate-here"]);
const INTERNAL_ONLY = new Set<BgRequest["t"]>(["prefs:patch", "permissions:granted", "extension:reload", "disconnect", "diagnostics"]);

async function dispatch(message: unknown, sender: Sender): Promise<BgResponse> {
  // Messages from other extensions never arrive here (onMessageExternal is not registered); a sender without OUR id
  // (or without any id) is refused all the same.
  if (sender.id !== chrome.runtime.id) return fail("origin", "Unknown sender.");
  if (!message || typeof message !== "object" || typeof (message as { t?: unknown }).t !== "string") return fail("invalid", "Malformed message.");
  const request = message as BgRequest;
  const tabId = senderTab(sender);
  const internal = isExtensionPage(sender);
  if (INTERNAL_ONLY.has(request.t) && !internal) return fail("origin", "Not allowed from here.");
  if (!internal) {
    if (tabId === null) return fail("origin", "Unknown sender.");
    if (TOP_FRAME_ONLY.has(request.t) && (sender.frameId ?? 0) !== 0) return fail("origin", "Not allowed from here.");
    if (!FROM_ANY_CONTENT.has(request.t) && !(await getTabState(tabId))?.active) {
      // `session:release` and `visibility` can legitimately trail a deactivation: nothing to do, nothing to report
      if (request.t === "session:release" || request.t === "visibility") return ok(null);
      return fail("inactive", "The assistant is not active in this tab.");
    }
  }

  switch (request.t) {
    case "ctx": {
      const prefs = await getPrefs();
      const info: CtxInfo = {
        tabId: tabId ?? -1, frameId: sender.frameId ?? 0, isTop: (sender.frameId ?? 0) === 0, settings: prefs.settings, prefs: await honouredPrefs(prefs, (sender.frameId ?? 0) === 0 ? senderHost(sender) : null),
        connection: await quickConnection(tabId, sender.frameId ?? 0), e2e: __E2E__,
        pending: tabId === null ? null : await getPending(tabId), // every frame of the tab sees the submit the tab is waiting on
      };
      return ok(info);
    }
    case "conn:state":
      return ok((await resolveConnection({ force: request.refresh === true })).state);
    case "pair:start":
      // The confirmation is the user's, given on the options page with the whole explanation in front of them: a script that runs
      // next to a page cannot give it (the panel has no such button, and what it sends is not taken as one).
      return ok(await startPairing({ confirmUnknown: internal && request.confirmUnknown === true }));
    case "pair:poll":
      return ok(await pollPairing(String(request.pairingId ?? "")));
    case "pair:cancel":
      await cancelPairing(String(request.pairingId ?? ""));
      return ok(null);
    case "status": {
      const status = await bridgeCall<StatusResponse>("/v1/status");
      await rememberSettings(status.settings);
      return ok(status);
    }
    case "session:open": {
      const body: SessionCreateRequest = { page: bodyObject(request.report) as unknown as PageReport, tab_key: String(request.tabKey ?? "").slice(0, 64), locale: browserLanguage().slice(0, 16) };
      const session = await bridgeCall<SessionCreateResponse>("/v1/sessions", { method: "POST", body });
      await rememberSettings(session.settings);
      if (tabId !== null && session.session_id) await rememberSession(tabId, session.session_id);
      if (session.session_id) sessionOpened(session.session_id); // opened again: whatever the bridge said about an earlier life of this id is void
      return ok(session);
    }
    case "session:release": {
      const path = sessionPath(request.sessionId);
      if (tabId !== null && (await sessionsUsedByOtherTabs(tabId)).has(request.sessionId)) {
        // another tab is on the same page and shares this session: this tab lets go of it, the session stays
        dropTabSession(tabId, request.sessionId);
        await forgetSession(tabId, request.sessionId);
        return ok(null);
      }
      dropSession(request.sessionId); // nobody waits for events of a session that is being released, whatever the bridge says
      try {
        await bridgeCall(path, { method: "DELETE", retry: false, timeoutMs: 8000 });
      } catch (error) {
        if (!(error instanceof BridgeCallError) || (error.detail.status !== 404 && error.detail.code !== "not_found")) throw error;
      }
      if (tabId !== null) await forgetSession(tabId, request.sessionId);
      return ok(null);
    }
    case "suggest":
      return ok(await bridgeCall<SuggestResponse>(sessionPath(request.sessionId, "/suggest"), { method: "POST", body: bodyObject(request.request), timeoutMs: 30_000 }));
    case "poll": {
      // Served from the worker's ONE multiplexed long poll (polling.ts): this content script's wait is a small in-memory
      // entry there, not a connection of its own.
      const wait = Math.max(0, Math.min(25_000, Math.trunc(Number(request.waitMs) || 0)));
      const cursor = Math.max(0, Math.trunc(Number(request.cursor) || 0));
      if (typeof request.sessionId !== "string" || !ID.test(request.sessionId)) throw new BridgeCallError({ code: "invalid", detail: "Bad session id." });
      return ok(await waitForEvents(request.sessionId, cursor, wait, tabId));
    }
    case "regenerate":
      return ok(await bridgeCall(sessionPath(request.sessionId, "/regenerate"), { method: "POST", body: bodyObject(request.request), retry: false, timeoutMs: 60_000 }));
    case "feedback":
      return ok(await bridgeCall(sessionPath(request.sessionId, "/feedback"), { method: "POST", body: bodyObject(request.request), retry: false }));
    case "track":
      return ok(await bridgeCall(sessionPath(request.sessionId, "/track"), { method: "POST", body: bodyObject(request.request), retry: false }));
    case "research:site": {
      const body: ResearchSiteRequest = { domain: String(request.domain ?? "").slice(0, 300) };
      return ok(await bridgeCall(sessionPath(request.sessionId, "/research/site"), { method: "POST", body, retry: false }));
    }
    case "research:company": {
      const body: ResearchCompanyRequest = { company: String(request.company ?? "").slice(0, 200) };
      return ok(await bridgeCall(sessionPath(request.sessionId, "/research/company"), { method: "POST", body, retry: false }));
    }
    case "research:refresh":
      return ok(await bridgeCall(sessionPath(request.sessionId, "/research/refresh"), { method: "POST", body: {}, retry: false }));
    case "documents":
      return ok(await bridgeCall("/v1/documents"));
    case "document:content": {
      if (typeof request.id !== "string" || !ID.test(request.id)) return fail("invalid", "Bad document id.");
      return ok(await bridgeCall(`/v1/documents/${encodeURIComponent(request.id)}/content`, { timeoutMs: 60_000, maxChars: 24_000_000 }));
    }
    case "prefs:patch": {
      const before = await getPrefs();
      const next = await patchPrefs(pickPrefsPatch(request.patch));
      const added = next.alwaysOn.filter((host) => !before.alwaysOn.includes(host));
      for (const host of added) {
        if (!(await hasHost(host)) && !(await requestHost(host))) await openOptions({ reason: "permission", site: host, anchor: "permissions" });
      }
      return ok(null);
    }
    case "settings:patch": {
      const settings = await bridgeCall<ExtensionSettings>("/v1/settings", { method: "PATCH", body: pickSettingsPatch(request.patch), retry: false });
      await rememberSettings(settings);
      return ok(settings);
    }
    case "meridian:open":
      await openMeridianApp();
      return ok(null);
    case "options:open":
      await openOptions({ anchor: OPTIONS_ANCHORS.includes(request.anchor as OptionsAnchor) ? request.anchor : undefined });
      return ok(null);
    case "permissions:granted":
      await onPermissionsChanged();
      return ok(null);
    case "frame:relay":
      await relayFrame(tabId ?? undefined, sender.frameId ?? 0, request.to, request.payload);
      return ok(null);
    case "badge": {
      if (tabId === null) return ok(null);
      if (request.tone === "off") {
        await setActiveFromContent(tabId, false);
        return ok(null);
      }
      if ((await getTabState(tabId))?.active) {
        await applyBadge(tabId, badgeKindForTone(request.tone), { text: String(request.text ?? "") });
      }
      return ok(null);
    }
    case "hint": {
      if (tabId === null) return ok(null);
      const fields = Math.trunc(Number(request.fields) || 0);
      if (!(await getTabState(tabId))?.active) await applyBadge(tabId, fields >= 3 ? "hint" : "off");
      return ok(null);
    }
    case "activate-here": {
      if (tabId === null) return ok(null);
      await activateTab(tabId, { url: sender.tab?.url });
      return ok(null);
    }
    case "activation":
      if (tabId !== null) {
        // A script can switch itself off in any tab it runs in, but only report itself ON in a tab this worker started.
        if (request.active === true && !(await getTabState(tabId))?.active) return ok(null);
        await setActiveFromContent(tabId, request.active === true);
      }
      return ok(null);
    case "frames:inject":
      if (tabId !== null) await refreshFrames(tabId);
      return ok(null);
    case "site:always-on": {
      const host = senderHost(sender);
      if (!host) return fail("invalid", "This page cannot be added.");
      const prefs = await getPrefs();
      const without = prefs.alwaysOn.filter((entry) => entry !== host);
      if (request.enabled !== true) {
        await patchPrefs({ alwaysOn: without });
        // The site's own entry is gone. A rule for a parent site, or one set in Meridian, still starts the assistant here: it is
        // not this switch's to remove (the options page and Meridian's settings are), and the page is told so, not "off".
        const covering = prefs.settings?.enabled === false ? [] : alwaysOnRules({ ...prefs, alwaysOn: without }).filter((rule) => hostMatchesRules(host, [rule]));
        if (covering.length > 0) {
          return fail("rule", `The assistant still starts by itself here because of the rule for ${covering[0]}. Remove it on the extension's options page or in Meridian (Settings → Browser assistant).`);
        }
        return ok(null);
      }
      // Switching ON stores the rule only once the browser lets the extension work on the site, so that "on" is always true.
      // A worker has no click to ask with (Chrome refuses; some engines allow it): where it cannot, the question is shown
      // by the options page in a small window, the application tab stays where it is, and the options page stores the rule
      // when the answer is yes. Until then the caller is told so (and the panel keeps the switch off).
      if (!(await hasHost(host)) && !(await requestHost(host))) {
        await openPermissionPrompt(host, "always-on");
        return fail("permission", "Meridian needs your permission to start automatically on this site. Allow it in the window that opened.");
      }
      await patchPrefs({ alwaysOn: [...without, host] });
      return ok(null);
    }
    case "hint:hide": {
      const host = senderHost(sender);
      if (!host) return fail("invalid", "This page cannot be hidden.");
      const prefs = await getPrefs();
      if (!prefs.hintOff.includes(host)) await patchPrefs({ hintOff: [...prefs.hintOff, host] });
      if (tabId !== null && !(await getTabState(tabId))?.active) await applyBadge(tabId, "off");
      return ok(null);
    }
    case "hint:config": {
      const prefs = await getPrefs();
      const host = hostOf(sender.tab?.url ?? sender.url);
      const config: HintConfig = {
        show: host !== null && prefs.settings?.hint_on_ats !== false && prefs.settings?.enabled !== false && !hostMatchesRules(host, prefs.hintOff),
        // a credential Meridian stopped recognising is kept (nothing unproven is ever forgotten) but is not a connection
        language: languageFromPrefs(prefs, browserLanguage()), connected: (await getConnection()) !== null && !["revoked", "unknown_listener"].includes((await cachedConnection())?.phase ?? ""),
      };
      return ok(config);
    }
    case "ui:get":
      return ok(await getUiPrefs());
    case "ui:set":
      return ok(await setUiPrefs(request.value));
    case "extension:reload":
      setTimeout(() => chrome.runtime.reload(), 150);
      return ok(null);
    case "disconnect": {
      const { state, remoteRevoked } = await disconnectBrowser();
      return ok({ ...state, remoteRevoked });
    }
    case "visibility":
      if (tabId !== null) await setTabVisible(tabId, request.visible !== false);
      return ok(null);
    case "pending:set": {
      // only a tab the worker started gets here (the generic check above); one record per tab, bounded and expiring
      if (tabId === null) return fail("origin", "Unknown sender.");
      if (!(await setPending(tabId, request.record))) return fail("invalid", "Malformed record.");
      return ok(null);
    }
    case "pending:clear":
      if (tabId !== null) await clearPending(tabId); // harmless from any script: it can only forget its own tab's record
      return ok(null);
    case "diagnostics":
      return ok(await diagnostics());
    default:
      return fail("invalid", "Unknown request.");
  }
}
