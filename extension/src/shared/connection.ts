// Connection state machine of the worker (docs/EXTENSION_DESIGN.md 4.2-4.4, 7.2): turns discovery + proof + the stored
// credential into a ConnectionState, caches it in storage.session with retry backoff, and runs credentialed calls.
//
// Stateless by construction: every function re-reads storage. The only in-memory state is the check in flight (and the
// one waiting behind it), the verdicts being judged and the change listeners, and losing any of it is harmless.
//
// The rule this file exists for: a credential leaves the worker only behind a hello proof that was MADE FOR the request
// carrying it. `proveConnection` is the only producer of credentials; a cached verification never is (docs 4.3).

import type { BgError, ConnectionPhase, ConnectionState, StoredConnection } from "./messages";
import {
  BridgeHttpError, BridgeNetworkError, BridgeRequestTooLarge, HELLO_TIMEOUT_MS, PATIENT_HELLO_TIMEOUT_MS, bodyFits, bridgeFetch,
  candidatePorts, discoverBridge, markProven,
} from "./bridge-client";
import type { BridgeCreds, FetchOptions } from "./bridge-client";
import {
  KEYS, clearConnection, getConnection, localGet, recordError, sessionGet, sessionRemove, sessionSet, setConnection, withLock,
} from "./storage";
import { isOutdated } from "./version";

/**
 * How long a `ready` STATE may be shown from the cache (panels and the badge ask often). This is about the picture of the
 * connection only: it never lets a credential out. Every credentialed request is preceded by its own hello proof
 * (`proveConnection`), so a process that takes over Meridian's port at any moment receives no token.
 */
export const STATE_FRESH_MS = 2_000;
/** Minimum spacing between two network discoveries when nothing forces one (many panels may ask at once). */
export const MIN_RECHECK_MS = 700;
/** Pause before the single second look when a bridge that answered before suddenly does not. */
export const TRANSIENT_RETRY_MS = 300;

/** Per-probe patience (test hook: the unit tests shrink it to keep slow-machine scenarios fast). */
const tuning = { helloMs: HELLO_TIMEOUT_MS, patientMs: PATIENT_HELLO_TIMEOUT_MS };
export function setProbeTimeouts(helloMs: number, patientMs: number): void {
  tuning.helloMs = helloMs;
  tuning.patientMs = patientMs;
}

const FAILURE_PHASES: readonly ConnectionPhase[] = ["no_bridge", "error", "restoring", "proof_failed", "disabled"];

/** What the connection looks like (UI, badge, diagnostics). Carries no credential, by construction. */
export interface Resolved { state: ConnectionState }
/**
 * The outcome of a proof made FOR the request that asked: `creds` is non-null only when phase is `ready` and a hello (fresh
 * nonce, sent after the request began) came back with a proof that verified. They are for the requests that started together
 * with it and for nobody else.
 */
export interface Proven { state: ConnectionState; creds: BridgeCreds | null }

interface ConnCache {
  state: ConnectionState;
  at: number;
  verifiedAt: number | null;
  failures: number;
  nextAttemptAt: number;
}

export interface PairingRecord { pairingId: string; code: string; expiresAt: number; port: number; startedAt: number }

export function backoffMs(failures: number): number {
  return Math.min(10_000, 1500 * 2 ** Math.max(0, failures - 1));
}

export function ownVersion(): string {
  try {
    return chrome.runtime.getManifest().version;
  } catch {
    return "0.0.0";
  }
}

const DIGEST_PATTERN = /^[0-9a-f]{64}$/;
let ownDigestPromise: Promise<string> | null = null;

/**
 * The digest the build stamped into this extension's own `build.json` ("" when this build carries no stamp: Safari and test
 * builds, which are never compared). Compared with the digest Meridian advertises for the build it bundles, so a rebuilt
 * extension whose version number did not change is still offered as an update. Read once per worker start.
 */
export function ownDigest(): Promise<string> {
  ownDigestPromise ??= (async () => {
    try {
      const response = await fetch(chrome.runtime.getURL("build.json"), { cache: "no-store" });
      if (!response.ok) return "";
      const body = (await response.json()) as { digest?: unknown };
      return typeof body?.digest === "string" && DIGEST_PATTERN.test(body.digest) ? body.digest : "";
    } catch {
      return "";
    }
  })();
  return ownDigestPromise;
}

