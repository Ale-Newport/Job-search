import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import {
  PAIRING_ALARM, cancelPairing, disconnectBrowser, disconnectLocally, pollPairing, pollPendingPairing, setRemoteRevokeMs, setWatchInterval,
  startPairing,
} from "../../src/background/pairing.ts";
import { bridgeCall, resolveConnection } from "../../src/shared/connection.ts";
import { authedRequests, world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  setWatchInterval(1500);
  setRemoteRevokeMs(6000);
  await current?.close();
  current = null;
});
const setup = async (options) => {
  current = await world(options);
  setWatchInterval(0); // the background watcher stays off unless a test asks for it
  return current;
};
const stored = async (w) => (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
const pairingRecord = async (w) => (await w.chrome.storage.session.get("meridian.pairing"))["meridian.pairing"];

test("start: shows the code, remembers the pairing, arms the alarm and reports the pairing phase", async () => {
  const w = await setup();
  const started = await startPairing();
  assert.equal(started.code, "482913");
  assert.ok(started.expiresAt > Date.now() + 100_000);
  const post = w.bridge.log.find((entry) => entry.path === "/v1/pair/start");
  assert.equal(post.auth, null, "pairing never carries a credential");
  assert.equal(post.body.client_name, "Google Chrome");
  assert.equal(post.body.extension_version, "0.1.0");
  assert.equal(post.version, "0.1.0");
  assert.equal((await pairingRecord(w)).pairingId, started.pairingId);
  assert.ok(w.chrome.__log.alarms.has(PAIRING_ALARM));
  const { state } = await resolveConnection();
  assert.equal(state.phase, "pairing");
  assert.equal(state.pairing.code, "482913");
});

test("approve: the token is stored before anyone is answered, then the connection is ready", async () => {
  const w = await setup();
  const { pairingId } = await startPairing();
  const pending = await pollPairing(pairingId);
  assert.equal(pending.state, "pending");
  assert.equal(pending.connection.phase, "pairing");
  const issued = w.bridge.approve(pairingId);
  const done = await pollPairing(pairingId);
  assert.equal(done.state, "approved");
  assert.equal(done.connection.phase, "ready");
  const connection = await stored(w);
  assert.equal(connection.token, issued.token);
  assert.equal(connection.clientId, issued.clientId);
  assert.equal(connection.port, w.bridge.port);
  assert.equal(await pairingRecord(w), undefined);
  assert.equal(w.chrome.__log.alarms.has(PAIRING_ALARM), false);
  assert.ok(authedRequests(w.bridge).every((entry) => entry.path !== "/v1/pair/start"));
  const again = await pollPairing(pairingId);
  assert.equal(again.state, "claimed", "a late poll finds the pairing already complete");
  assert.equal(again.connection.phase, "ready");
});

test("concurrent polls share one request, so the one-time token cannot be lost to a race", async () => {
  const w = await setup();
  const { pairingId } = await startPairing();
  w.bridge.approve(pairingId);
  const before = w.bridge.log.filter((entry) => entry.path.startsWith("/v1/pair/") && entry.method === "GET").length;
  const results = await Promise.all([pollPairing(pairingId), pollPairing(pairingId), pollPairing(pairingId)]);
  assert.deepEqual(results.map((result) => result.state), ["approved", "approved", "approved"]);
  assert.equal(w.bridge.log.filter((entry) => entry.path.startsWith("/v1/pair/") && entry.method === "GET").length - before, 1);
  assert.ok(await stored(w));
});

test("the background watcher completes a pairing nobody is polling", async () => {
  const w = await setup();
  setWatchInterval(20);
  const { pairingId } = await startPairing();
  w.bridge.approve(pairingId);
  const deadline = Date.now() + 3000;
  while (!(await stored(w)) && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 20));
  assert.ok(await stored(w), "token stored by the watcher");
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
});

test("the alarm backup polls a pending pairing and retires itself when none is left", async () => {
  const w = await setup();
  const { pairingId } = await startPairing();
  w.bridge.approve(pairingId);
  await pollPendingPairing();
  assert.ok(await stored(w));
  w.chrome.__log.alarms.set(PAIRING_ALARM, {});
  await pollPendingPairing();
  assert.equal(w.chrome.__log.alarms.has(PAIRING_ALARM), false);
});

