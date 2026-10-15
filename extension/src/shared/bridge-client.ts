// Transport to the Meridian bridge (docs/EXTENSION_DESIGN.md section 4): port discovery, hello + proof verification and
// JSON calls. Pure fetch, no chrome.* API, so it is unit-testable against a fake bridge. Only the service worker uses it.
//
// Invariant: a credential is attached to a request ONLY by `bridgeFetch(creds, ...)`, and `creds` exist only as the
// product of a hello whose proof verified AND that was started after the request being served asked for it (see
// `proveConnection` in connection.ts: one hello per request, requests that start together share one, a proof is never
// reused for a later request). `bridgeFetch` refuses credentials that were not marked by `markProven`, and a source
// scan in the unit tests pins that only connection.ts marks them. Hello probes never carry the token, only the
// (non-secret) client id.

import { BRIDGE_PORTS } from "./protocol";
import type { ErrorBody, ErrorCode, HelloResponse } from "./protocol";
import { parseToken, randomNonce, verifyProof, verifyRevokedProof } from "./hello-proof";

export const HELLO_TIMEOUT_MS = 2500;
/** Per-probe patience of a check that follows no verified state (first look of a browser session, or a recovery). */
export const PATIENT_HELLO_TIMEOUT_MS = 6000;
export const VERSION_HEADER = "X-Meridian-Version";
const VERSION_PATTERN = /^[0-9A-Za-z.+_-]{1,32}$/;
export const CALL_TIMEOUT_MS = 20000;
const HELLO_MAX_BYTES = 64 * 1024;
/** Default ceiling for any JSON answer except documents (those pass `maxChars`). */
export const DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
/** The bridge refuses bodies above 1 MiB (413): the worker refuses first, without a network round trip. */
export const MAX_REQUEST_BYTES = 1024 * 1024 - 2048;
const BRIDGE_HOST = "http://127.0.0.1";

export interface BridgeCreds { port: number; clientId: string; token: string }

const proven = new WeakMap<BridgeCreds, number>(); // credentials -> when their proof verified
/**
 * Marks credentials as the product of a verified hello proof for the request that is about to use them. ONLY connection.ts
 * may call this (a unit test scans the sources for it): `bridgeFetch` refuses every credential that was not marked here, and
 * asks for a new proof when the one behind them is older than PROOF_MAX_AGE_MS by the time a connection is free.
 */
export function markProven(creds: BridgeCreds): BridgeCreds {
  proven.set(creds, Date.now());
  return creds;
}

/**
 * `verified`: the listener holds the secret. `revoked`: it holds the secret AND says Meridian revoked this client (a proof of its own).
 * `absent`: it said nothing either way (an impostor, or a Meridian that does not know the client). `invalid`: it claimed one of the
 * two and could not back it. `not_requested`: no credential to ask about.
 */
export type ProofStatus = "verified" | "revoked" | "absent" | "invalid" | "not_requested";
export type HelloProbe =
  | { kind: "meridian"; port: number; hello: HelloResponse; proof: ProofStatus } // `hello` is well formed: see isRecognisedHello
  | { kind: "unavailable"; port: number; reason: "restoring" | "disabled" | "error"; code: string; detail: string }
  | { kind: "foreign"; port: number }
  | { kind: "down"; port: number };

const HELLO_DIGEST = /^[0-9a-f]{64}$/;
const HELLO_VERSION = /^\d{1,6}(\.\d{1,6}){0,3}([-+][0-9A-Za-z.+_-]{0,30})?$/;

/**
 * Does this hello look like the one Meridian sends: every field present and well formed (a bridge protocol, an app version
 * and the bundled extension version, both numeric versions)? Anything less is not treated as Meridian at all. This stops a
 * careless listener that merely echoes {"app":"Meridian"}; it is NOT authentication: a listener that copies the complete
 * shape passes, and before the first pairing there is no secret that could tell it apart (design 4.3, docs "first pairing").
 */
export function isRecognisedHello(hello: unknown, port: number): hello is HelloResponse {
  if (!hello || typeof hello !== "object") return false;
  const body = hello as Partial<HelloResponse>;
  return body.app === "Meridian" && typeof body.bridge === "number" && Number.isInteger(body.bridge) && body.bridge >= 1
    && body.port === port && typeof body.app_version === "string" && HELLO_VERSION.test(body.app_version)
    // "" is what Meridian says when it knows of no bundled extension (the update check treats it as "unknown")
    && typeof body.latest_extension_version === "string" && (body.latest_extension_version === "" || HELLO_VERSION.test(body.latest_extension_version));
}