/** Test hook: pretend this build was stamped with `digest` (null: read the real file again). */
export function setOwnDigest(digest: string | null): void {
  ownDigestPromise = digest === null ? null : Promise.resolve(digest);
}

/** How long a replaced credential is kept as the fallback when the app that issued it is not seen again. */
export const FALLBACK_KEEP_MS = 30 * 24 * 3600 * 1000;

/**
 * What a script that runs next to a web page is told about the connection. The in-page panel has no card for "a listener nobody
 * could vouch for" (and no way to confirm one: that choice is made on the options page, with the whole explanation), so it is
 * told the truthful short version it already knows how to show: something answered but could not prove it is Meridian, nothing is
 * sent. Everything else passes through untouched.
 */
export function forPage(state: ConnectionState): ConnectionState {
  return state.phase === "unknown_listener" ? { ...state, phase: "proof_failed" } : state;
}

export function isFailurePhase(phase: ConnectionPhase): boolean {
  return FAILURE_PHASES.includes(phase);
}

// ---- pairing record (storage.session) -----------------------------------------------------------------------------

export async function getPairing(): Promise<PairingRecord | null> {
  const raw = await sessionGet<PairingRecord>(KEYS.pairing);
  if (!raw || typeof raw.pairingId !== "string" || typeof raw.code !== "string") return null;
  if (typeof raw.expiresAt !== "number" || typeof raw.port !== "number") return null;
  return raw;
}

export function setPairing(record: PairingRecord): Promise<void> {
  return withLock(KEYS.pairing, () => sessionSet({ [KEYS.pairing]: record }));
}

export function clearPairing(): Promise<void> {
  return withLock(KEYS.pairing, () => sessionRemove(KEYS.pairing));
}

/**
 * Forgets the pairing record only while it is still the one that just finished. A newer pairing the user started in the
 * meantime (a second click on Connect while the first approval was being processed) must survive: clearing it would
 * strand its approval, which the server releases exactly once.
 */
export function clearPairingIf(pairingId: string): Promise<boolean> {
  return withLock(KEYS.pairing, async () => {
    const current = await sessionGet<PairingRecord>(KEYS.pairing);
    if (current && current.pairingId !== pairingId) return false;
    await sessionRemove(KEYS.pairing);
    return true;
  });
}

// ---- change listeners ---------------------------------------------------------------------------------------------

type Listener = (state: ConnectionState, previous: ConnectionState | null) => void;
const listeners = new Set<Listener>();
export function onConnectionChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function signature(state: ConnectionState | null): string {
  if (!state) return "";
  return [state.phase, state.port ?? "", state.pairing?.code ?? "", state.extensionOutdated ? 1 : 0, state.clientName ?? "",
    (state.meridianPorts ?? []).join(",")].join("|");
}

// ---- resolution ---------------------------------------------------------------------------------------------------

/** Only for a port whose proof just verified: the object is marked as proven, which `bridgeFetch` insists on. */
function provenCreds(stored: StoredConnection, port: number): BridgeCreds {
  return markProven({ port, clientId: stored.clientId, token: stored.token });
}

async function portOverride(): Promise<number[] | null> {
  if (__E2E__) {
    const ports = await localGet<unknown>("meridian.e2e.ports");
    if (Array.isArray(ports)) return ports.filter((port): port is number => typeof port === "number");
  }
  return null;
}

// ---- who shares a check ---------------------------------------------------------------------------------------------
// A check is a hello (fresh nonce) plus the decision taken from its answer. Anyone who only wants the PICTURE of the
// connection may join a check that is already running. A caller that is about to send the credential may not: the answer of
// a hello that started before it asked says nothing about who listens now. Such a caller joins a check that has NOT started
// yet, so one hello serves everything that started together while a running one finishes, and a request that begins later
// gets a hello of its own. At most one check runs and one waits, so a burst costs one extra hello, not one per request.

interface Run { force: boolean; strict: boolean; begin: () => void; promise: Promise<Proven> }
let running: Promise<Proven> | null = null;
let queued: Run | null = null;

