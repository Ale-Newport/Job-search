// The worker's ONE long poll (docs/EXTENSION_DESIGN.md 5.2, wave 2). Every assisted tab used to hold its own 20 s poll
// to the bridge; a browser allows 6 connections per host:port, so the 7th tab's first suggestion queued behind six idle
// polls. Now content scripts still send {t:"poll"} per session, but the worker keeps their requests as in-memory
// waiters and serves all of them from a single multiplexed request: POST /v1/poll {sessions:[{session_id, cursor}], wait_ms}.
//
// Rules this module keeps:
//  * at most one multiplexed request in flight (the connection gate in bridge-client caps everything at 4 anyway);
//  * nothing is asked while no session is waiting, or while every waiting tab is hidden (a hidden tab loses nothing:
//    events are cursor-based on the server, so it catches up when it becomes visible);
//  * the request is replaced only when a waiter appears that it does not already cover;
//  * two tabs on the same page share one bridge session: each keeps its OWN wait (they do not replace each other), the
//    request carries the lowest cursor, and the others are served right after from theirs, so nobody gets another tab's
//    events twice or misses any;
//  * a bridge that answers 404 for POST /v1/poll (an older Meridian) is served per session instead, two lanes at a time,
//    in short slices so every session gets its turn;
//  * what the bridge says about each page decides what happens to that page's waits (distribute): `ok` hands over the events,
//    `expired` and `unknown` end the wait with the error codes the content script re-opens the page on (`expired`,
//    `not_found`), `released` stops all asking about that page, `superseded` is not an error at all (a newer poll of this
//    browser took the page over: no events, cursor unchanged, the wait stays and the loop asks again);
//  * a real long poll asks for MORE than 250 ms: the bridge answers anything up to 250 ms at once, as a quick check that
//    never takes a page over from the poll waiting for it;
//  * the worker keeps no cursor of its own: it uses the one the content script sent, so a worker restart only costs the
//    content script's next poll (its pending message fails and it asks again).

import type { PollManyEntry, PollManyRequest, PollManyResponse, PollResponse } from "../shared/protocol";
import { BridgeCallError, bridgeCall } from "../shared/connection";
import { KEYS, sessionGet, sessionSet, withLock } from "../shared/storage";

/** The longest the bridge may hold a poll. */
export const MAX_POLL_WAIT_MS = 25_000;
/** A poll shorter than this is a plain "anything now?" check and bypasses the shared loop. */
const QUICK_POLL_MS = 100;
/** The bridge answers a poll asking to wait this long or less at once and never lets it take a page over: a long poll asks for more. */
export const LONG_POLL_MIN_MS = 251;
/** Nothing is asked about a page the bridge reported released for this long (a page that comes back is re-opened and heard at once). */
const RELEASED_RECHECK_MS = 60_000;
/** The bridge takes at most this many pages per multiplexed request (each once). More waiting pages take turns. */
const MAX_SESSIONS_PER_REQUEST = 16;
/** With more waiting pages than fit in one request, each request is short so every page is covered every few seconds. */
const ROTATION_WAIT_MS = 3_000;
/** A bridge without POST /v1/poll is asked again after this long (the app may have been updated). */
const UNSUPPORTED_RECHECK_MS = 5 * 60_000;
const SINGLE_LANES = 2;
/** A request that returns this fast with nothing is not a long poll: pause so the loop can never spin. */
const FAST_EMPTY_MS = 200;
const FAST_EMPTY_PAUSE_MS = 400;

interface Waiter {
  /** one per tab and session: a tab's newer wait replaces its older one, another tab's wait coexists */
  key: string;
  sessionId: string;
  cursor: number;
  tabId: number | null;
  deadline: number;
  timer: ReturnType<typeof setTimeout>;
  settle: (result: PollResponse | BridgeCallError) => void;
}

interface Flight {
  controllers: AbortController[];
  /** session -> the cursor it was sent with */
  sessions: Map<string, number>;
}

const waiters = new Map<string, Waiter>();
/**
 * When a request last covered each session (it outlives a wait, which the page re-creates every 20 s): with more pages
 * than one request can carry, the least recently covered go first. Forgotten when the session is released or its tab goes.
 */
