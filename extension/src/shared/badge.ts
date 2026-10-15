// Toolbar badge and tooltip, per tab. Chrome resets a tab's badge when it navigates, so callers re-apply after a load.
// Safari ignores colours (setBadgeBackgroundColor is a no-op there) but shows the text; every call is best-effort.

import type { ConnectionPhase } from "./messages";
import { languageFromPrefs, t } from "./i18n-core";
import type { StringKey } from "./i18n-core";
import { browserLanguage } from "./i18n-core";
import { getPrefs } from "./storage";

export type BadgeKind = "off" | "on" | "attention" | "hint";

interface Look { text: string; background: string; color: string; title: StringKey }

const LOOKS: Record<Exclude<BadgeKind, "off">, Look> = {
  on: { text: "ON", background: "#173F36", color: "#A8E4CE", title: "action.title.on" },
  attention: { text: "!", background: "#D6B782", color: "#173F36", title: "action.title.attention" },
  hint: { text: "●", background: "#00000000", color: "#1FA971", title: "action.title.hint" },
};

/** Which badge an ACTIVE tab shows for a connection phase. */
export function badgeKindForPhase(phase: ConnectionPhase): BadgeKind {
  // `connecting` is a first look that has not finished: neither "all good" nor a problem, so it keeps the calm badge
  return phase === "ready" || phase === "connecting" ? "on" : "attention";
}

export function badgeText(kind: BadgeKind): string {
  return kind === "off" ? "" : LOOKS[kind].text;
}

async function safely(call: () => Promise<unknown> | undefined): Promise<void> {
  try {
    await call();
  } catch {
    // tab closed, API missing in this browser, or the page is restricted: the badge is cosmetic
  }
}

export async function applyBadge(tabId: number, kind: BadgeKind, custom?: { text?: string }): Promise<void> {
  if (tabId < 0) return;
  const look = kind === "off" ? null : LOOKS[kind];
  const text = custom?.text !== undefined ? custom.text.slice(0, 4) : look?.text ?? "";
  await safely(() => chrome.action.setBadgeText({ tabId, text }));
  if (look && text) {
    await safely(() => chrome.action.setBadgeBackgroundColor({ tabId, color: look.background }));
    const setColor = (chrome.action as { setBadgeTextColor?: (details: { tabId: number; color: string }) => Promise<void> }).setBadgeTextColor;
    if (setColor) await safely(() => setColor.call(chrome.action, { tabId, color: look.color }));
  }
  let title = "";
  try {
    const lang = languageFromPrefs(await getPrefs(), browserLanguage());
    title = t(lang, look ? look.title : "action.title");
  } catch {
    title = "";
  }
  if (title) await safely(() => chrome.action.setTitle({ tabId, title }));
}
