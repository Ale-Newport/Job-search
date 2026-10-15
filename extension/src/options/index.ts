// Options and welcome page (docs/EXTENSION_DESIGN.md 7.6). Plain TypeScript and DOM, no framework.
// It talks only to the service worker (chrome.runtime.sendMessage) and reads preferences from storage; it holds no
// credential and never shows one. Every string comes from shared/i18n-core.ts (English and Spanish).

import type { BgRequest, BgResponse, BgResponseData, ConnectionState, Diagnostics, StoredPrefs } from "../shared/messages";
import type { DocumentInfo, ExtensionSettings, Language, SaveCorrections } from "../shared/protocol";
import { normalizeHost } from "../shared/hosts";
import { browserLanguage, languageFromPrefs, t as translate } from "../shared/i18n-core";
import type { Lang, StringKey } from "../shared/i18n-core";
import { hasAllSites, hasHost, removeHost, requestAllSites, requestHost } from "../shared/permissions";
import { defaultStartShortcut, isMacPlatform, startKeyRow } from "../shared/platform";
import { KEYS, alwaysOnRules, defaultPrefs, defaultSettings, getPrefs } from "../shared/storage";
import { compareVersions } from "../shared/version";
import { svgMark } from "../shared/mark";
import { Bindings, h, keyedList } from "./dom";

type PairState = "pending" | "approved" | "denied" | "expired" | "claimed";
type Action = "connect" | "connect_again" | "cancel" | "disconnect" | "open" | "retry" | "connect_unknown" | "confirm_unknown" | "cancel_unknown";
type BooleanSetting = "skip_filled" | "auto_advance" | "tab_inserts_protected" | "hint_on_ats";

interface Notice { kind: "restricted" | "permission" | "failed"; site: string | null; /** the answer also stores the "start automatically" rule */ always: boolean }
/** One line of "Start automatically on these sites": a site chosen here, or one chosen in Meridian's own settings. */
interface SiteRule { host: string; own: boolean; allowed: boolean }
interface Flash { text: StringKey; ok: boolean }

const state = {
  prefs: defaultPrefs() as StoredPrefs,
  conn: null as ConnectionState | null,
  diag: null as Diagnostics | null,
  allSites: false,
  pairError: null as StringKey | null,
  /** after "Disconnect": did Meridian drop this browser too ("remote"), or only this side forgot it ("local")? */
  disconnected: null as "remote" | "local" | null,
  starting: false,
  /** the user pressed "Connect to it…" for a listener that could not be recognised and has not yet said "Yes" */
  confirmingUnknown: false,
  notice: null as Notice | null,
  /** what the last "Allow" on the notice did: the confirmation shown instead of the question */
  noticeResult: null as { site: string; always: boolean } | { site: string; denied: true } | null,
  /** shown alone in the small window the worker opens to ask for a permission */
  popup: false,
  /** can the extension work on each rule's site right now? (a stored rule without access does nothing) */
  siteAccess: {} as Record<string, boolean>,
  documents: null as DocumentInfo[] | null,
  welcomeRequested: false,
  flash: null as Flash | null,
  siteError: null as StringKey | null,
  permError: null as StringKey | null,
  shortcut: "",
  copied: false,
  busy: new Set<string>(),
};

const bindings = new Bindings();
const manifestVersion = chrome.runtime.getManifest().version;

function lang(): Lang {
  return languageFromPrefs(state.prefs, browserLanguage());
}
function tr(key: StringKey, params?: Record<string, string | number>): string {
  return translate(lang(), key, params);
}
function settings(): ExtensionSettings {
  return state.prefs.settings ?? defaultSettings();
}
function ready(): boolean {
  return state.conn?.phase === "ready";
}

async function call<R extends BgRequest>(request: R): Promise<BgResponse<BgResponseData<R["t"]>>> {
  try {
    return (await chrome.runtime.sendMessage(request)) as BgResponse<BgResponseData<R["t"]>>;
  } catch {
    return { ok: false, error: { code: "error", detail: "The extension worker did not answer." } };
  }
}

// ---- data flow ----------------------------------------------------------------------------------------------------

async function loadPrefs(): Promise<void> {
  state.prefs = await getPrefs();
  await loadSiteAccess();
  bindings.refresh();
}

async function loadSiteAccess(): Promise<void> {
  const access: Record<string, boolean> = {};
  for (const host of alwaysOnRules(state.prefs)) access[host] = await hasHost(host);
  state.siteAccess = access;
}

async function loadDocuments(): Promise<void> {
  if (!ready()) {
    state.documents = null;
    return;
  }
  const response = await call({ t: "documents" });
  state.documents = response.ok && Array.isArray(response.data) ? response.data : null;
  bindings.refresh();
}

async function loadConnection(refresh = false): Promise<void> {
  const before = state.conn?.phase;
  const response = await call({ t: "conn:state", refresh });
  if (response.ok) state.conn = response.data;
  else if (response.error.connection) state.conn = response.error.connection;
  if (state.conn?.phase === "ready") state.disconnected = null; // a later outage is not about that old disconnect
  if (state.conn?.phase !== "unknown_listener") state.confirmingUnknown = false; // the question only makes sense while the listener is unknown
  bindings.refresh();
  if (before !== undefined && before !== state.conn?.phase) {
    void loadDiagnostics();
    void loadDocuments();
  }
}

