// Page-side half of the preview rig: mounts the real createUI() on a standalone page and exposes `window.rig`
// to the Playwright driver (run.py). The driver does the screenshots, real keyboard/mouse input and assertions.

import { dictionaries, enterKeyName, formatClock, formatMoney, formatNumber, formatRelative, getLocale, resolveLocale, setLocale, t, tn, type Locale } from "../../src/content/i18n";
import { createUI } from "../../src/content/ui/create";
import { chipKeyNumber } from "../../src/content/ui/ctx";
import type { AnnounceView, AssistUI, ChipView, FieldMark, PanelView, SaveAskView, ToastView, TrackAskView, UIAction } from "../../src/content/ui-api";
import { resetPrefsForTests, sanitize as sanitizePrefs } from "../../src/content/ui/prefs";
import { auditRoot, freePointInChip, type AuditResult } from "./checks";
import { SCENES, chipView, panelView, researchView, type Anchor, type Scene } from "./scenarios";

type Lang = "en" | "es";

const ui = createUI();
const actions: UIAction[] = [];
let dataStrings: string[] = [];
ui.onAction((action) => { actions.push(action); });

function collect(value: unknown): void {
  if (typeof value === "string") dataStrings.push(value);
  else if (Array.isArray(value)) value.forEach(collect);
  else if (value && typeof value === "object") Object.values(value).forEach(collect);
}

function recorder(real: AssistUI): AssistUI {
  return {
    mount: (o) => real.mount(o),
    unmount: () => real.unmount(),
    setPanel: (v: PanelView) => { collect(v); real.setPanel(v); },
    showChip: (v: ChipView) => { collect(v); real.showChip(v); },
    updateChipAnchor: (a) => real.updateChipAnchor(a),
    hideChip: () => real.hideChip(),
    flash: (a, tone) => real.flash(a, tone),
    setMarks: (marks: FieldMark[]) => { collect(marks); real.setMarks?.(marks); },
    toast: (v: ToastView) => { collect(v); real.toast(v); },
    announce: (v: AnnounceView) => { collect(v); real.announce?.(v); },
    askSave: (v: SaveAskView | null) => { collect(v); real.askSave(v); },
    askTrack: (v: TrackAskView | null) => { collect(v); real.askTrack(v); },
    onAction: (h) => real.onAction(h),
    setLanguage: (l) => real.setLanguage(l),
    focusPanel: () => real.focusPanel(),
  };
}

function fieldAnchor(id: string | undefined): Anchor {
  const el = id ? document.getElementById(id) : null;
  if (!el) return { x: 24, y: 80, w: 220, h: 34 };
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}

function root(): ShadowRoot {
  const r = ui.root;
  if (!r) throw new Error("the e2e/open bundle is required for DOM audits");
  return r;
}

function find(id: string): Scene {
  const scene = SCENES.find((s) => s.id === id);
  if (!scene) throw new Error(`unknown scene ${id}`);
  return scene;
}