const covered = new Map<string, number>();
const hidden = new Set<number>();
/** Pages the bridge says were closed or navigated away: session id -> when it said so. Their waits stay (until their own deadline) but nothing is asked. */
const released = new Map<string, number>();
let hiddenLoaded: Promise<void> | null = null;
let running = false;
let flight: Flight | null = null;
let wakeIdle: (() => void) | null = null;
let manyUnsupportedUntil = 0;
const stats = { requests: 0, many: 0, single: 0, restarts: 0 };

const keyOf = (tabId: number | null, sessionId: string): string => `${tabId ?? "-"}|${sessionId}`;

// ---- visibility ---------------------------------------------------------------------------------------------------

/** Reads the hidden tabs once per worker start; every caller waits for the same read. */
function loadHidden(): Promise<void> {
  if (!hiddenLoaded) {
    hiddenLoaded = sessionGet<unknown>(KEYS.hidden).then((stored) => {
      if (Array.isArray(stored)) for (const id of stored) if (Number.isInteger(id)) hidden.add(id as number);
    });
  }
  return hiddenLoaded;
}

function persistHidden(): Promise<void> {
  return withLock(KEYS.hidden, () => sessionSet({ [KEYS.hidden]: [...hidden] })); // writes keep the order of the changes
}

function isVisible(tabId: number | null): boolean {
  return tabId === null || !hidden.has(tabId);
}

/** A tab's page became visible or hidden (content script report). Polling follows. */
export async function setTabVisible(tabId: number, visible: boolean): Promise<void> {
  await loadHidden();
  if (hidden.has(tabId) === !visible) return; // nothing changed
  if (visible) hidden.delete(tabId);
  else hidden.add(tabId);
  await persistHidden();
  if (visible) wake();
  else abortIfNothingToAsk();
}

export async function forgetTabVisibility(tabId: number): Promise<void> {
  await loadHidden();
  if (hidden.delete(tabId)) await persistHidden();
}

// ---- waiters ------------------------------------------------------------------------------------------------------

function empty(waiter: Waiter): PollResponse {
  return { cursor: waiter.cursor, events: [] };
}

function finish(waiter: Waiter, result: PollResponse | BridgeCallError): void {
  if (waiters.get(waiter.key) !== waiter) return;
  waiters.delete(waiter.key);
  clearTimeout(waiter.timer);
  waiter.settle(result);
  abortIfNothingToAsk();
}

function markReleased(sessionId: string): void {
  const now = Date.now();
  if (released.size >= 200) for (const [id, at] of released) if (now - at > RELEASED_RECHECK_MS || released.size >= 200) released.delete(id); // bounded: oldest first
  released.set(sessionId, now);
}

function isReleased(sessionId: string): boolean {
  const at = released.get(sessionId);
  if (at === undefined) return false;
  if (Date.now() - at <= RELEASED_RECHECK_MS) return true;
  released.delete(sessionId); // long enough: ask once more (one cheap entry; the bridge says `released` again if it still is)
  return false;
}

/** The waits worth asking the bridge about: the tab is visible and the bridge has not said the page is gone. */
function liveWaiters(): Waiter[] {
  return [...waiters.values()].filter((waiter) => isVisible(waiter.tabId) && !isReleased(waiter.sessionId));
}

/** A page's bridge session was (re)opened: it is no longer "released", whatever was said before. */
export function sessionOpened(sessionId: string): void {
  if (released.delete(sessionId)) wake();
}

function ofSession(sessionId: string): Waiter[] {
  return [...waiters.values()].filter((waiter) => waiter.sessionId === sessionId);
}

/** Nobody is waiting (or only hidden tabs are): the request in flight has no reader any more. */
function abortIfNothingToAsk(): void {
  if (flight && liveWaiters().length === 0) abortFlight();
}

function abortFlight(): void {
  const current = flight;
  if (!current) return;
  stats.restarts += 1;
  for (const controller of current.controllers) controller.abort();
}

function wake(): void {
  const resume = wakeIdle;
  wakeIdle = null;
  resume?.();
}

/**
 * Events for one session after `cursor`: resolves as soon as the multiplexed loop has some, or empty at `waitMs`.
 * A tab's newer wait for the same session replaces its older one (which resolves empty at once), as on the bridge itself.
 */
