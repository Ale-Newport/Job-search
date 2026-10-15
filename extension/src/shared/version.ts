/** Numeric dotted-version comparison (`1.2.10` > `1.2.9`); non-numeric parts compare as 0. Returns -1, 0 or 1. */
export function compareVersions(a: string, b: string): number {
  const parse = (text: string) => String(text || "0").split(/[.-]/).map((part) => Number.parseInt(part, 10) || 0);
  const left = parse(a);
  const right = parse(b);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const diff = (left[i] ?? 0) - (right[i] ?? 0);
    if (diff) return diff < 0 ? -1 : 1;
  }
  return 0;
}

/**
 * Is the extension that Meridian bundles newer than the one running? A newer version number says so. A rebuild that kept the
 * version number is told apart by the digest of its tree: when BOTH the app and this build carry one and they differ, the loaded
 * copy is not the bundled one (an older build with the same number, or a copy that was edited), so the app's copy is offered.
 * An unknown latest version ("") or a missing digest on either side is never "outdated" (nothing to compare).
 */
export function isOutdated(latestVersion: string, latestDigest: string, ownVersion: string, ownDigest: string): boolean {
  if (latestVersion === "") return false;
  const order = compareVersions(latestVersion, ownVersion);
  if (order !== 0) return order > 0;
  return latestDigest !== "" && ownDigest !== "" && latestDigest !== ownDigest;
}
