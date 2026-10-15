// ATS hint (docs/EXTENSION_DESIGN.md 7.2): a tiny script that runs only on the curated applicant-tracking-system hosts.
// When the page shows what looks like an application form it offers a small "Meridian is ready" pill and lights a dot
// on the toolbar icon. It counts controls and nothing else: it never reads a value, a label or any page text, and it
// never touches the form. Clicking the pill asks the worker to start the assistant exactly like a toolbar click.

import type { Lang } from "../shared/i18n-core";
import { svgMark } from "../shared/mark";
import type { BgResponse, HintConfig } from "../shared/messages";

const COPY: Record<Lang, { ready: string; connect: string; hide: string; open: string }> = {
  en: {
    ready: "Meridian is ready — click to assist", connect: "Connect Meridian to assist with this application",
    hide: "Hide this hint on this site", open: "Meridian Assistant",
  },
  es: {
    ready: "Meridian está listo — haz clic para ayudar", connect: "Conecta Meridian para ayudarte con esta candidatura",
    hide: "Ocultar este aviso en este sitio", open: "Asistente de Meridian",
  },
};

const FIELDS =
  'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]):not([type=password]):not([type=search]), textarea, select';
const MAX_SCANNED = 120;
const MIN_FIELDS = 3;
const HOST_ATTRIBUTE = "data-meridian-hint";

const guard = globalThis as { __meridianHint?: boolean };

function isVisible(element: Element): boolean {
  if (!(element instanceof HTMLElement) || element.hidden || element.getClientRects().length === 0) return false;
  const style = getComputedStyle(element);
  return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
}

/** Highest number of visible fields inside one form-like container, with a cheap "does this look like an application" test. */
function countApplicationFields(root: ParentNode = document): number {
  const groups = new Map<Element, { count: number; strong: boolean }>();
  const controls = Array.from(root.querySelectorAll(FIELDS)).slice(0, MAX_SCANNED);
  for (const control of controls) {
    if (!isVisible(control)) continue;
    if (control.getAttribute("role") === "searchbox") continue;
    const container = (control as HTMLInputElement).form
      ?? control.closest('[role="form"], form, main, section, article, [class*="application"], [id*="application"]')
      ?? document.body;
    const group = groups.get(container) ?? { count: 0, strong: false };
    group.count += 1;
    const type = (control.getAttribute("type") ?? "").toLowerCase();
    if (control.tagName === "TEXTAREA" || type === "file" || type === "email" || type === "tel") group.strong = true;
    groups.set(container, group);
  }
  let best = 0;
  for (const group of groups.values()) {
    // Three controls in one container, and either a telltale control (email, phone, file, long text) or plenty of them.
    if (group.count >= MIN_FIELDS && (group.strong || group.count >= 5)) best = Math.max(best, group.count);
  }
  return best;
}

interface Pill { host: HTMLElement; remove: () => void }

function buildPill(lang: Lang, connected: boolean, onActivate: () => void, onHide: () => void): Pill {
  const copy = COPY[lang];
  const host = document.createElement("div");
  if (__E2E__) host.setAttribute(HOST_ATTRIBUTE, ""); // production leaves no marker a page script could use to spot the extension
  host.style.cssText = "all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483646;";
  const root = host.attachShadow({ mode: __SHADOW__ });
  const style = document.createElement("style");
  style.textContent = `
    :host{all:initial}
    .pill{display:flex;align-items:center;gap:2px;background:#173F36;color:#F3F3EA;border:1px solid #A8E4CE;border-radius:999px;
      box-shadow:0 6px 24px rgba(10,30,26,.35);font:600 13px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:min(92vw,420px);
      animation:in .18s ease-out}
    button{all:unset;box-sizing:border-box;cursor:pointer;color:inherit;font:inherit}
    .main{display:flex;align-items:center;gap:8px;padding:9px 6px 9px 12px;border-radius:999px 0 0 999px}
    .close{display:flex;align-items:center;justify-content:center;width:30px;height:30px;margin-right:4px;border-radius:50%;opacity:.8;font-size:16px}
    button:hover{background:rgba(168,228,206,.16)} button:focus-visible{outline:2px solid #A8E4CE;outline-offset:1px}
    svg{flex:none}
    @keyframes in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
    @media (prefers-reduced-motion:reduce){.pill{animation:none}}`;
  const pill = document.createElement("div");
  pill.className = "pill";
  pill.setAttribute("role", "group");
  pill.setAttribute("aria-label", copy.open);
  const main = document.createElement("button");
  main.className = "main";
  main.type = "button";
  main.append(svgMark(18));
  const label = document.createElement("span");
  label.textContent = connected ? copy.ready : copy.connect;
  main.append(label);
  const close = document.createElement("button");
  close.className = "close";
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", copy.hide);
  close.title = copy.hide;
  main.addEventListener("click", (event) => {
    if (event.isTrusted) onActivate();
  });
  close.addEventListener("click", (event) => {
    if (event.isTrusted) onHide();
  });
  pill.append(main, close);
  root.append(style, pill);
  document.documentElement.append(host);
  return { host, remove: () => host.remove() };
}

function send(message: unknown): void {
  try {
    void chrome.runtime.sendMessage(message)?.catch?.(() => undefined);
  } catch {
    // the extension was reloaded or updated: this script is orphaned and simply goes quiet
  }
}

async function run(): Promise<void> {
  if (guard.__meridianHint || window.top !== window) return;
  guard.__meridianHint = true;
  // Content scripts cannot read extension storage (the credential lives there): the worker answers from its own copy.
  let config: HintConfig | null = null;
  try {
    const reply = (await chrome.runtime.sendMessage({ t: "hint:config" })) as BgResponse<HintConfig> | undefined;
    config = reply?.ok ? reply.data : null;
  } catch {
    return;
  }
  if (!config || !config.show) return;
  const { language: lang, connected } = config;

  let pill: Pill | null = null;
  let observer: MutationObserver | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let finished = false;

  const stop = (): void => {
    finished = true;
    observer?.disconnect();
    clearTimeout(timer);
    pill?.remove();
    pill = null;
  };
  const evaluate = (): void => {
    if (finished) return;
    if ((globalThis as { __meridianAssist?: unknown }).__meridianAssist) return stop();
    const fields = countApplicationFields();
    if (fields >= MIN_FIELDS && !pill) {
      pill = buildPill(lang, connected, () => {
        send({ t: "activate-here" });
        stop();
      }, () => {
        send({ t: "hint:hide" });
        stop();
      });
      send({ t: "hint", fields });
    } else if (fields < MIN_FIELDS && pill) {
      pill.remove();
      pill = null;
      send({ t: "hint", fields: 0 });
    }
  };
  const schedule = (): void => {
    if (timer !== undefined || finished) return; // throttle: a busy page must not starve the check
    timer = setTimeout(() => {
      timer = undefined;
      evaluate();
    }, 900);
  };

  try {
    chrome.runtime.onMessage.addListener((message: unknown) => {
      if (message && typeof message === "object" && (message as { t?: unknown }).t === "activate") stop();
      return false;
    });
  } catch {
    return;
  }
  observer = new MutationObserver(schedule);
  observer.observe(document.body ?? document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "style", "class"] });
  evaluate();
  setTimeout(evaluate, 1800);
  setTimeout(evaluate, 5000);
}

run().catch(() => undefined); // an orphaned script (extension reloaded) simply goes quiet