export async function waitForEvents(sessionId: string, cursor: number, waitMs: number, tabId: number | null): Promise<PollResponse> {
  await loadHidden();
  const wait = Math.max(0, Math.min(MAX_POLL_WAIT_MS, Math.trunc(waitMs)));
  if (wait < QUICK_POLL_MS) return quickPoll(sessionId, cursor, wait);
  const key = keyOf(tabId, sessionId);
  const older = waiters.get(key);
  if (older) {
    // replaced, not abandoned: the newer wait takes over, so the request in flight must not be cancelled for it
    waiters.delete(key);
    clearTimeout(older.timer);
    older.settle(empty(older));
  }
  return new Promise<PollResponse>((resolve, reject) => {
    const waiter: Waiter = {
      key, sessionId, cursor, tabId, deadline: Date.now() + wait,
      timer: setTimeout(() => finish(waiter, empty(waiter)), wait),
      settle: (result) => (result instanceof BridgeCallError ? reject(result) : resolve(result)),
    };
    waiters.set(key, waiter);
    // The request in flight serves this wait when it already asks about the session from this cursor, or from a LOWER one
    // (the lower waiters are answered first and this one right after, from its own cursor). Anything else needs a new request.
    const asked = flight?.sessions.get(sessionId);
    // (a page the bridge reported released is not asked about, so its wait is no reason to restart the request in flight)
    if (flight && !singleMode() && !isReleased(sessionId) && (asked === undefined || cursor < asked)) abortFlight();
    wake();
    start();
  });
}

/** Cancels the waits of a tab that closed or stopped (they resolve empty; the content script is gone anyway). */
export function dropTab(tabId: number): void {
  for (const waiter of [...waiters.values()]) {
    if (waiter.tabId !== tabId) continue;
    if (!ofSession(waiter.sessionId).some((other) => other.tabId !== tabId)) covered.delete(waiter.sessionId);
    finish(waiter, empty(waiter));
  }
}

/** Every wait ends empty (the credential is gone: the content scripts hear about the new connection state separately). */
export function dropAllWaiters(): void {
  covered.clear();
  for (const waiter of [...waiters.values()]) finish(waiter, empty(waiter));
}

/** One tab stops waiting for a session another tab still uses. */
export function dropTabSession(tabId: number, sessionId: string): void {
  const waiter = waiters.get(keyOf(tabId, sessionId));
  if (waiter) finish(waiter, empty(waiter));
}

/** A session was released: every wait for it ends (whatever tab it came from). */
export function dropSession(sessionId: string): void {
  covered.delete(sessionId);
  released.delete(sessionId);
  for (const waiter of ofSession(sessionId)) finish(waiter, empty(waiter));
}

/** Test and diagnostics view. */
export function pollingState(): { waiting: number; inFlight: boolean; hidden: number[]; mode: "many" | "single"; stats: typeof stats } {
  return { waiting: waiters.size, inFlight: flight !== null, hidden: [...hidden], mode: singleMode() ? "single" : "many", stats: { ...stats } };
}

/** Test hook: forget everything (a worker restart). */
export function resetPolling(): void {
  covered.clear();
  released.clear();
  for (const waiter of [...waiters.values()]) finish(waiter, empty(waiter));
  abortFlight();
  hidden.clear();
  hiddenLoaded = null;
  manyUnsupportedUntil = 0;
  stats.requests = stats.many = stats.single = stats.restarts = 0;
}

// ---- the loop -----------------------------------------------------------------------------------------------------

function singleMode(): boolean {
  return Date.now() < manyUnsupportedUntil;
}

function start(): void {
  if (running) return;
  running = true;
  void run().catch(() => undefined).finally(() => {
    running = false;
    if (waiters.size > 0) start(); // a waiter arrived while the loop was ending
  });
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      wakeIdle = null;
      resolve();
    }, ms);
    wakeIdle = () => {
      clearTimeout(timer);
      resolve();
    };
  });
}

async function run(): Promise<void> {
  while (waiters.size > 0) {
    const live = liveWaiters();
    if (live.length === 0) {
      await pause(2_000); // every waiting tab is hidden: ask nothing until one becomes visible or a wait runs out
      continue;
    }
    const began = Date.now();
    try {
      const answered = singleMode() ? await cycleSingle(live) : await cycleMany(live);
      if (!answered && Date.now() - began < FAST_EMPTY_MS) await pause(FAST_EMPTY_PAUSE_MS);
    } catch (error) {
      if (error instanceof BridgeCallError && error.detail.code === "aborted") continue; // replaced or nothing left to ask
      const failure = error instanceof BridgeCallError ? error : new BridgeCallError({ code: "internal", detail: "Polling failed." });
      for (const waiter of live) finish(waiter, failure);
    }
  }
}

