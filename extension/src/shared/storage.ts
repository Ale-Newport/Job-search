// Typed access to chrome.storage plus the schema migration. The worker is stateless: it re-reads this on every event.
//   storage.local   survives browser restarts: the credential, the user's preferences.
//   storage.session cleared on restart/reload: cached connection facts, the pairing in progress, per-tab activation.
// Web pages cannot read either area. Nothing in here ever logs a value.

import type { ExtensionSettings, Language, SaveCorrections } from "./protocol";
import type { StoredConnection, StoredPrefs, UiPrefs } from "./messages";
import { normalizeHost } from "./hosts";
import { parseToken } from "./hello-proof";
import { isSealed, seal, unseal, vaultSupported } from "./vault";
import type { Sealed } from "./vault";

export const STORAGE_VERSION = 1;

export const KEYS = {
  schema: "meridian.schema",
  connection: "meridian.connection",
  /** the credential where storage.local cannot be protected from content scripts and no vault exists (storage.session) */
  connectionSession: "meridian.connection.session",
  /**
   * The FALLBACK credential: the one a newer pairing replaced. Kept (stored exactly like the current one) until the app that issued
   * it is recognised again (then it becomes current and the newer one is dropped), Meridian says it was revoked, or a month passes.
   * A pairing with a listener nobody could vouch for must never destroy the credential of the real app.
   */
  connectionPrevious: "meridian.connection.previous",
  connectionPreviousSession: "meridian.connection.previous.session",
  prefs: "meridian.prefs",
  ui: "meridian.ui",
  // session area
  bridge: "meridian.bridge",
  conn: "meridian.conn",
  pairing: "meridian.pairing",
  lastError: "meridian.lastError",
  revoked: "meridian.revoked",
  disabled: "meridian.disabled",
  tabPrefix: "meridian.tab.",
  /** one record per tab: the submit its content script is waiting to see confirmed (survives a navigation to another origin) */
  pendingPrefix: "meridian.pending.",
  /** tab ids whose page is hidden (the worker asks the bridge for events only while some waiting tab is visible) */
  hidden: "meridian.hidden",
  /** when this browser last heard Meridian's settings (a page load asks again only when this is old) */
  settingsAt: "meridian.settingsAt",
} as const;

/** Keys that older prototypes or earlier schema versions wrote into storage.local and that must disappear. */
const LEGACY_KEYS = ["apiBase", "token", "meridian.token", "meridian.apiBase", "meridian.settings"];

export const MAX_SITE_RULES = 200;

// ---- area helpers -------------------------------------------------------------------------------------------------

const memorySession = new Map<string, unknown>();

function sessionArea(): chrome.storage.StorageArea | null {
  try {
    return chrome.storage.session ?? null;
  } catch {
    return null;
  }
}

export async function localGet<T>(key: string): Promise<T | undefined> {
  const result = await chrome.storage.local.get(key);
  return result[key] as T | undefined;
}

export async function localSet(values: Record<string, unknown>): Promise<void> {
  await chrome.storage.local.set(values);
}

export async function localRemove(...keys: string[]): Promise<void> {
  await chrome.storage.local.remove(keys);
}

export async function sessionGet<T>(key: string): Promise<T | undefined> {
  const area = sessionArea();
  if (!area) return memorySession.get(key) as T | undefined;
  const result = await area.get(key);
  return result[key] as T | undefined;
}

export async function sessionSet(values: Record<string, unknown>): Promise<void> {
  const area = sessionArea();
  if (!area) {
    for (const [key, value] of Object.entries(values)) memorySession.set(key, value);
    return;
  }
  await area.set(values);
}

export async function sessionRemove(...keys: string[]): Promise<void> {
  const area = sessionArea();
  if (!area) {
    for (const key of keys) memorySession.delete(key);
    return;
  }
  await area.remove(keys);
}

export async function sessionAll(): Promise<Record<string, unknown>> {
  const area = sessionArea();
  if (!area) return Object.fromEntries(memorySession);
  return (await area.get(null)) as Record<string, unknown>;
}