/** The server answered with an error status. `code` is the bridge ErrorCode when the body had one. */
export class BridgeHttpError extends Error {
  readonly status: number;
  readonly code: ErrorCode | string;
  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.name = "BridgeHttpError";
    this.status = status;
    this.code = code;
  }
}

/** Nothing usable answered: connection refused, reset, timed out or aborted. */
export class BridgeNetworkError extends Error {
  readonly timedOut: boolean;
  /** the CALLER cancelled the request (a poll that was replaced): not a failure of the bridge */
  readonly aborted: boolean;
  constructor(message: string, timedOut = false, aborted = false) {
    super(message);
    this.name = "BridgeNetworkError";
    this.timedOut = timedOut;
    this.aborted = aborted;
  }
}

/** The request would exceed what the bridge accepts; nothing was sent. */
export class BridgeRequestTooLarge extends Error {
  constructor() {
    super("The request is too large for Meridian.");
    this.name = "BridgeRequestTooLarge";
  }
}

const ERROR_CODES: readonly string[] = [
  "unauthorized", "origin", "host", "restoring", "invalid", "too_large", "rate_limited", "not_found", "expired",
  "disabled", "unavailable", "conflict", "internal",
];

export function codeForStatus(status: number): ErrorCode {
  if (status === 401) return "unauthorized";
  if (status === 403) return "origin";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 410) return "expired";
  if (status === 413) return "too_large";
  if (status === 422 || status === 400) return "invalid";
  if (status === 429) return "rate_limited";
  if (status === 503) return "unavailable";
  return "internal";
}

function parseErrorBody(status: number, body: unknown): { code: string; detail: string } {
  const record = body && typeof body === "object" ? (body as Partial<ErrorBody> & { detail?: unknown }) : {};
  const code = typeof record.code === "string" && ERROR_CODES.includes(record.code) ? record.code : codeForStatus(status);
  const detail = typeof record.detail === "string" ? record.detail : `HTTP ${status}`;
  return { code, detail: detail.slice(0, 400) };
}

/**
 * Reads a JSON body without ever holding more than `limit` bytes: a listener that is not Meridian (it has not proved
 * anything yet) must not be able to exhaust the worker's memory by streaming an endless answer.
 */
