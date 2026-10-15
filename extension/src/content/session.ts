// Session client: everything between this frame and the bridge, always through the worker (sendBg).
//  - top frame: opens/releases the session, runs the single poll loop (only while the page is visible: a hidden page expects
//    no events and the worker is told so; becoming visible does one immediate catch-up poll), relays poll events to children;
//  - every frame: prefetches suggestions for ALL eligible elements in batches (<= 80 per request) so the Tab handler
//    decides synchronously from a cache, and re-requests only new/changed elements afterwards.
// Nothing typed by the user is ever put in a request here (ElementIn carries has_value only; see scan.ts).

import type { BgError, ConnectionState } from "../shared/messages";
import type {
  FeedbackRequest, FeedbackResponse, PageReport, PollEvent, RegenerateRequest, ResearchSnapshot, SessionCreateResponse,
  Suggestion, SuggestRequest, SuggestResponse, TrackRequest, TrackResponse,
} from "../shared/protocol";
import type { AssistField, PageFlags } from "./state";
import { sendBg, sleep } from "./util";

export const BATCH_SIZE = 80;
const POLL_WAIT_MS = 20000;

export interface SuggestContext {
  documentId: string;
  frameUrl: string;
  flags: PageFlags;
}

export interface SuggestBatch {
  fields: AssistField[];
  response: SuggestResponse;
  /** the knowledge version we believed in when the request was sent: older answers are never kept */
  sentKv: string;
}

export interface SessionHost {
  frameKey: string;
  onBatch(batch: SuggestBatch): void;
  onEvent(event: PollEvent): void;
  /** the worker tells us the connection is not usable (no bridge, revoked, ...) */
  onConnection(connection: ConnectionState): void;
  /** the server forgot the session (restart/expiry): re-open and resync */
  onExpired(): void;
  onOrphaned(): void;
  onRequestFailed(fields: AssistField[]): void;
}

interface Job {
  fields: AssistField[];
  mode: SuggestRequest["mode"];
  focusNodeId: number | null;
  ctx: SuggestContext;
  attempts: number;
}

export type Failure = { code: string; detail: string; connection?: ConnectionState };

export class SessionClient {
  sessionId: string | null = null;
  knowledgeVersion = "";
  cursor = 0;
  private jobs: Job[] = [];
  private running = false;
  private polling = false;
  private epoch = 0;
  /** the controller wants events for this session (it opened one and the connection is usable) */
  private wanted = false;
  /** the page is visible: hidden pages never poll */
  private visible = typeof document === "undefined" || document.visibilityState !== "hidden";
  /** every (re)start of the loop gets a number: a loop whose number is stale drops its answer WITHOUT moving the cursor */
  private generation = 0;
  stats = { polls: 0, catchUps: 0 };

  constructor(private readonly host: SessionHost) {}

  // ---- session lifecycle (top frame) -------------------------------------------------------------------------------------

  async open(report: PageReport, tabKey: string): Promise<SessionCreateResponse | Failure> {
    const res = await sendBg<"session:open">({ t: "session:open", report, tabKey });
    if (!res.ok) return this.failure(res.error);
    this.adopt(res.data.session_id, res.data.knowledge_version, res.data.cursor);
    return res.data;
  }

  /** Child frames adopt the session the top frame opened. */
  adopt(sessionId: string, knowledgeVersion: string, cursor = 0): void {
    if (this.sessionId !== sessionId) {
      this.epoch++;
      this.jobs = [];
    }
    this.sessionId = sessionId;
    this.knowledgeVersion = knowledgeVersion;
    this.cursor = cursor;
  }

  setKnowledge(version: string): void {
    this.knowledgeVersion = version;
  }

  async release(): Promise<void> {
    const sid = this.sessionId;
    this.stopPolling();
    this.sessionId = null;
    this.jobs = [];
    this.epoch++;
    if (sid) await sendBg<"session:release">({ t: "session:release", sessionId: sid }, 5000);
  }

  /** Forget the session locally without telling the server (it is already gone). */
  reset(): void {
    this.stopPolling();
    this.sessionId = null;
    this.jobs = [];
    this.epoch++;
  }