async function loadDiagnostics(): Promise<void> {
  const response = await call({ t: "diagnostics" });
  if (response.ok) state.diag = response.data;
  state.allSites = await hasAllSites();
  await loadSiteAccess();
  bindings.refresh();
}

async function loadSettingsFromApp(): Promise<void> {
  if (!ready()) return;
  const response = await call({ t: "status" });
  if (response.ok) await loadPrefs();
}

async function loadShortcut(): Promise<void> {
  try {
    const commands = await chrome.commands.getAll();
    const action = commands.find((command) => command.name === "_execute_action");
    state.shortcut = action?.shortcut ?? "";
  } catch {
    state.shortcut = ""; // Safari has no commands API
  }
  bindings.refresh();
}

async function refreshAll(force = false): Promise<void> {
  await loadPrefs();
  await loadConnection(force);
  await Promise.all([loadDiagnostics(), loadShortcut()]);
  await loadSettingsFromApp();
  await loadDocuments();
}

// ---- pairing ------------------------------------------------------------------------------------------------------

async function startPairing(confirmUnknown = false): Promise<void> {
  if (state.starting) return;
  state.starting = true;
  state.confirmingUnknown = false;
  state.pairError = null;
  state.disconnected = null;
  bindings.refresh();
  const response = await call(confirmUnknown ? { t: "pair:start", confirmUnknown: true } : { t: "pair:start" });
  state.starting = false;
  if (!response.ok) {
    if (response.error.code === "disabled") state.pairError = "conn.disabled";
    else if (response.error.connection) state.conn = response.error.connection;
    else state.pairError = response.error.code === "rate_limited" ? "pair.rate_limited" : "pair.failed";
  }
  await loadConnection(true);
  schedulePoll(0);
}

async function pollPairing(): Promise<void> {
  const pairing = state.conn?.pairing;
  if (!pairing) return;
  const response = await call({ t: "pair:poll", pairingId: pairing.pairingId });
  if (!response.ok) {
    if (response.error.connection) state.conn = response.error.connection;
    bindings.refresh();
    return;
  }
  const result = response.data as { state: PairState; connection: ConnectionState };
  state.conn = result.connection;
  if (result.state === "denied") state.pairError = "pair.denied";
  else if (result.state === "expired") state.pairError = "pair.expired";
  else if (result.state === "approved" || result.state === "claimed") {
    state.pairError = null;
    await Promise.all([loadDiagnostics(), loadSettingsFromApp(), loadDocuments()]);
  }
  bindings.refresh();
}

async function cancelPairing(): Promise<void> {
  const pairing = state.conn?.pairing;
  if (pairing) await call({ t: "pair:cancel", pairingId: pairing.pairingId });
  state.pairError = null;
  await loadConnection(true);
}

async function disconnect(): Promise<void> {
  const response = await call({ t: "disconnect" });
  if (response.ok) {
    state.conn = response.data;
    state.disconnected = response.data.remoteRevoked === true ? "remote" : "local";
  }
  state.pairError = null;
  await loadDiagnostics();
  bindings.refresh();
}

// ---- settings -----------------------------------------------------------------------------------------------------

let flashTimer: ReturnType<typeof setTimeout> | undefined;
function flash(text: StringKey, ok: boolean): void {
  state.flash = { text, ok };
  bindings.refresh();
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    state.flash = null;
    bindings.refresh();
  }, 2500);
}

async function patchSetting(patch: Partial<ExtensionSettings>): Promise<void> {
  state.prefs = { ...state.prefs, settings: { ...settings(), ...patch } };
  bindings.refresh();
  const response = await call({ t: "settings:patch", patch });
  await loadPrefs();
  if (!response.ok && response.error.connection) state.conn = response.error.connection;
  flash(response.ok ? "beh.saved" : "beh.save_failed", response.ok);
}

async function setLanguage(value: Language): Promise<void> {
  const nextSettings = state.prefs.settings ? { ...state.prefs.settings, language: value } : null;
  await call({ t: "prefs:patch", patch: { language: value, settings: nextSettings } });
  await loadPrefs();
  if (ready()) {
    const response = await call({ t: "settings:patch", patch: { language: value } });
    await loadPrefs();
    flash(response.ok ? "beh.saved" : "beh.save_failed", response.ok);
  }
}

function languageChoice(): Language {
  const fromApp = state.prefs.settings?.language;
  return fromApp && fromApp !== "auto" ? fromApp : state.prefs.language;
}

// ---- permissions and sites ----------------------------------------------------------------------------------------

async function allowAllSites(): Promise<void> {
  state.permError = null;
  const granted = await requestAllSites(); // first await: keeps the click's user activation
  if (granted) await call({ t: "permissions:granted" });
  else state.permError = "perm.all.denied";
  await loadDiagnostics();
}

async function allowSite(site: string): Promise<boolean> {
  // the click is still fresh at the first await: a permission request needs it
  const granted = (await hasHost(site)) || (await requestHost(site));
  if (granted) await call({ t: "permissions:granted" });
  await loadDiagnostics();
  return granted;
}