async function readJson(response: Response, limit: number): Promise<unknown> {
  const declared = Number(response.headers?.get("content-length"));
  if (Number.isFinite(declared) && declared > limit) {
    void response.body?.cancel().catch(() => undefined);
    throw new BridgeNetworkError("response too large");
  }
  let text: string;
  const reader = response.body && typeof response.body.getReader === "function" ? response.body.getReader() : null;
  if (reader) {
    const decoder = new TextDecoder();
    const parts: string[] = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel().catch(() => undefined);
        throw new BridgeNetworkError("response too large");
      }
      parts.push(decoder.decode(value, { stream: true }));
    }
    parts.push(decoder.decode());
    text = parts.join("");
  } else {
    text = await response.text();
    if (text.length > limit) throw new BridgeNetworkError("response too large");
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function networkFailure(error: unknown): BridgeNetworkError {
  const name = error && typeof error === "object" && "name" in error ? String((error as { name: unknown }).name) : "";
  const timedOut = name === "TimeoutError" || name === "AbortError";
  return new BridgeNetworkError(timedOut ? "timeout" : "network", timedOut);
}

// ---- concurrency gate ---------------------------------------------------------------------------------------------
// Browsers allow 6 connections per host:port and a long poll holds one for up to 25 s. At most MAX_IN_FLIGHT requests are
// ever in flight to one bridge port, plus ONE hello probe on a lane of its own (HELLO_RESERVE): a request's proof must never
// wait behind slow requests, or the credential would be sent long after the proof that vouched for it. Everything else
// queues, so a person with many assisted tabs can never starve the connection check or the next suggestion.

export const MAX_IN_FLIGHT = 4;
export const HELLO_RESERVE = 1;
/** A request that cannot get a slot for this long fails like a timeout instead of waiting forever. */
export let QUEUE_WAIT_MS = 15_000;
/** Test hook: how long a request may wait for a connection slot. */
export function setQueueWaitMs(ms: number): void {
  QUEUE_WAIT_MS = ms;
}
/**
 * A hello proof vouches for the request it was made for only while that request goes out: one that had to wait this long for
 * a connection slot proves itself again, holding the slot, before the credential is attached (see bridgeFetch `reprove`).
 */
export let PROOF_MAX_AGE_MS = 1000;
/** Test hook: how old a proof may be when its request finally gets a connection. */
export function setProofMaxAgeMs(ms: number): void {
  PROOF_MAX_AGE_MS = ms;
}

interface Waiting { take: () => void; dispose: () => void }
interface Lane { limit: number; active: number; queue: Waiting[] }
interface Gate { request: Lane; hello: Lane; peak: number }
const gates = new Map<number, Gate>();

function gateFor(port: number): Gate {
  let gate = gates.get(port);
  if (!gate) {
    gate = { request: { limit: MAX_IN_FLIGHT, active: 0, queue: [] }, hello: { limit: HELLO_RESERVE, active: 0, queue: [] }, peak: 0 };
    gates.set(port, gate);
  }
  return gate;
}

function drain(lane: Lane): void {
  while (lane.active < lane.limit && lane.queue.length) {
    const waiting = lane.queue.shift() as Waiting;
    waiting.dispose();
    waiting.take();
  }
}

/** Test and diagnostics view of a port's gate (`active`, `queued` and `peak` are the requests; `hello` the probe lane). */
export function gateStats(port: number): { active: number; queued: number; peak: number; hello: number } {
  const gate = gates.get(port);
  return { active: gate?.request.active ?? 0, queued: gate?.request.queue.length ?? 0, peak: gate?.peak ?? 0, hello: gate?.hello.active ?? 0 };
}

export function resetGates(): void {
  gates.clear();
}

/** Resolves with the (idempotent) release function once a slot of `port` is free on the lane of this `kind`. */
function acquire(port: number, kind: "request" | "hello", signal?: AbortSignal, maxWaitMs = QUEUE_WAIT_MS): Promise<() => void> {
  const gate = gateFor(port);
  const lane = gate[kind];
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new BridgeNetworkError("aborted", false, true));
      return;
    }
    const take = (): void => {
      lane.active += 1;
      if (kind === "request") gate.peak = Math.max(gate.peak, lane.active);
      let released = false;
      resolve(() => {
        if (released) return;
        released = true;
        lane.active -= 1;
        drain(lane);
      });
    };
    if (lane.active < lane.limit && lane.queue.length === 0) {
      take();
      return;
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onAbort = (): void => leave(new BridgeNetworkError("aborted", false, true));
    const waiting: Waiting = {
      take,
      dispose: () => {
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
      },
    };
    const leave = (error: BridgeNetworkError): void => {
      const index = lane.queue.indexOf(waiting);
      if (index < 0) return;
      lane.queue.splice(index, 1);
      waiting.dispose();
      reject(error);
    };
    timer = setTimeout(() => leave(new BridgeNetworkError("queue", true)), maxWaitMs);
    signal?.addEventListener("abort", onAbort, { once: true });
    lane.queue.push(waiting);
  });
}

// ---- hello --------------------------------------------------------------------------------------------------------

export interface HelloCreds { clientId: string; token: string }

/**
 * GET /v1/hello on one port. With credentials, asks for the proof and verifies it (constant time) against the secret.
 * Never throws: every failure is a classified `HelloProbe`.
 */
