// Optional host permissions (docs/EXTENSION_DESIGN.md 7.1, 7.6). The manifest ships a curated list of ATS hosts as
// static permissions; everything else is `optional_host_permissions`, requested from a user gesture on an extension page
// (the options page). The worker only checks and re-checks: `permissions.request` from a worker has no gesture.

import { originsForHost } from "./hosts";

export const ALL_SITES_ORIGINS = ["https://*/*", "http://*/*"];

export interface PermissionSnapshot { allSites: boolean; origins: string[] }

export async function hasAllSites(): Promise<boolean> {
  try {
    return await chrome.permissions.contains({ origins: ALL_SITES_ORIGINS });
  } catch {
    return false;
  }
}

/** Must be called from a click handler on an extension page. Resolves false when the user declines. */
export async function requestAllSites(): Promise<boolean> {
  try {
    return await chrome.permissions.request({ origins: ALL_SITES_ORIGINS });
  } catch {
    return false;
  }
}

export async function hasHost(host: string, secure = true): Promise<boolean> {
  try {
    if (await hasAllSites()) return true;
    return await chrome.permissions.contains({ origins: originsForHost(host, secure) });
  } catch {
    return false;
  }
}

/** Must be called from a click handler on an extension page. */
export async function requestHost(host: string, secure = true): Promise<boolean> {
  try {
    return await chrome.permissions.request({ origins: originsForHost(host, secure) });
  } catch {
    return false;
  }
}

export async function removeHost(host: string): Promise<void> {
  try {
    await chrome.permissions.remove({ origins: originsForHost(host, false) });
  } catch {
    // static manifest permissions cannot be removed; that is fine
  }
}

export async function permissionSnapshot(): Promise<PermissionSnapshot> {
  try {
    const all = await chrome.permissions.getAll();
    const origins = (all.origins ?? []).slice(0, 80);
    return { allSites: await hasAllSites(), origins };
  } catch {
    return { allSites: false, origins: [] };
  }
}
