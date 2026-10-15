import assert from "node:assert/strict";
import { test } from "node:test";
import { hostMatchesRules, hostOf, isRestrictedUrl, matchesAnyPattern, matchesPattern, normalizeHost, originOf, originsForHost } from "../../src/shared/hosts.ts";
import { compareVersions } from "../../src/shared/version.ts";

test("normalizeHost accepts hostnames and URLs, refuses everything dangerous", () => {
  assert.equal(normalizeHost("jobs.acme.com"), "jobs.acme.com");
  assert.equal(normalizeHost("  Jobs.Acme.COM  "), "jobs.acme.com");
  assert.equal(normalizeHost("https://jobs.acme.com/apply?x=1#y"), "jobs.acme.com");
  assert.equal(normalizeHost("jobs.acme.com:8443/path"), "jobs.acme.com");
  assert.equal(normalizeHost("localhost"), "localhost");
  assert.equal(normalizeHost("127.0.0.1"), "127.0.0.1");
  for (const bad of ["", "   ", "acme", "*.acme.com", "https://user:pw@acme.com", "ftp://acme.com", "chrome://extensions", "javascript:alert(1)",
    "a b.com", "-bad.com", "acme..com", "x".repeat(300) + ".com", null, undefined, 42, "%00.com"]) {
    assert.equal(normalizeHost(bad), null, JSON.stringify(bad));
  }
});

test("hostMatchesRules: the rule itself and its subdomains, never lookalikes", () => {
  assert.equal(hostMatchesRules("jobs.acme.com", ["acme.com"]), true);
  assert.equal(hostMatchesRules("acme.com", ["acme.com"]), true);
  assert.equal(hostMatchesRules("notacme.com", ["acme.com"]), false);
  assert.equal(hostMatchesRules("acme.com.evil.org", ["acme.com"]), false);
  assert.equal(hostMatchesRules(null, ["acme.com"]), false);
  assert.equal(hostMatchesRules("a.com", []), false);
});

test("match patterns behave like the browser's", () => {
  const gh = "https://*.greenhouse.io/*";
  assert.equal(matchesPattern(gh, "https://boards.greenhouse.io/acme/jobs/1"), true);
  assert.equal(matchesPattern(gh, "https://greenhouse.io/"), true, "the bare domain matches *.domain");
  assert.equal(matchesPattern(gh, "http://boards.greenhouse.io/"), false, "scheme is respected");
  assert.equal(matchesPattern(gh, "https://greenhouse.io.evil.com/"), false);
  assert.equal(matchesPattern(gh, "https://evilgreenhouse.io/"), false);
  assert.equal(matchesPattern("https://ats.rippling.com/*", "https://ats.rippling.com/x"), true);
  assert.equal(matchesPattern("https://ats.rippling.com/*", "https://x.rippling.com/x"), false);
  assert.equal(matchesPattern("*://example.com/*", "http://example.com/a"), true);
  assert.equal(matchesPattern("*://example.com/*", "ftp://example.com/a"), false);
  assert.equal(matchesPattern(gh, "not a url"), false);
  assert.equal(matchesAnyPattern([gh], undefined), false);
});

test("restricted pages are the ones no extension may script", () => {
  for (const url of [undefined, "", "chrome://extensions", "chrome-extension://abc/page.html", "about:blank", "file:///tmp/a.html", "view-source:https://x.com",
    "https://chromewebstore.google.com/detail/x", "https://chrome.google.com/webstore/detail/x", "https://addons.mozilla.org/en/firefox/", "https://x.com/cv.PDF", "not a url"]) {
    assert.equal(isRestrictedUrl(url), true, String(url));
  }
  for (const url of ["https://boards.greenhouse.io/a/jobs/1", "http://127.0.0.1:8000/form.html", "https://chrome.google.com/other"]) {
    assert.equal(isRestrictedUrl(url), false, url);
  }
});

test("url helpers", () => {
  assert.equal(hostOf("https://Jobs.Acme.com./x"), "jobs.acme.com");
  assert.equal(hostOf("chrome://x"), null);
  assert.equal(originOf("https://a.com:8443/x?y"), "https://a.com:8443");
  assert.equal(originOf("file:///x"), null);
  assert.deepEqual(originsForHost("a.com"), ["https://a.com/*"]);
  assert.deepEqual(originsForHost("a.com", false), ["https://a.com/*", "http://a.com/*"]);
});

test("version comparison is numeric, not lexical", () => {
  assert.equal(compareVersions("0.1.10", "0.1.9"), 1);
  assert.equal(compareVersions("0.1.0", "0.1"), 0);
  assert.equal(compareVersions("0.2", "0.10"), -1);
  assert.equal(compareVersions("1.0.0", "1.0.0"), 0);
  assert.equal(compareVersions("", "0.0.1"), -1);
});