/** The answer to the question the worker opened this page for ("Allow {site}"). */
async function answerNotice(): Promise<void> {
  const notice = state.notice;
  if (!notice?.site) return;
  const site = notice.site;
  // the click is still fresh at the first await: a permission request needs it
  const granted = (await hasHost(site)) || (await requestHost(site));
  if (!granted) {
    await loadDiagnostics();
    state.noticeResult = { site, denied: true };
    bindings.refresh();
    return;
  }
  if (notice.always) {
    // the rule is stored only now that the browser has agreed (a switch that says "on" is always true), and BEFORE the worker is
    // told about the permission, so that it also starts the tabs of that site that are open right now
    const alwaysOn = [...state.prefs.alwaysOn.filter((entry) => entry !== site), site];
    await call({ t: "prefs:patch", patch: { alwaysOn } });
  }
  await call({ t: "permissions:granted" });
  await loadPrefs();
  await loadDiagnostics();
  state.notice = null;
  state.noticeResult = { site, always: notice.always };
  bindings.refresh();
  if (state.popup) setTimeout(() => window.close(), 1600); // its job is done: the application tab was never left
}

function dismissNotice(): void {
  state.notice = null;
  state.noticeResult = null;
  bindings.refresh();
  if (state.popup) window.close();
}

async function addAlwaysOnSite(raw: string): Promise<boolean> {
  const host = normalizeHost(raw);
  if (!host) {
    state.siteError = "sites.invalid";
    bindings.refresh();
    return false;
  }
  state.siteError = null;
  const granted = await requestHost(host);
  if (!granted && !(await hasHost(host))) {
    state.siteError = "sites.denied";
    bindings.refresh();
    return false;
  }
  const alwaysOn = [...state.prefs.alwaysOn.filter((entry) => entry !== host), host];
  await call({ t: "prefs:patch", patch: { alwaysOn } });
  await call({ t: "permissions:granted" });
  await loadPrefs();
  await loadDiagnostics();
  return true;
}

async function removeAlwaysOnSite(host: string): Promise<void> {
  await call({ t: "prefs:patch", patch: { alwaysOn: state.prefs.alwaysOn.filter((entry) => entry !== host) } });
  // the permission goes too, unless Meridian's own list still names the site or the browser has all sites
  if (!state.allSites && !(state.prefs.settings?.always_on_sites ?? []).includes(host)) await removeHost(host);
  await loadPrefs();
  await loadDiagnostics();
}

async function allowRuleSite(host: string): Promise<void> {
  await allowSite(host);
  await loadPrefs();
}

function siteRules(): SiteRule[] {
  const own = new Set(state.prefs.alwaysOn);
  return alwaysOnRules(state.prefs).map((host) => ({ host, own: own.has(host), allowed: state.siteAccess[host] === true }));
}

async function removeHintSite(host: string): Promise<void> {
  await call({ t: "prefs:patch", patch: { hintOff: state.prefs.hintOff.filter((entry) => entry !== host) } });
  await loadPrefs();
}

// ---- view ---------------------------------------------------------------------------------------------------------

function button(label: () => string, kind: "primary" | "secondary" | "quiet", onClick: () => void, extra: Record<string, string | boolean> = {}): HTMLButtonElement {
  const node = h("button", { type: "button", class: `btn ${kind}`, ...extra });
  node.addEventListener("click", onClick);
  return bindings.bind(node, (el) => {
    const next = label();
    if (el.textContent !== next) el.textContent = next;
  });
}

function section(id: string, titleKey: StringKey, ...children: (Node | null)[]): HTMLElement {
  const title = bindings.text("h2", { id: `${id}-title`, tabindex: "-1" }, () => tr(titleKey));
  return h("section", { id, class: "card", "aria-labelledby": `${id}-title` }, title, ...children);
}

function switchRow(key: BooleanSetting, labelKey: StringKey, hintKey: StringKey): HTMLElement {
  const input = h("input", { type: "checkbox", role: "switch", id: `sw-${key}`, "aria-describedby": `sw-${key}-hint` });
  input.addEventListener("change", () => void patchSetting({ [key]: input.checked } as Partial<ExtensionSettings>));
  bindings.bind(input, (el) => {
    el.checked = settings()[key];
    el.disabled = !ready();
  });
  const label = bindings.text("label", { for: `sw-${key}`, class: "row-label" }, () => tr(labelKey));
  const hint = bindings.text("p", { id: `sw-${key}-hint`, class: "hint" }, () => tr(hintKey));
  return h("div", { class: "row" }, h("div", { class: "row-text" }, label, hint), h("span", { class: "switch" }, input, h("span", { class: "track", "aria-hidden": "true" })));
}

function selectRow<T extends string>(id: string, labelKey: StringKey, options: { value: T; label: StringKey }[], read: () => T, write: (value: T) => void, disabled: () => boolean): HTMLElement {
  const select = h("select", { id, class: "select" });
  for (const option of options) select.append(h("option", { value: option.value }));
  select.addEventListener("change", () => write(select.value as T));
  bindings.bind(select, (el) => {
    options.forEach((option, index) => {
      const node = el.options[index];
      if (node && node.textContent !== tr(option.label)) node.textContent = tr(option.label);
    });
    if (el.value !== read()) el.value = read();
    el.disabled = disabled();
  });
  const label = bindings.text("label", { for: id, class: "row-label" }, () => tr(labelKey));
  return h("div", { class: "row" }, h("div", { class: "row-text" }, label), select);
}

/** Two or more programs answer like Meridian and no credential can tell them apart: pairing is refused (see connection.ts). */
function ambiguous(): boolean {
  const conn = state.conn;
  return (conn?.phase === "unpaired" || conn?.phase === "revoked" || conn?.phase === "unknown_listener") && (conn.meridianPorts?.length ?? 0) > 1;
}