function newRun(force: boolean, strict: boolean): Run {
  let begin: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    begin = resolve;
  });
  const run: Run = { force, strict, begin, promise: null as unknown as Promise<Proven> };
  run.promise = gate.then(() => discoverAndDecide(run.force, run.strict));
  return run;
}

function launch(run: Run): void {
  running = run.promise;
  const next = (): void => {
    if (running === run.promise) running = null;
    const waiting = queued;
    queued = null;
    if (waiting) launch(waiting);
  };
  run.promise.then(next, next);
  run.begin();
}

function schedule(force: boolean, strict: boolean): Promise<Proven> {
  if (queued) {
    queued.force = queued.force || force;
    queued.strict = queued.strict || strict;
    return queued.promise;
  }
  if (running && !strict) return running;
  const run = newRun(force, strict);
  if (running === null) launch(run);
  else queued = run;
  return run.promise;
}

/** Test hook: resolves when no check is running or waiting (one that outlived its test would commit into the next one's storage). */
export async function connectionIdle(): Promise<void> {
  for (let guard = 0; guard < 20 && (running || queued); guard++) await (queued?.promise ?? running)?.catch(() => undefined);
}

/**
 * The connection as of now: cached when still fresh, otherwise re-discovered (all candidate ports concurrently).
 * The result is the picture only; use `proveConnection` for anything that sends the credential.
 */
export function resolveConnection(options: { force?: boolean } = {}): Promise<Resolved> {
  return schedule(options.force === true, false).then(({ state }) => ({ state }));
}

/**
 * A hello proof made for the request that calls this: starts after the call, carries a fresh nonce, and resolves with
 * credentials only when the listener on the stored port proved it knows the secret. Never served from the cache, never
 * shared with a request that began later (see above).
 */
export function proveConnection(options: { force?: boolean } = {}): Promise<Proven> {
  return schedule(options.force === true, true);
}

export async function invalidateConnection(): Promise<void> {
  const cache = await sessionGet<ConnCache>(KEYS.conn);
  if (cache) await sessionSet({ [KEYS.conn]: { ...cache, verifiedAt: null, at: 0, nextAttemptAt: 0 } });
}

/** The cached state without any network traffic (may be stale); null when nothing was ever resolved. */
export async function cachedConnection(): Promise<ConnectionState | null> {
  const cache = await sessionGet<ConnCache>(KEYS.conn);
  return cache?.state ?? null;
}

