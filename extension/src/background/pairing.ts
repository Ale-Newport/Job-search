// Pairing (docs/EXTENSION_DESIGN.md 4.2): start, poll, cancel, and the local "disconnect".
// The worker is the only poller that ever claims the token, and polls are de-duplicated, so a token that the server
// releases exactly once cannot be lost to a race between the options page, a panel and the worker's own watcher.

import { trace } from "../shared/trace";
import type { PairStartRequest, PairStartResponse, PairStatusResponse } from "../shared/protocol";
import type { ConnectionState, PairStarted } from "../shared/messages";
import { BridgeHttpError, BridgeNetworkError, bridgeFetch } from "../shared/bridge-client";
import {
  BridgeCallError, UNKNOWN_LISTENER_DETAIL, ambiguousDetail, bridgeCall, clearPairing, clearPairingIf, failureFromState, getPairing, invalidateConnection, openCall,
  ownVersion, pairingPort, resolveConnection, setPairing,
} from "../shared/connection";
import type { PairingRecord } from "../shared/connection";
import { KEYS, clearConnection, getConnection, sessionRemove, setConnection } from "../shared/storage";
import { browserLanguage } from "../shared/i18n-core";
import { parseToken } from "../shared/hello-proof";
import { dropAllWaiters } from "./polling";

export const PAIRING_ALARM = "meridian-pairing";
export let WATCH_INTERVAL_MS = 1500;
/** Test hook: the background watcher's polling interval (0 disables the watcher). */
export function setWatchInterval(ms: number): void {
  WATCH_INTERVAL_MS = ms;
}

export type PairState = "pending" | "approved" | "denied" | "expired" | "claimed";
export interface PairPollResult { state: PairState; connection: ConnectionState }