function connectionCard(): HTMLElement {
  const visibleActions = (): Action[] => {
    const phase = state.conn?.phase;
    switch (phase) {
      case "ready": return ["open", "disconnect"];
      case "no_bridge": return ["open", "retry"];
      case "unpaired": return ambiguous() ? ["retry", "open"] : ["connect", "open"];
      case "pairing": return ["open", "cancel"];
      case "revoked": return ambiguous() ? ["retry", "open"] : ["connect_again", "open"];
      case "proof_failed": return ["retry", "disconnect"];
      case "unknown_listener":
        return ambiguous() ? ["retry", "open"] : state.confirmingUnknown ? ["confirm_unknown", "cancel_unknown"] : ["connect_unknown", "retry", "open"];
      case "disabled": return ["open", "retry"];
      case "restoring": return ["retry"];
      case "error": return ["retry", "open"];
      default: return [];
    }
  };
  const headline = (): string => {
    const phase = state.conn?.phase;
    if (phase === "pairing") return tr("conn.pairing.waiting");
    return phase ? tr(`conn.${phase}` as StringKey) : tr("conn.checking");
  };
  const dot = bindings.bind(h("span", { class: "dot", "aria-hidden": "true" }), (el) => {
    const phase = state.conn?.phase;
    el.dataset.tone = phase === "ready" ? "ok" : phase === "proof_failed" || phase === "error" ? "bad" : phase ? "warn" : "idle";
  });
  const status = bindings.bind(h("div", { class: "status", role: "status", "aria-live": "polite" }), (el) => {
    const next = headline();
    if (el.textContent !== next) el.textContent = next;
  });
  const detail = bindings.bind(h("p", { class: "hint" }), (el) => {
    const conn = state.conn;
    let text = "";
    if (conn?.phase === "ready") {
      text = tr("conn.ready.detail", { version: conn.appVersion || "?", port: conn.port ?? "?" });
      if (conn.clientName) text += `. ${tr("conn.ready.client", { name: conn.clientName })}`;
    } else if (ambiguous()) text = tr("conn.ambiguous", { ports: (conn?.meridianPorts ?? []).join(", ") });
    else if (state.disconnected && conn?.phase !== "pairing") text = tr(state.disconnected === "remote" ? "conn.disconnected" : "conn.disconnected.local");
    else if (conn?.phase === "no_bridge") text = tr("conn.no_bridge.hint");
    else if (conn?.phase === "unknown_listener" && conn.port !== undefined) {
      text = tr(state.confirmingUnknown ? "conn.unknown_listener.confirm" : "conn.unknown_listener.hint", { port: conn.port });
    } else if (state.pairError) text = tr(state.pairError);
    else if ((conn?.phase === "unpaired" || conn?.phase === "revoked") && conn.port !== undefined) text = tr("conn.unpaired.found", { port: conn.port });
    if (el.textContent !== text) el.textContent = text;
    el.hidden = text === "";
  });
  const code = bindings.bind(h("div", { class: "code", "aria-live": "off" }), (el) => {
    const digits = state.conn?.pairing?.code ?? "";
    const spaced = digits.length === 6 ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
    if (el.textContent !== spaced) el.textContent = spaced;
    el.setAttribute("aria-label", digits.split("").join(" "));
  });
  const pairBox = bindings.bind(h("div", { class: "pair", role: "group" }, bindings.text("p", { class: "pair-title" }, () => tr("conn.pairing")), code,
    bindings.text("p", { class: "hint" }, () => {
      const left = Math.max(0, Math.round(((state.conn?.pairing?.expiresAt ?? 0) - Date.now()) / 1000));
      return tr("conn.pairing.expires", { seconds: left });
    }),
    // the first pairing has no secret to check anything against: the user is the check, so say exactly what to expect
    bindings.text("p", { class: "hint pair-trust" }, () => tr("conn.pairing.window", { port: state.conn?.port ?? "?" }))), (el) => {
    el.hidden = state.conn?.phase !== "pairing";
  });
  const labels: Record<Action, () => string> = {
    connect: () => (state.starting ? tr("conn.checking") : tr("btn.connect")),
    connect_again: () => tr("btn.connect_again"),
    cancel: () => tr("btn.cancel"),
    disconnect: () => tr("btn.disconnect"),
    open: () => tr("btn.open_meridian"),
    retry: () => tr("btn.retry"),
    connect_unknown: () => tr("btn.connect_unknown"),
    confirm_unknown: () => (state.starting ? tr("conn.checking") : tr("btn.confirm_unknown")),
    cancel_unknown: () => tr("btn.cancel_unknown"),
  };
  const run: Record<Action, () => void> = {
    connect: () => void startPairing(),
    connect_again: () => void startPairing(),
    cancel: () => void cancelPairing(),
    disconnect: () => void disconnect(),
    open: () => void call({ t: "meridian:open" }),
    retry: () => void refreshAll(true),
    connect_unknown: () => {
      state.confirmingUnknown = true;
      bindings.refresh();
    },
    confirm_unknown: () => void startPairing(true),
    cancel_unknown: () => {
      state.confirmingUnknown = false;
      bindings.refresh();
    },
  };
  const kinds: Record<Action, "primary" | "secondary" | "quiet"> = {
    connect: "primary", connect_again: "primary", cancel: "secondary", disconnect: "quiet", open: "secondary", retry: "secondary",
    connect_unknown: "secondary", confirm_unknown: "primary", cancel_unknown: "secondary",
  };
  const buttons = (Object.keys(labels) as Action[]).map((action) => {
    const node = button(labels[action], kinds[action], run[action], { "data-action": action });
    bindings.bind(node, (el) => {
      el.hidden = !visibleActions().includes(action);
      el.disabled = (action === "connect" || action === "confirm_unknown") && state.starting;
    });
    return node;
  });
  const note = bindings.text("p", { class: "hint note" }, () => tr("conn.disconnect.note"));
  bindings.bind(note, (el) => {
    el.hidden = !ready();
  });
  return section("connection", "conn.title", h("div", { class: "status-line" }, dot, status), detail, pairBox, h("div", { class: "actions" }, ...buttons), note);
}