async function discoverAndDecide(force: boolean, strict: boolean): Promise<Proven> {
  const now = Date.now();
  let [stored, cache, pairing, wasRevoked, previous] = await Promise.all([
    getConnection(), sessionGet<ConnCache>(KEYS.conn), getPairing(), sessionGet<boolean>(KEYS.revoked), getConnection("previous"),
  ]);
  if (previous && !stored) {
    // only the fallback is left (the newer credential was forgotten): it is the credential again
    const { replacedAt: _since, ...restored } = previous;
    await setConnection(restored);
    await clearConnection("previous");
    stored = restored;
    previous = null;
  } else if (previous && !(Date.parse(previous.replacedAt ?? "") + FALLBACK_KEEP_MS >= now)) {
    await clearConnection("previous"); // the app that issued it has not been seen for a month (or the date is unreadable): not coming back
    previous = null;
  }
  if (!force && cache) {
    const fresh = cache.state.phase === "ready" && stored !== null && cache.verifiedAt !== null
      && now - cache.verifiedAt < STATE_FRESH_MS && cache.state.port === stored.port;
    if (fresh && !strict) return { state: cache.state, creds: null }; // the picture only: a credential needs a proof of its own
    if (cache.state.phase !== "ready" && now - cache.at < MIN_RECHECK_MS) return { state: cache.state, creds: null };
    if (isFailurePhase(cache.state.phase) && now < cache.nextAttemptAt) {
      return { state: { ...cache.state, retryAfterMs: cache.nextAttemptAt - now }, creds: null };
    }
  }
  const livePairing = pairing && pairing.expiresAt > now ? pairing : null;
  if (pairing && !livePairing) await clearPairing();
  const preferred = [stored?.port, livePairing?.port, cache?.state.port].filter((port): port is number => typeof port === "number");
  const ports = candidatePorts(preferred, await portOverride());
  const hello = stored ? { clientId: stored.clientId, token: stored.token } : null;
  // Nothing verified a moment ago (first look of the browser session, or recovering): a loaded machine may take seconds
  // to answer, and calling that "not running" would show an error for what is only slowness. The port where Meridian
  // was last verified is asked first and alone; only if it does not prove itself is everything scanned.
  const patient = !cache || cache.state.phase !== "ready";
  const probeMs = patient ? tuning.patientMs : tuning.helloMs;
  let discovery = await discoverBridge(ports, hello, probeMs, ownVersion(), stored?.port ?? null);
  if (!discovery.anyAnswer && cache && !isFailurePhase(cache.state.phase)) {
    // Meridian was reachable a moment ago: one patient second look before calling it gone (a busy or restarting app).
    await new Promise((resolve) => setTimeout(resolve, TRANSIENT_RETRY_MS));
    discovery = await discoverBridge(ports, hello, tuning.patientMs, ownVersion(), stored?.port ?? null);
  }
  if (stored && previous) {
    // A pairing with a listener nobody could vouch for replaced this credential but did not destroy it: ask the ports about it
    // too. Where the app that issued it answers with its proof, THAT is Meridian, and what a pairing since then handed out
    // (by an impostor, or by a Meridian that forgot this browser) is dropped. Where it says the credential was revoked, it is useless.
    const fallback = await discoverBridge(ports, { clientId: previous.clientId, token: previous.token }, probeMs, ownVersion(), previous.port);
    const same = fallback.verified && discovery.verified && fallback.verified.port === discovery.verified.port;
    if (fallback.verified && !same) {
      const { replacedAt: _since, ...restored } = previous;
      const back = { ...restored, port: fallback.verified.port };
      await setConnection(back);
      await clearConnection("previous");
      stored = back;
      discovery = fallback;
    } else if (same || fallback.revokedBy) {
      await clearConnection("previous");
    }
  }

  const latest = discovery.verified?.hello ?? discovery.meridian?.hello;
  const base: ConnectionState = { phase: "no_bridge" };
  if (latest) {
    base.appVersion = latest.app_version;
    base.latestExtensionVersion = latest.latest_extension_version;
    base.extensionOutdated = isOutdated(latest.latest_extension_version, latest.latest_extension_digest ?? "", ownVersion(), await ownDigest());
  }
  if (stored?.clientName) base.clientName = stored.clientName;

  let state: ConnectionState;
  let creds: BridgeCreds | null = null;
  const meridianOnPairingPort = livePairing !== null
    && discovery.probes.some((probe) => probe.kind === "meridian" && probe.port === livePairing.port);
  if (livePairing && meridianOnPairingPort) {
    state = { ...base, phase: "pairing", port: livePairing.port, pairing: {
      pairingId: livePairing.pairingId, code: livePairing.code, expiresAt: livePairing.expiresAt,
    } };
  } else if (stored && discovery.verified) {
    const port = discovery.verified.port;
    if (port !== stored.port) await setConnection({ ...stored, port });
    state = { ...base, phase: "ready", port };
    creds = provenCreds(stored, port);
  } else if (stored && discovery.invalidProof) {
    state = { ...base, phase: "proof_failed", port: discovery.meridian?.port, detail: "A listener on this Mac could not prove it is Meridian." };
  } else if (stored && discovery.revokedBy) {
    // Meridian ITSELF said so, with a proof only the holder of the secret can check. Silence is never read as this.
    state = { ...base, phase: "revoked", port: discovery.revokedBy.port };
  } else if (discovery.unavailable) {
    const unavailable = discovery.unavailable;
    state = { ...base, phase: unavailable.reason === "restoring" ? "restoring" : unavailable.reason === "disabled" ? "disabled" : "error",
      port: unavailable.port, detail: unavailable.detail };
  } else if (discovery.meridian) {
    // With a credential in hand, a listener that proves nothing (no proof, not even a revoked one) is UNKNOWN: it may be Meridian
    // after a reinstall or a restored backup, or it may be a program that wants the browser to pair with it. Only without a
    // credential is there nothing to compare, and the (documented) first pairing is the user's call alone.
    state = { ...base, phase: stored ? "unknown_listener" : wasRevoked === true ? "revoked" : "unpaired", port: discovery.meridian.port };
    if (stored) state.detail = UNKNOWN_LISTENER_DETAIL;
  } else {
    state = { ...base, phase: "no_bridge" };
  }
  if (state.phase === "unpaired" || state.phase === "revoked" || state.phase === "pairing" || state.phase === "unknown_listener") {
    // No credential can tell the listeners apart here. One that answers like Meridian is where pairing goes; two or more
    // are reported as such and pairing refuses (startPairing): the extension will not pick one on its own.
    const answering = discovery.meridians.map((probe) => probe.port);
    if (answering.length > 0) state = { ...state, meridianPorts: answering };
    if (answering.length > 1 && state.phase !== "pairing") {
      const { port: _unchosen, ...unchosen } = state;
      state = { ...unchosen, detail: ambiguousDetail(answering) };
    }
  }
  if (state.phase === "ready" && creds && typeof (await sessionGet<number>(KEYS.disabled)) === "number") {
    // Meridian answered "disabled" a moment ago: ask again with the verified credential before calling it ready.
    try {
      // on the hello lane: this check belongs to a proof, which may be renewing while the request it serves holds a request slot
      await bridgeFetch(creds.port, "/v1/status", creds, { timeoutMs: 4000, version: ownVersion(), lane: "hello" });
      await sessionRemove(KEYS.disabled);
    } catch (error) {
      if (error instanceof BridgeHttpError && error.code === "disabled") {
        state = { ...state, phase: "disabled", detail: error.message };
        creds = null;
      }
    }
  }
  return commit(state, cache, creds, now);
}

