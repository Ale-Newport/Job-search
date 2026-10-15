// Content script entry. Idempotent (`globalThis.__meridianAssist`): the worker may inject this file again on every
// toolbar click. Nothing happens until the worker sends `activate`; every runtime call is orphan-safe (util.sendBg).
// Top frame and child frames run the same code; the controller decides its role from the worker's `ctx` answer.

import type { ToContent } from "../shared/messages";
import { Controller } from "./controller";
import { createHeadlessUI } from "./headless-ui";
import { createUI } from "./ui/create";
import { addRuntimeListener } from "./util";

const TYPES = new Set(["activate", "deactivate", "conn:changed", "frame"]);

(() => {
  const g = globalThis as unknown as Record<string, unknown>;
  if (g.__meridianAssist) return;
  // The recording UI exists only in E2E builds (`__E2E__` is a compile-time false in production, so it is stripped).
  const makeUI = () => (__E2E__ && g.__MERIDIAN_HEADLESS_UI__ === true ? createHeadlessUI() : createUI());
  const controller = new Controller(makeUI);
  const api: Record<string, unknown> = { version: 1 };
  if (__E2E__) api.debug = () => (controller as unknown as { debugState: () => unknown }).debugState();
  g.__meridianAssist = api;

  addRuntimeListener((message, _sender, sendResponse) => {
    const msg = message as ToContent | null;
    if (!msg || typeof msg !== "object" || typeof (msg as { t?: unknown }).t !== "string" || !TYPES.has(msg.t)) return false;
    try {
      controller.onWorkerMessage(msg);
    } catch {
      /* never let a bug here surface in the page */
    }
    try {
      sendResponse({ ok: true });
    } catch {
      /* the sender may already be gone */
    }
    return false;
  });
})();