/**
 * One entry per page for the next request: the waits of a page (several tabs may share it) collapse into the one with the
 * lowest cursor, so the bridge is asked from the earliest point anyone needs; the least recently covered pages come first.
 */
function targets(live: Waiter[]): Waiter[] {
  const lowest = new Map<string, Waiter>();
  for (const waiter of live) {
    const current = lowest.get(waiter.sessionId);
    if (!current || waiter.cursor < current.cursor || (waiter.cursor === current.cursor && waiter.deadline > current.deadline)) lowest.set(waiter.sessionId, waiter);
  }
  return [...lowest.values()].sort((a, b) => (covered.get(a.sessionId) ?? 0) - (covered.get(b.sessionId) ?? 0) || a.deadline - b.deadline);
}

// ---- multiplexed ---------------------------------------------------------------------------------------------------

/** The answer's entries, defensively read: an unexpected shape is "nothing", never an exception into the loop. */
function manyEntries(raw: unknown): PollManyEntry[] {
  const list = raw && typeof raw === "object" && Array.isArray((raw as Partial<PollManyResponse>).sessions) ? (raw as PollManyResponse).sessions : [];
  return list.filter((entry): entry is PollManyEntry => Boolean(entry) && typeof entry === "object" && typeof entry.session_id === "string");
}

/** What a page's content script is told when the bridge no longer has its session (it opens the page again). */
function goneError(status: string): BridgeCallError {
  return status === "unknown"
    ? new BridgeCallError({ code: "not_found", detail: "Meridian does not know this page." })
    : new BridgeCallError({ code: "expired", detail: "This page's session is not available any more." });
}

function manyBody(targetList: { session_id: string; cursor: number }[], waitMs: number): PollManyRequest {
  return { sessions: targetList, wait_ms: waitMs };
}

/** One multiplexed request. Returns true when some waiter was answered. */
async function cycleMany(live: Waiter[]): Promise<boolean> {
  const all = targets(live);
  const picked = all.slice(0, MAX_SESSIONS_PER_REQUEST);
  const longest = Math.max(...live.filter((waiter) => picked.some((target) => target.sessionId === waiter.sessionId)).map((waiter) => waiter.deadline - Date.now()));
  const waitMs = Math.max(LONG_POLL_MIN_MS, Math.min(MAX_POLL_WAIT_MS, all.length > MAX_SESSIONS_PER_REQUEST ? Math.min(longest, ROTATION_WAIT_MS) : longest));
  const controller = new AbortController();
  const sent = new Map(picked.map((waiter) => [waiter.sessionId, waiter.cursor] as const));
  const now = Date.now();
  for (const waiter of picked) covered.set(waiter.sessionId, now);
  flight = { controllers: [controller], sessions: sent };
  stats.requests += 1;
  stats.many += 1;
  let raw: unknown;
  try {
    raw = await bridgeCall<PollManyResponse>("/v1/poll", {
      method: "POST", retry: false, signal: controller.signal, timeoutMs: waitMs + 8_000,
      body: manyBody(picked.map((waiter) => ({ session_id: waiter.sessionId, cursor: waiter.cursor })), waitMs),
    });
  } catch (error) {
    if (error instanceof BridgeCallError && (error.detail.status === 404 || error.detail.status === 405)) {
      manyUnsupportedUntil = Date.now() + UNSUPPORTED_RECHECK_MS; // an older Meridian: per-session polls from now on
      return true; // not an empty answer: go straight on with the per-session loop
    }
    throw error;
  } finally {
    if (flight && flight.controllers[0] === controller) flight = null;
  }
  return distribute(manyEntries(raw), sent);
}

/**
 * Hands each page's answer to the waits that asked from the cursor it was asked with (the tabs of one page may be at
 * different cursors: the others are served by the next request, from theirs). True when some wait was answered.
 */
function distribute(entries: PollManyEntry[], sent: Map<string, number>): boolean {
  let answered = false;
  for (const entry of entries) {
    const asked = sent.get(entry.session_id);
    if (asked === undefined) continue;
    const mine = ofSession(entry.session_id);
    switch (entry.status) {
      case "ok": {
        if (!Array.isArray(entry.events) || entry.events.length === 0) break;
        const cursor = Number.isFinite(Number(entry.cursor)) ? Number(entry.cursor) : asked;
        for (const waiter of mine) {
          if (waiter.cursor !== asked) continue; // asked again from a later cursor: its events come with its own request
          finish(waiter, { cursor, events: entry.events });
          answered = true;
        }
        break;
      }
      case "expired":
      case "unknown":
        for (const waiter of mine) finish(waiter, goneError(entry.status)); // the content script opens the page again
        answered = answered || mine.length > 0;
        break;
      case "released":
        markReleased(entry.session_id); // the page is gone: stop asking about it; its waits end at their own deadline
        break;
      default:
        break; // superseded (a newer poll took the page over) or a status from a newer bridge: no events, cursor unchanged, keep waiting
    }
  }
  return answered;
}