export async function probeHello(
  port: number, creds?: HelloCreds | null, timeoutMs = HELLO_TIMEOUT_MS, version?: string,
): Promise<HelloProbe> {
  const parsed = creds ? parseToken(creds.token) : null;
  const nonce = parsed && creds ? randomNonce() : null; // fresh per probe: a recorded proof is useless for any later one
  const query = parsed && creds && nonce ? `?client=${encodeURIComponent(creds.clientId)}&nonce=${encodeURIComponent(nonce)}` : "";
  let response: Response;
  let body: unknown;
  let release: (() => void) | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    release = await acquire(port, "hello");
    const controller = new AbortController();
    timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), timeoutMs);
    response = await fetch(`${BRIDGE_HOST}:${port}/v1/hello${query}`, {
      method: "GET", cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
      signal: controller.signal,
      // lets Meridian say "found the extension, connect it" while this browser is still unpaired (a page cannot send it)
      headers: version && VERSION_PATTERN.test(version) ? { [VERSION_HEADER]: version } : {},
    });
    body = await readJson(response, HELLO_MAX_BYTES);
  } catch {
    return { kind: "down", port };
  } finally {
    clearTimeout(timer);
    release?.();
  }
  if (!response.ok) {
    if (body && typeof body === "object" && "detail" in body) {
      const { code, detail } = parseErrorBody(response.status, body);
      if (code === "restoring") return { kind: "unavailable", port, reason: "restoring", code, detail };
      if (code === "disabled") return { kind: "unavailable", port, reason: "disabled", code, detail };
      if (response.status === 503 || response.status >= 500) return { kind: "unavailable", port, reason: "error", code, detail };
    }
    return { kind: "foreign", port };
  }
  const hello = body as Partial<HelloResponse> | null | undefined;
  if (!isRecognisedHello(hello, port)) return { kind: "foreign", port };
  const clean: HelloResponse = {
    app: "Meridian",
    bridge: hello.bridge,
    app_version: hello.app_version,
    latest_extension_version: hello.latest_extension_version,
    port,
    proof: typeof hello.proof === "string" ? hello.proof : null,
    revoked: hello.revoked === true,
    revoked_proof: typeof hello.revoked_proof === "string" ? hello.revoked_proof : null,
    // "" = no digest to compare (an older Meridian, or a build without a stamp); anything that is not a sha-256 hex is ignored
    latest_extension_digest: typeof hello.latest_extension_digest === "string" && HELLO_DIGEST.test(hello.latest_extension_digest)
      ? hello.latest_extension_digest : "",
  };
  let proof: ProofStatus = "not_requested";
  if (parsed && nonce) {
    if (clean.proof) proof = (await verifyProof(parsed.secret, nonce, port, clean.proof)) ? "verified" : "invalid";
    else if (clean.revoked || clean.revoked_proof) {
      // "revoked" is believed only with its proof: the flag alone is what any listener could say
      proof = clean.revoked && (await verifyRevokedProof(parsed.secret, nonce, port, clean.revoked_proof)) ? "revoked" : "invalid";
    } else proof = "absent";
  }
  return { kind: "meridian", port, hello: clean, proof };
}

export interface Discovery {
  probes: HelloProbe[];
  /** the port a credential may be sent to: the first whose proof verified (only when credentials were given) */
  verified: Extract<HelloProbe, { kind: "meridian" }> | null;
  /** the first listener that proved it holds the secret AND that Meridian revoked this client (never inferred from silence) */
  revokedBy: Extract<HelloProbe, { kind: "meridian" }> | null;
  /** first listener that identifies as Meridian (proof irrelevant): where an UNPAIRED extension starts pairing */
  meridian: Extract<HelloProbe, { kind: "meridian" }> | null;
  /** EVERY listener that identifies as Meridian, in port order: two or more means nobody can say which one is real */
  meridians: Extract<HelloProbe, { kind: "meridian" }>[];
  unavailable: Extract<HelloProbe, { kind: "unavailable" }> | null;
  invalidProof: boolean;
  anyAnswer: boolean;
}

export function candidatePorts(preferred: readonly number[] = [], override?: readonly number[] | null): number[] {
  const base = override && override.length ? override : BRIDGE_PORTS;
  const seen = new Set<number>();
  const ordered: number[] = [];
  for (const port of [...preferred, ...base]) {
    if (Number.isInteger(port) && port >= 1 && port <= 65535 && !seen.has(port)) {
      seen.add(port);
      ordered.push(port);
    }
  }
  return ordered;
}

function assemble(probes: HelloProbe[]): Discovery {
  const meridianProbes = probes.filter((probe): probe is Extract<HelloProbe, { kind: "meridian" }> => probe.kind === "meridian");
  return {
    probes,
    verified: meridianProbes.find((probe) => probe.proof === "verified") ?? null,
    revokedBy: meridianProbes.find((probe) => probe.proof === "revoked") ?? null,
    meridian: meridianProbes[0] ?? null,
    meridians: meridianProbes,
    unavailable: probes.find((probe): probe is Extract<HelloProbe, { kind: "unavailable" }> => probe.kind === "unavailable") ?? null,
    invalidProof: meridianProbes.some((probe) => probe.proof === "invalid"),
    anyAnswer: probes.some((probe) => probe.kind !== "down"),
  };
}

/**
 * Finds Meridian. With credentials and a `firstPort` (where it was last verified), that port is asked ALONE first and,
 * when its proof verifies, nothing else is probed: the common case costs one request and never scans. Otherwise every
 * candidate port is probed concurrently (a refused loopback connection fails instantly) and port order is kept.
 */
export async function discoverBridge(
  ports: readonly number[], creds?: HelloCreds | null, timeoutMs = HELLO_TIMEOUT_MS, version?: string, firstPort?: number | null,
): Promise<Discovery> {
  let first: HelloProbe | null = null;
  if (creds && typeof firstPort === "number" && ports.includes(firstPort)) {
    first = await probeHello(firstPort, creds, timeoutMs, version);
    if (first.kind === "meridian" && first.proof === "verified") return assemble([first]);
  }
  const rest = await Promise.all(ports.filter((port) => port !== first?.port).map((port) => probeHello(port, creds, timeoutMs, version)));
  const probes = first ? [first, ...rest] : rest;
  return assemble(ports.map((port) => probes.find((probe) => probe.port === port) as HelloProbe));
}