/** Name shown to the user in Meridian's approval dialog. The server derives the real browser from the Origin. */
export function describeBrowser(): string {
  if (__SAFARI__) return "Safari";
  const agent = typeof navigator !== "undefined" ? navigator.userAgent : "";
  if (/Edg\//.test(agent)) return "Microsoft Edge";
  if (/Brave/.test(agent)) return "Brave";
  return "Google Chrome";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function setPairingAlarm(): Promise<void> {
  try {
    await chrome.alarms.create(PAIRING_ALARM, { delayInMinutes: 0.5, periodInMinutes: 0.5 });
  } catch {
    // alarms are only a backup for a worker that was stopped while the user switched to Meridian
  }
}

async function clearPairingAlarm(): Promise<void> {
  try {
    await chrome.alarms.clear(PAIRING_ALARM);
  } catch {
    // nothing to clear
  }
}

export async function startPairing(options: { confirmUnknown?: boolean } = {}): Promise<PairStarted> {
  const { port, state, ambiguous, unconfirmed } = await pairingPort(options.confirmUnknown === true);
  if (ambiguous) {
    // The first pairing has no secret to check anything against. With two listeners that both say they are Meridian the
    // extension cannot tell the real one from an impostor, so it does not choose: the user must close the extra one.
    throw new BridgeCallError({ code: "ambiguous", detail: state.detail ?? ambiguousDetail(state.meridianPorts ?? []), connection: state });
  }
  if (unconfirmed) {
    // This browser holds a credential and the listener could not be recognised (it gave no proof, not even "revoked"). Pairing
    // with it is the user's explicit decision: the old credential is kept as the fallback meanwhile (see pollOnce).
    throw new BridgeCallError({ code: "confirm_required", detail: state.detail ?? UNKNOWN_LISTENER_DETAIL, connection: state });
  }
  if (port === null) throw failureFromState(state);
  const request: PairStartRequest = { client_name: describeBrowser(), extension_version: ownVersion(), locale: browserLanguage().slice(0, 16) };
  const started = await openCall<PairStartResponse>(port, "/v1/pair/start", { method: "POST", body: request, timeoutMs: 10_000 });
  if (!started || typeof started.pairing_id !== "string" || !/^\d{6}$/.test(String(started.code))) {
    throw new BridgeCallError({ code: "internal", detail: "Unexpected answer from Meridian." });
  }
  const now = Date.now();
  const record: PairingRecord = {
    pairingId: started.pairing_id, code: started.code, port, startedAt: now,
    expiresAt: now + Math.max(5, Math.min(600, Number(started.expires_in) || 120)) * 1000,
  };
  await setPairing(record);
  await setPairingAlarm();
  await resolveConnection({ force: true }); // publishes the `pairing` phase to every listener
  if (WATCH_INTERVAL_MS > 0) void watchPairing(record.pairingId).catch(() => undefined);
  return { pairingId: record.pairingId, code: record.code, expiresAt: record.expiresAt };
}

const inflight = new Map<string, Promise<PairPollResult>>();

/** One poll of the server (shared by every caller that asks while it is running). */
export function pollPairing(pairingId: string): Promise<PairPollResult> {
  const running = inflight.get(pairingId);
  if (running) return running;
  const promise = pollOnce(pairingId).finally(() => inflight.delete(pairingId));
  inflight.set(pairingId, promise);
  return promise;
}

async function settled(pairingId: string, state: PairState): Promise<PairPollResult> {
  // only OUR pairing is retired: if the user already started another one, its record and alarm stay
  if (await clearPairingIf(pairingId)) await clearPairingAlarm();
  trace("pair resolving", { state });
  const connection = (await resolveConnection({ force: true })).state;
  trace("pairing settled", { state, phase: connection.phase });
  return { state, connection };
}

async function pollOnce(pairingId: string): Promise<PairPollResult> {
  const record = await getPairing();
  if (!record || record.pairingId !== pairingId) {
    // Nothing pending under this id: it finished (claimed by another caller) or never existed.
    const resolved = await resolveConnection();
    return { state: resolved.state.phase === "ready" ? "claimed" : "expired", connection: resolved.state };
  }
  if (Date.now() > record.expiresAt + 3000) return settled(pairingId, "expired");
  let status: PairStatusResponse;
  try {
    status = await bridgeFetch<PairStatusResponse>(record.port, `/v1/pair/${encodeURIComponent(pairingId)}`, null, { timeoutMs: 8000, version: ownVersion() });
  } catch (error) {
    trace("pair status failed", { kind: error instanceof Error ? error.name : "unknown", code: error instanceof BridgeHttpError ? error.code : "" });
    if (error instanceof BridgeHttpError) {
      if (error.status === 404 || error.code === "not_found" || error.code === "expired") return settled(pairingId, "expired");
      if (error.code === "restoring" || error.code === "disabled") throw failureFromState((await resolveConnection({ force: true })).state);
      throw new BridgeCallError({ code: error.code, detail: error.message, status: error.status });
    }
    if (error instanceof BridgeNetworkError) {
      await invalidateConnection();
      return { state: "pending", connection: (await resolveConnection({ force: true })).state };
    }
    throw error;
  }
  trace("pair status", { state: status.state });
  switch (status.state) {
    case "pending":
      return { state: "pending", connection: (await resolveConnection()).state };
    case "approved": {
      const parsed = parseToken(status.token);
      trace("pair approved", { parsed: parsed !== null });
      if (!parsed || (status.client_id && status.client_id !== parsed.clientId)) return settled(pairingId, "expired");
      // A credential that is already stored is NOT thrown away: the newcomer may be a listener nobody could vouch for. The old one
      // stays as the fallback (connection.ts promotes it back when the app that issued it answers with its proof; the newer one
      // is dropped then). An older fallback is never replaced by a younger one: it is the one issued by the longest-known app.
      const existing = await getConnection();
      if (existing && existing.clientId !== parsed.clientId && !(await getConnection("previous"))) {
        await setConnection({ ...existing, replacedAt: new Date().toISOString() }, "previous");
      }
      // Persist BEFORE answering anyone: the server will never show this token again.
      await setConnection({
        port: record.port, clientId: parsed.clientId, token: status.token as string,
        clientName: describeBrowser(), pairedAt: new Date().toISOString(),
      });
      trace("pair stored");
      await sessionRemove(KEYS.revoked);
      return settled(pairingId, "approved");
    }
    case "claimed":
      return settled(pairingId, (await getConnection()) ? "claimed" : "expired");
    case "denied":
      return settled(pairingId, "denied");
    default:
      return settled(pairingId, "expired");
  }
}

/** Best-effort background poller so a pairing completes even if the page that started it was closed. */
export async function watchPairing(pairingId: string): Promise<void> {
  for (;;) {
    await sleep(WATCH_INTERVAL_MS);
    const record = await getPairing();
    if (!record || record.pairingId !== pairingId || Date.now() > record.expiresAt + 3000) return;
    const result = await pollPairing(pairingId).catch(() => null);
    if (result && result.state !== "pending") return;
  }
}

/** Called by the alarm: poll whatever pairing is pending, or retire the alarm when none is. */
export async function pollPendingPairing(): Promise<void> {
  const record = await getPairing();
  if (!record) {
    await clearPairingAlarm();
    return;
  }
  await pollPairing(record.pairingId).catch(() => undefined);
}

export async function cancelPairing(pairingId: string): Promise<ConnectionState> {
  const record = await getPairing();
  await clearPairing();
  await clearPairingAlarm();
  if (record && record.pairingId === pairingId) {
    try {
      await bridgeFetch(record.port, `/v1/pair/${encodeURIComponent(pairingId)}/cancel`, null, {
        method: "POST", body: {}, timeoutMs: 5000, version: ownVersion(),
      });
    } catch {
      // the pairing expires by itself after two minutes
    }
  }
  return (await resolveConnection({ force: true })).state;
}

/** Forget this browser's credential on this side only (nothing is sent to Meridian). */
export async function disconnectLocally(): Promise<ConnectionState> {
  await clearConnection();
  await clearPairing();
  await clearPairingAlarm();
  await sessionRemove(KEYS.revoked);
  return (await resolveConnection({ force: true })).state;
}

/** How long "Disconnect" waits for Meridian before it forgets the credential anyway. */
export let REMOTE_REVOKE_MS = 6000;
export function setRemoteRevokeMs(ms: number): void {
  REMOTE_REVOKE_MS = ms;
}

/**
 * Asks Meridian to drop this browser (POST /v1/disconnect revokes the calling client), best effort. The request goes
 * through the normal credentialed path, so it is only ever sent to a port whose hello proof verified; with Meridian
 * closed, unreachable or not proving itself nothing is sent at all. True when Meridian no longer lists this browser
 * (revoked now, or it already had been).
 */
async function revokeRemotely(): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const giveUp = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new BridgeCallError({ code: "unavailable", detail: "Meridian took too long to answer." })), REMOTE_REVOKE_MS);
  });
  try {
    await Promise.race([bridgeCall("/v1/disconnect", { method: "POST", body: {}, retry: false, timeoutMs: Math.min(5000, REMOTE_REVOKE_MS) }), giveUp]);
    return true;
  } catch (error) {
    // "unauthorized" and "revoked": Meridian already treats this browser as disconnected, which is what was asked for
    return error instanceof BridgeCallError && (error.detail.code === "unauthorized" || error.detail.code === "revoked");
  } finally {
    clearTimeout(timer);
  }
}

export interface DisconnectResult { state: ConnectionState; remoteRevoked: boolean }

/**
 * "Disconnect this browser": revoke it in Meridian first (so the app does not keep listing a browser that is gone),
 * then forget the credential here. Meridian being unreachable never blocks the local part.
 */
export async function disconnectBrowser(): Promise<DisconnectResult> {
  dropAllWaiters(); // nobody is waiting for events of a browser that is being disconnected
  const remoteRevoked = (await getConnection()) !== null ? await revokeRemotely() : false;
  const state = await disconnectLocally();
  return { state, remoteRevoked };
}
