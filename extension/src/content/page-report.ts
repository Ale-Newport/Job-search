// PageReport of the TOP frame (what the bridge needs to identify the role and research the employer).
// Employer-authored text only: never form values, never full HTML. The description reuses the observer's own
// employer sections (same selectors and removal rules as observation.js `employerText`), with a conservative
// fallback for pages that mark the posting up differently.

import type { ObservationSnapshot } from "meridian-observation";
import type { PageLink, PageReport } from "../shared/protocol";
import { cut, inUiHost, isRendered, tidy } from "./util";

const MAX_LINKS = 80;
const MAX_JSONLD = 8;
const MAX_JSONLD_CHARS = 200_000;
/** All the JSON-LD of a page together: eight scripts of the per-script maximum would not even fit the bridge's request limit (1 MiB). */
const MAX_JSONLD_TOTAL = 400_000;
const MAX_DESCRIPTION = 24_000;

/** Same removal list as observation.js `employerText`: widgets, answers and alerts never count as employer text. */
/** Rich-text editors (contenteditable in any spelling, role=textbox): what is typed in them is an answer, never employer text. */
const EDITORS = '[contenteditable]:not([contenteditable="false"]),[role="textbox"]';
const REMOVE = `form,input,textarea,select,button,script,style,nav,footer,${EDITORS},[role="combobox"],` +
  '[role="listbox"],.select__container,.select2-container,[data-field-path],.ashby-application-form-field-entry,' +
  '.application-question,.application-field,[data-applicant-answer],[data-candidate-answer],[role="alert"],' +
  ".field-error,.error-message,[data-meridian-assistant]";

const META_KEYS = [
  "og:site_name", "og:title", "og:description", "og:url", "og:type", "twitter:title", "twitter:description",
  "description", "application-name",
];

const GENERIC_NAME = /^(?:logo|home|homepage|careers?|jobs?|menu|brand|company|company logo|site logo|inicio)$/i;
const LOGO_SUFFIX = /\s*[-–|·]?\s*(?:logo|careers?|jobs?|home(?:page)?|inicio)\s*$/i;

function plainText(node: Element): string {
  const copy = node.cloneNode(true) as Element;
  copy.querySelectorAll(REMOVE).forEach((n) => n.remove());
  return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
}

function metaContent(selector: string): string {
  const el = document.querySelector(selector);
  return tidy(el?.getAttribute("content") ?? "");
}

function readMeta(): Record<string, string> {
  const meta: Record<string, string> = {};
  for (const key of META_KEYS) {
    const value = metaContent(`meta[property="${key}"],meta[name="${key}"]`);
    if (value) meta[key] = cut(value, 600);
  }
  return meta;
}

function firstH1(snapshot: ObservationSnapshot | null): string {
  for (const h of document.querySelectorAll("h1")) {
    if (inUiHost(h)) continue;
    const text = tidy(h.textContent ?? "");
    if (text) return cut(text, 400);
  }
  return cut(tidy(snapshot?.job_context?.title ?? ""), 400);
}

function cleanName(value: string): string | null {
  const name = tidy(value).replace(LOGO_SUFFIX, "").trim();
  if (name.length < 2 || name.length > 80 || GENERIC_NAME.test(name)) return null;
  return name;
}

function companyHint(meta: Record<string, string>): string | null {
  const itemprop = document.querySelector('[itemprop="hiringOrganization"] [itemprop="name"],[itemprop="hiringOrganization"]');
  const candidates: string[] = [
    itemprop?.getAttribute("content") ?? "", itemprop?.textContent ?? "", meta["og:site_name"] ?? "",
  ];
  const logo = document.querySelector(
    'header img[alt],[role="banner"] img[alt],a[class*="logo" i] img[alt],[class*="logo" i] img[alt],img[class*="logo" i][alt]',
  );
  candidates.push(logo?.getAttribute("alt") ?? "");
  const named = document.querySelector('.company-name,[class*="company-name" i],[data-company],[class*="employer-name" i]');
  candidates.push(named?.textContent ?? "");
  for (const c of candidates) {
    const name = cleanName(c);
    if (name) return name;
  }
  return null;
}

function readJsonLd(): string[] {
  const out: string[] = [];
  let total = 0;
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    const text = (script.textContent ?? "").trim();
    if (text && text.length <= MAX_JSONLD_CHARS && total + text.length <= MAX_JSONLD_TOTAL) {
      out.push(text);
      total += text.length;
    }
    if (out.length >= MAX_JSONLD) break;
  }
  return out;
}

function descriptionText(snapshot: ObservationSnapshot | null): string {
  const sections = snapshot?.job_context?.sections ?? [];
  let text = sections.map((s) => s.text).filter(Boolean).join("\n\n");
  if (!text) {
    // conservative fallback: a main/article container with NO form controls inside
    for (const node of document.querySelectorAll('[itemprop="description"],main,article,[role="main"]')) {
      if (inUiHost(node) || !isRendered(node)) continue;
      if (node.querySelector(`form,input,textarea,select,${EDITORS}`)) continue;
      const t = plainText(node);
      if (t.length >= 200) {
        text = t;
        break;
      }
    }
  }
  return cut(text, MAX_DESCRIPTION);
}

/**
 * Where the page itself says the job is, from the posting header ONLY: `itemprop=jobLocation`, `data-qa*=location`, `.job__location`
 * (Greenhouse) or a `[class*=location]` element near the job title. Never from a form field, a search/filter control, the
 * navigation or the assistant's own UI; plain text only (textContent), at most 200 characters, and an element that is really a
 * wrapper of the whole posting (longer than that, or holding another location element) is skipped instead of truncated.
 */
