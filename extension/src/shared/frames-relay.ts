// Frame relay: content scripts in different frames cannot message each other, so the worker forwards focus hand-offs and
// progress between them (docs/EXTENSION_DESIGN.md 7.4 "Cross-frame"). Payloads are relayed verbatim and never interpreted.

import type { BgRequest, FramePayload } from "./messages";
import { sendToTab } from "./tabs";

type RelayRequest = Extract<BgRequest, { t: "frame:relay" }>;

const TOP_FRAME = 0;

function validTarget(to: RelayRequest["to"]): boolean {
  if (to === "top" || to === "all") return true;
  return typeof to === "object" && to !== null && Number.isInteger(to.frameId) && to.frameId >= 0;
}

function validPayload(payload: unknown): payload is FramePayload {
  return typeof payload === "object" && payload !== null && typeof (payload as { k?: unknown }).k === "string";
}

/**
 * Forwards `payload` from the frame that sent it to the requested frames of the same tab. A frame can only reach frames
 * of its own tab. The receiving script knows the sender from `fromFrameId` and ignores its own broadcasts.
 */
export async function relayFrame(tabId: number | undefined, fromFrameId: number, to: RelayRequest["to"], payload: unknown): Promise<boolean> {
  if (typeof tabId !== "number" || tabId < 0 || !validTarget(to) || !validPayload(payload)) return false;
  const message = { t: "frame" as const, payload, fromFrameId };
  if (to === "all") return sendToTab(tabId, message);
  return sendToTab(tabId, message, to === "top" ? TOP_FRAME : to.frameId);
}
