import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  MIN_RECHECK_MS, backoffMs, bridgeCall, cachedConnection, invalidateConnection, onConnectionChanged, proveConnection,
  resolveConnection, setProbeTimeouts,
} from "../../src/shared/connection.ts";
import { candidatePorts } from "../../src/shared/bridge-client.ts";
import { FakeBridge } from "./fake-bridge.mjs";
import { authedRequests, world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  await current?.close();
  current = null;
});
const setup = async (options) => (current = await world(options));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("candidate ports: stored port first, defaults after, e2e override replaces the defaults", () => {
  assert.deepEqual(candidatePorts([47617]).slice(0, 3), [47617, 47615, 47616]);
  assert.equal(candidatePorts([]).length, 10);
  assert.deepEqual(candidatePorts([5], [7, 8]), [5, 7, 8]);
  assert.deepEqual(candidatePorts([0, 70000, 1.5]), candidatePorts([]));
});

test("no bridge: no_bridge with a growing retry delay; forced checks bypass the backoff", async () => {
  const w = await setup({ bridges: [] });
  await w.chrome.storage.local.set({ "meridian.e2e.ports": [await w.closedPort()] });
  const first = await resolveConnection();
  assert.equal(first.state.phase, "no_bridge");
  assert.equal((await proveConnection()).creds, null, "no listener: nothing to hand a credential to");
  assert.equal(first.state.retryAfterMs, backoffMs(1));
  await sleep(MIN_RECHECK_MS + 20);
  const second = await resolveConnection();
  assert.equal(second.state.phase, "no_bridge");
  assert.ok(second.state.retryAfterMs <= backoffMs(1), "served from the backoff window, no new network probe");
  const forced = await resolveConnection({ force: true });
  assert.equal(forced.state.retryAfterMs, backoffMs(2));
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(backoffMs), [1500, 3000, 6000, 10000, 10000, 10000, 10000]);
});

test("unpaired: Meridian answers, nothing stored; no credential is involved", async () => {
  const w = await setup();
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "unpaired");
  assert.equal(state.port, w.bridge.port);
  assert.equal(state.appVersion, "1.4.0");
  assert.deepEqual(state.meridianPorts, [w.bridge.port]);
  assert.equal((await proveConnection({ force: true })).creds, null);
  assert.equal(authedRequests(w.bridge).length, 0);
  assert.ok(w.bridge.log.every((entry) => entry.query.client === undefined), "no client id either: nothing to prove");
});

test("ready: a verified proof yields credentials; the STATE may be served from the cache, a credential never is", async () => {
  const w = await setup({ paired: {} });
  const first = await proveConnection({ force: true });
  assert.equal(first.state.phase, "ready");
  assert.equal(first.state.clientName, "Google Chrome");
  assert.equal(first.creds.token, w.issued.token);
  assert.ok(w.bridge.log.every((entry) => entry.auth === null), "hello never carries the token");
  const hellos = w.bridge.log.length;
  const again = await resolveConnection();
  assert.equal(again.state.phase, "ready");
  assert.equal("creds" in again, false, "the picture of the connection carries no credential");
  assert.equal(w.bridge.log.length, hellos, "within the freshness window no new hello is made for the state");
  const second = await proveConnection();
  assert.equal(second.creds.token, w.issued.token);
  assert.equal(w.bridge.log.length, hellos + 1, "but a credential needs a hello of its own, cache or no cache");
  await invalidateConnection();
  await resolveConnection();
  assert.ok(w.bridge.log.length > hellos + 1, "after invalidation the proof is verified again");
  assert.equal((await cachedConnection()).phase, "ready");
});

test("a bridge that moved to another port is found and the stored port follows", async () => {
  const w = await setup({ paired: { port: 1 } });
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.port, w.bridge.port);
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"].port, w.bridge.port);
});

test("revoked on the server: no proof is given, the token is never sent, phase revoked", async () => {
  const w = await setup({ paired: {} });
  w.bridge.revoke(w.issued.clientId);
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "revoked");
  assert.equal(creds, null);
  assert.equal(authedRequests(w.bridge).length, 0);
});