/** Serialises read-modify-write sequences inside one worker instance (the only writer of these keys). */
const locks = new Map<string, Promise<unknown>>();
export function withLock<T>(name: string, task: () => Promise<T>): Promise<T> {
  const previous = locks.get(name) ?? Promise.resolve();
  const run = previous.then(task);
  locks.set(name, run.catch(() => undefined));
  return run;
}

// ---- normalisation ------------------------------------------------------------------------------------------------

const LANGUAGES: readonly Language[] = ["auto", "en", "es"];
const SAVE_MODES: readonly SaveCorrections[] = ["ask", "always", "never"];

export function normalizeLanguage(value: unknown): Language {
  const text = typeof value === "string" ? value.trim().toLowerCase() : "";
  return (LANGUAGES as readonly string[]).includes(text) ? (text as Language) : "auto";
}

function bool(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function hostList(value: unknown): string[] {
  const items = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
  const seen = new Set<string>();
  for (const item of items) {
    const host = normalizeHost(item);
    if (host && seen.size < MAX_SITE_RULES) seen.add(host);
  }
  return [...seen];
}

export function defaultSettings(): ExtensionSettings {
  return {
    enabled: true, language: "auto", auto_advance: true, skip_filled: true, tab_inserts_protected: false,
    save_corrections: "ask", background_research: true, background_drafts: true, web_lookup: true, hint_on_ats: true,
    draft_budget_usd: null, always_on_sites: [],
  };
}

/** The last settings object received from Meridian, defaulting every missing key; null when it is not an object. */
export function normalizeSettings(raw: unknown): ExtensionSettings | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;
  const base = defaultSettings();
  const save = typeof source.save_corrections === "string" ? source.save_corrections : "";
  const budget = source.draft_budget_usd;
  return {
    enabled: bool(source.enabled, base.enabled),
    language: normalizeLanguage(source.language),
    auto_advance: bool(source.auto_advance, base.auto_advance),
    skip_filled: bool(source.skip_filled, base.skip_filled),
    tab_inserts_protected: bool(source.tab_inserts_protected, base.tab_inserts_protected),
    save_corrections: (SAVE_MODES as readonly string[]).includes(save) ? (save as SaveCorrections) : base.save_corrections,
    background_research: bool(source.background_research, base.background_research),
    background_drafts: bool(source.background_drafts, base.background_drafts),
    web_lookup: bool(source.web_lookup, base.web_lookup),
    hint_on_ats: bool(source.hint_on_ats, base.hint_on_ats),
    draft_budget_usd: typeof budget === "number" && Number.isFinite(budget) && budget >= 0 ? budget : null,
    always_on_sites: hostList(source.always_on_sites),
  };
}

export function defaultPrefs(): StoredPrefs {
  return { language: "auto", alwaysOn: [], hintOff: [], settings: null, welcomed: false };
}

/**
 * Every site the assistant should start on by itself: the ones chosen in this browser plus the ones set in Meridian
 * (Settings > Browser assistant > "Start automatically on these sites"). Both lists only name a site: the browser still has
 * to grant the extension access to it (the options page asks), so a rule is never more than a wish until it does.
 */
export function alwaysOnRules(prefs: Pick<StoredPrefs, "alwaysOn" | "settings">): string[] {
  return [...new Set([...prefs.alwaysOn, ...(prefs.settings?.always_on_sites ?? [])])];
}

export function normalizePrefs(raw: unknown): StoredPrefs {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return {
    language: normalizeLanguage(source.language),
    alwaysOn: hostList(source.alwaysOn),
    hintOff: hostList(source.hintOff),
    settings: normalizeSettings(source.settings),
    welcomed: source.welcomed === true,
  };
}

/** A stored credential is trusted only when its parts agree with each other; anything else is dropped. */
export function normalizeConnection(raw: unknown): StoredConnection | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  const token = parseToken(source.token);
  const port = source.port;
  if (!token || typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) return null;
  if (typeof source.clientId !== "string" || source.clientId !== token.clientId) return null;
  return {
    port,
    clientId: token.clientId,
    token: source.token as string,
    clientName: typeof source.clientName === "string" ? source.clientName.slice(0, 60) : "",
    pairedAt: typeof source.pairedAt === "string" ? source.pairedAt : "",
    ...(typeof source.replacedAt === "string" && source.replacedAt ? { replacedAt: source.replacedAt.slice(0, 40) } : {}),
  };
}