function welcomeCard(): HTMLElement {
  const step = (index: number, titleKey: StringKey, body: StringKey | (() => string), done: () => boolean): HTMLElement => {
    const badge = bindings.bind(h("span", { class: "step-badge" }), (el) => {
      el.textContent = done() ? "✓" : String(index);
      el.dataset.done = done() ? "true" : "false";
      el.setAttribute("aria-label", done() ? `${index}. ${tr("welcome.step.done")}` : String(index));
    });
    return h("li", { class: "step" }, badge, h("div", {}, bindings.text("strong", {}, () => tr(titleKey)), bindings.text("p", { class: "hint" }, () => (typeof body === "string" ? tr(body) : body()))));
  };
  const dismiss = button(() => tr("welcome.dismiss"), "primary", () => {
    void call({ t: "prefs:patch", patch: { welcomed: true } }).then(loadPrefs);
  });
  const card = h("section", { id: "welcome", class: "card welcome", "aria-labelledby": "welcome-title" },
    bindings.text("h2", { id: "welcome-title", tabindex: "-1" }, () => tr("welcome.title")),
    h("ol", { class: "steps" },
      step(1, "welcome.step1.title", "welcome.step1.body", () => state.conn !== null && state.conn.phase !== "no_bridge"),
      step(2, "welcome.step2.title", "welcome.step2.body", ready),
      step(3, "welcome.step3.title", () => `${tr("welcome.step3.body")}${state.shortcut ? ` ${tr("welcome.step3.shortcut", { keys: state.shortcut })}` : ""}`, () => (state.diag?.activeTabs ?? 0) > 0)),
    bindings.bind(h("p", { class: "hint tip" }), (el) => {
      el.textContent = tr("welcome.tip");
      el.hidden = state.allSites;
    }),
    bindings.text("p", { class: "hint tip" }, () => tr("welcome.help")),
    h("div", { class: "actions" }, dismiss));
  return bindings.bind(card, (el) => {
    el.hidden = !(state.welcomeRequested || !state.prefs.welcomed);
  });
}

function noticeBanner(): HTMLElement {
  const text = bindings.text("p", { class: "notice-text" }, () => {
    const notice = state.notice;
    if (!notice) return "";
    if (notice.kind === "permission") return tr(notice.always ? "reason.permission.always" : "reason.permission", { site: notice.site ?? "" });
    return tr(notice.kind === "restricted" ? "reason.restricted" : "reason.failed");
  });
  const allow = button(() => tr("perm.site.btn", { site: state.notice?.site ?? "" }), "primary", () => void answerNotice(), { "data-action": "allow-notice" });
  bindings.bind(allow, (el) => {
    el.hidden = !(state.notice?.kind === "permission" && state.notice.site);
  });
  const dismiss = button(() => tr("reason.dismiss"), "quiet", dismissNotice);
  const banner = h("div", { class: "notice", role: "alert" }, text, h("div", { class: "actions" }, allow, dismiss));
  bindings.bind(banner, (el) => {
    el.hidden = state.notice === null;
  });
  // what the last answer did: a refusal changes nothing and says so; an allow says what now works
  const result = bindings.bind(h("p", { class: "notice-result", role: "status" }), (el) => {
    const outcome = state.noticeResult;
    let next = "";
    if (outcome && "denied" in outcome) next = tr("perm.site.denied", { site: outcome.site });
    else if (outcome) next = tr(outcome.always ? "perm.site.done.always" : "perm.site.done", { site: outcome.site });
    if (el.textContent !== next) el.textContent = next;
    el.hidden = next === "";
    el.dataset.ok = outcome && "denied" in outcome ? "false" : "true";
  });
  const box = h("div", { class: "notice-box" }, banner, result);
  if (state.popup) queueMicrotask(() => allow.focus()); // a key press is a gesture too: Enter answers at once
  return box;
}

function behaviourCard(): HTMLElement {
  const need = bindings.text("p", { class: "hint need" }, () => tr("beh.need_connection"));
  bindings.bind(need, (el) => {
    el.hidden = ready();
  });
  const flashLine = bindings.bind(h("p", { class: "flash", role: "status", "aria-live": "polite" }), (el) => {
    const next = state.flash ? tr(state.flash.text) : "";
    if (el.textContent !== next) el.textContent = next;
    el.dataset.ok = state.flash?.ok === false ? "false" : "true";
  });
  const shortcut = bindings.text("p", { class: "hint" }, () => (state.shortcut ? tr("beh.shortcut", { keys: state.shortcut }) : ""));
  bindings.bind(shortcut, (el) => {
    el.hidden = state.shortcut === "";
  });
  return section("behaviour", "beh.title",
    need,
    selectRow<Language>("language", "beh.language", [
      { value: "auto", label: "beh.language.auto" }, { value: "en", label: "beh.language.en" }, { value: "es", label: "beh.language.es" },
    ], languageChoice, (value) => void setLanguage(value), () => false),
    switchRow("skip_filled", "beh.skip_filled", "beh.skip_filled.hint"),
    switchRow("auto_advance", "beh.auto_advance", "beh.auto_advance.hint"),
    switchRow("tab_inserts_protected", "beh.protected_tab", "beh.protected_tab.hint"),
    selectRow<SaveCorrections>("save-corrections", "beh.save_corrections", [
      { value: "ask", label: "beh.save.ask" }, { value: "always", label: "beh.save.always" }, { value: "never", label: "beh.save.never" },
    ], () => settings().save_corrections, (value) => void patchSetting({ save_corrections: value }), () => !ready()),
    switchRow("hint_on_ats", "beh.hint_on_ats", "beh.hint_on_ats.hint"),
    flashLine, shortcut);
}