async function commit(state: ConnectionState, previous: ConnCache | undefined, creds: BridgeCreds | null, now: number): Promise<Proven> {
  const failing = isFailurePhase(state.phase);
  const failures = failing ? (previous && isFailurePhase(previous.state.phase) ? previous.failures + 1 : 1) : 0;
  const nextAttemptAt = failing ? now + backoffMs(failures) : 0;
  if (failing) state = { ...state, retryAfterMs: backoffMs(failures) };
  const cache: ConnCache = { state, at: now, verifiedAt: creds ? now : null, failures, nextAttemptAt };
  await sessionSet({ [KEYS.conn]: cache });
  if (signature(previous?.state ?? null) !== signature(state)) {
    for (const listener of listeners) {
      try {
        listener(state, previous?.state ?? null);
      } catch {
        // a faulty listener must never break connection handling
      }
    }
  }
  return { state, creds };
}

// ---- credentialed calls -------------------------------------------------------------------------------------------

/** Thrown by bridgeCall; the router converts it into a BgResponse error. */
export class BridgeCallError extends Error {
  readonly detail: BgError;
  constructor(detail: BgError) {
    super(detail.detail);
    this.name = "BridgeCallError";
    this.detail = detail;
  }
}

export const UNKNOWN_LISTENER_DETAIL =
  "A program on this Mac answered as Meridian but did not recognise this browser, and Meridian itself did not say it had disconnected it. Nothing is sent to it. If you reinstalled Meridian or restored a backup, that is expected: you can connect to it after confirming.";

const PHASE_DETAIL: Record<ConnectionPhase, string> = {
  ready: "Connected to Meridian.",
  no_bridge: "Meridian is not running.",
  unpaired: "This browser is not connected to Meridian yet.",
  pairing: "Waiting for you to confirm the code in Meridian.",
  revoked: "Meridian no longer recognises this browser.",
  unknown_listener: UNKNOWN_LISTENER_DETAIL,
  proof_failed: "Something on this Mac answered but could not prove it is Meridian.",
  disabled: "The browser assistant is switched off in Meridian.",
  restoring: "Meridian is restoring a backup.",
  error: "Meridian could not be reached.",
  connecting: "Checking for Meridian.",
};

export function ambiguousDetail(ports: readonly number[]): string {
  return `More than one program on this Mac answers as Meridian (ports ${ports.join(", ")}). Nothing is sent to any of them: quit the extra one and try again.`;
}

export function failureFromState(state: ConnectionState): BridgeCallError {
  return new BridgeCallError({ code: state.phase, detail: state.detail ?? PHASE_DETAIL[state.phase], connection: state });
}

export interface CallOptions extends FetchOptions {
  /** Retry once through a fresh discovery after a connection failure. Off for requests that must not run twice. */
  retry?: boolean;
}

