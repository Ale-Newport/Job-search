import assert from "node:assert/strict";
import { createServer } from "node:http";
import { afterEach, test } from "node:test";
import {
  BridgeNetworkError, BridgeRequestTooLarge, HELLO_RESERVE, MAX_IN_FLIGHT, MAX_REQUEST_BYTES, PROOF_MAX_AGE_MS, bridgeFetch, gateStats, markProven,
  probeHello, resetGates, setProofMaxAgeMs, setQueueWaitMs,
} from "../../src/shared/bridge-client.ts";
import { bridgeCall } from "../../src/shared/connection.ts";
import { world } from "./helpers.mjs";

let current = null;
const servers = [];
afterEach(async () => {
  setQueueWaitMs(15_000);
  setProofMaxAgeMs(1000);
  resetGates();
  for (const server of servers.splice(0)) await new Promise((resolve) => { server.closeAllConnections?.(); server.close(resolve); });
  await current?.close();
  current = null;
});
const setup = async (options) => (current = await world(options));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test("at most four requests (plus one hello probe on its own lane) are ever in flight to one bridge port, however many are asked for", async () => {
  const w = await setup({ paired: {} });
  const calls = Array.from({ length: 14 }, () => bridgeCall("/v1/slow"));
  await sleep(120);
  assert.equal(gateStats(w.bridge.port).active, MAX_IN_FLIGHT);
  assert.ok(gateStats(w.bridge.port).queued >= 9);
  await Promise.all(calls);
  assert.ok(w.bridge.maxConcurrent <= MAX_IN_FLIGHT + HELLO_RESERVE, `the bridge saw ${w.bridge.maxConcurrent} at once`);
  assert.ok(gateStats(w.bridge.port).peak <= MAX_IN_FLIGHT, "never more than four requests");
  assert.equal(gateStats(w.bridge.port).active, 0);
  assert.equal(gateStats(w.bridge.port).queued, 0);
  assert.equal(w.bridge.log.filter((entry) => entry.path === "/v1/slow").length, 14, "every request was served, none dropped");
});

test("the connection check has a lane of its own: it is answered at once even when every request slot is busy", async () => {
  const w = await setup({ paired: {} });
  const calls = Array.from({ length: 10 }, () => bridgeCall("/v1/slow", { timeoutMs: 5000 }));
  await sleep(80);
  const probe = await probeHello(w.bridge.port, null, 2000, "0.1.0");
  assert.equal(probe.kind, "meridian");
  const order = w.bridge.log.map((entry) => entry.path);
  const hello = order.lastIndexOf("/v1/hello");
  const slowBefore = order.slice(0, hello).filter((path) => path === "/v1/slow").length;
  // first come first served would put the hello behind all ten calls; it needs no request slot at all
  assert.ok(slowBefore <= MAX_IN_FLIGHT, `${slowBefore} of 10 slow calls started before the hello`);
  assert.ok(gateStats(w.bridge.port).active <= MAX_IN_FLIGHT);
  await Promise.all(calls);
});

test("a request that cannot get a slot in time fails like a timeout, and one cancelled while queued never goes out", async () => {
  const w = await setup({ paired: {} });
  setQueueWaitMs(150);
  // these tests are about the transport, not about how credentials are earned (that is connection.test.mjs)
  const creds = markProven({ port: w.bridge.port, clientId: w.issued.clientId, token: w.issued.token });
  const busy = Array.from({ length: MAX_IN_FLIGHT }, () => bridgeFetch(w.bridge.port, "/v1/slow", creds, { timeoutMs: 3000 }));
  await sleep(60);
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/status", creds), (error) => error instanceof BridgeNetworkError && error.timedOut);
  setQueueWaitMs(15_000);
  const controller = new AbortController();
  const queued = bridgeFetch(w.bridge.port, "/v1/status", creds, { signal: controller.signal });
  await sleep(30);
  controller.abort();
  await assert.rejects(queued, (error) => error instanceof BridgeNetworkError && error.aborted);
  await Promise.all(busy);
  assert.equal(w.bridge.log.filter((entry) => entry.path === "/v1/status").length, 0, "neither queued request was ever sent");
});

test("a request cancelled while running stops and says it was cancelled, not that Meridian failed", async () => {
  const w = await setup({ paired: {} });
  const creds = markProven({ port: w.bridge.port, clientId: w.issued.clientId, token: w.issued.token });
  const controller = new AbortController();
  const running = bridgeFetch(w.bridge.port, "/v1/slow", creds, { signal: controller.signal });
  await sleep(50);
  controller.abort();
  await assert.rejects(running, (error) => error instanceof BridgeNetworkError && error.aborted && !error.timedOut);
  assert.equal(gateStats(w.bridge.port).active, 0, "its slot was given back");
});

test("a request bigger than the bridge's limit is refused before it is sent, counting bytes not characters", async () => {
  const w = await setup({ paired: {} });
  const creds = markProven({ port: w.bridge.port, clientId: w.issued.clientId, token: w.issued.token });
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/sessions", creds, { method: "POST", body: { x: "a".repeat(MAX_REQUEST_BYTES) } }), BridgeRequestTooLarge);
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/sessions", creds, { method: "POST", body: { x: "é".repeat(MAX_REQUEST_BYTES / 2) } }), BridgeRequestTooLarge, "two bytes each");
  assert.equal(w.bridge.log.length, 0);
  const small = await bridgeFetch(w.bridge.port, "/v1/settings", creds, { method: "PATCH", body: { skip_filled: false } });
  assert.equal(small.skip_filled, false);
});