/**
 * One line of the key list: what to press and what it does. Both are functions because the key names follow the platform (Option on a
 * Mac, Alt elsewhere) and the language, and the start row follows what the browser really offers (a shortcut, none, or no such thing).
 */
interface KeyRow { key: () => string; text: () => string }

function keyRows(): KeyRow[] {
  const mac = isMacPlatform();
  const alt = mac ? "Option" : "Alt";
  const plain = (key: StringKey, text: StringKey): KeyRow => ({ key: () => tr(key, { alt }), text: () => tr(text) });
  const start: KeyRow = {
    key: () => (startKeyRow(__SAFARI__, state.shortcut).key === "shortcut" ? state.shortcut : tr("help.k.start")),
    text: () => tr(startKeyRow(__SAFARI__, state.shortcut).text, { default: defaultStartShortcut(mac) }),
  };
  return [
    plain("help.k.click", "help.d.click"), plain("help.k.tab", "help.d.tab"), plain("help.k.shifttab", "help.d.shifttab"),
    plain("help.k.enter", "help.d.enter"), plain("help.k.esc", "help.d.esc"), plain("help.k.undo", "help.d.undo"),
    plain("help.k.rewrite", "help.d.rewrite"), plain(mac ? "help.k.chip.mac" : "help.k.chip.other", "help.d.chip"),
    plain("help.k.panel", "help.d.panel"), plain("help.k.undoall", "help.d.undoall"), plain("help.k.arrows", "help.d.arrows"), start,
    plain("help.k.pause", "help.d.pause"),
  ];
}

function helpCard(): HTMLElement {
  const list = h("dl", { class: "keys" });
  for (const row of keyRows()) {
    list.append(h("div", { class: "key-row" },
      h("dt", {}, bindings.text("kbd", {}, row.key)),
      bindings.text("dd", {}, row.text)));
  }
  const tones: [string, StringKey, StringKey][] = [
    ["verified", "help.c.verified", "help.d.verified"], ["inferred", "help.c.inferred", "help.d.inferred"],
    ["draft", "help.c.draft", "help.d.draft"], ["grey", "help.c.grey", "help.d.grey"],
  ];
  const colours = h("ul", { class: "colours" }, ...tones.map(([tone, label, text]) => h("li", {},
    h("span", { class: "swatch", "data-tone": tone, "aria-hidden": "true" }),
    h("span", {}, bindings.text("strong", {}, () => tr(label)), " ", bindings.text("span", {}, () => tr(text))))));
  return section("help", "help.title", bindings.text("p", {}, () => tr("help.intro")),
    bindings.text("h3", {}, () => tr("help.keys.title")), list,
    bindings.text("h3", {}, () => tr("help.colours.title")), colours,
    bindings.text("p", { class: "hint" }, () => tr("help.never")));
}

function documentsCard(): HTMLElement {
  const list = h("ul", { class: "docs" });
  const rebuild = keyedList(list, () => `${lang()}|${(state.documents ?? []).map((doc) => `${doc.id}:${doc.default}`).join(",")}`, () => (state.documents ?? []).map((doc) =>
    h("li", {}, h("span", { class: "host" }, doc.filename || doc.name),
      h("span", { class: "hint" }, tr("docs.version", { version: doc.version })), doc.default ? h("span", { class: "tag" }, tr("docs.default")) : null)));
  bindings.bind(list, rebuild);
  const empty = bindings.text("p", { class: "hint" }, () => (state.documents === null ? tr("docs.unknown") : tr("docs.none")));
  bindings.bind(empty, (el) => {
    el.hidden = (state.documents?.length ?? 0) > 0;
  });
  const listNote = bindings.text("p", { class: "hint" }, () => tr("docs.list"));
  bindings.bind(listNote, (el) => {
    el.hidden = (state.documents?.length ?? 0) === 0;
  });
  return section("documents", "docs.title", bindings.text("p", {}, () => tr("docs.body")), listNote, list, empty);
}

function permissionsCard(): HTMLElement {
  const grant = button(() => tr("perm.all.btn"), "primary", () => void allowAllSites());
  bindings.bind(grant, (el) => {
    el.hidden = state.allSites;
  });
  const granted = bindings.text("p", { class: "granted" }, () => `✓ ${tr("perm.all.granted")}`);
  bindings.bind(granted, (el) => {
    el.hidden = !state.allSites;
  });
  const error = bindings.text("p", { class: "error", role: "alert" }, () => (state.permError ? tr(state.permError) : ""));
  bindings.bind(error, (el) => {
    el.hidden = state.permError === null;
  });
  const safari = bindings.text("p", { class: "hint" }, () => tr("perm.safari"));
  safari.hidden = !__SAFARI__;
  return section("permissions", "perm.title",
    bindings.text("p", {}, () => tr("perm.intro")),
    h("div", { class: "perm-box" },
      bindings.text("h3", {}, () => tr("perm.all.title")),
      bindings.text("p", { class: "hint" }, () => tr("perm.all.body")),
      h("div", { class: "actions" }, grant, granted), error,
      bindings.text("p", { class: "hint" }, () => tr("perm.all.note")), safari));
}