test("a listener with a WRONG proof is proof_failed and receives no credential, ever", async () => {
  const w = await setup({ bridges: [{ proof: "invalid" }], paired: {} });
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "proof_failed");
  assert.equal(creds, null);
  await assert.rejects(bridgeCall("/v1/status"), (error) => error.detail.code === "proof_failed" && error.detail.connection.phase === "proof_failed");
  assert.equal(authedRequests(w.bridge).length, 0, "the squatter never saw an Authorization header");
});

test("a squatter on an earlier port does not get the token; the real bridge on a later port does", async () => {
  const w = await setup({ bridges: [{ proof: "invalid" }, {}], paired: { on: 1 } });
  const [squatter, real] = w.servers;
  const status = await bridgeCall("/v1/status");
  assert.equal(status.app_version, "1.4.0");
  assert.equal(authedRequests(squatter).length, 0);
  assert.equal(authedRequests(real).length, 1);
  assert.equal((await cachedConnection()).port, real.port);
});

test("a listener that is not Meridian is ignored", async () => {
  await setup({ bridges: [{ app: "SomethingElse" }] });
  assert.equal((await resolveConnection({ force: true })).state.phase, "no_bridge");
});

test("a listener that claims another port in hello is treated as foreign", async () => {
  await setup({ bridges: [{ helloPort: 9 }] });
  assert.equal((await resolveConnection({ force: true })).state.phase, "no_bridge");
});

test("restoring is mapped from the bridge's 503 on hello", async () => {
  const w = await setup({ paired: {} });
  w.bridge.restoring = true;
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "restoring");
  assert.equal(state.retryAfterMs, backoffMs(1));
});

test("disabled in the app: calls fail with disabled, the phase says so, and it recovers when re-enabled", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  w.bridge.disabled = true;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => {
    assert.equal(error.detail.code, "disabled");
    assert.equal(error.detail.connection.phase, "disabled");
    return true;
  });
  assert.equal((await resolveConnection({ force: true })).state.phase, "disabled");
  w.bridge.disabled = false;
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
  assert.equal((await bridgeCall("/v1/status")).app_version, "1.4.0");
});

test("extensionOutdated is set when the app ships a newer extension", async () => {
  await setup({ bridges: [{ latest: "0.2.0" }], paired: {} });
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.latestExtensionVersion, "0.2.0");
  assert.equal(state.extensionOutdated, true);
});

test("a client revoked in Meridian is told apart BEFORE anything is sent: no proof, no token on the wire, phase revoked", async () => {
  const w = await setup({ paired: {} });
  await bridgeCall("/v1/status");
  w.bridge.revoke(w.issued.clientId);
  const before = authedRequests(w.bridge).length;
  await assert.rejects(bridgeCall("/v1/status"), (error) => {
    assert.equal(error.detail.code, "revoked");
    assert.equal(error.detail.connection.phase, "revoked");
    return true;
  });
  assert.equal(authedRequests(w.bridge).length, before, "the credential was not even offered: the hello came back without a proof");
  assert.equal((await resolveConnection()).state.phase, "revoked");
  assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], "an unproven refusal forgets nothing");
});

test("a network failure re-discovers once and then reports the bridge as gone", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  await w.bridge.stop();
  await assert.rejects(bridgeCall("/v1/status"), (error) => error.detail.code === "no_bridge" && error.detail.connection.phase === "no_bridge");
});

test("requests that must not run twice are not retried", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  let posts = 0;
  const original = w.bridge.route.bind(w.bridge);
  w.bridge.route = async (req, res, url, entry) => {
    if (req.method === "POST") {
      posts += 1;
      return req.socket.destroy();
    }
    return original(req, res, url, entry);
  };
  await assert.rejects(bridgeCall("/v1/sessions/s1x/feedback", { method: "POST", body: {}, retry: false }));
  assert.equal(posts, 1);
});

test("listeners hear about phase changes, and only about changes", async () => {
  const w = await setup({ paired: {} });
  const seen = [];
  const off = onConnectionChanged((state) => seen.push(state.phase));
  await resolveConnection({ force: true });
  await invalidateConnection();
  await resolveConnection();
  w.bridge.revoke(w.issued.clientId);
  await resolveConnection({ force: true });
  off();
  assert.deepEqual(seen, ["ready", "revoked"]);
});

test("a pending pairing shows as the pairing phase with its code", async () => {
  const w = await setup();
  await w.chrome.storage.session.set({
    "meridian.pairing": { pairingId: "p1", code: "482913", expiresAt: Date.now() + 60_000, port: w.bridge.port, startedAt: Date.now() },
  });
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "pairing");
  assert.equal(state.pairing.code, "482913");
});