// ---- calls --------------------------------------------------------------------------------------------------------

export interface FetchOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** starts when the request gets a connection slot, not while it queues (see MAX_IN_FLIGHT) */
  timeoutMs?: number;
  /** response size ceiling in bytes (documents are large and pass their own; everything else is small) */
  maxChars?: number;
  /** this extension's version, reported to Meridian so the app can tell when the extension is outdated */
  version?: string;
  /** lets the caller cancel (a long poll that was replaced); the failure is a BridgeNetworkError with `aborted` */
  signal?: AbortSignal;
  /**
   * Credentialed requests only: a new proof (credentials from a hello verified NOW), used when the one this request came with
   * is older than PROOF_MAX_AGE_MS once a connection slot is free. Without it such a request is not sent.
   */
  reprove?: () => Promise<BridgeCreds | null>;
  /**
   * Which connection lane the request uses. The default is the request lane; `hello` is for the one credentialed check that a
   * proof itself makes (Meridian answered "disabled" a moment ago), which can run while the request it proves holds a slot.
   */
  lane?: "request" | "hello";
}

function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** False when the body would be refused for its size: callers check before spending a proof round trip on it. */
export function bodyFits(body: unknown): boolean {
  if (body === undefined) return true;
  const json = JSON.stringify(body);
  return typeof json !== "string" || utf8Length(json) <= MAX_REQUEST_BYTES;
}

/**
 * One JSON request to a bridge port. `creds` adds the bearer token; pass null for the unauthenticated pairing calls.
 * Resolves with the parsed body; throws BridgeHttpError (status >= 400), BridgeNetworkError or BridgeRequestTooLarge.
 */
export async function bridgeFetch<T>(port: number, path: string, creds: BridgeCreds | null, options: FetchOptions = {}): Promise<T> {
  if (creds && creds.port !== port) throw new BridgeNetworkError("credential bound to another port");
  if (creds && !proven.has(creds)) throw new BridgeNetworkError("credential without a verified proof"); // fail closed
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = {
    method: options.method ?? "GET", headers, cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer",
  };
  if (options.version && VERSION_PATTERN.test(options.version)) headers[VERSION_HEADER] = options.version;
  if (options.body !== undefined) {
    const json = JSON.stringify(options.body);
    if (typeof json === "string" && utf8Length(json) > MAX_REQUEST_BYTES) throw new BridgeRequestTooLarge();
    headers["Content-Type"] = "application/json";
    init.body = json;
  }
  const release = await acquire(port, options.lane ?? "request", options.signal);
  try {
    if (creds) {
      // The token is attached only now, at the moment a connection is free, and only behind a proof that is still fresh.
      let sending = creds;
      if (Date.now() - (proven.get(creds) ?? 0) > PROOF_MAX_AGE_MS) {
        const again = options.reprove ? await options.reprove().catch(() => null) : null;
        if (!again || again.port !== port || again.clientId !== creds.clientId || !proven.has(again)
          || Date.now() - (proven.get(again) ?? 0) > PROOF_MAX_AGE_MS) {
          throw new BridgeNetworkError("the proof was out of date and could not be renewed");
        }
        sending = again;
      }
      headers.Authorization = `Bearer ${sending.token}`;
    }
  } catch (error) {
    release();
    throw error;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), options.timeoutMs ?? CALL_TIMEOUT_MS);
  const onCallerAbort = (): void => controller.abort(new DOMException("aborted", "AbortError"));
  options.signal?.addEventListener("abort", onCallerAbort, { once: true });
  init.signal = controller.signal;
  let response: Response;
  let body: unknown;
  try {
    response = await fetch(`${BRIDGE_HOST}:${port}${path}`, init);
    body = await readJson(response, options.maxChars ?? DEFAULT_MAX_RESPONSE_BYTES);
  } catch (error) {
    if (options.signal?.aborted) throw new BridgeNetworkError("aborted", false, true);
    if (error instanceof BridgeNetworkError) throw error;
    throw networkFailure(error);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", onCallerAbort);
    release();
  }
  if (!response.ok) {
    const { code, detail } = parseErrorBody(response.status, body);
    throw new BridgeHttpError(response.status, code, detail);
  }
  return body as T;
}
