// A fake Meridian bridge on 127.0.0.1:<port 0> that follows docs/EXTENSION_DESIGN.md section 4/5 closely enough to
// exercise the worker: hello + proof (computed with node:crypto, independent of the WebCrypto code under test),
// pairing, bearer auth, sessions, long poll, errors. Every request is recorded in `log`.

import { createHash, createHmac, randomBytes } from "node:crypto";
import { createServer } from "node:http";

export const EXT_ORIGIN = "chrome-extension://ophngbnbiekjhemmabbpildjedfphlbc";

export function referenceProof(secret, nonce, port) {
  const key = createHash("sha256").update(secret, "utf8").digest();
  return createHmac("sha256", key).update(`meridian-bridge-v1|${nonce}|${port}`, "utf8").digest("base64url");
}

/** What a real Meridian says to a client it revoked: same key as the active proof, its own message prefix. */
export function referenceRevokedProof(secret, nonce, port) {
  const key = createHash("sha256").update(secret, "utf8").digest();
  return createHmac("sha256", key).update(`meridian-bridge-v1-revoked|${nonce}|${port}`, "utf8").digest("base64url");
}

export class FakeBridge {
  /**
   * @param {{latestDigest?: string, proof?: "valid"|"invalid"|"absent", appVersion?: string, latest?: string, app?: string, helloPort?: number|null,
   *   helloDelayMs?: number, manyPoll?: boolean, pairDelayMs?: number, shareSessions?: boolean, helloBody?: (body: object) => object}} [options]
   *   manyPoll=false answers 404 for POST /v1/poll like a Meridian from before the multiplexed poll.
   */
  constructor(options = {}) {
    this.options = { proof: "valid", appVersion: "1.4.0", latest: "0.1.0", app: "Meridian", helloPort: null, helloDelayMs: 0, manyPoll: true, pairDelayMs: 0, ...options };
    this.concurrent = 0; // requests being served right now (hello included): the worker must keep this <= 4
    this.maxConcurrent = 0;
    this.disconnects = [];
    this.sessionEvents = new Map(); // session id -> events (cursor = index)
    this.log = [];
    this.clients = new Map();
    this.pairings = new Map();
    this.startCount = 0;
    this.restoring = false;
    this.disabled = false;
    this.settings = {
      enabled: true, language: "auto", auto_advance: true, skip_filled: true, tab_inserts_protected: false, save_corrections: "ask",
      background_research: true, background_drafts: true, web_lookup: true, hint_on_ats: true, draft_budget_usd: null, always_on_sites: [],
    };
    this.sessions = new Map();
    this.events = [];
    this.waiters = [];
    this.port = 0;
    this.server = createServer((req, res) => this.handle(req, res));
  }

  async start(port = 0) {
    await new Promise((resolve) => this.server.listen(port, "127.0.0.1", resolve));
    this.port = this.server.address().port;
    return this.port;
  }

  async stop() {
    for (const waiter of this.waiters) waiter();
    this.server.closeAllConnections?.();
    await new Promise((resolve) => this.server.close(resolve));
  }

  issueClient() {
    const clientId = crypto.randomUUID();
    const secret = randomBytes(32).toString("base64url");
    this.clients.set(clientId, { secret, revoked: false });
    return { clientId, secret, token: `mrd1.${clientId}.${secret}` };
  }

  revoke(clientId) {
    this.clients.get(clientId).revoked = true;
  }

  approve(pairingId) {
    const pairing = this.pairings.get(pairingId);
    const issued = this.issueClient();
    pairing.state = "approved";
    pairing.token = issued.token;
    pairing.clientId = issued.clientId;
    return issued;
  }

  deny(pairingId) {
    this.pairings.get(pairingId).state = "denied";
  }

  /** Adds an event to one session, or to every session that exists now; wakes every waiting poll. */
  pushEvent(event, sessionId) {
    this.events.push(event);
    for (const id of sessionId ? [sessionId] : this.sessions.keys()) {
      if (!this.sessionEvents.has(id)) this.sessionEvents.set(id, []);
      this.sessionEvents.get(id).push(event);
    }
    for (const waiter of this.waiters.splice(0)) waiter();
  }

  eventsFor(sessionId) {
    return this.sessionEvents.get(sessionId) ?? [];
  }

  authorized(req) {
    // test knobs: a listener that proves itself in hello and still refuses the bearer (true: always, a number: that many times)
    if (this.rejectBearer === true) return false;
    if (typeof this.rejectBearer === "number" && this.rejectBearer > 0) {
      this.rejectBearer -= 1;
      return false;
    }
    const header = req.headers.authorization ?? "";
    const match = /^Bearer mrd1\.([0-9a-f-]{36})\.([A-Za-z0-9_-]+)$/.exec(header);
    const client = match ? this.clients.get(match[1]) : null;
    return Boolean(client && !client.revoked && client.secret === match[2]);
  }

  async readBody(req) {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const text = Buffer.concat(chunks).toString("utf8");
    return text ? JSON.parse(text) : null;
  }

  send(res, status, body) {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  }

  error(res, status, code, detail) {
    this.send(res, status, { detail, code });
  }