test("an expired pairing record is dropped", async () => {
  const w = await setup();
  await w.chrome.storage.session.set({
    "meridian.pairing": { pairingId: "p1", code: "482913", expiresAt: Date.now() - 1000, port: w.bridge.port, startedAt: Date.now() - 200_000 },
  });
  assert.equal((await resolveConnection({ force: true })).state.phase, "unpaired");
  assert.equal((await w.chrome.storage.session.get("meridian.pairing"))["meridian.pairing"], undefined);
});

test("a slow answer is reported as the transient `unavailable`, is not retried and does not disturb the connection state", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  await assert.rejects(bridgeCall("/v1/slow", { timeoutMs: 80 }), (error) => {
    assert.equal(error.detail.code, "unavailable");
    assert.equal(error.detail.connection, undefined);
    return true;
  });
  assert.equal(w.bridge.log.filter((entry) => entry.path === "/v1/slow").length, 1);
  assert.equal((await cachedConnection()).phase, "ready");
});

// ---- wave 2: patience, the stored port first, a short proof window ---------------------------------------------------

test("a slow first answer is waited for, not reported as 'Meridian is not running'", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 500 }], paired: {} });
  setProbeTimeouts(150, 2500); // normal patience would give up long before the bridge answers
  try {
    const started = Date.now();
    const { state, creds } = await proveConnection({ force: true });
    assert.equal(state.phase, "ready", "a loaded machine is slow, not absent");
    assert.equal(creds.token, w.issued.token);
    assert.ok(Date.now() - started >= 450);
    assert.equal(w.bridge.log.filter((entry) => entry.path === "/v1/hello").length, 1, "one patient probe, no scan and no retry");
  } finally {
    setProbeTimeouts(2500, 6000);
  }
});

test("recovering from a failure is just as patient as the first look", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 400 }], paired: {} });
  setProbeTimeouts(120, 2500);
  try {
    await w.chrome.storage.session.set({
      "meridian.conn": { state: { phase: "no_bridge" }, at: 0, verifiedAt: null, failures: 3, nextAttemptAt: 0 },
    });
    assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
    // once Meridian is known to be fine, the normal (short) patience applies, with the one patient second look as a safety net
    const known = await w.chrome.storage.session.get("meridian.conn");
    assert.equal(known["meridian.conn"].state.phase, "ready");
  } finally {
    setProbeTimeouts(2500, 6000);
  }
});

test("failures are retried at once and then back off 1.5 s, 3 s, 6 s, 10 s", async () => {
  const w = await setup({ bridges: [] });
  await w.chrome.storage.local.set({ "meridian.e2e.ports": [await w.closedPort()] });
  const seen = [];
  for (let i = 0; i < 5; i++) seen.push((await resolveConnection({ force: true })).state.retryAfterMs);
  assert.deepEqual(seen, [1500, 3000, 6000, 10_000, 10_000]);
});

test("the port where Meridian was last verified is asked ALONE first: nothing else is probed while it proves itself", async () => {
  const w = await setup({ bridges: [{ proof: "invalid" }, {}], paired: { on: 1 } });
  const [other, real] = w.servers;
  await resolveConnection({ force: true });
  await invalidateConnection();
  await resolveConnection();
  assert.equal(other.log.length, 0, "the other listener was never contacted");
  assert.equal(real.log.filter((entry) => entry.path === "/v1/hello").length, 2);
  assert.ok(real.log.every((entry) => entry.query.client && entry.query.nonce), "each probe asked for a proof");
});

test("when that port does not prove itself, every candidate is scanned, and the credential still goes only to a verified one", async () => {
  const w = await setup({ bridges: [{}, { proof: "invalid" }], paired: { on: 0 } });
  const [real, squatter] = w.servers;
  await w.chrome.storage.local.set({
    "meridian.connection": { ...(await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], port: squatter.port },
  });
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.port, real.port, "found behind the squatter's port");
  assert.equal(authedRequests(squatter).length, 0);
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"].port, real.port);
});

