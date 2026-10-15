// Per-tab activation (docs/EXTENSION_DESIGN.md 7.2): toolbar click, hint pill, always-on sites, navigation follow-up,
// badges and frame injection. All state lives in storage.session (see shared/tabs.ts), never in this module.

import type { ConnectionState } from "../shared/messages";
import type { StatusResponse } from "../shared/protocol";
import { applyBadge, badgeKindForPhase } from "../shared/badge";
import type { BadgeKind } from "../shared/badge";
import { bridgeCall, cachedConnection, resolveConnection } from "../shared/connection";
import { hostMatchesRules, hostOf, isRestrictedUrl, matchesAnyPattern, originOf } from "../shared/hosts";
import { hasHost } from "../shared/permissions";
import { alwaysOnRules, getConnection, getPrefs, markSettingsHeard, rememberSettings, settingsHeardAt } from "../shared/storage";
import {
  clearPending, clearTabState, getPending, getTabState, injectContent, listTabStates, openOptions, sendToTab, setTabState,
} from "../shared/tabs";
import type { InjectFailure } from "../shared/tabs";
import { dropTab, forgetTabVisibility } from "./polling";

/** Delays (ms) of the follow-up frame checks after activation: embedded forms are usually inserted a moment after load. */
export let SETTLE_DELAYS_MS: number[] = [1200, 3500, 8000];
export function setSettleDelays(delays: number[]): void {
  SETTLE_DELAYS_MS = delays;
}

/** An activation that must survive a page load only follows into the same site, a curated ATS or an always-on site. */
const NAVIGATION_GRACE_MS = 6 * 60 * 60 * 1000;

/**
 * How old this browser's copy of Meridian's settings may be before a page load asks again. The "start automatically on these
 * sites" list is edited in Meridian, which cannot tell the browser: without a look at page-load time a site added there would
 * not start until the assistant happened to be used somewhere else, and a site removed there would keep starting.
 */
export let SETTINGS_FRESH_MS = 30_000;
/** A page load waits at most this long for that look (a slow Meridian must not hold back a start the stored rules already allow). */
export let SETTINGS_WAIT_MS = 2500;
export function setSettingsFreshness(freshMs: number, waitMs: number = SETTINGS_WAIT_MS): void {
  SETTINGS_FRESH_MS = freshMs;
  SETTINGS_WAIT_MS = waitMs;
}

let refreshing: Promise<void> | null = null;