  private failure(error: BgError): Failure {
    if (error.connection) this.host.onConnection(error.connection);
    if (error.code === "orphaned") this.host.onOrphaned();
    return { code: error.code, detail: error.detail, connection: error.connection };
  }

  // ---- suggestions -------------------------------------------------------------------------------------------------------

  /** Queue fields for suggestion; batches are sent sequentially, DOM order, the first batch with the requested mode. */
  request(fields: AssistField[], mode: SuggestRequest["mode"], ctx: SuggestContext, focusNodeId: number | null = null): void {
    if (!this.sessionId || !fields.length) return;
    const pending = new Set(this.jobs.flatMap((j) => j.fields.map((f) => `${f.key}:${f.digest}`)));
    const fresh = fields.filter((f) => !pending.has(`${f.key}:${f.digest}`));
    if (!fresh.length) return;
    for (let i = 0; i < fresh.length; i += BATCH_SIZE) {
      this.jobs.push({ fields: fresh.slice(i, i + BATCH_SIZE), mode: i === 0 ? mode : mode === "scan" ? "refresh" : mode, focusNodeId, ctx, attempts: 0 });
    }
    void this.pump();
  }

  /** Move a field's pending request to the front of the queue (the user just focused it). */
  prioritize(field: AssistField): void {
    const idx = this.jobs.findIndex((j) => j.fields.some((f) => f.key === field.key));
    if (idx > 0) this.jobs.unshift(...this.jobs.splice(idx, 1));
  }