/** A request that waited for a connection longer than a proof stays fresh renews it (holding its slot, on the hello lane). */
async function reprove(): Promise<BridgeCreds | null> {
  const again = await proveConnection({ force: true });
  return again.state.phase === "ready" ? again.creds : null;
}

/**
 * One authenticated bridge request. Every call starts with a hello proof of its own (`proveConnection`: a fresh nonce, sent
 * after this call began; calls that start together share one), sends the credential only to the port that proved itself,
 * and maps every failure to a BgError carrying the new ConnectionState. A cached verification is never enough: a process
 * that takes over Meridian's port at any moment (not just within some window) is asked for a proof before it sees a token.
 */
export async function bridgeCall<T>(path: string, options: CallOptions = {}): Promise<T> {
  const retry = options.retry !== false;
  let attempt = 0;
  for (;;) {
    if (options.signal?.aborted) throw new BridgeCallError({ code: "aborted", detail: "The request was cancelled." });
    if (!bodyFits(options.body)) throw new BridgeCallError({ code: "too_large", detail: new BridgeRequestTooLarge().message, status: 413 });
    const proven = await proveConnection({ force: attempt > 0 });
    if (proven.state.phase !== "ready" || !proven.creds) throw failureFromState(proven.state);
    try {
      return await bridgeFetch<T>(proven.creds.port, path, proven.creds, { ...options, version: ownVersion(), reprove });
    } catch (error) {
      if (error instanceof BridgeRequestTooLarge) throw new BridgeCallError({ code: "too_large", detail: error.message, status: 413 });
      if (error instanceof BridgeNetworkError) {
        if (error.aborted) throw new BridgeCallError({ code: "aborted", detail: "The request was cancelled." });
        if (error.timedOut) {
          await recordError("timeout");
          // same code the bridge itself uses when its deadline passes, so callers treat both as one transient condition
          throw new BridgeCallError({ code: "unavailable", detail: "Meridian took too long to answer." });
        }
        await invalidateConnection();
        if (retry && attempt === 0) {
          attempt += 1;
          continue;
        }
        const again = await resolveConnection({ force: true });
        const state = again.state.phase === "ready" ? { ...again.state, phase: "error" as const, detail: PHASE_DETAIL.error } : again.state;
        throw failureFromState(state);
      }
      if (error instanceof BridgeHttpError) throw await mapHttpError(error, proven.creds.clientId);
      throw error;
    }
  }
}

/** The 401 verdicts in progress, one per credential (a burst of refused requests is judged once). */
const judgements = new Map<string, Promise<ConnectionState>>();

/**
 * A request that was sent only after a verified proof still came back 401. That is NOT yet evidence that the credential is
 * dead: the listener could have changed between the hello and the request (the one gap that remains between a proof and the
 * request that follows it), and a listener that is not Meridian is free to answer 401 to make the extension forget its
 * credential and pair again. So the answer is checked, and nothing is forgotten on an unproven 401:
 *  1. a fresh hello, nonce made now: unless it proves the stored credential's secret (a revoked or unknown client gets no
 *     proof; an impostor cannot make one) the state it yields (revoked / proof_failed / no_bridge ...) is reported and the
 *     credential stays where it is;
 *  2. a listener that has just proved it holds the secret is asked once more (GET /v1/status); if it accepts, it was a race;
 *  3. only if it refuses again AND yet another fresh hello still proves the same listener is the credential forgotten: the
 *     refusal is then bracketed by two proofs, so it came from the real Meridian.
 * Meridian itself revokes a client by withholding the proof, so in practice a revocation shows up at step 1 as `revoked`.
 */
async function judgeRejection(clientId: string): Promise<ConnectionState> {
  const first = await proveConnection({ force: true });
  if (first.state.phase !== "ready" || !first.creds || first.creds.clientId !== clientId) return first.state;
  try {
    await bridgeFetch(first.creds.port, "/v1/status", first.creds, { timeoutMs: 4000, version: ownVersion() });
    return first.state; // it accepts the credential now: the first refusal was a race
  } catch (error) {
    if (error instanceof BridgeHttpError && error.status === 401) {
      // refused again, by a listener that proved itself a moment ago: go on to the second proof
    } else if (error instanceof BridgeHttpError || error instanceof BridgeNetworkError) {
      return first.state; // any other answer, or none, is not a verdict
    } else {
      throw error;
    }
  }
  const second = await proveConnection({ force: true });
  if (second.state.phase !== "ready" || !second.creds || second.creds.clientId !== clientId) return second.state;
  const current = await getConnection();
  if (current && current.clientId === clientId) {
    // "revoked" is written BEFORE the credential is removed: whoever sees no credential must also see why
    await sessionSet({ [KEYS.revoked]: true });
    await clearConnection("current"); // a fallback credential (a replaced pairing) is judged on its own, by its own proof
  }
  return (await resolveConnection({ force: true })).state;
}