function siteList(listKey: "alwaysOn" | "hintOff", emptyKey: StringKey, remove: (host: string) => Promise<void>): HTMLElement {
  const list = h("ul", { class: "sites" });
  const rules = (): SiteRule[] => (listKey === "alwaysOn" ? siteRules() : state.prefs.hintOff.map((host) => ({ host, own: true, allowed: true })));
  const rebuild = keyedList(list, () => `${lang()}|${rules().map((rule) => `${rule.host}:${rule.own}:${rule.allowed}`).join(",")}`, () => {
    const entries = rules();
    if (!entries.length) return [h("li", { class: "empty" }, tr(emptyKey))];
    return entries.map((rule) => {
      const row = h("li", { "data-host": rule.host, "data-allowed": String(rule.allowed) }, h("span", { class: "host" }, rule.host));
      if (!rule.allowed) {
        row.append(h("span", { class: "pill warn" }, tr("sites.needs_permission")));
        const allow = h("button", { type: "button", class: "btn secondary small", "data-action": "allow-site", "aria-label": tr("perm.site.btn", { site: rule.host }) }, tr("perm.site.btn", { site: rule.host }));
        allow.addEventListener("click", () => void allowRuleSite(rule.host));
        row.append(allow);
      }
      if (rule.own) {
        const removeButton = h("button", { type: "button", class: "btn quiet small", "aria-label": tr("sites.remove.label", { site: rule.host }) }, tr("sites.remove"));
        removeButton.addEventListener("click", () => {
          void remove(rule.host).then(() => (document.getElementById(`add-${listKey}`) ?? list).focus?.());
        });
        row.append(removeButton);
      } else {
        row.append(h("span", { class: "hint" }, tr("sites.from_meridian")));
      }
      return row;
    });
  });
  bindings.bind(list, rebuild);
  return list;
}

function sitesCard(): HTMLElement {
  const input = h("input", { type: "text", id: "add-alwaysOn", class: "input", inputmode: "url", autocomplete: "off", spellcheck: "false" });
  const inputLabel = bindings.text("label", { for: "add-alwaysOn", class: "row-label" }, () => tr("sites.add.label"));
  const add = button(() => tr("sites.add.btn"), "secondary", () => {
    void addAlwaysOnSite(input.value).then((added) => {
      if (added) input.value = "";
    });
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add.click();
    }
  });
  const error = bindings.text("p", { class: "error", role: "alert" }, () => (state.siteError ? tr(state.siteError) : ""));
  bindings.bind(error, (el) => {
    el.hidden = state.siteError === null;
  });
  return section("sites", "sites.title",
    bindings.text("h3", {}, () => tr("sites.always.title")),
    bindings.text("p", { class: "hint" }, () => tr("sites.always.body")),
    siteList("alwaysOn", "sites.always.empty", removeAlwaysOnSite),
    h("div", { class: "add" }, inputLabel, h("div", { class: "add-row" }, input, add)), error,
    bindings.text("h3", { class: "spaced" }, () => tr("sites.hint.title")),
    siteList("hintOff", "sites.hint.empty", removeHintSite));
}

function updatesCard(): HTMLElement {
  const outdated = (): boolean => state.conn?.extensionOutdated === true
    || (!!state.conn?.latestExtensionVersion && compareVersions(state.conn.latestExtensionVersion, manifestVersion) > 0);
  const message = bindings.text("p", {}, () => {
    if (outdated()) return tr("upd.ready", { latest: state.conn?.latestExtensionVersion ?? "" });
    if (state.conn?.latestExtensionVersion) return tr("upd.uptodate");
    return tr("upd.unknown");
  });
  const reload = button(() => tr("upd.btn"), "primary", () => void call({ t: "extension:reload" }));
  bindings.bind(reload, (el) => {
    el.hidden = !outdated();
  });
  return section("updates", "upd.title", bindings.text("p", { class: "hint" }, () => tr("upd.current", { version: manifestVersion })), message, h("div", { class: "actions" }, reload));
}

function privacyCard(): HTMLElement {
  const keys: StringKey[] = ["privacy.1", "privacy.2", "privacy.3", "privacy.4", "privacy.5", "privacy.6", "privacy.7"];
  return section("privacy", "privacy.title", h("ul", { class: "bullets" }, ...keys.map((key) => h("li", {}, bindings.text("span", {}, () => tr(key))))));
}