// ---- per-session fallback --------------------------------------------------------------------------------------------

/** Older bridges: up to SINGLE_LANES sessions at a time, the longest-unserved first, in slices short enough to rotate. */
async function cycleSingle(live: Waiter[]): Promise<boolean> {
  const all = targets(live);
  const lanes = all.slice(0, SINGLE_LANES);
  const slice = Math.max(1_000, Math.min(4_000, Math.floor(8_000 / Math.max(1, all.length))));
  const controllers = lanes.map(() => new AbortController());
  flight = { controllers, sessions: new Map(lanes.map((waiter) => [waiter.sessionId, waiter.cursor] as const)) };
  stats.requests += lanes.length;
  stats.single += lanes.length;
  const now = Date.now();
  for (const waiter of lanes) covered.set(waiter.sessionId, now);
  let answered = false;
  let fatal: unknown = null;
  try {
    await Promise.all(lanes.map(async (lane, index) => {
      const waitMs = Math.max(LONG_POLL_MIN_MS, Math.min(slice, lane.deadline - Date.now()));
      try {
        const response = await bridgeCall<PollResponse>(`/v1/sessions/${encodeURIComponent(lane.sessionId)}/poll`, {
          method: "POST", retry: false, signal: controllers[index].signal, timeoutMs: waitMs + 8_000, body: { cursor: lane.cursor, wait_ms: waitMs },
        });
        if (Array.isArray(response?.events) && response.events.length > 0) {
          for (const waiter of ofSession(lane.sessionId)) {
            if (waiter.cursor !== lane.cursor) continue;
            finish(waiter, { cursor: Number(response.cursor) || lane.cursor, events: response.events });
            answered = true;
          }
        }
      } catch (error) {
        if (!(error instanceof BridgeCallError)) throw error;
        const code = error.detail.code;
        if (code === "aborted") return;
        if (code === "not_found" || code === "expired" || code === "invalid") for (const waiter of ofSession(lane.sessionId)) finish(waiter, error); // this session only
        else fatal = fatal ?? error; // the connection itself failed: every waiter hears about it
      }
    }));
  } finally {
    if (flight && flight.controllers === controllers) flight = null;
  }
  if (fatal) throw fatal;
  return answered;
}

// ---- "anything right now?" ------------------------------------------------------------------------------------------

async function quickPoll(sessionId: string, cursor: number, waitMs: number): Promise<PollResponse> {
  if (isReleased(sessionId)) return { cursor, events: [] }; // the bridge said this page is gone: nothing to ask
  stats.requests += 1;
  if (!singleMode()) {
    try {
      const raw = await bridgeCall<PollManyResponse>("/v1/poll", {
        method: "POST", retry: false, timeoutMs: waitMs + 8_000, body: manyBody([{ session_id: sessionId, cursor }], waitMs),
      });
      const entry = manyEntries(raw).find((item) => item.session_id === sessionId);
      if (entry?.status === "released") markReleased(sessionId);
      if (!entry || entry.status === "superseded" || entry.status === "released") return { cursor, events: [] };
      if (entry.status === "expired" || entry.status === "unknown") throw goneError(entry.status);
      if (entry.status !== "ok") return { cursor, events: [] };
      return { cursor: Number.isFinite(Number(entry.cursor)) ? Number(entry.cursor) : cursor, events: Array.isArray(entry.events) ? entry.events : [] };
    } catch (error) {
      if (!(error instanceof BridgeCallError) || (error.detail.status !== 404 && error.detail.status !== 405)) throw error;
      manyUnsupportedUntil = Date.now() + UNSUPPORTED_RECHECK_MS;
    }
  }
  return bridgeCall<PollResponse>(`/v1/sessions/${encodeURIComponent(sessionId)}/poll`, {
    method: "POST", retry: false, timeoutMs: waitMs + 8_000, body: { cursor, wait_ms: waitMs },
  });
}
