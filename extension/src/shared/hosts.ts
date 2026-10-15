// Host and URL helpers shared by the worker, the options page and the hint script. Pure functions, no chrome.* calls.

/** Hostname of an http(s) URL, lower case, without a trailing dot; null for anything else. */
export function hostOf(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    return parsed.hostname.toLowerCase().replace(/\.$/, "") || null;
  } catch {
    return null;
  }
}

export function originOf(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.origin : null;
  } catch {
    return null;
  }
}

/**
 * A user-typed or pasted site (`jobs.acme.com`, `https://jobs.acme.com/apply?x=1`, `Jobs.Acme.com:8443`) -> bare hostname.
 * Wildcards, credentials, single labels (except localhost) and anything that is not a DNS name or IP are refused.
 */
export function normalizeHost(input: unknown): string | null {
  if (typeof input !== "string") return null;
  let text = input.trim();
  if (!text || text.length > 253 + 16) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = `https://${text}`;
  let parsed: URL;
  try {
    parsed = new URL(text);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (!host || host.length > 253) return null;
  if (host.includes("*") || host.includes("%")) return null;
  const ipv4 = /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
  const dns = /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(host);
  const punycodeOk = /^[a-z0-9.-]+$/.test(host);
  if (host !== "localhost" && !ipv4 && !(dns && punycodeOk)) return null;
  return host;
}

/** `host` equals a rule or is a subdomain of it (rules are bare hostnames: `acme.com` covers `jobs.acme.com`). */
export function hostMatchesRules(host: string | null, rules: readonly string[]): boolean {
  if (!host) return false;
  return rules.some((rule) => host === rule || host.endsWith(`.${rule}`));
}

/** Chrome match pattern (`https://*.greenhouse.io/*`) against a URL. Only the forms used by ats-hosts.json are supported. */
export function matchesPattern(pattern: string, url: string): boolean {
  const parsedPattern = /^(\*|https?):\/\/([^/]+)(\/.*)$/.exec(pattern);
  if (!parsedPattern) return false;
  let target: URL;
  try {
    target = new URL(url);
  } catch {
    return false;
  }
  const [, scheme, hostPattern] = parsedPattern;
  const targetScheme = target.protocol.replace(":", "");
  if (targetScheme !== "http" && targetScheme !== "https") return false;
  if (scheme !== "*" && scheme !== targetScheme) return false;
  const host = target.hostname.toLowerCase();
  if (hostPattern === "*") return true;
  if (hostPattern.startsWith("*.")) {
    const base = hostPattern.slice(2);
    if (host !== base && !host.endsWith(`.${base}`)) return false;
  } else if (host !== hostPattern) {
    return false;
  }
  return true;
}

export function matchesAnyPattern(patterns: readonly string[], url: string | undefined | null): boolean {
  if (!url) return false;
  return patterns.some((pattern) => matchesPattern(pattern, url));
}

/** Pages an extension can never script: browser UI, other extensions, store pages, view-source, PDFs and file: pages. */
export function isRestrictedUrl(url: string | undefined | null): boolean {
  if (!url) return true;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return true;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return true;
  const host = parsed.hostname.toLowerCase();
  if (host === "chrome.google.com" && parsed.pathname.startsWith("/webstore")) return true;
  if (host === "chromewebstore.google.com") return true;
  if (host === "microsoftedge.microsoft.com" && parsed.pathname.startsWith("/addons")) return true;
  if (host === "addons.mozilla.org") return true;
  if (/\.pdf$/i.test(parsed.pathname)) return true;
  return false;
}

/** Match patterns granting access to one site (both schemes only when the page itself is plain http). */
export function originsForHost(host: string, secure = true): string[] {
  return secure ? [`https://${host}/*`] : [`https://${host}/*`, `http://${host}/*`];
}
