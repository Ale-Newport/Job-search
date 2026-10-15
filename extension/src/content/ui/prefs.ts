// The panel's remembered look (dock side, dismissed notes).
//
// Content scripts cannot read extension storage (the worker sets storage.local to TRUSTED_CONTEXTS), so the values live
// in the worker and travel as {t:"ui:get"} / {t:"ui:set"} messages. The in-memory copy below is both the synchronous
// answer while the worker is being asked and the fallback when it cannot be reached (orphaned script, worker error):
// the panel keeps working and simply forgets the choice when the page closes.

import type { UiPrefs } from "../../shared/messages";
import { sendBg } from "../util";

export type NoteKind = "drafts_unavailable" | "budget" | "cross_origin" | "first_use";
export type Dock = UiPrefs["dock"];

/** How long a dismissed note stays dismissed. */
export const NOTE_TTL_MS: Record<NoteKind, number> = {
  drafts_unavailable: 7 * 24 * 3600_000,
  budget: 12 * 3600_000,
  cross_origin: 3 * 24 * 3600_000,
  // "Got it" on the first-use card is for good: about a year, after which a person who comes back to the tool gets the three steps again
  first_use: 400 * 24 * 3600_000,
};

// The worker accepts at most this many entries with keys of this shape (storage.ts normalizeUiPrefs); anything else is
// dropped there, so it is never sent in the first place.
const KEY = /^[A-Za-z0-9_:.-]{1,40}$/;
const MAX_ENTRIES = 24;
// A sleeping worker wakes within a second or two; a longer silence means it is not coming (the page keeps its in-memory copy).
const WORKER_WAIT_MS = 5000;

let memory: UiPrefs = { dock: "right", dismissed: {} };
let loading: Promise<void> | null = null;
let inFlight = false;
let dirty = false;

/** Shape a value the way the worker will accept it: valid keys, finite numbers, newest 24, no expired notes. */
export function sanitize(raw: unknown, now = Date.now()): UiPrefs {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const entries = source.dismissed && typeof source.dismissed === "object" && !Array.isArray(source.dismissed)
    ? Object.entries(source.dismissed as Record<string, unknown>) : [];
  const live: [string, number][] = [];
  for (const [key, at] of entries) {
    if (!KEY.test(key) || typeof at !== "number" || !Number.isFinite(at)) continue;
    const ttl = NOTE_TTL_MS[key as NoteKind];
    if (ttl !== undefined && now - at >= ttl) continue; // expired: no need to keep remembering it
    live.push([key, at]);
  }
  live.sort((a, b) => b[1] - a[1]);
  return { dock: source.dock === "left" ? "left" : "right", dismissed: Object.fromEntries(live.slice(0, MAX_ENTRIES)) };
}

/** What this script instance already knows (synchronous). */
export function currentPrefs(): UiPrefs {
  return { dock: memory.dock, dismissed: { ...memory.dismissed } };
}

/** Fold what the worker holds into memory without overwriting choices made since the request was sent. */
function adopt(saved: UiPrefs, options: { keepDock: boolean }): UiPrefs {
  const dismissed = { ...saved.dismissed };
  for (const [key, at] of Object.entries(memory.dismissed)) dismissed[key] = Math.max(at, dismissed[key] ?? 0);
  memory = sanitize({ dock: options.keepDock ? memory.dock : saved.dock, dismissed });
  return currentPrefs();
}

/**
 * Ask the worker for the saved look and merge it into memory. Resolves to the merged value, or null when the worker
 * cannot be reached (the caller keeps what it has). `dockTouched` says whether the person already chose a side since
 * the panel appeared: that choice wins over the stored one.
 */
export function loadPrefs(dockTouched: () => boolean): Promise<UiPrefs | null> {
  const run = async (): Promise<UiPrefs | null> => {
    const response = await sendBg<"ui:get">({ t: "ui:get" }, WORKER_WAIT_MS);
    if (!response.ok) return null;
    return adopt(sanitize(response.data), { keepDock: dockTouched() });
  };
  const request = run();
  loading = request.then(() => undefined, () => undefined);
  return request;
}

/** Replace what is remembered. Memory updates at once; the worker gets the latest value, one message at a time. */
export function savePrefs(value: UiPrefs): void {
  memory = sanitize(value);
  dirty = true;
  void flush();
}

async function flush(): Promise<void> {
  if (inFlight) return;
  inFlight = true;
  try {
    // Never write before the stored value has been read and merged: it would erase dismissals this script has not seen.
    await loading;
    while (dirty) {
      dirty = false;
      const response = await sendBg<"ui:set">({ t: "ui:set", value: currentPrefs() }, WORKER_WAIT_MS);
      if (!response.ok) break; // unreachable worker: the in-memory copy still serves this page
    }
  } finally {
    inFlight = false;
  }
}

/** Remember for good that a note was seen, when no panel of this script holds the remembered look (a frame without a panel). */
export function rememberNoteDismissed(kind: NoteKind, at = Date.now()): void {
  const now = currentPrefs();
  savePrefs({ dock: now.dock, dismissed: { ...now.dismissed, [kind]: at } });
}

/** Test hook: forget everything (the rig mounts many independent scenes in one page). */
export function resetPrefsForTests(): void {
  memory = { dock: "right", dismissed: {} };
  loading = null;
  inFlight = false;
  dirty = false;
}