  async handle(req, res) {
    const url = new URL(req.url, "http://127.0.0.1");
    const entry = { at: Date.now(), method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), auth: req.headers.authorization ?? null, origin: req.headers.origin ?? null, version: req.headers["x-meridian-version"] ?? null, body: null };
    this.log.push(entry);
    this.concurrent += 1;
    this.maxConcurrent = Math.max(this.maxConcurrent, this.concurrent);
    entry.concurrentAtStart = this.concurrent;
    let counted = true;
    const done = () => {
      if (counted) this.concurrent -= 1;
      counted = false;
    };
    res.on("close", done);
    try {
      entry.body = ["POST", "PATCH", "PUT"].includes(req.method) ? await this.readBody(req) : null;
      await this.route(req, res, url, entry);
    } catch (error) {
      this.error(res, 500, "internal", String(error?.message ?? error));
    }
  }

  async route(req, res, url, entry) {
    const path = url.pathname;
    if (this.restoring) return this.error(res, 503, "restoring", "Restoring a backup");
    if (path === "/v1/hello") {
      if (this.options.helloDelayMs) await new Promise((resolve) => setTimeout(resolve, this.options.helloDelayMs));
      return this.hello(res, url);
    }
    if (this.disabled) return this.error(res, 403, "disabled", "The browser assistant is turned off");
    if (path === "/v1/pair/start" && req.method === "POST") return this.pairStart(req, res);
    const pair = /^\/v1\/pair\/([A-Za-z0-9_-]+)(\/cancel)?$/.exec(path);
    if (pair) return this.pairRoute(req, res, pair[1], Boolean(pair[2]));
    if (!this.authorized(req)) return this.error(res, 401, "unauthorized", "Invalid or revoked credential");
    if (path === "/v1/disconnect" && req.method === "POST") {
      const clientId = /^Bearer mrd1\.([0-9a-f-]{36})\./.exec(req.headers.authorization ?? "")?.[1];
      this.clients.get(clientId).revoked = true;
      this.disconnects.push(clientId);
      return this.send(res, 200, { disconnected: true });
    }
    if (path === "/v1/poll" && req.method === "POST") {
      if (!this.options.manyPoll) return this.error(res, 404, "not_found", "Not Found");
      return this.pollMany(req, res, entry);
    }
    if (path === "/v1/status") {
      return this.send(res, 200, {
        protocol: 1, app_version: this.options.appVersion, latest_extension_version: this.options.latest, language: "auto",
        profile: { verified_facts: 12, has_cv: true, display_name: "Test" }, generation: { available: false, tested: false }, settings: this.settings,
      });
    }
    if (path === "/v1/settings" && req.method === "PATCH") {
      Object.assign(this.settings, entry.body);
      return this.send(res, 200, this.settings);
    }
    if (path === "/v1/sessions" && req.method === "POST") {
      // shareSessions: like the real bridge, one session per page of a browser, so two tabs on the same page get the SAME id
      this.byUrl = this.byUrl ?? new Map();
      const known = this.options.shareSessions ? this.byUrl.get(entry.body?.page?.url) : undefined;
      const id = known && this.sessions.has(known) ? known : `s${this.sessions.size + 1}x${randomBytes(4).toString("hex")}`;
      if (!this.sessions.has(id)) {
        this.sessions.set(id, { page: entry.body.page });
        this.sessionEvents.set(id, []);
        if (entry.body?.page?.url) this.byUrl.set(entry.body.page.url, id);
      }
      return this.send(res, 200, { session_id: id, protocol: 1, knowledge_version: "k1", job: { company: "Acme", title: "Engineer", location: "", job_id: null, application_id: null, tracked: "none" }, research: null, generation: { available: false, tested: false }, settings: this.settings, cursor: 0 });
    }
    const session = /^\/v1\/sessions\/([^/]+)(?:\/(.+))?$/.exec(path);
    if (session) return this.sessionRoute(req, res, session[1], session[2] ?? "", entry);
    if (path === "/v1/slow") {
      await new Promise((resolve) => setTimeout(resolve, Number(url.searchParams.get("ms")) || 400));
      return this.send(res, 200, {});
    }
    if (path === "/v1/documents") return this.send(res, 200, []);
    const doc = /^\/v1\/documents\/([^/]+)\/content$/.exec(path);
    if (doc) return this.send(res, 200, { id: doc[1], filename: "cv.pdf", mime: "application/pdf", size: 3, sha256: "x", data_b64: "AAAA" });
    return this.error(res, 404, "not_found", "No such route");
  }

  /** Waits for a pushed event, the timeout, or the client hanging up (an aborted long poll). */
  sleepUntilEvent(wait, req) {
    return new Promise((resolve) => {
      const finish = () => {
        clearTimeout(timer);
        const index = this.waiters.indexOf(finish);
        if (index >= 0) this.waiters.splice(index, 1);
        resolve();
      };
      const timer = setTimeout(finish, wait);
      this.waiters.push(finish);
      req.on("close", finish);
    });
  }

  /**
   * POST /v1/poll {sessions:[{session_id, cursor}], wait_ms}: ONE wait for every listed page (B's contract: at most 16
   * pages, each once). Answers {sessions:[{session_id, status, cursor, events}]} in request order, as soon as ANY page has
   * events; status is ok | expired | unknown | released | superseded.
   */
  async pollMany(req, res, entry) {
    const { sessions = [], wait_ms: wait = 0 } = entry.body ?? {};
    if (sessions.length < 1 || sessions.length > 16 || new Set(sessions.map((item) => item.session_id)).size !== sessions.length) {
      return this.error(res, 422, "invalid", "List each page once, at most 16");
    }
    this.pollSuperseded = this.pollSuperseded ?? new Set();
    const build = () => sessions.map(({ session_id: id, cursor = 0 }) => {
      if (this.expired?.has(id)) return { session_id: id, status: "expired", cursor, events: [] };
      if (this.released?.has(id)) return { session_id: id, status: "released", cursor, events: [] };
      if (this.pollSuperseded.has(id)) return { session_id: id, status: "superseded", cursor, events: [] };
      if (!this.sessions.has(id)) return { session_id: id, status: "unknown", cursor, events: [] };
      return { session_id: id, status: "ok", cursor: this.eventsFor(id).length, events: this.eventsFor(id).slice(cursor) };
    });
    const ready = () => build().some((item) => item.events.length || item.status !== "ok");
    if (!ready() && wait > 0) await this.sleepUntilEvent(wait, req);
    return this.send(res, 200, { sessions: build() });
  }

  hello(res, url) {
    const clientId = url.searchParams.get("client");
    const nonce = url.searchParams.get("nonce");
    const body = {
      app: this.options.app, bridge: 1, app_version: this.options.appVersion, latest_extension_version: this.options.latest,
      port: this.options.helloPort ?? this.port, proof: null, revoked: false, revoked_proof: null,
      latest_extension_digest: this.options.latestDigest ?? "",
    };
    const client = clientId ? this.clients.get(clientId) : null;
    if (client && !client.revoked && nonce && this.options.proof !== "absent") {
      body.proof = this.options.proof === "invalid" ? referenceProof("not-the-secret", nonce, this.port) : referenceProof(client.secret, nonce, this.port);
    } else if (client && client.revoked && nonce && this.options.proof !== "absent") {
      // like Meridian itself: a revoked client is TOLD so, with a proof only the holder of the secret can check
      body.revoked = true;
      body.revoked_proof = referenceRevokedProof(this.options.proof === "invalid" ? "not-the-secret" : client.secret, nonce, this.port);
    }
    if (this.options.helloBody) return this.send(res, 200, this.options.helloBody(body)); // a listener with its own idea of the hello shape
    this.afterHello?.(client); // test hook: what happens right after a hello was answered (a takeover, a revocation)
    return this.send(res, 200, body);
  }

  pairStart(req, res) {
    const origin = req.headers.origin ?? "";
    if (this.options.requireOrigin && !/^chrome-extension:\/\/[A-Za-z0-9-]{8,64}$/.test(origin)) return this.error(res, 403, "origin", "Pairing must come from the Meridian extension");
    this.startCount += 1;
    if (this.startCount > 5) return this.error(res, 429, "rate_limited", "Too many connection attempts");
    const id = randomBytes(18).toString("base64url");
    this.pairings.set(id, { state: "pending", origin });
    this.lastPairingId = id;
    return this.send(res, 200, { pairing_id: id, code: "482913", expires_in: 120, poll_after_ms: 50 });
  }

  async pairRoute(req, res, id, cancel) {
    const pairing = this.pairings.get(id);
    if (!pairing) return this.error(res, 404, "not_found", "This connection request does not exist");
    if (cancel) {
      if (pairing.state === "pending") pairing.state = "expired";
      return this.send(res, 200, { state: pairing.state });
    }
    let body;
    if (pairing.state === "approved") {
      pairing.state = "claimed";
      body = { state: "approved", token: pairing.token, client_id: pairing.clientId };
    } else {
      body = { state: pairing.state };
    }
    // the state was decided now; only the ANSWER is slow (lets a test start a second pairing while this one is in flight)
    if (this.options.pairDelayMs) await new Promise((resolve) => setTimeout(resolve, this.options.pairDelayMs));
    return this.send(res, 200, body);
  }

  async sessionRoute(req, res, id, rest, entry) {
    if (!this.sessions.has(id)) return this.error(res, 404, "not_found", "Unknown session");
    if (req.method === "DELETE" && rest === "") {
      this.sessions.delete(id);
      return this.send(res, 200, {});
    }
    if (rest === "poll") {
      const { cursor = 0, wait_ms: wait = 0 } = entry.body ?? {};
      const pending = () => this.eventsFor(id).slice(cursor);
      if (!pending().length && wait > 0) await this.sleepUntilEvent(wait, req);
      return this.send(res, 200, { cursor: this.eventsFor(id).length, events: pending() });
    }
    return this.send(res, 200, { ok: true, route: rest, body: entry.body });
  }
}

/** A port that nothing listens on (bound and released). */
export async function closedPort() {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}