test("denied and expired end the pairing and say so", async () => {
  const w = await setup();
  const first = await startPairing();
  w.bridge.deny(first.pairingId);
  const denied = await pollPairing(first.pairingId);
  assert.equal(denied.state, "denied");
  assert.equal(denied.connection.phase, "unpaired");
  assert.equal(await pairingRecord(w), undefined);
  const second = await startPairing();
  w.bridge.pairings.delete(second.pairingId); // the app restarted: it forgot the request
  const gone = await pollPairing(second.pairingId);
  assert.equal(gone.state, "expired");
  assert.equal(gone.connection.phase, "unpaired");
});

test("a malformed token from the server is refused and never stored", async () => {
  const w = await setup();
  const { pairingId } = await startPairing();
  w.bridge.pairings.get(pairingId).state = "approved";
  w.bridge.pairings.get(pairingId).token = "not-a-token";
  const result = await pollPairing(pairingId);
  assert.equal(result.state, "expired");
  assert.equal(await stored(w), undefined);
});

test("cancel tells the app and forgets the code", async () => {
  const w = await setup();
  const { pairingId } = await startPairing();
  const state = await cancelPairing(pairingId);
  assert.equal(state.phase, "unpaired");
  assert.equal(await pairingRecord(w), undefined);
  assert.ok(w.bridge.log.some((entry) => entry.path === `/v1/pair/${pairingId}/cancel` && entry.method === "POST"));
  assert.equal(w.bridge.pairings.get(pairingId).state, "expired");
});

test("errors: no bridge, rate limit, wrong sender", async () => {
  const w = await setup({ bridges: [{ requireOrigin: true }] });
  // Node's fetch sends no Origin, exactly the case the real bridge refuses with `origin`.
  await assert.rejects(startPairing(), (error) => error.detail.code === "origin");
  w.bridge.options.requireOrigin = false;
  for (let i = 0; i < 5; i += 1) await startPairing();
  await assert.rejects(startPairing(), (error) => error.detail.code === "rate_limited" && error.detail.status === 429);
  await w.bridge.stop();
  await assert.rejects(startPairing(), (error) => error.detail.code === "no_bridge" && error.detail.connection.phase === "no_bridge");
});

test("disconnect forgets the credential locally and returns to unpaired", async () => {
  const w = await setup({ paired: {} });
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
  const state = await disconnectLocally();
  assert.equal(state.phase, "unpaired");
  assert.equal(await stored(w), undefined);
});

test("pairing again after a revocation clears the revoked state", async () => {
  const w = await setup({ paired: {} });
  w.bridge.revoke(w.issued.clientId);
  assert.equal((await resolveConnection({ force: true })).state.phase, "revoked");
  const { pairingId } = await startPairing();
  assert.equal((await resolveConnection({ force: true })).state.phase, "pairing");
  w.bridge.approve(pairingId);
  const done = await pollPairing(pairingId);
  assert.equal(done.connection.phase, "ready");
  assert.notEqual((await stored(w)).clientId, w.issued.clientId);
});

// ---- wave 2: disconnect reaches Meridian, and a newer pairing survives an older one's settlement -----------------------

test("disconnect asks Meridian to revoke this browser BEFORE forgetting the credential, so the app does not list a stale browser", async () => {
  const w = await setup({ paired: {} });
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
  const { state, remoteRevoked } = await disconnectBrowser();
  assert.equal(remoteRevoked, true);
  assert.equal(state.phase, "unpaired");
  assert.deepEqual(w.bridge.disconnects, [w.issued.clientId], "exactly this browser's client");
  const call = w.bridge.log.find((entry) => entry.path === "/v1/disconnect");
  assert.equal(call.method, "POST");
  assert.equal(call.auth, `Bearer ${w.issued.token}`, "sent with the credential, which only a verified port ever gets");
  assert.ok(w.bridge.log.findIndex((entry) => entry.path === "/v1/hello") < w.bridge.log.indexOf(call), "after the hello proof");
  assert.equal(w.bridge.clients.get(w.issued.clientId).revoked, true);
  assert.equal(await stored(w), undefined, "and then forgotten here");
});