test("every proof request carries a fresh nonce, so a recorded proof can never be replayed", async () => {
  const w = await setup({ paired: {} });
  for (let i = 0; i < 12; i++) {
    await invalidateConnection();
    await resolveConnection({ force: true });
  }
  const nonces = w.bridge.log.filter((entry) => entry.path === "/v1/hello").map((entry) => entry.query.nonce);
  assert.equal(nonces.length, 12);
  assert.equal(new Set(nonces).size, 12);
  assert.ok(nonces.every((nonce) => /^[A-Za-z0-9_-]{22}$/.test(nonce)));
});

test("a process that takes over the port the moment Meridian quits gets no token, however soon after the last proof it asks", async () => {
  const w = await setup({ paired: {} });
  const port = w.bridge.port;
  assert.equal((await bridgeCall("/v1/status")).app_version, "1.4.0"); // verified just now: the old 2 s window would still be open
  await w.bridge.stop(); // Meridian quits and something else binds the same port
  const squatter = new FakeBridge({ proof: "invalid" });
  squatter.clients.set(w.issued.clientId, { secret: "the-squatter-has-no-idea-of-the-secret", revoked: false });
  await squatter.start(port);
  try {
    await assert.rejects(bridgeCall("/v1/status"), (error) => error.detail.code === "proof_failed");
    assert.equal(authedRequests(squatter).length, 0, "the squatter never saw an Authorization header");
    assert.ok(squatter.log.some((entry) => entry.path === "/v1/hello"), "it was asked for a proof, which it could not give");
    const stored = (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
    assert.equal(stored.token, w.issued.token, "and its answers cost the credential nothing");
  } finally {
    await squatter.stop();
  }
});

test("a squatter that answers every request 401 cannot make the worker forget the credential", async () => {
  const w = await setup({ paired: {} });
  const port = w.bridge.port;
  await bridgeCall("/v1/status");
  await w.bridge.stop();
  const mute = new FakeBridge({ proof: "absent" }); // answers hello like Meridian, with no proof (nothing it could prove)
  await mute.start(port);
  try {
    for (let i = 0; i < 3; i++) await assert.rejects(bridgeCall("/v1/status"));
    assert.equal(authedRequests(mute).length, 0);
    assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], "still there");
  } finally {
    await mute.stop();
  }
});

test("revoked between the hello and the request: the 401 is re-checked with a fresh proof, finds no proof, forgets nothing and reports revoked", async () => {
  const w = await setup({ paired: {} });
  w.bridge.afterHello = () => w.bridge.revoke(w.issued.clientId); // the app revokes this browser right after answering the hello
  const before = authedRequests(w.bridge).length;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => {
    assert.equal(error.detail.code, "unauthorized");
    assert.equal(error.detail.status, 401);
    assert.equal(error.detail.connection.phase, "revoked");
    return true;
  });
  w.bridge.afterHello = null;
  assert.equal(authedRequests(w.bridge).length - before, 1, "the 401 was judged with hellos only: nothing more was sent");
  assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], "an unproven 401 clears nothing");
  assert.equal((await resolveConnection()).state.phase, "revoked");
});

test("a listener that replaced Meridian between the hello and the request answers 401: the credential is kept and nothing more is sent", async () => {
  const w = await setup({ paired: {} });
  w.bridge.afterHello = () => { // the takeover: from now on this port is a squatter that has no secret and says 401 to everything
    w.bridge.options.proof = "invalid";
    w.bridge.rejectBearer = true;
  };
  const before = authedRequests(w.bridge).length;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => {
    assert.equal(error.detail.code, "unauthorized");
    assert.equal(error.detail.connection.phase, "proof_failed", "reported as what it is, not as a revocation");
    return true;
  });
  assert.equal(authedRequests(w.bridge).length - before, 1, "only the request that had been sent before anything could be known");
  const stored = (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
  assert.equal(stored.token, w.issued.token, "the squatter's 401 did not make the worker forget the credential");
  assert.equal((await w.chrome.storage.session.get("meridian.revoked"))["meridian.revoked"], undefined);
  w.bridge.afterHello = null;
  for (let i = 0; i < 3; i++) await assert.rejects(bridgeCall("/v1/status"), (error) => error.detail.code === "proof_failed");
  assert.equal(authedRequests(w.bridge).length - before, 1, "and later requests never reach it at all");
});