  private async pump(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (this.jobs.length && this.sessionId) {
        const job = this.jobs[0];
        const epoch = this.epoch;
        const sid = this.sessionId;
        const sentKv = this.knowledgeVersion;
        const request: SuggestRequest = {
          document_id: job.ctx.documentId,
          frame_url: job.ctx.frameUrl,
          frame_key: this.host.frameKey,
          // `position` = where the control sits in the document (the server orders a partial request by it: a refresh may name the
          // follow-up before its parent). The scanner numbers every control; a field that predates that numbering falls back to its order.
          elements: job.fields.map((f) => (f.element.position == null ? { ...f.element, position: f.order } : f.element)),
          focus_node_id: job.focusNodeId,
          mode: job.mode,
          challenge: job.ctx.flags.challenge,
          mfa: job.ctx.flags.mfa,
          login: job.ctx.flags.login,
        };
        const res = await sendBg<"suggest">({ t: "suggest", sessionId: sid, request });
        const index = this.jobs.indexOf(job);
        if (epoch !== this.epoch || index < 0) continue; // session replaced while waiting
        if (res.ok) {
          this.jobs.splice(index, 1);
          // the knowledge version is owned by the controller (events, adopt); a late answer must never move it backwards
          try {
            this.host.onBatch({ fields: job.fields, response: res.data, sentKv });
          } catch {
            /* a page that makes processing one answer fail must not stop the answers queued behind it */
          }
          continue;
        }
        const code = res.error.code;
        if (res.error.connection) this.host.onConnection(res.error.connection);
        if (code === "orphaned") {
          this.host.onOrphaned();
          this.jobs = [];
          break;
        }
        if (code === "not_found" || code === "expired") {
          this.jobs = [];
          this.host.onExpired();
          break;
        }
        const transient = code === "rate_limited" || code === "transport" || code === "no_response" || code === "unavailable" || code === "internal";
        if (transient && job.attempts < 2) {
          job.attempts++;
          await sleep(code === "rate_limited" ? 2000 : 1200 * job.attempts);
          continue;
        }
        this.jobs.splice(index, 1);
        try {
          this.host.onRequestFailed(job.fields);
        } catch {
          /* idem */
        }
      }
    } finally {
      this.running = false;
    }
  }

  // ---- long poll (top frame only) --------------------------------------------------------------------------------------

  startPolling(): void {
    this.wanted = true;
    if (this.polling || !this.sessionId || !this.visible) return;
    this.begin(false);
  }

  stopPolling(): void {
    this.wanted = false;
    this.polling = false;
    this.generation++;
  }

  /** The page was hidden / shown. Hidden: stop expecting events (the in-flight answer is dropped, its events are fetched later). */
  setVisible(visible: boolean): void {
    if (visible === this.visible) return;
    this.visible = visible;
    if (!visible) {
      this.polling = false;
      this.generation++;
      return;
    }
    if (this.wanted && this.sessionId && !this.polling) this.begin(true);
  }

  private begin(catchUp: boolean): void {
    this.polling = true;
    void this.loop(this.epoch, ++this.generation, catchUp);
  }

  private async loop(epoch: number, generation: number, catchUp: boolean): Promise<void> {
    let backoff = 1000;
    let first = true;
    const alive = (sid: string): boolean =>
      this.polling && this.visible && generation === this.generation && epoch === this.epoch && sid === this.sessionId;
    while (this.polling && this.visible && this.sessionId && epoch === this.epoch && generation === this.generation) {
      const started = Date.now();
      const sid = this.sessionId;
      // the first poll after the page came back to the foreground returns at once with whatever was missed
      const waitMs = first && catchUp ? 0 : POLL_WAIT_MS;
      if (waitMs === 0) this.stats.catchUps++;
      first = false;
      this.stats.polls++;
      const res = await sendBg<"poll">({ t: "poll", sessionId: sid, cursor: this.cursor, waitMs }, waitMs + 12000);
      if (!alive(sid)) return;
      if (res.ok) {
        backoff = 1000;
        this.cursor = res.data.cursor;
        for (const event of res.data.events ?? []) {
          try {
            this.host.onEvent(event);
          } catch {
            /* one bad event never stops the loop */
          }
        }
        // a long poll that returns at once with nothing is a worker/bridge hiccup, not an answer: never spin (the catch-up poll is meant to)
        if (waitMs > 0 && !(res.data.events ?? []).length && Date.now() - started < 150) await sleep(400);
        continue;
      }
      const code = res.error.code;
      if (res.error.connection) this.host.onConnection(res.error.connection);
      if (code === "orphaned") {
        this.host.onOrphaned();
        this.polling = false;
        return;
      }
      if (code === "not_found" || code === "expired") {
        this.polling = false;
        this.host.onExpired();
        return;
      }
      await sleep(backoff);
      backoff = Math.min(backoff * 2, 15000);
    }
    if (generation === this.generation) this.polling = false;
  }

  // ---- user-driven calls -----------------------------------------------------------------------------------------------

  async regenerate(request: RegenerateRequest): Promise<Suggestion | Failure> {
    if (!this.sessionId) return { code: "no_session", detail: "No active session." };
    const res = await sendBg<"regenerate">({ t: "regenerate", sessionId: this.sessionId, request });
    return res.ok ? res.data : this.failure(res.error);
  }

  async feedback(request: FeedbackRequest): Promise<FeedbackResponse | Failure> {
    if (!this.sessionId) return { code: "no_session", detail: "No active session." };
    const res = await sendBg<"feedback">({ t: "feedback", sessionId: this.sessionId, request });
    return res.ok ? res.data : this.failure(res.error);
  }

  async track(request: TrackRequest): Promise<TrackResponse | Failure> {
    if (!this.sessionId) return { code: "no_session", detail: "No active session." };
    const res = await sendBg<"track">({ t: "track", sessionId: this.sessionId, request });
    return res.ok ? res.data : this.failure(res.error);
  }

  async research(kind: "company" | "site" | "refresh", value = ""): Promise<ResearchSnapshot | Failure> {
    if (!this.sessionId) return { code: "no_session", detail: "No active session." };
    const sid = this.sessionId;
    const res = kind === "company"
      ? await sendBg<"research:company">({ t: "research:company", sessionId: sid, company: value })
      : kind === "site"
        ? await sendBg<"research:site">({ t: "research:site", sessionId: sid, domain: value })
        : await sendBg<"research:refresh">({ t: "research:refresh", sessionId: sid });
    return res.ok ? (res.data as ResearchSnapshot) : this.failure(res.error);
  }
}

export function isFailure(value: unknown): value is Failure {
  return !!value && typeof value === "object" && "code" in value && "detail" in value && !("session_id" in value);
}