// ---- credential ---------------------------------------------------------------------------------------------------
// The bridge credential is the one secret this extension holds. Content scripts run next to hostile pages, so it must
// never sit where they can read it. In order of preference, per engine:
//   "local"   storage.local after setAccessLevel(TRUSTED_CONTEXTS) succeeded: content scripts cannot read the area at all
//             (Chrome). The credential is stored as is.
//   "sealed"  the engine cannot restrict storage.local (content scripts CAN read it, e.g. Safari without setAccessLevel):
//             only ciphertext goes there, encrypted under a non-extractable key held in the extension's own IndexedDB.
//   "session" no vault possible either: the credential lives in storage.session only (trusted contexts by default),
//             so it is lost when the browser quits and the user connects again. Failing closed beats leaking.
// Nothing else this extension stores is secret (see the notes at the top of this file).

export type CredentialPlacement = "local" | "sealed" | "session";

let placementPromise: Promise<CredentialPlacement> | null = null;

async function decidePlacement(): Promise<CredentialPlacement> {
  try {
    // storage.session is trusted-contexts-only by default; saying so explicitly costs nothing where the API exists
    await Promise.resolve(chrome.storage.session?.setAccessLevel?.({ accessLevel: "TRUSTED_CONTEXTS" }));
  } catch {
    // the default already applies
  }
  try {
    if (typeof chrome.storage.local.setAccessLevel === "function") {
      await chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
      return "local";
    }
  } catch {
    // refused: treated exactly like an engine without the API
  }
  // Safari: IndexedDB opens inside the worker but its transactions never run (measured with a WKWebExtension of this build: the
  // first `get` never answers), so a vault there is a pairing that hangs. The credential stays in storage.session, which content
  // scripts cannot read: it is lost when Safari quits and the user connects again (the documented fail-closed tier).
  if (typeof __SAFARI__ !== "undefined" && __SAFARI__) return "session";
  return vaultSupported() ? "sealed" : "session";
}

/** Applies the strongest protection the engine offers (once per worker start) and says where the credential may live. */
export function credentialPlacement(): Promise<CredentialPlacement> {
  if (!placementPromise) placementPromise = decidePlacement();
  return placementPromise;
}

/** Test hook: forget the decision so the next call looks at the (mocked) engine again. */
export function resetCredentialPlacement(): void {
  placementPromise = null;
  opened = { current: null, previous: null };
}

interface SealedConnection {
  port: number;
  clientId: string;
  clientName: string;
  pairedAt: string;
  replacedAt?: string;
  sealed: Sealed;
}

function normalizeSealedConnection(raw: unknown): SealedConnection | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  const port = source.port;
  if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) return null;
  if (typeof source.clientId !== "string" || !/^[A-Za-z0-9-]{8,64}$/.test(source.clientId) || !isSealed(source.sealed)) return null;
  return {
    port, clientId: source.clientId, sealed: source.sealed,
    clientName: typeof source.clientName === "string" ? source.clientName.slice(0, 60) : "",
    pairedAt: typeof source.pairedAt === "string" ? source.pairedAt : "",
    ...(typeof source.replacedAt === "string" && source.replacedAt ? { replacedAt: source.replacedAt.slice(0, 40) } : {}),
  };
}

/** Which of the two stored credentials: the one in use, or the fallback a newer pairing replaced (see KEYS.connectionPrevious). */
export type CredentialSlot = "current" | "previous";

function slotKeys(slot: CredentialSlot): { local: string; session: string } {
  return slot === "previous"
    ? { local: KEYS.connectionPrevious, session: KEYS.connectionPreviousSession }
    : { local: KEYS.connection, session: KEYS.connectionSession };
}