test("a proven listener that refuses the credential twice, between two proofs, is believed: the credential is forgotten and 'revoked' remembered", async () => {
  const w = await setup({ paired: {} });
  w.bridge.rejectBearer = true; // proves itself in hello (it knows the secret) yet refuses the bearer every time
  const before = authedRequests(w.bridge).length;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => {
    assert.equal(error.detail.code, "unauthorized");
    assert.equal(error.detail.connection.phase, "revoked");
    return true;
  });
  assert.equal(authedRequests(w.bridge).length - before, 2, "the request, and ONE confirming request to a listener that had just proved itself");
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], undefined);
  assert.equal((await w.chrome.storage.session.get("meridian.revoked"))["meridian.revoked"], true);
  assert.equal((await resolveConnection()).state.phase, "revoked", "revoked, not unpaired, until the user connects again");
});

test("a refusal is only believed when a proof comes AFTER it too: a listener that stops proving itself in between costs the credential nothing", async () => {
  const w = await setup({ paired: {} });
  w.bridge.rejectBearer = true;
  let hellos = 0;
  w.bridge.afterHello = () => { // hello 1 precedes the request, 2 is the re-check, 3 is the one that must bracket the refusal
    hellos += 1;
    if (hellos === 2) w.bridge.options.proof = "invalid"; // from the next hello on, whatever listens here cannot prove itself
  };
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => error.detail.code === "unauthorized" && error.detail.connection.phase === "proof_failed");
  assert.equal(hellos, 3);
  assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], "unproven at the end: nothing forgotten");
  assert.equal((await w.chrome.storage.session.get("meridian.revoked"))["meridian.revoked"], undefined);
});

test("a credential replaced while a refusal is being judged is left alone: nothing more is sent with either", async () => {
  const w = await setup({ paired: {} });
  w.bridge.rejectBearer = true;
  const replacement = w.bridge.issueClient();
  let hellos = 0;
  w.bridge.afterHello = () => { // the user connects again (a new credential) right after the first hello was answered
    hellos += 1;
    if (hellos === 1) w.chrome.storage.local.data.set("meridian.connection", { port: w.bridge.port, clientId: replacement.clientId, token: replacement.token, clientName: "Google Chrome", pairedAt: "2026-02-01T00:00:00.000Z" });
  };
  const before = authedRequests(w.bridge).length;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => error.detail.code === "unauthorized");
  assert.equal(authedRequests(w.bridge).length - before, 1, "only the original request: the judgement did not spend the NEW credential");
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"].clientId, replacement.clientId, "and did not forget it");
});

test("a refusal that does not repeat is a race, not a revocation: the credential stays", async () => {
  const w = await setup({ paired: {} });
  w.bridge.rejectBearer = 1;
  await assert.rejects(bridgeCall("/v1/status", { retry: false }), (error) => error.detail.code === "unauthorized" && error.detail.connection.phase === "ready");
  assert.ok((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"]);
  assert.equal((await bridgeCall("/v1/status")).app_version, "1.4.0", "and it works again straight away");
});

test("several requests refused at once are judged once: one confirming request, not one each", async () => {
  const w = await setup({ paired: {} });
  w.bridge.rejectBearer = true;
  const before = authedRequests(w.bridge).length;
  const outcomes = await Promise.all([1, 2, 3, 4].map(() => bridgeCall("/v1/status", { retry: false }).then(() => null, (error) => error)));
  for (const error of outcomes) assert.equal(error?.detail.connection?.phase, "revoked", JSON.stringify(error?.detail));
  assert.ok(authedRequests(w.bridge).length - before <= 5, `${authedRequests(w.bridge).length - before} credentialed requests for 4 calls`);
  assert.equal((await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"], undefined);
});

test("a listener that merely claims to be Meridian cannot move the stored port, and never sees the credential", async () => {
  const w = await setup({ bridges: [{}, {}], paired: { on: 0 } });
  const [real, claimant] = w.servers;
  const before = (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
  await real.stop(); // Meridian quits; something else that answers like it (no proof: it never had the secret) is all that is left
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "unknown_listener", "a stored credential that gets no proof is not trusted anywhere, and silence is not a revocation");
  assert.equal(creds, null);
  assert.equal(authedRequests(claimant).length, 0);
  const after = (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
  assert.equal(after.port, before.port, "only a port that PROVED itself is ever written as the stored port");
  assert.equal(after.token, before.token);
});