/** A listener that answers with an endless body, counting what it managed to write before the client hung up. */
async function endlessServer() {
  const state = { written: 0, closed: false };
  const server = createServer((_req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    const chunk = "x".repeat(16 * 1024);
    const timer = setInterval(() => {
      if (res.destroyed || state.written > 64 * 1024 * 1024) return clearInterval(timer);
      state.written += chunk.length;
      res.write(chunk);
    }, 1);
    res.on("close", () => {
      state.closed = true;
      clearInterval(timer);
    });
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { port: server.address().port, state };
}

test("an endless answer from a listener that is not Meridian is cut off at the limit instead of filling the worker's memory", async () => {
  await setup({ bridges: [] });
  const { port, state } = await endlessServer();
  const probe = await probeHello(port, null, 3000, "0.1.0");
  assert.equal(probe.kind, "down", "treated as unusable");
  await sleep(100);
  assert.equal(state.closed, true, "the connection was dropped");
  assert.ok(state.written < 2 * 1024 * 1024, `the listener got to write ${state.written} bytes`);
});

test("an endless answer to a credentialed call is cut off at the response ceiling", async () => {
  const w = await setup({ paired: {} });
  const { port, state } = await endlessServer();
  const creds = markProven({ port, clientId: w.issued.clientId, token: w.issued.token });
  await assert.rejects(bridgeFetch(port, "/v1/status", creds, { maxChars: 256 * 1024, timeoutMs: 3000 }), (error) => error instanceof BridgeNetworkError && !error.timedOut);
  assert.ok(state.written < 4 * 1024 * 1024, `${state.written} bytes written`);
});

test("a declared length above the ceiling is refused without reading the body", async () => {
  await setup({ bridges: [] });
  const server = createServer((_req, res) => {
    res.writeHead(200, { "content-type": "application/json", "content-length": String(500 * 1024 * 1024) });
    res.write("{");
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const started = Date.now();
  const probe = await probeHello(server.address().port, null, 3000, "0.1.0");
  assert.equal(probe.kind, "down");
  assert.ok(Date.now() - started < 1500);
});

test("a request that had to wait for a connection renews its proof before the token is attached: the credential never goes out behind an old proof", async () => {
  assert.equal(PROOF_MAX_AGE_MS, 1000);
  setProofMaxAgeMs(150);
  const w = await setup({ paired: {} });
  const busy = Array.from({ length: MAX_IN_FLIGHT }, () => bridgeCall("/v1/slow?ms=700", { timeoutMs: 5000 })); // every request slot is taken for 0.7 s
  await sleep(80);
  const status = await bridgeCall("/v1/status"); // proved at once, then queued for a slot: ~600 ms later its proof is old
  assert.equal(status.app_version, "1.4.0");
  await Promise.all(busy);
  const log = w.bridge.log;
  const at = log.findIndex((entry) => entry.path === "/v1/status");
  const hello = log.slice(0, at).reverse().find((entry) => entry.path === "/v1/hello");
  assert.ok(hello, "a hello precedes it");
  assert.ok(log[at].at - hello.at <= 150 + 150, `the proof behind the credential was ${log[at].at - hello.at} ms old when it was sent (limit 150)`);
  assert.ok(log.filter((entry) => entry.path === "/v1/hello").length >= 3, "the waiting request proved itself again (a hello after the busy calls were sent)");
  assert.ok(gateStats(w.bridge.port).peak <= MAX_IN_FLIGHT);
});

test("a proof that is too old and cannot be renewed means nothing is sent, and the slot is given back", async () => {
  setProofMaxAgeMs(50);
  const w = await setup({ paired: {} });
  const creds = markProven({ port: w.bridge.port, clientId: w.issued.clientId, token: w.issued.token });
  await sleep(120);
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/status", creds), (error) => error instanceof BridgeNetworkError && /out of date/.test(error.message));
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/status", creds, { reprove: async () => creds }), (error) => /out of date/.test(error.message), "renewing with the same old proof is no renewal");
  const fresh = markProven({ ...creds });
  assert.equal((await bridgeFetch(w.bridge.port, "/v1/status", fresh)).app_version, "1.4.0");
  assert.equal(w.bridge.log.filter((entry) => entry.auth).length, 1, "the two refused attempts sent nothing");
  assert.equal(gateStats(w.bridge.port).active, 0, "no slot is leaked");
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/status", creds, { reprove: async () => ({ ...fresh, port: fresh.port + 1 }) }), (error) => /out of date/.test(error.message));
});

test("a POST names its origin (Safari sends `Origin: null` when the referrer policy is no-referrer) while a GET sends no referrer at all", async () => {
  const w = await setup({ paired: {} });
  const seen = [];
  const real = globalThis.fetch;
  globalThis.fetch = (url, init) => { seen.push({ method: init?.method, policy: init?.referrerPolicy }); return real(url, init); };
  try {
    await bridgeCall("/v1/status").catch(() => undefined);
    await bridgeCall("/v1/feedback", { method: "POST", body: { x: 1 } }).catch(() => undefined);
  } finally {
    globalThis.fetch = real;
  }
  const get = seen.find((entry) => entry.method === "GET");
  const post = seen.find((entry) => entry.method === "POST");
  assert.equal(get?.policy, "no-referrer");
  assert.equal(post?.policy, "origin");
  assert.ok(w);
});