/** Waits for `task`, but never longer than `ms` (the task keeps running; its timer is cleared as soon as nobody waits any more). */
async function waitAtMost(task: Promise<void>, ms: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([task, new Promise<void>((resolve) => {
      timer = setTimeout(resolve, ms);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Brings the remembered copy of Meridian's settings up to date when it is older than SETTINGS_FRESH_MS (a page load is the moment
 * that matters). One look serves every tab that loads meanwhile. A browser that is not paired asks nobody; a Meridian that is not
 * running is not an error: the last known copy stays, and the next look waits the same time. The attempt is stamped BEFORE the
 * request, so a Meridian that is closed is not probed again by every page that loads. Never throws.
 */
export function refreshAppSettings(): Promise<void> {
  if (!refreshing) {
    refreshing = (async () => {
      const heard = await settingsHeardAt();
      const now = Date.now();
      if (heard > 0 && now >= heard && now - heard < SETTINGS_FRESH_MS) return;
      await markSettingsHeard(now);
      if ((await getConnection()) === null) return;
      const status = await bridgeCall<StatusResponse>("/v1/status", { retry: false, timeoutMs: 4000 });
      await rememberSettings(status.settings);
    })()
      .catch(() => undefined)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

export function atsPatterns(): string[] {
  try {
    const scripts = chrome.runtime.getManifest().content_scripts ?? [];
    const hint = scripts.find((entry) => (entry.js ?? []).includes("hint.js"));
    return hint?.matches ?? [];
  } catch {
    return [];
  }
}

export type ActivateResult = { ok: true } | { ok: false; reason: InjectFailure };

async function currentKind(): Promise<BadgeKind> {
  const known = await cachedConnection();
  if (known) return badgeKindForPhase(known.phase);
  return badgeKindForPhase((await resolveConnection()).state.phase);
}

/** Re-applies the toolbar badge of a tab from its stored activation state. */
export async function refreshBadge(tabId: number): Promise<void> {
  // The state can change while the badge is being worked out (the page reports itself off, a navigation ends the tab):
  // look again afterwards and apply the newer truth, so a slow refresh can never overwrite a faster, newer change.
  for (let pass = 0; pass < 3; pass++) {
    const active = (await getTabState(tabId))?.active === true;
    await applyBadge(tabId, active ? await currentKind() : "off");
    if (((await getTabState(tabId))?.active === true) === active) return;
  }
}

export async function activateTab(tabId: number, options: { auto?: boolean; url?: string } = {}): Promise<ActivateResult> {
  let url = options.url;
  if (url === undefined) {
    try {
      url = (await chrome.tabs.get(tabId)).url;
    } catch {
      return { ok: false, reason: "failed" };
    }
  }
  const outcome = await injectContent(tabId, url);
  if (!outcome.ok) {
    await clearTabState(tabId);
    await clearPending(tabId); // nothing can pick the record up where the assistant cannot run
    await applyBadge(tabId, "off");
    if (!options.auto) {
      const host = hostOf(url);
      await openOptions({
        reason: outcome.reason,
        site: outcome.reason === "permission" && host ? host : undefined,
        anchor: outcome.reason === "permission" ? "permissions" : undefined,
      });
    }
    return { ok: false, reason: outcome.reason };
  }
  const previous = await getTabState(tabId);
  await forgetTabVisibility(tabId); // a new start knows nothing about the page's visibility: assume visible until it says otherwise
  await setTabState(tabId, {
    active: true, since: Date.now(), origin: originOf(url), auto: options.auto === true, sessions: previous?.sessions ?? [],
  });
  await sendToTab(tabId, { t: "activate", auto: options.auto === true });
  await refreshBadge(tabId);
  void settleFrames(tabId);
  return { ok: true };
}

export async function deactivateTab(tabId: number, options: { notify?: boolean } = {}): Promise<void> {
  const state = await getTabState(tabId);
  if (options.notify !== false) await sendToTab(tabId, { t: "deactivate" });
  await clearTabState(tabId);
  await clearPending(tabId); // switched off: nobody is waiting for a confirmation any more
  dropTab(tabId); // its waits for events end here; the shared poll no longer covers its sessions
  await applyBadge(tabId, "off");
  if (state?.sessions.length) await releaseSessions(state.sessions, tabId);
}

/**
 * The bridge sessions that tabs OTHER than `tabId` still use. Two tabs on the same page share one session (the bridge
 * hands out one per browser and page), so the first of them to close must not take it away from the other.
 */
export async function sessionsUsedByOtherTabs(tabId: number): Promise<Set<string>> {
  const used = new Set<string>();
  for (const { tabId: other, state } of await listTabStates()) {
    if (other !== tabId && state.active) for (const id of state.sessions) used.add(id);
  }
  return used;
}

/** Releases sessions at the bridge, except those another tab still uses (`leavingTab` is the tab that is going away). */
export async function releaseSessions(sessionIds: string[], leavingTab?: number): Promise<void> {
  const shared = leavingTab === undefined ? new Set<string>() : await sessionsUsedByOtherTabs(leavingTab);
  for (const sessionId of sessionIds) {
    if (shared.has(sessionId)) continue;
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(sessionId)) continue;
    try {
      await bridgeCall(`/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE", retry: false, timeoutMs: 5000 });
    } catch {
      // already gone, or Meridian is not running: the server cancels orphaned queued work by itself
    }
  }
}

/** Is the content script alive in the top frame right now? (A reload or navigation removes it.) */
async function topFrameHasContent(tabId: number): Promise<boolean> {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId }, func: () => Boolean((globalThis as { __meridianAssist?: unknown }).__meridianAssist),
    });
    return results[0]?.result === true;
  } catch {
    return false;
  }
}

/**
 * `chrome.action.onClicked`. A click on a tab where the assistant is already running does not decide anything itself:
 * the content script owns the panel and knows whether it is on screen, so it gets `toggle` and chooses between "switch
 * off" (panel showing) and "show the panel" (hidden, collapsed away, or a quiet automatic start on a page without a
 * form). It reports its on/off state back with {t:"activation"}; this worker only follows those reports, so what the
 * toolbar shows can never disagree with what the page does. A tab where it is not running is injected and started.
 */
export async function onActionClicked(tab: chrome.tabs.Tab): Promise<void> {
  if (typeof tab.id !== "number") return;
  const state = await getTabState(tab.id);
  if (state?.active && (await topFrameHasContent(tab.id))) {
    // the panel lives in the top frame: only it is asked (child frames follow the top frame's decision)
    if (await sendToTab(tab.id, { t: "activate", auto: false, toggle: true }, 0)) return;
    // the script is there but deaf (a leftover of an earlier load): start afresh below
  }
  await activateTab(tab.id, { url: tab.url });
}

/** The content script reports its own on/off switch (panel menu, Esc) so the next toolbar click does the obvious thing. */
export async function setActiveFromContent(tabId: number, active: boolean): Promise<void> {
  if (active) {
    const previous = await getTabState(tabId);
    if (!previous?.active) {
      let url: string | undefined;
      try {
        url = (await chrome.tabs.get(tabId)).url;
      } catch {
        url = undefined;
      }
      await setTabState(tabId, { active: true, since: Date.now(), origin: originOf(url), auto: false, sessions: previous?.sessions ?? [] });
    }
    await refreshBadge(tabId);
    return;
  }
  await deactivateTab(tabId, { notify: false });
}

/** Injects into frames that appeared after activation and tells exactly those frames to start. */
export async function refreshFrames(tabId: number): Promise<void> {
  const state = await getTabState(tabId);
  if (!state?.active) return;
  const outcome = await injectContent(tabId);
  if (!outcome.ok) return;
  for (const frameId of outcome.fresh) await sendToTab(tabId, { t: "activate", auto: state.auto }, frameId);
}

async function settleFrames(tabId: number): Promise<void> {
  for (const delay of SETTLE_DELAYS_MS) {
    await new Promise((resolve) => setTimeout(resolve, delay));
    const state = await getTabState(tabId);
    if (!state?.active) return;
    await refreshFrames(tabId);
  }
}

/** tabs.onUpdated (status complete): keep an active tab active across page loads where that is allowed, or start always-on sites. */
export async function onTabLoaded(tabId: number, url: string | undefined): Promise<void> {
  const prefs = await getPrefs();
  const state = await getTabState(tabId);
  if (state?.active) {
    const stale = Date.now() - state.since > NAVIGATION_GRACE_MS;
    const host = hostOf(url);
    // A submit waiting for its confirmation follows the tab to wherever the form posted (often another origin: the ATS's own
    // thank-you page). The browser still decides whether the assistant may run there; where it may not, activateTab gives up
    // and the record goes with it.
    const awaitingConfirmation = (await getPending(tabId)) !== null;
    const allowed = Boolean(url) && !stale && !isRestrictedUrl(url)
      && (originOf(url) === state.origin || matchesAnyPattern(atsPatterns(), url) || hostMatchesRules(host, alwaysOnRules(prefs)) || awaitingConfirmation);
    if (!allowed) {
      await clearTabState(tabId);
      await clearPending(tabId);
      dropTab(tabId);
      await applyBadge(tabId, "off");
      if (state.sessions.length) await releaseSessions(state.sessions, tabId);
      return;
    }
    if (await topFrameHasContent(tabId)) {
      await refreshBadge(tabId);
      return;
    }
    dropTab(tabId); // a new document: whatever the old one was waiting for is of no interest to anyone any more
    await activateTab(tabId, { auto: true, url });
    return;
  }
  const host = hostOf(url);
  if (!host || isRestrictedUrl(url)) return;
  // Meridian's own list of sites may have changed since the last look; a slow answer lands for the next page instead
  await waitAtMost(refreshAppSettings(), SETTINGS_WAIT_MS);
  const current = await getPrefs();
  if (!hostMatchesRules(host, alwaysOnRules(current)) || current.settings?.enabled === false) return;
  if (!(await hasHost(host, url?.startsWith("https:") !== false))) return;
  await activateTab(tabId, { auto: true, url });
}

/** After a permission change: reach frames that were out of reach, and start always-on tabs that just became possible. */
export async function onPermissionsChanged(): Promise<void> {
  for (const { tabId, state } of await listTabStates()) {
    if (!state.active) continue;
    const outcome = await injectContent(tabId);
    if (outcome.ok) for (const frameId of outcome.fresh) await sendToTab(tabId, { t: "activate", auto: state.auto }, frameId);
  }
  const prefs = await getPrefs();
  const rules = alwaysOnRules(prefs);
  if (!rules.length || prefs.settings?.enabled === false) return; // Meridian has the assistant switched off: no site starts by itself
  let tabs: chrome.tabs.Tab[] = [];
  try {
    tabs = await chrome.tabs.query({});
  } catch {
    return;
  }
  for (const tab of tabs) {
    if (typeof tab.id !== "number" || !tab.url) continue;
    const host = hostOf(tab.url);
    if (!host || !hostMatchesRules(host, rules)) continue;
    if ((await getTabState(tab.id))?.active) continue;
    if (await hasHost(host, tab.url.startsWith("https:"))) await activateTab(tab.id, { auto: true, url: tab.url });
  }
}

/** Broadcast a new connection state to every active tab and refresh their badges. */
export async function onConnectionState(state: ConnectionState): Promise<void> {
  for (const { tabId, state: tab } of await listTabStates()) {
    if (!tab.active) continue;
    await sendToTab(tabId, { t: "conn:changed", connection: state });
    await applyBadge(tabId, badgeKindForPhase(state.phase));
  }
}
