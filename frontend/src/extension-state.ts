import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useResource } from "./api";
import type {
  ExtensionAssist,
  ExtensionBrowser,
  ExtensionOverallState,
  ExtensionStatus,
} from "./types";

/** Poll cadence: 2 s while the user is mid-flow, 10 s otherwise, 30 s when the window is hidden. */
export const POLL_FAST_MS = 2000;
export const POLL_IDLE_MS = 10000;
export const POLL_HIDDEN_MS = 30000;
/** How long a clicked install helper keeps the fast cadence, waiting for the extension to show up. */
export const GUIDED_MS = 10 * 60 * 1000;

/** One shared status feed (App owns the single poller; the Settings panel, nav chip and banner all read it). */
export interface ExtensionFeed {
  data: ExtensionStatus | null;
  loading: boolean;
  error: string;
  reload: () => void;
  /** The user is following the install steps in another app: keep polling fast until the extension appears. */
  watch: () => void;
  /** True while a clicked install helper is waiting for the extension to show up. */
  watching: boolean;
}
export const ExtensionFeedContext = createContext<ExtensionFeed | null>(null);
export function useExtensionFeed(): ExtensionFeed {
  const feed = useContext(ExtensionFeedContext);
  if (!feed) throw new Error("The browser assistant status is only available inside the app shell.");
  return feed;
}

/**
 * GET /extension with adaptive polling. Lives in a .ts file on purpose: exporting a hook next to
 * components from a .tsx file triggers react-refresh/only-export-components.
 */
export function useExtensionStatus(refresh: number): ExtensionFeed {
  const { data, loading, error, reload } = useResource<ExtensionStatus>("/extension", refresh);
  const [guidedUntil, setGuidedUntil] = useState(0);
  const watch = useCallback(() => setGuidedUntil(Date.now() + GUIDED_MS), []);
  const state = data?.state;
  const pairing = !!data?.pending_pairing;
  useEffect(() => {
    if (loading) return;
    const guided = Date.now() < guidedUntil && (state === "not_installed" || state === "outdated");
    const quick = guided || pairing || state === "detected";
    const delay = document.hidden ? POLL_HIDDEN_MS : quick ? POLL_FAST_MS : POLL_IDLE_MS;
    const timer = setTimeout(reload, delay);
    return () => clearTimeout(timer);
  }, [loading, reload, guidedUntil, state, pairing]);
  useEffect(() => {
    // Returning from Chrome/Safari (where the user just clicked Connect) refreshes at once.
    const wake = () => {
      if (!document.hidden) reload();
    };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, [reload]);
  const watching = state === "not_installed" && Date.now() < guidedUntil;
  return { data, loading, error, reload, watch, watching };
}

/** Today's "Set up the browser assistant" callout: "Not now" hides it for 30 days (kept in this browser only). */
export const CALLOUT_KEY = "meridian-assistant-callout-snoozed-at";
export const CALLOUT_SNOOZE_MS = 30 * 24 * 60 * 60 * 1000;
/** Is the setup callout snoozed? Storage that cannot be read (private window, blocked) means "not snoozed". */
export function calloutSnoozed(now: number = Date.now()): boolean {
  try {
    const at = Number(localStorage.getItem(CALLOUT_KEY));
    // a clock set back (or a corrupt value) must not hide the callout for ever: only a past moment inside the window counts
    return Number.isFinite(at) && at > 0 && at <= now + 60_000 && now - at < CALLOUT_SNOOZE_MS;
  } catch {
    return false;
  }
}
export function snoozeCallout(now: number = Date.now()): void {
  try {
    localStorage.setItem(CALLOUT_KEY, String(now));
  } catch {
    // nothing to remember it with: the callout simply comes back next time
  }
}

/** "482913" -> "482 913" */
export const formatCode = (code: string) => code.replace(/(\d{3})(?=\d)/g, "$1 ").trim();
/** "482913" -> "4 8 2 9 1 3" for screen readers (VoiceOver otherwise says "four hundred eighty-two thousand…"). */
export const spokenCode = (code: string) => code.split("").join(" ");
export const browserName = (browser: ExtensionBrowser) => (browser === "chrome" ? "Chrome" : "Safari");

/** Seconds until `iso`, never negative; re-evaluates every second while mounted. */
export function useCountdown(iso: string | undefined): number {
  const target = iso ? Date.parse(iso) : NaN;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (Number.isNaN(target)) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [target]);
  return Number.isNaN(target) ? 0 : Math.max(0, Math.round((target - now) / 1000));
}
export const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

/** "3 minutes ago". Pinned to English so a sentence never mixes languages (the rest of the UI is English). */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const seconds = Math.round((Date.parse(iso) - Date.now()) / 1000);
  if (Number.isNaN(seconds)) return "unknown";
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

export const plural = (count: number, one: string, many = `${one}s`) =>
  `${count.toLocaleString()} ${count === 1 ? one : many}`;
export const money = (value: number) =>
  value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: value < 1 ? 4 : 2 });

/** "Assisted in Chrome · 14 fields" for tracker rows and the application header. */
export function assistedLine(assist: ExtensionAssist | null | undefined): string {
  if (!assist) return "";
  const where = `Assisted in ${browserName(assist.browser)}`;
  return assist.fields_inserted > 0 ? `${where} · ${plural(assist.fields_inserted, "field")}` : where;
}

/** Primitive that is safe to put in AppContext (stable identity between polls). */
export type AssistantFlag = ExtensionOverallState | "unknown";
export const assistantFlag = (status: ExtensionStatus | null | undefined): AssistantFlag =>
  status?.state ?? "unknown";

/** What the Settings nav item shows. Healthy and not-installed states are deliberately silent. */
export function chipFor(status: ExtensionStatus | null | undefined): { text: string; tone: "amber" | "neutral" } | null {
  switch (status?.state) {
    case "pairing":
      return { text: "Approve", tone: "amber" };
    case "bridge_error":
      return { text: "Check", tone: "amber" };
    case "outdated":
      return { text: "Update", tone: "amber" };
    case "detected":
      return { text: "Connect", tone: "neutral" };
    default:
      return null;
  }
}
/** Text twin of the chip for aria-describedby (a description does not alter the accessible name). */
export function chipHint(status: ExtensionStatus | null | undefined): string {
  switch (status?.state) {
    case "pairing":
      return "Browser assistant: a connection request is waiting for your approval.";
    case "bridge_error":
      return "Browser assistant: Meridian could not open its browser connection.";
    case "outdated":
      return "Browser assistant: the extension needs updating.";
    case "detected":
      return "Browser assistant: extension found, connect it in Settings.";
    default:
      return "";
  }
}

/** Normalises what a person might paste ("https://www.Example.com/jobs?x=1") to a bare hostname, or null. */
export function normalizeHost(input: string): string | null {
  let text = input.trim().toLowerCase();
  if (!text) return null;
  text = text.replace(/^[a-z][a-z0-9+.-]*:\/\//, "").replace(/^www\./, "");
  text = text.split(/[/?#]/)[0].replace(/:\d+$/, "").replace(/\.$/, "");
  const label = "[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?";
  const valid = new RegExp(`^(${label}\\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$`).test(text) && text.length <= 253;
  return valid ? text : null;
}