function diagnosticsText(): string {
  const d = state.diag;
  if (!d) return "";
  const yes = (value: boolean) => (value ? tr("diag.yes") : tr("diag.no"));
  return [
    `${tr("diag.extension")}: ${d.extensionVersion}`, `${tr("diag.browser")}: ${d.browser}`, `${tr("diag.protocol")}: ${d.protocol}`,
    `${tr("diag.app")}: ${d.appVersion || tr("diag.none")}`, `${tr("diag.status")}: ${d.phase}`, `${tr("diag.port")}: ${d.port ?? tr("diag.none")}`,
    `${tr("diag.ports")}: ${d.candidatePorts.join(", ")}`, `${tr("diag.meridians")}: ${(d.meridianPorts ?? []).join(", ") || tr("diag.none")}`,
    `${tr("diag.checked")}: ${d.checkedAt ?? tr("diag.none")}`,
    `${tr("diag.error")}: ${d.lastError ? `${d.lastError.code} (${d.lastError.at})` : tr("diag.none")}`,
    `${tr("diag.tabs")}: ${d.activeTabs}`, `${tr("diag.allsites")}: ${yes(d.permissions.allSites)}`,
  ].join("\n");
}

function diagnosticsCard(): HTMLElement {
  const pre = bindings.bind(h("pre", { class: "diag", tabindex: "0", "aria-label": "diagnostics" }), (el) => {
    const next = diagnosticsText();
    if (el.textContent !== next) el.textContent = next;
  });
  const copy = button(() => (state.copied ? tr("diag.copied") : tr("diag.copy")), "secondary", () => {
    const text = diagnosticsText();
    const done = () => {
      state.copied = true;
      bindings.refresh();
      setTimeout(() => {
        state.copied = false;
        bindings.refresh();
      }, 1800);
    };
    navigator.clipboard.writeText(text).then(done, () => {
      const area = h("textarea", {}, text);
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
      done();
    });
  });
  const twoListeners = bindings.text("p", { class: "hint diag-ambiguous", role: "note" }, () => tr("diag.ambiguous"));
  bindings.bind(twoListeners, (el) => {
    el.hidden = !ambiguous();
  });
  return section("diagnostics", "diag.title", bindings.text("p", { class: "hint" }, () => tr("diag.note")), pre, twoListeners,
    bindings.text("p", { class: "hint diag-copies" }, () => tr("diag.copies")),
    bindings.text("p", { class: "hint diag-trust" }, () => tr("diag.trust")), h("div", { class: "actions" }, copy));
}

function header(): HTMLElement {
  return h("header", { class: "top" },
    h("div", { class: "brand" }, h("span", { class: "mark" }, svgMark(34)), h("div", {},
      bindings.text("h1", {}, () => tr("app.name")),
      bindings.text("p", { class: "tagline" }, () => tr("app.tagline")))));
}

function build(): void {
  const root = document.getElementById("root");
  if (!root) return;
  bindings.bind(document.documentElement, (el) => {
    el.lang = lang();
  });
  bindings.bind(document.head, () => {
    document.title = tr("app.name");
  });
  const skip = bindings.bind(h("a", { class: "skip", href: "#connection" }), (el) => {
    el.textContent = tr("skip.link");
  });
  root.replaceChildren(skip, header(), h("main", { id: "main" }, noticeBanner(), welcomeCard(), connectionCard(), behaviourCard(), helpCard(), permissionsCard(), sitesCard(), documentsCard(), updatesCard(), privacyCard(), diagnosticsCard()));
}

// ---- boot ---------------------------------------------------------------------------------------------------------

let pollTimer: ReturnType<typeof setTimeout> | undefined;
let countdownTimer: ReturnType<typeof setInterval> | undefined;

function schedulePoll(delay?: number): void {
  clearTimeout(pollTimer);
  const phase = state.conn?.phase;
  const next = delay ?? (phase === "pairing" ? 1500 : phase === "ready" ? 15_000 : 3000);
  pollTimer = setTimeout(async () => {
    if (!document.hidden) {
      if (state.conn?.phase === "pairing") await pollPairing();
      else await loadConnection(false);
    }
    schedulePoll();
  }, next);
}

function readLocation(): void {
  const params = new URLSearchParams(location.search);
  state.welcomeRequested = params.get("welcome") === "1";
  const reason = params.get("reason");
  if (reason === "restricted" || reason === "permission" || reason === "failed") {
    const site = params.get("site");
    const host = site ? normalizeHost(site) : null;
    // `intent=always-on` is what the "Start automatically on this site" switch asks for: the answer also stores the rule
    state.notice = { kind: reason, site: host, always: reason === "permission" && host !== null && params.get("intent") === "always-on" };
    // `popup=1` is the small window the worker opens for exactly that question: show the question and nothing else
    state.popup = params.get("popup") === "1" && reason === "permission" && host !== null;
    document.documentElement.dataset.popup = state.popup ? "1" : "0";
  }
  const anchor = location.hash.replace("#", "");
  if (anchor === "permissions" || anchor === "connection" || anchor === "behaviour" || anchor === "help") {
    requestAnimationFrame(() => {
      const target = document.getElementById(`${anchor}-title`);
      target?.scrollIntoView({ block: "start" });
      target?.focus({ preventScroll: true });
    });
  }
}

async function boot(): Promise<void> {
  readLocation();
  build();
  await refreshAll(true); // opening the page is a good moment for a fresh look (version, port, proof)
  bindings.refresh();
  schedulePoll();
  countdownTimer = setInterval(() => {
    if (state.conn?.phase === "pairing") bindings.refresh();
  }, 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refreshAll();
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && (KEYS.prefs in changes || KEYS.connection in changes)) void loadPrefs();
  });
  chrome.permissions.onAdded?.addListener(() => void loadDiagnostics());
  chrome.permissions.onRemoved?.addListener(() => void loadDiagnostics());
  window.addEventListener("pagehide", () => {
    clearTimeout(pollTimer);
    clearInterval(countdownTimer);
  });
}

void boot();
