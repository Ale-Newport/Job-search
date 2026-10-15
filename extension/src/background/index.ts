// Meridian Assistant service worker (docs/EXTENSION_DESIGN.md 7.2).
//
// Rules this file keeps:
//  * every listener is registered synchronously, at the top level, in the first turn of the worker;
//  * the worker is stateless: it can be stopped at any time, so each handler re-reads chrome.storage;
//  * it never logs a token or page text; all bridge traffic is made here and nowhere else.

import { onConnectionChanged } from "../shared/connection";
import {
  KEYS, credentialPlacement, getPrefs, migrateStorage, patchPrefs, resetCredentialPlacement, sessionRemove, setConnection,
} from "../shared/storage";
import { clearPending, clearTabState, getTabState, openOptions } from "../shared/tabs";
import {
  activateTab, deactivateTab, onActionClicked, onConnectionState, onPermissionsChanged, onTabLoaded, releaseSessions,
} from "./activation";
import { PAIRING_ALARM, pollPendingPairing } from "./pairing";
import { dropTab, forgetTabVisibility, pollingState } from "./polling";
import { handleMessage } from "./router";

/** Fire-and-forget for event handlers: a failure inside one must never surface as an unhandled rejection in the worker. */
function fire(task: Promise<unknown>): void {
  task.catch(() => undefined);
}

async function handleInstalled(details: chrome.runtime.InstalledDetails): Promise<void> {
  try {
    await migrateStorage();
  } catch {
    // damaged storage is re-normalised on every read; the welcome page must still open
  }
  if (details.reason === "install" && !__E2E__) await openOptions({ welcome: true });
}

async function handleTabRemoved(tabId: number): Promise<void> {
  const state = await getTabState(tabId);
  await clearTabState(tabId);
  await clearPending(tabId);
  dropTab(tabId);
  await forgetTabVisibility(tabId);
  if (state?.sessions.length) await releaseSessions(state.sessions, tabId);
}

async function handleAlarm(alarm: chrome.alarms.Alarm): Promise<void> {
  if (alarm.name === PAIRING_ALARM) await pollPendingPairing();
}

/** Test-only: seeds credential, preferences and candidate ports. Present only in __E2E__ builds. */
async function seedE2e(seed: { connection?: unknown; prefs?: unknown; ports?: unknown; reset?: unknown }): Promise<void> {
  if (seed.reset === true) {
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
  }
  if (seed.connection) await setConnection(seed.connection as Parameters<typeof setConnection>[0]);
  if (seed.prefs) await patchPrefs(seed.prefs as Parameters<typeof patchPrefs>[0]);
  if (Array.isArray(seed.ports)) await chrome.storage.local.set({ "meridian.e2e.ports": seed.ports });
  await sessionRemove(KEYS.conn, KEYS.pairing, KEYS.revoked);
}

// Content scripts run next to hostile page content: they must never be able to read the credential. Where the engine can
// restrict storage.local to trusted contexts (Chrome) that is done; where it cannot (Safari without setAccessLevel) the
// credential is kept encrypted, or in storage.session only (see storage.ts). Either way this never throws.
void credentialPlacement().catch(() => undefined);

chrome.action.onClicked.addListener((tab) => {
  fire(onActionClicked(tab));
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (__E2E__ && message && typeof message === "object" && (message as { t?: unknown }).t === "e2e:seed") {
    seedE2e(message as Parameters<typeof seedE2e>[0]).then(
      () => sendResponse({ ok: true, data: null }),
      () => sendResponse({ ok: false, error: { code: "invalid", detail: "seed rejected" } }),
    );
    return true;
  }
  void handleMessage(message, sender).then(sendResponse);
  return true; // the answer is asynchronous
});

chrome.runtime.onInstalled.addListener((details) => {
  fire(handleInstalled(details));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete") fire(onTabLoaded(tabId, tab.url));
});

chrome.tabs.onRemoved.addListener((tabId) => {
  fire(handleTabRemoved(tabId));
});

chrome.alarms.onAlarm.addListener((alarm) => {
  fire(handleAlarm(alarm));
});

chrome.permissions.onAdded?.addListener(() => {
  fire(onPermissionsChanged());
});
chrome.permissions.onRemoved?.addListener(() => {
  fire(onPermissionsChanged());
});

onConnectionChanged((state) => {
  fire(onConnectionState(state));
});

if (__E2E__) {
  (globalThis as { __e2e?: unknown }).__e2e = {
    activate: (tabId: number) => activateTab(tabId, {}),
    deactivate: (tabId: number) => deactivateTab(tabId),
    seed: seedE2e,
    prefs: getPrefs,
    polling: pollingState, // how many waits the shared poll holds, whether a request is in flight, many|single mode
    resetPlacement: resetCredentialPlacement, // lets a test pretend the browser lacks setAccessLevel, then decide again
  };
}