const LOCATION_SELECTOR = '[itemprop="jobLocation"],[data-qa*="location" i],.job__location,[class*="location" i]';
const NOT_A_POSTING_PLACE = "form,input,textarea,select,button,[contenteditable],[role=search],search,nav,footer,aside,[role=combobox],[role=listbox],[data-meridian-assistant]";
const LOCATION_LABEL = /^\s*(?:job\s+)?(?:location|locations|ubicaci[oó]n|localizaci[oó]n|lugar(?: de trabajo)?)\s*[:\-–—]\s*/i;
/** The posting header when the page has no h1 (Lever titles are h2s inside .posting-headline). */
const HEADER_SELECTOR = '[class*="posting-headline" i],[class*="posting-header" i],[class*="job-header" i],[class*="job__header" i],[data-automation-id="jobPostingHeader"]';
export const MAX_LOCATION_HINT = 200;

function placeIn(scope: ParentNode, selector: string): string | null {
  const matches = [...scope.querySelectorAll(selector)];
  for (const el of matches) {
    if (inUiHost(el) || !isRendered(el) || el.closest(NOT_A_POSTING_PLACE) || el.querySelector(NOT_A_POSTING_PLACE)) continue;
    if (matches.some((other) => other !== el && el.contains(other))) continue; // a wrapper: its inner element is the place
    const text = tidy((el.textContent ?? "").replace(LOCATION_LABEL, ""));
    if (text.length >= 2 && text.length <= MAX_LOCATION_HINT) return text;
  }
  return null;
}

function locationHint(): string | null {
  // structured markup is authoritative wherever it sits (never inside a form)
  const structured = placeIn(document, '[itemprop="jobLocation"]');
  if (structured) return structured;
  // otherwise only the posting header: the nearest containers of the job title (at most four levels) that hold a location element
  const title = [...document.querySelectorAll("h1")].find((h) => !inUiHost(h) && isRendered(h) && tidy(h.textContent ?? "") !== "");
  const header = title ?? [...document.querySelectorAll(HEADER_SELECTOR)].find((h) => !inUiHost(h) && isRendered(h));
  let scope: Element | null = title ? title.parentElement : header ?? null;
  for (let depth = 0; scope && depth < 4 && !scope.matches("body,html,form,main"); depth++, scope = scope.parentElement) {
    const place = placeIn(scope, LOCATION_SELECTOR);
    if (place) return place;
  }
  return null;
}

const LINK_CONTEXT = "header,nav,footer,[role=banner],[role=navigation],[role=contentinfo]";
const LINK_WORDS = /\b(?:about|careers?|jobs?|company|team|mission|values|culture|who we are|our story|sobre|empleo|carreras|nosotros|qui[eé]nes|equipo|cultura)\b/i;

function readLinks(): PageLink[] {
  const seen = new Set<string>();
  const links: PageLink[] = [];
  for (const a of document.querySelectorAll("a[href]")) {
    if (links.length >= MAX_LINKS) break;
    if (inUiHost(a) || a.closest("form")) continue;
    const anchor = a as HTMLAnchorElement;
    let url: URL;
    try {
      url = new URL(anchor.href, location.href);
    } catch {
      continue;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") continue;
    const text = tidy(anchor.textContent || anchor.getAttribute("aria-label") || anchor.title || "");
    const inChrome = !!anchor.closest(LINK_CONTEXT);
    if (!inChrome && !LINK_WORDS.test(`${text} ${url.pathname}`)) continue;
    const bare = `${url.origin}${url.pathname}${url.search}`;
    if (bare === `${location.origin}${location.pathname}${location.search}`) continue;
    // company/about links need no query string or fragment; dropping them keeps tokens and tracking ids out of the report
    const href = `${url.origin}${url.pathname}`;
    if (href.length > 2000 || seen.has(href)) continue;
    seen.add(href);
    links.push({ href, text: cut(text, 200) });
  }
  return links;
}

export function buildPageReport(snapshot: ObservationSnapshot | null): PageReport {
  const meta = readMeta();
  const canonical = document.querySelector('link[rel="canonical"]')?.getAttribute("href");
  let canonicalUrl: string | null = null;
  if (canonical) {
    try {
      canonicalUrl = new URL(canonical, location.href).href;
    } catch {
      canonicalUrl = null;
    }
  }
  return {
    url: cut(location.href, 4000),
    title: cut(tidy(document.title), 400),
    h1: firstH1(snapshot),
    lang: (document.documentElement.lang || "").slice(0, 16),
    company_hint: companyHint(meta),
    meta,
    canonical: canonicalUrl && canonicalUrl.length <= 4000 ? canonicalUrl : null,
    jsonld: readJsonLd(),
    description_text: descriptionText(snapshot),
    location_hint: locationHint(),
    links: readLinks(),
    user: {},
  };
}

/**
 * A stable identity of "which job is this page" used to decide whether an SPA URL change is a new job (re-open the
 * session) or just the next step of the same application (keep it).
 */
export function jobIdentity(report: Pick<PageReport, "url" | "canonical">): string {
  const raw = report.canonical || report.url;
  try {
    const url = new URL(raw);
    const path = url.pathname.replace(/\/(?:apply|application|apply-now|candidate|candidatura|solicitud)(?:\/.*)?$/i, "").replace(/\/+$/, "");
    return `${url.origin}${path}`.toLowerCase();
  } catch {
    return raw;
  }
}

/** `jobIdentity` of the document as it is right now (canonical link when it has one), without building a whole report. */
export function currentJobIdentity(): string {
  let canonical: string | null = null;
  try {
    const href = document.querySelector('link[rel="canonical"]')?.getAttribute("href");
    if (href) canonical = new URL(href, location.href).href;
  } catch {
    canonical = null;
  }
  return jobIdentity({ url: location.href, canonical });
}
