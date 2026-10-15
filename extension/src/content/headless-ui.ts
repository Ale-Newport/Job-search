// A UI that draws nothing and records every call. Dev/test fallback for the visual layer (workstream D2): the content-only
// rig selects it with `window.__MERIDIAN_HEADLESS_UI__ = true` in E2E builds, so logic can be asserted on the exact view
// models it hands to the UI. Never reachable in production builds (the only reference is behind `__E2E__`).

import type { Language } from "../shared/protocol";
import type { AssistUI, UIAction } from "./ui-api";

export interface UiCall { n: number; at: number; name: string; arg: unknown }

const MAX_CALLS = 4000;

function plain(value: unknown): unknown {
  if (value === undefined) return null;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
}

export function createHeadlessUI(): AssistUI {
  const handlers = new Set<(action: UIAction) => void>();
  const g = globalThis as unknown as Record<string, unknown>;
  const log: UiCall[] = (g.__meridianUiLog as UiCall[] | undefined) ?? [];
  g.__meridianUiLog = log;
  let counter = log.length;
  const record = (name: string, arg?: unknown): void => {
    log.push({ n: ++counter, at: Date.now(), name, arg: plain(arg) });
    if (log.length > MAX_CALLS) log.splice(0, log.length - MAX_CALLS);
  };
  // lets a test play the part of the user clicking a chip / panel button
  g.__meridianUiEmit = (action: UIAction): void => {
    for (const handler of [...handlers]) handler(action);
  };
  // Test switches (E2E builds only): a UI without the confirmation row of "Fill verified" (the one-click fallback), a UI whose chip has numbered
  // buttons (`__meridianChipActions` = the numbers it shows), a UI that keeps the "first-use" note itself (`dismissNote`).
  const withoutPreview = g.__MERIDIAN_NO_FILL_PREVIEW__ === true;
  const ui: AssistUI = {
    capabilities: { fillPreview: !withoutPreview, chipActions: true },
    mount: (options) => record("mount", options),
    unmount: () => record("unmount"),
    setPanel: (view) => record("setPanel", view),
    showChip: (view) => record("showChip", view),
    updateChipAnchor: (anchor) => record("updateChipAnchor", anchor),
    hideChip: () => record("hideChip"),
    flash: (anchor, tone) => record("flash", { anchor, tone }),
    setMarks: (marks) => record("setMarks", marks),
    toast: (view) => record("toast", view),
    askSave: (view) => record("askSave", view),
    askTrack: (view) => record("askTrack", view),
    onAction(handler) {
      handlers.add(handler);
      return () => { handlers.delete(handler); };
    },
    setLanguage: (language: Language) => record("setLanguage", language),
    focusPanel() {
      record("focusPanel");
      return false;
    },
    chipAction(n: number) {
      record("chipAction", n);
      const shown = g.__meridianChipActions;
      return Array.isArray(shown) && shown.includes(n);
    },
  };
  if (g.__MERIDIAN_DISMISS_NOTE__ === true) ui.dismissNote = (kind) => record("dismissNote", kind);
  return ui;
}