async function mapHttpError(error: BridgeHttpError, usedClientId: string): Promise<BridgeCallError> {
  await recordError(error.code);
  if (error.status === 401) {
    let judging = judgements.get(usedClientId);
    if (!judging) {
      judging = judgeRejection(usedClientId).finally(() => judgements.delete(usedClientId));
      judgements.set(usedClientId, judging);
    }
    return new BridgeCallError({ code: "unauthorized", detail: error.message, status: 401, connection: await judging });
  }
  if (error.code === "disabled") await sessionSet({ [KEYS.disabled]: Date.now() });
  if (error.code === "restoring" || error.code === "disabled") {
    const resolved = await resolveConnection({ force: true });
    return new BridgeCallError({ code: error.code, detail: error.message, status: error.status, connection: resolved.state });
  }
  return new BridgeCallError({ code: error.code, detail: error.message, status: error.status });
}

/**
 * An UNAUTHENTICATED bridge call (pairing). Never carries a credential. Failures map exactly like bridgeCall's, except
 * that a 401 cannot happen here and nothing is cleared.
 */
export async function openCall<T>(port: number, path: string, options: FetchOptions = {}): Promise<T> {
  try {
    return await bridgeFetch<T>(port, path, null, { ...options, version: ownVersion() });
  } catch (error) {
    if (error instanceof BridgeRequestTooLarge) throw new BridgeCallError({ code: "too_large", detail: error.message, status: 413 });
    if (error instanceof BridgeNetworkError) {
      if (error.aborted) throw new BridgeCallError({ code: "aborted", detail: "The request was cancelled." });
      if (error.timedOut) throw new BridgeCallError({ code: "unavailable", detail: "Meridian took too long to answer." });
      await invalidateConnection();
      const again = await resolveConnection({ force: true });
      throw failureFromState(again.state.phase === "ready" ? { ...again.state, phase: "error", detail: PHASE_DETAIL.error } : again.state);
    }
    if (error instanceof BridgeHttpError) {
      await recordError(error.code);
      if (error.code === "restoring" || error.code === "disabled") {
        const resolved = await resolveConnection({ force: true });
        const phase = error.code === "disabled" ? "disabled" : "restoring";
        throw new BridgeCallError({
          code: error.code, detail: error.message, status: error.status, connection: { ...resolved.state, phase, detail: error.message },
        });
      }
      throw new BridgeCallError({ code: error.code, detail: error.message, status: error.status });
    }
    throw error;
  }
}

/**
 * The bridge port that unauthenticated pairing calls should use right now (null: Meridian is not answering, or more than one
 * program says it is Meridian: `ambiguous`, and nothing is paired with any of them, or the only listener could not be recognised
 * while a credential is stored and the user has not confirmed: `unconfirmed`).
 */
export async function pairingPort(
  confirmUnknown = false,
): Promise<{ port: number | null; state: ConnectionState; ambiguous: boolean; unconfirmed: boolean }> {
  const resolved = await resolveConnection({ force: true });
  const state = resolved.state;
  const open = state.phase === "unpaired" || state.phase === "revoked" || state.phase === "pairing" || state.phase === "ready"
    || state.phase === "unknown_listener";
  const ambiguous = open && state.phase !== "pairing" && state.phase !== "ready" && (state.meridianPorts?.length ?? 0) > 1;
  // a listener that could not be recognised is paired with only after the user said so (and the old credential is kept, see pairing.ts)
  const unconfirmed = state.phase === "unknown_listener" && !confirmUnknown;
  return { port: open && !ambiguous && !unconfirmed ? state.port ?? null : null, state, ambiguous, unconfirmed };
}
