// What the options page needs to know about the keyboard it is shown on. Pure; no chrome.* calls.

/** Is this browser running on macOS? Key names differ: Option and Control + Option there, Alt and Alt + Shift elsewhere. */
export function isMacPlatform(): boolean {
  try {
    const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
    const platform = nav.userAgentData?.platform || nav.platform || nav.userAgent || "";
    return /mac|iphone|ipad|ipod/i.test(platform);
  } catch {
    return false;
  }
}

/** The browser's default shortcut for the toolbar button (manifest commands._execute_action), as it is written on this platform. */
export function defaultStartShortcut(mac: boolean): string {
  return mac ? "⌘⇧Y" : "Ctrl+Shift+Y";
}

/**
 * The "start or stop" line of the key list. Chrome reports the shortcut the person really has (they may have changed it); with
 * none set the line says what the default is and where to set one; Safari has no way to give an extension a shortcut at all, and
 * says so instead of listing a key that does not exist.
 */
export interface StartKeyRow {
  /** `shortcut`: print the shortcut the browser reported; `icon`: there is none to print, name the toolbar icon */
  key: "shortcut" | "icon";
  text: "help.d.start" | "help.d.start.unset" | "help.d.start.safari";
}

export function startKeyRow(safari: boolean, shortcut: string): StartKeyRow {
  if (safari) return { key: "icon", text: "help.d.start.safari" };
  return shortcut !== "" ? { key: "shortcut", text: "help.d.start" } : { key: "icon", text: "help.d.start.unset" };
}