/** The last credential opened in this worker instance, per slot (opening is cheap but not free); losing it is harmless. */
let opened: Record<CredentialSlot, { ct: string; value: StoredConnection } | null> = { current: null, previous: null };

async function openSealed(record: SealedConnection, slot: CredentialSlot): Promise<StoredConnection | null> {
  const last = opened[slot];
  if (last && last.ct === record.sealed.ct) return last.value;
  const token = await unseal(record.sealed, record.clientId);
  if (token === null) return null;
  const value = normalizeConnection({
    port: record.port, clientId: record.clientId, token, clientName: record.clientName, pairedAt: record.pairedAt, replacedAt: record.replacedAt,
  });
  if (value) opened[slot] = { ct: record.sealed.ct, value };
  return value;
}

export async function getConnection(slot: CredentialSlot = "current"): Promise<StoredConnection | null> {
  const where = await credentialPlacement();
  const keys = slotKeys(slot);
  if (where === "session") {
    const fromSession = normalizeConnection(await sessionGet(keys.session));
    if (fromSession) return fromSession;
  } else {
    const raw = await localGet(keys.local);
    if (where === "local") return normalizeConnection(raw);
    const sealed = normalizeSealedConnection(raw);
    if (sealed) return openSealed(sealed, slot);
  }
  if (where !== "session") {
    // where the credential lives when the vault could not hold it (a worker restarted with the vault still unavailable)
    const fromSession = normalizeConnection(await sessionGet(keys.session));
    if (fromSession) return fromSession;
  }
  // A plaintext credential written where content scripts can read it (before protection existed, or by an older
  // build): hand it to the protected store and scrub the plaintext copy.
  const plain = normalizeConnection(await localGet(keys.local));
  if (plain) {
    await setConnection(plain, slot);
    return plain;
  }
  return null;
}

export async function setConnection(connection: StoredConnection, slot: CredentialSlot = "current"): Promise<void> {
  const clean = normalizeConnection(connection);
  if (!clean) throw new Error("invalid credential");
  const where = await credentialPlacement();
  const keys = slotKeys(slot);
  if (where === "local") {
    await localSet({ [keys.local]: clean });
    await sessionRemove(keys.session);
    return;
  }
  if (where === "sealed") {
    const sealed = await seal(clean.token, clean.clientId);
    if (sealed) {
      const record: SealedConnection = {
        port: clean.port, clientId: clean.clientId, clientName: clean.clientName, pairedAt: clean.pairedAt, sealed,
        ...(clean.replacedAt ? { replacedAt: clean.replacedAt } : {}),
      };
      await localSet({ [keys.local]: record });
      await sessionRemove(keys.session);
      opened[slot] = { ct: sealed.ct, value: clean };
      return;
    }
  }
  await sessionSet({ [keys.session]: clean });
  await localRemove(keys.local); // never a plaintext copy next to the protected one
}

/** Forgets the credential(s): by default both the one in use and the fallback (the user disconnected: nothing of this pairing stays). */
export async function clearConnection(slot: CredentialSlot | "all" = "all"): Promise<void> {
  for (const which of ["current", "previous"] as const) {
    if (slot !== "all" && slot !== which) continue;
    const keys = slotKeys(which);
    opened[which] = null;
    await localRemove(keys.local);
    await sessionRemove(keys.session);
  }
}

// ---- preferences --------------------------------------------------------------------------------------------------

export async function getPrefs(): Promise<StoredPrefs> {
  return normalizePrefs(await localGet(KEYS.prefs));
}

/** Validated partial update; unknown keys are ignored, bad values keep the stored one. Returns the new prefs. */
export function patchPrefs(patch: Partial<StoredPrefs>): Promise<StoredPrefs> {
  return withLock(KEYS.prefs, async () => {
    const current = await getPrefs();
    const next: StoredPrefs = { ...current };
    if (patch && typeof patch === "object") {
      if ("language" in patch) next.language = normalizeLanguage(patch.language);
      if ("alwaysOn" in patch) next.alwaysOn = hostList(patch.alwaysOn);
      if ("hintOff" in patch) next.hintOff = hostList(patch.hintOff);
      if ("welcomed" in patch) next.welcomed = patch.welcomed === true;
      if ("settings" in patch) next.settings = patch.settings === null ? null : normalizeSettings(patch.settings) ?? current.settings;
    }
    await localSet({ [KEYS.prefs]: next });
    return next;
  });
}