test("disconnect with Meridian closed still forgets the credential here and says Meridian was not told", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  await w.bridge.stop();
  const { state, remoteRevoked } = await disconnectBrowser();
  assert.equal(remoteRevoked, false);
  assert.equal(await stored(w), undefined);
  assert.equal(state.phase, "no_bridge");
});

test("disconnect never sends the credential to a listener that cannot prove it is Meridian", async () => {
  const w = await setup({ bridges: [{ proof: "invalid" }], paired: {} });
  const { remoteRevoked } = await disconnectBrowser();
  assert.equal(remoteRevoked, false);
  assert.equal(authedRequests(w.bridge).length, 0, "the squatter saw no credential, not even for the revocation");
  assert.equal(await stored(w), undefined, "but it is forgotten here");
});

test("a browser Meridian already dropped counts as disconnected, and a Meridian that hangs does not block forgetting", async () => {
  const w = await setup({ paired: {} });
  w.bridge.revoke(w.issued.clientId);
  const already = await disconnectBrowser();
  assert.equal(already.remoteRevoked, true, "the app does not list it any more: nothing left to ask");
  assert.equal(await stored(w), undefined);
  await w.close();
  const hung = await setup({ paired: {} });
  setRemoteRevokeMs(300);
  const original = hung.bridge.route.bind(hung.bridge);
  hung.bridge.route = (req, res, url, entry) => (url.pathname === "/v1/disconnect" ? new Promise(() => undefined) : original(req, res, url, entry));
  const started = Date.now();
  const result = await disconnectBrowser();
  assert.ok(Date.now() - started < 1500, `waited ${Date.now() - started} ms`);
  assert.equal(result.remoteRevoked, false);
  assert.equal(await stored(hung), undefined);
});

test("disconnect with nothing paired asks nobody", async () => {
  const w = await setup();
  const result = await disconnectBrowser();
  assert.equal(result.remoteRevoked, false);
  assert.equal(w.bridge.log.some((entry) => entry.path === "/v1/disconnect"), false);
});

test("a request that was already in flight when the user disconnected cannot flag the browser as revoked afterwards", async () => {
  const w = await setup({ paired: {} });
  await resolveConnection({ force: true });
  const original = w.bridge.route.bind(w.bridge);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  w.bridge.route = async (req, res, url, entry) => {
    if (url.pathname === "/v1/status") {
      await gate; // held while the user disconnects, then refused as the revoked credential it now is
      return w.bridge.error(res, 401, "unauthorized", "Invalid or revoked credential");
    }
    return original(req, res, url, entry);
  };
  const inFlight = bridgeCall("/v1/status").catch((error) => error);
  await new Promise((resolve) => setTimeout(resolve, 80));
  await disconnectBrowser();
  release();
  const failure = await inFlight;
  assert.equal(failure.detail.code, "unauthorized");
  assert.equal((await resolveConnection({ force: true })).state.phase, "unpaired", "the user's disconnect stays a disconnect, not 'Meridian disconnected this browser'");
});

test("a pairing the user started while an older one was being settled is not wiped by it", async () => {
  const w = await setup({ bridges: [{ pairDelayMs: 250 }] });
  const first = await startPairing();
  w.bridge.approve(first.pairingId);
  const settling = pollPairing(first.pairingId); // the answer to this poll is slow
  await new Promise((resolve) => setTimeout(resolve, 60));
  const second = await startPairing(); // the user pressed Connect again meanwhile
  const result = await settling;
  assert.equal(result.state, "approved");
  const record = await pairingRecord(w);
  assert.equal(record?.pairingId, second.pairingId, "the newer pairing's record survived the older one's settlement");
  assert.ok(w.chrome.__log.alarms.has(PAIRING_ALARM), "and so did its alarm");
  w.bridge.approve(second.pairingId);
  const done = await pollPairing(second.pairingId);
  assert.equal(done.state, "approved", "so its approval can still be claimed");
});