function words(text: string): string[] {
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

function placeholdersOf(text: string): string {
  return [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
}

function i18nSelfTest(): string[] {
  const failures: string[] = [];
  const en = dictionaries.en;
  const es = dictionaries.es;
  const enKeys = Object.keys(en.strings).sort();
  const esKeys = Object.keys(es.strings).sort();
  if (enKeys.join("|") !== esKeys.join("|")) failures.push("string keys differ between en and es");
  for (const key of enKeys) {
    const a = (en.strings as Record<string, string>)[key];
    const b = (es.strings as Record<string, string>)[key];
    if (!a || !a.trim() || a !== a.trim()) failures.push(`en ${key} is empty or untrimmed`);
    if (!b || !b.trim() || b !== b.trim()) failures.push(`es ${key} is empty or untrimmed`);
    if (placeholdersOf(a) !== placeholdersOf(b)) failures.push(`${key}: placeholders differ (${placeholdersOf(a)} vs ${placeholdersOf(b)})`);
    if (b === a && /[a-z]{4,}/i.test(a) && !/^\{|^[+\d]/.test(a) && !["No"].includes(a)) failures.push(`${key}: es equals en ("${a}") - untranslated?`);
  }
  // Spanish register and glossary: informal tú, candidatura / oferta / borrador / campo / sugerencia / asistente, no English words
  // (the desktop app's own labels, "Settings → AI" and "Settings → Browser assistant", are quoted as they appear there)
  const banned: [RegExp, string][] = [
    [/\b(aplicaci[oó]n|aplicar|aplica)\b/i, "aplicación/aplica: say candidatura/corresponde"],
    [/\bmarco\b/i, "marco: literal 'frame'"],
    [/\b(usted|ustedes|vosotros|vuestro)\b/i, "informal tú only"],
    [/\b(job|offer|draft|field|assistant|suggestion|application|insert|settings|submit)s?\b/i, "English word in Spanish copy"],
    [/["“”]/, "straight or curly English quotes: use «»"],
    [/\b(web)s\b/i, "'webs': say sitios web"],
    [/\bmodelo de escritura\b/i, "calque of 'writing model': say modelo de IA"],
    [/\b(hablar con|hablarte)\b/i, "calque of 'talk to': say comunicarme con"],
    [/\b(Intro|Retorno|Enter)\b/, "name the Enter key with {enter}: a Mac says Retorno, the rest Intro"],
  ];
  const strings = [...Object.entries(es.strings), ...Object.entries(es.plurals).flatMap(([k, p]) => [[`${k}.one`, p.one], [`${k}.other`, p.other]] as [string, string][])];
  for (const [key, text] of strings) {
    const plain = text.replace(/Settings → (?:AI|Browser assistant)/g, "").replace(/\{\w+\}/g, "");
    for (const [pattern, why] of banned) if (pattern.test(plain)) failures.push(`es ${key}: ${why} ("${text}")`);
  }
  for (const [key, text] of Object.entries(en.strings)) {
    if (/\bEnter\b/.test(text)) failures.push(`en ${key}: name the Enter key with {enter} ("${text}")`);
  }
  for (const key of Object.keys(en.plurals)) {
    const a = (en.plurals as Record<string, { one: string; other: string }>)[key];
    const b = (es.plurals as Record<string, { one: string; other: string }>)[key];
    if (!b) { failures.push(`plural ${key} missing in es`); continue; }
    for (const form of ["one", "other"] as const) {
      if (!a[form].includes("{n}") || !b[form].includes("{n}")) failures.push(`plural ${key}.${form} lacks {n}`);
    }
  }
  const before = getLocale();
  // the Enter key in running text: a Spanish Mac says Retorno, a Spanish PC Intro, English always Enter
  if (enterKeyName("es", true) !== "Retorno" || enterKeyName("es", false) !== "Intro") failures.push("es Enter key names wrong");
  if (enterKeyName("en", true) !== "Enter" || enterKeyName("en", false) !== "Enter") failures.push("en Enter key name wrong");
  setLocale("en");
  if (tn("chip.words", 1) !== "1 word" || tn("chip.words", 2) !== "2 words") failures.push("en plural words wrong");
  if (formatMoney(1.2) !== "$1.20") failures.push(`en money ${formatMoney(1.2)}`);
  if (formatClock(125) !== "2:05") failures.push(`clock ${formatClock(125)}`);
  if (formatRelative(-120_000) !== "2 minutes ago") failures.push(`en relative ${formatRelative(-120_000)}`);
  if (formatNumber(12345.5) !== "12,345.5") failures.push(`en number ${formatNumber(12345.5)}`);
  setLocale("es");
  if (tn("chip.words", 1) !== "1 palabra" || tn("chip.words", 2) !== "2 palabras") failures.push("es plural words wrong");
  if (!/^1,20\s?US\$$/.test(formatMoney(1.2).replace(/ /g, " "))) failures.push(`es money ${formatMoney(1.2)}`);
  if (formatRelative(-120_000) !== "hace 2 minutos") failures.push(`es relative ${formatRelative(-120_000)}`);
  if (!/^12\.?345,5$/.test(formatNumber(12345.5))) failures.push(`es number ${formatNumber(12345.5)}`);
  if (t("chip.trust.verified") !== "Verificado") failures.push("es lookup wrong");
  const cases: [string | undefined, string | undefined, Locale][] = [
    ["auto", "es-MX", "es"], ["auto", "es", "es"], ["auto", "en-GB", "en"], ["auto", "fr-FR", "en"], ["en", "es-ES", "en"], ["es", "en-US", "es"], [undefined, "ES-es", "es"],
  ];
  for (const [language, nav, expected] of cases) {
    const got = resolveLocale(language as never, nav);
    if (got !== expected) failures.push(`resolveLocale(${language}, ${nav}) = ${got}, expected ${expected}`);
  }
  setLocale(before);
  return failures;
}

function tabbables(): string[] {
  const r = root();
  const list: string[] = [];
  let index = 0;
  for (const el of r.querySelectorAll<HTMLElement>("button, a[href], input, select, textarea, [tabindex]")) {
    el.removeAttribute("data-rig-idx");
    const hidden = getComputedStyle(el).display === "none" || el.closest(".is-hidden") !== null || (el as HTMLButtonElement).disabled;
    if (hidden || el.tabIndex < 0) continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) continue;
    el.setAttribute("data-rig-idx", String(index));
    list.push(`${index}:${el.getAttribute("data-testid") ?? el.tagName.toLowerCase()}`);
    index += 1;
  }
  return list;
}

function focusedInfo(): { inHost: boolean; idx: string | null; testid: string | null; outline: string | null; outlineColor: string | null; outlineOffset: string | null } {
  const host = document.querySelector("[data-meridian-assistant]");
  const inHost = !!host && document.activeElement === host;
  const inner = ui.root?.activeElement ?? null;
  if (!inHost || !inner) return { inHost, idx: null, testid: null, outline: null, outlineColor: null, outlineOffset: null };
  const cs = getComputedStyle(inner);
  return { inHost, idx: inner.getAttribute("data-rig-idx"), testid: inner.getAttribute("data-testid"), outline: `${cs.outlineStyle} ${cs.outlineWidth}`, outlineColor: cs.outlineColor, outlineOffset: cs.outlineOffset };
}

/** The assistant's host element wherever it is: in the document, or inside a modal dialog of a page component's shadow root. */
function findHost(scope: Document | ShadowRoot): Element | null {
  const direct = scope.querySelector("[data-meridian-assistant]");
  if (direct) return direct;
  for (const el of scope.querySelectorAll("*")) {
    if (el.shadowRoot && !el.hasAttribute("data-meridian-assistant")) {
      const nested = findHost(el.shadowRoot);
      if (nested) return nested;
    }
  }
  return null;
}

function layoutSnapshot(): string {
  const parts: string[] = [`${document.documentElement.scrollWidth}x${document.documentElement.scrollHeight}`];
  for (const el of document.querySelectorAll("body > :not([data-meridian-assistant]):not(script)")) {
    const r = el.getBoundingClientRect();
    parts.push(`${el.tagName}#${el.id}:${r.x.toFixed(1)},${r.y.toFixed(1)},${r.width.toFixed(1)},${r.height.toFixed(1)}`);
  }
  return parts.join("|");
}

function perf(): { avgMs: number; maxMs: number } {
  ui.unmount();
  ui.mount({ panel: false });
  ui.setLanguage("en");
  const anchor = fieldAnchor("fx-top");
  const samples: number[] = [];
  for (let i = 0; i < 120; i++) {
    const long = i % 3 === 0;
    const view = chipView(anchor, {
      key: `f0:${i}`, value: long ? "A longer value that has to wrap over a couple of lines, ".repeat(6) : `value ${i}`, state: long ? "GENERATED_DRAFT" : "VERIFIED",
      tone: long ? "draft" : "verified", canRegenerate: long,
    });
    const start = performance.now();
    ui.showChip(view);
    samples.push(performance.now() - start);
  }
  ui.unmount();
  return { avgMs: samples.reduce((a, b) => a + b, 0) / samples.length, maxMs: Math.max(...samples) };
}

const rig = {
  ui,
  actions,
  scenes: () => SCENES.map(({ id, group, panel, responsive, zoom, clicks, typing, noShot, fullShot, expect }) => ({ id, group, panel: !!panel, responsive: !!responsive, zoom: !!zoom, clicks: clicks ?? [], typing: typing ?? null, noShot: !!noShot, fullShot: !!fullShot, expect: expect ?? null })),
  run(id: string, options: { lang: Lang }): { anchor: Anchor } {
    const scene = find(id);
    ui.unmount();
    resetPrefsForTests();
    actions.length = 0;
    dataStrings = [];
    (window as unknown as { __pwned?: number }).__pwned = undefined;
    ui.mount({ panel: !!scene.panel });
    ui.setLanguage(options.lang);
    const anchor = fieldAnchor(scene.field);
    scene.setup({ ui: recorder(ui), anchor });
    return { anchor };
  },
  audit(id: string, lang: Lang): AuditResult {
    return auditRoot(root(), find(id).expect, lang);
  },
  dataStrings: () => [...new Set(dataStrings)],
  vocabulary(lang: Lang): string[] {
    const d = dictionaries[lang];
    // `{enter}` is filled in from the platform: both names are this language's own words
    const all = [...Object.values(d.strings), ...Object.values(d.plurals).flatMap((p) => [p.one, p.other]), enterKeyName(lang, true), enterKeyName(lang, false)];
    return [...new Set(all.flatMap(words))];
  },
  words,
  enterKeyName,
  chipKeyNumber,
  i18nSelfTest,
  tabbables,
  focusedInfo,
  layoutSnapshot,
  perf,
  make: { chipView, panelView, researchView },
  /** Every string of both dictionaries (plural forms flattened), to look for wording that must not exist anywhere. */
  allStrings(): Record<Lang, Record<string, string>> {
    const flat = (d: (typeof dictionaries)[Lang]): Record<string, string> => ({
      ...d.strings,
      ...Object.fromEntries(Object.entries(d.plurals).flatMap(([key, forms]) => [[`${key}.one`, forms.one], [`${key}.other`, forms.other]])),
    });
    return { en: flat(dictionaries.en), es: flat(dictionaries.es) };
  },
  anchorOf: fieldAnchor,
  pwned: () => (window as unknown as { __pwned?: number }).__pwned ?? null,
  hostInfo(): { present: boolean; shadowRootExposed: boolean; parent: string; popoverOpen: boolean; rootExposed: boolean } {
    const host = findHost(document);
    return {
      present: !!host, shadowRootExposed: !!host?.shadowRoot, parent: host?.parentElement?.tagName.toLowerCase() ?? "",
      popoverOpen: !!host && (() => { try { return host.matches(":popover-open"); } catch { return false; } })(), rootExposed: !!ui.root,
    };
  },
  prefs: { sanitize: sanitizePrefs },
  /** A pixel of the chip that is not one of its controls (null when the chip is nothing but controls). */
  freePoint: () => freePointInChip(root()),
  controlCenter(testid: string): { x: number; y: number } | null {
    const el = root().querySelector(`[data-testid="${testid}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  },
  hitTest(x: number, y: number): string {
    let el = document.elementFromPoint(x, y);
    // look through the page components' open shadow roots (never into the assistant's own)
    for (let depth = 0; el?.shadowRoot && !el.hasAttribute("data-meridian-assistant") && depth < 8; depth++) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el?.hasAttribute("data-meridian-assistant") ? "host" : el?.tagName.toLowerCase() ?? "none";
  },
  resetActions: () => { actions.length = 0; },
};

export type Rig = typeof rig;
(window as unknown as { rig: Rig }).rig = rig;