/** Remember the settings Meridian reported so the assistant keeps behaving while the bridge is unreachable. */
export async function rememberSettings(settings: unknown): Promise<void> {
  const clean = normalizeSettings(settings);
  if (!clean) return;
  await patchPrefs({ settings: clean });
  await markSettingsHeard();
}

/** Meridian's settings were just heard (or asked for): a page load will not ask again for a while. */
export async function markSettingsHeard(at: number = Date.now()): Promise<void> {
  await sessionSet({ [KEYS.settingsAt]: at });
}

/** Epoch ms when Meridian's settings were last heard or asked for in this browser session; 0 when never. */
export async function settingsHeardAt(): Promise<number> {
  const at = await sessionGet<unknown>(KEYS.settingsAt);
  return typeof at === "number" && Number.isFinite(at) && at > 0 ? at : 0;
}

// ---- panel look (written by content scripts through the worker) --------------------------------------------------

export function normalizeUiPrefs(raw: unknown): UiPrefs {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  const dismissed: Record<string, number> = {};
  const entries = source.dismissed && typeof source.dismissed === "object" && !Array.isArray(source.dismissed)
    ? Object.entries(source.dismissed as Record<string, unknown>) : [];
  for (const [key, value] of entries) {
    if (Object.keys(dismissed).length >= 24) break;
    if (/^[A-Za-z0-9_:.-]{1,40}$/.test(key) && typeof value === "number" && Number.isFinite(value)) dismissed[key] = value;
  }
  return { dock: source.dock === "left" ? "left" : "right", dismissed };
}

export async function getUiPrefs(): Promise<UiPrefs> {
  return normalizeUiPrefs(await localGet(KEYS.ui));
}

export async function setUiPrefs(value: unknown): Promise<UiPrefs> {
  const clean = normalizeUiPrefs(value);
  await localSet({ [KEYS.ui]: clean });
  return clean;
}

// ---- migration ----------------------------------------------------------------------------------------------------

/**
 * Brings storage.local to STORAGE_VERSION. Idempotent and tolerant: damaged or foreign values are normalised or removed,
 * never thrown. A store written by a NEWER version (downgrade) is left untouched; the normalisers read it defensively.
 */
export function migrateStorage(): Promise<{ from: number; to: number }> {
  return withLock("migrate", async () => {
    const all = await chrome.storage.local.get(null);
    const rawVersion = all[KEYS.schema];
    const from = typeof rawVersion === "number" && Number.isFinite(rawVersion) ? rawVersion : 0;
    if (from >= STORAGE_VERSION) return { from, to: from };
    const updates: Record<string, unknown> = { [KEYS.schema]: STORAGE_VERSION };
    if (KEYS.prefs in all) updates[KEYS.prefs] = normalizePrefs(all[KEYS.prefs]);
    const remove = LEGACY_KEYS.filter((key) => key in all);
    for (const key of [KEYS.connection, KEYS.connectionPrevious]) {
      if (!(key in all)) continue;
      const connection = normalizeConnection(all[key]);
      if (connection) updates[key] = connection;
      else if (!normalizeSealedConnection(all[key])) remove.push(key);
    }
    await chrome.storage.local.set(updates);
    if (remove.length) await chrome.storage.local.remove(remove);
    return { from, to: STORAGE_VERSION };
  });
}

// ---- last error (diagnostics) -------------------------------------------------------------------------------------

export async function recordError(code: string): Promise<void> {
  await sessionSet({ [KEYS.lastError]: { code: String(code).slice(0, 40), at: new Date().toISOString() } });
}

export async function lastError(): Promise<{ code: string; at: string } | null> {
  const value = await sessionGet<{ code: string; at: string }>(KEYS.lastError);
  return value && typeof value.code === "string" && typeof value.at === "string" ? value : null;
}
