// MEDIUM-1 (final security review): re-pairing against a listener nobody could vouch for must never replace a working
// credential. The repro: pair with the real Meridian, quit it, start a program on another bridge port that answers hello with
// the right shape but no proof and approves a pairing at once. Before: the worker said "revoked", one pair + poll later the
// impostor's credential had replaced the real one and credentialed requests went to the impostor.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cancelPairing, disconnectBrowser, pollPairing, setRemoteRevokeMs, setWatchInterval, startPairing } from "../../src/background/pairing.ts";
import { handleMessage } from "../../src/background/router.ts";
import {
  FALLBACK_KEEP_MS, bridgeCall, proveConnection, resolveConnection, setOwnDigest,
} from "../../src/shared/connection.ts";
import { computeProof, verifyProof, verifyRevokedProof } from "../../src/shared/hello-proof.ts";
import { isOutdated } from "../../src/shared/version.ts";
import { FakeBridge, referenceRevokedProof } from "./fake-bridge.mjs";
import { EXT_ID } from "./chrome-mock.mjs";
import { setTabState } from "../../src/shared/tabs.ts";
import { authedRequests, world } from "./helpers.mjs";

let current = null;
const extra = [];
afterEach(async () => {
  setWatchInterval(1500);
  setRemoteRevokeMs(6000);
  setOwnDigest("");
  for (const server of extra.splice(0)) await server.stop().catch(() => undefined);
  await current?.close();
  current = null;
});
const options = () => ({ id: EXT_ID, tab: { id: 900 }, frameId: 0, url: `chrome-extension://${EXT_ID}/options.html` });
const current_ = async (w) => (await w.chrome.storage.local.get("meridian.connection"))["meridian.connection"];
const previous = async (w) => (await w.chrome.storage.local.get("meridian.connection.previous"))["meridian.connection.previous"];
const posts = (server) => server.log.filter((entry) => entry.method === "POST");

/** The repro: paired with the real app on port 0, the real app is gone, an impostor (knows no client) answers on port 1. */
async function quitRealWithImpostorOn(bridges = [{}, {}]) {
  current = await world({ bridges, paired: { on: 0 } });
  setWatchInterval(0);
  const [real, impostor] = current.servers;
  const realPort = real.port;
  const clients = real.clients;
  const realToken = current.issued.token;
  await real.stop();
  return { w: current, real, impostor, realPort, clients, realToken };
}

/** The real Meridian starts again on its old port with the database it had. */
async function realReturns({ realPort, clients }) {
  const again = new FakeBridge();
  again.clients = clients;
  await again.start(realPort);
  extra.push(again);
  return again;
}

test("a stored credential and a listener that proves nothing is an UNKNOWN listener, not a revoked browser", async () => {
  const { w, impostor } = await quitRealWithImpostorOn();
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "unknown_listener", "silence is not a revocation");
  assert.equal(state.port, impostor.port);
  assert.match(state.detail, /did not recognise this browser/);
  assert.equal(creds, null);
  assert.equal(authedRequests(impostor).length, 0, "the credential was never sent to it");
  assert.equal(posts(impostor).length, 0, "and it was not asked to pair");
  assert.equal((await w.chrome.storage.session.get("meridian.revoked"))["meridian.revoked"], undefined, "nothing was recorded as revoked");
  assert.ok(await current_(w), "the credential is still stored");
});

test("pairing with an unknown listener needs an explicit confirmation, and without it nothing reaches the listener", async () => {
  const { w, impostor, realToken } = await quitRealWithImpostorOn();
  await assert.rejects(startPairing(), (error) => {
    assert.equal(error.detail.code, "confirm_required");
    assert.equal(error.detail.connection.phase, "unknown_listener");
    return true;
  });
  const viaMessage = await handleMessage({ t: "pair:start" }, options());
  assert.equal(viaMessage.ok, false);
  assert.equal(viaMessage.error.code, "confirm_required");
  assert.equal(viaMessage.error.connection.phase, "unknown_listener", "the panel gets the state it needs to explain");
  const falsyConfirmations = [{ confirmUnknown: false }, { confirmUnknown: "yes" }, { confirmUnknown: 1 }];
  for (const message of falsyConfirmations) {
    const answer = await handleMessage({ t: "pair:start", ...message }, options());
    assert.equal(answer.ok, false, JSON.stringify(message));
    assert.equal(answer.error.code, "confirm_required");
  }
  assert.equal(posts(impostor).length, 0);
  assert.equal((await current_(w)).token, realToken);
  const confirmed = await handleMessage({ t: "pair:start", confirmUnknown: true }, options());
  assert.equal(confirmed.ok, true);
  assert.equal(posts(impostor).length, 1);
  await cancelPairing(confirmed.data.pairingId);
});

test("the credential of the real app survives a confirmed pairing with the impostor, as the fallback", async () => {
  const { w, impostor, realToken } = await quitRealWithImpostorOn();
  const { pairingId } = await startPairing({ confirmUnknown: true });
  const issued = impostor.approve(pairingId);
  const done = await pollPairing(pairingId);
  assert.equal(done.state, "approved");
  assert.equal((await current_(w)).token, issued.token, "the new pairing is the one in use");
  const kept = await previous(w);
  assert.equal(kept.token, realToken, "the old credential was NOT replaced: it is kept as the fallback");
  assert.equal(kept.port, current.ports[0]);
  assert.ok(Date.parse(kept.replacedAt) > 0);
  // a second pairing never pushes the (older, longer known) fallback out
  const again = await startPairing({ confirmUnknown: true }).catch(() => null);
  if (again) {
    const second = impostor.approve(again.pairingId);
    await pollPairing(again.pairingId);
    assert.equal((await current_(w)).token, second.token);
    assert.equal((await previous(w)).token, realToken, "the fallback is still the first credential");
  }
});

test("when the real app comes back its credential is recognised and the impostor's is dropped", async () => {
  const ctx = await quitRealWithImpostorOn();
  const { w, impostor, realToken } = ctx;
  const { pairingId } = await startPairing({ confirmUnknown: true });
  const issued = impostor.approve(pairingId);
  await pollPairing(pairingId);
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready", "the impostor proves ITS secret, so the browser works with it meanwhile");
  const real = await realReturns(ctx);
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.port, real.port, "the app that knows the old credential wins");
  assert.equal(creds.token, realToken);
  assert.equal((await current_(w)).token, realToken);
  assert.equal(await previous(w), undefined, "the fallback slot is empty again");
  assert.notEqual((await current_(w)).token, issued.token);
  const before = impostor.log.length;
  await bridgeCall("/v1/status");
  assert.equal(authedRequests(impostor).filter((entry) => entry.auth.endsWith(issued.token)).length, authedRequests(impostor).length, "only what was sent while it was the one in use");
  assert.equal(impostor.log.slice(before).filter((entry) => entry.auth !== null).length, 0, "no credentialed request reaches the impostor any more");
  assert.ok(authedRequests(real).every((entry) => entry.auth === `Bearer ${realToken}`));
});

test("credentialed requests to a newly paired listener go only behind a proof made for each of them", async () => {
  const { w, impostor } = await quitRealWithImpostorOn();
  const { pairingId } = await startPairing({ confirmUnknown: true });
  const issued = impostor.approve(pairingId);
  await pollPairing(pairingId);
  await bridgeCall("/v1/status");
  await bridgeCall("/v1/status");
  const log = impostor.log;
  const sent = log.map((entry, index) => ({ entry, index })).filter(({ entry }) => entry.auth === `Bearer ${issued.token}` && entry.path !== "/v1/hello");
  assert.ok(sent.length >= 2);
  for (const { index } of sent) {
    const hello = log.slice(0, index).reverse().find((entry) => entry.path === "/v1/hello" && entry.query.client === issued.clientId);
    assert.ok(hello && hello.query.nonce, `request #${index} was preceded by a hello that asked for a proof of this very client`);
  }
  assert.equal(new Set(log.filter((entry) => entry.query.nonce).map((entry) => entry.query.nonce)).size, log.filter((entry) => entry.query.nonce).length, "every nonce was used once");
  void w;
});

test("a real Meridian that says the credential was REVOKED is believed, and then pairing needs no extra confirmation", async () => {
  const w = await world({ bridges: [{}], paired: {} });
  current = w;
  setWatchInterval(0);
  w.bridge.revoke(w.issued.clientId);
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "revoked", "authenticated: the app proved it knows the secret and says it revoked it");
  assert.equal(creds, null);
  assert.equal(authedRequests(w.bridge).length, 0);
  const started = await startPairing();
  assert.match(started.code, /^\d{6}$/, "no confirmation is asked: this is the real app");
  await cancelPairing(started.pairingId);
});

test("a listener cannot fake a revocation: the flag without a valid proof is a failed proof, never 'revoked'", async () => {
  const silent = (body) => ({ ...body, proof: null, revoked: false, revoked_proof: null }); // an impostor knows no secret: nothing valid to add
  const bogus = (body) => ({ ...silent(body), revoked: true, revoked_proof: "A".repeat(43) });
  const flagOnly = (body) => ({ ...silent(body), revoked: true });
  const proofOnly = (body) => ({ ...silent(body), revoked_proof: "A".repeat(43) });
  for (const helloBody of [bogus, flagOnly, proofOnly]) {
    const w = await world({ bridges: [{ helloBody }], paired: {} });
    current = w;
    const { state, creds } = await proveConnection({ force: true });
    assert.equal(state.phase, "proof_failed", String(helloBody));
    assert.equal(creds, null);
    assert.equal(authedRequests(w.bridge).length, 0);
    assert.ok(await current_(w), "and the credential is not touched");
    await w.close();
    current = null;
  }
});

test("a revoked proof made with another secret, another nonce or another port does not verify", async () => {
  const w = await world({ bridges: [{}], paired: {} });
  current = w;
  const secret = w.issued.secret;
  w.bridge.revoke(w.issued.clientId);
  const hello = async () => {
    const nonce = "AAECAwQFBgcICQoLDA0ODw";
    const body = await (await fetch(`http://127.0.0.1:${w.bridge.port}/v1/hello?client=${w.issued.clientId}&nonce=${nonce}`)).json();
    return { body, nonce };
  };
  const { body, nonce } = await hello();
  assert.equal(body.revoked, true);
  assert.equal(await verifyRevokedProof(secret, nonce, w.bridge.port, body.revoked_proof), true);
  assert.equal(await verifyRevokedProof(secret, nonce, w.bridge.port + 1, body.revoked_proof), false);
  assert.equal(await verifyRevokedProof(secret, "BBECAwQFBgcICQoLDA0ODw", w.bridge.port, body.revoked_proof), false);
  assert.equal(await verifyRevokedProof("another-secret-value-1234567890", nonce, w.bridge.port, body.revoked_proof), false);
  // domain separation: a proof of an ACTIVE client is never a revoked proof, and a revoked proof is never an active one
  const active = await computeProof(secret, nonce, w.bridge.port);
  assert.equal(await verifyRevokedProof(secret, nonce, w.bridge.port, active), false);
  assert.equal(await verifyProof(secret, nonce, w.bridge.port, body.revoked_proof), false);
  assert.equal(body.revoked_proof, referenceRevokedProof(secret, nonce, w.bridge.port));
});

test("a wrong proof is a failed proof (refused outright), never a revocation and never an invitation to pair", async () => {
  const w = await world({ bridges: [{ proof: "invalid" }], paired: {} });
  current = w;
  setWatchInterval(0);
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "proof_failed");
  await assert.rejects(startPairing({ confirmUnknown: true }), (error) => error.detail.code === "proof_failed");
  assert.equal(posts(w.bridge).length, 0);
});

test("two listeners that both fail to prove anything: the confirmation does not make pairing choose between them", async () => {
  const { impostor } = await quitRealWithImpostorOn([{}, {}, {}]);
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "unknown_listener");
  assert.equal(state.meridianPorts.length, 2);
  await assert.rejects(startPairing({ confirmUnknown: true }), (error) => error.detail.code === "ambiguous");
  assert.equal(posts(impostor).length, 0);
});

test("a Meridian that never knew this credential (reinstalled, restored backup) is unknown too; confirming pairs with it and keeps the old one aside", async () => {
  const w = await world({ bridges: [{}], paired: {} });
  current = w;
  setWatchInterval(0);
  const oldToken = w.issued.token;
  w.bridge.clients.clear(); // the app's database no longer holds the client
  assert.equal((await resolveConnection({ force: true })).state.phase, "unknown_listener");
  const { pairingId } = await startPairing({ confirmUnknown: true });
  const issued = w.bridge.approve(pairingId);
  assert.equal((await pollPairing(pairingId)).connection.phase, "ready");
  assert.equal((await current_(w)).token, issued.token);
  assert.equal((await previous(w)).token, oldToken);
  // the app does not know the old credential and never will: it is not a reason to keep probing for ever
  await w.chrome.storage.local.set({ "meridian.connection.previous": { ...(await previous(w)), replacedAt: new Date(Date.now() - FALLBACK_KEEP_MS - 60_000).toISOString() } });
  assert.equal((await proveConnection({ force: true })).state.phase, "ready");
  assert.equal(await previous(w), undefined, "a month later it is dropped");
});

test("a fallback that the real app says is revoked is dropped", async () => {
  const ctx = await quitRealWithImpostorOn();
  const { w, impostor } = ctx;
  const { pairingId } = await startPairing({ confirmUnknown: true });
  impostor.approve(pairingId);
  await pollPairing(pairingId);
  const real = await realReturns(ctx);
  real.clients.get(w.issued.clientId).revoked = true;
  await resolveConnection({ force: true });
  assert.equal(await previous(w), undefined);
});

test("when the same app knows both credentials the one in use stays and the fallback is simply dropped", async () => {
  const w = await world({ bridges: [{}], paired: {} });
  current = w;
  const older = w.issued;
  const newer = w.bridge.issueClient();
  const base = { port: w.bridge.port, clientName: "Google Chrome", pairedAt: "2026-01-01T00:00:00.000Z" };
  await w.chrome.storage.local.set({
    "meridian.connection": { ...base, clientId: newer.clientId, token: newer.token },
    "meridian.connection.previous": { ...base, clientId: older.clientId, token: older.token, replacedAt: new Date().toISOString() },
  });
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(creds.token, newer.token);
  assert.equal((await current_(w)).token, newer.token, "no swap: both belong to the app that answers");
  assert.equal(await previous(w), undefined);
});

test("when only the fallback is left it becomes the credential again", async () => {
  const ctx = await quitRealWithImpostorOn();
  const { w, impostor, realToken } = ctx;
  const { pairingId } = await startPairing({ confirmUnknown: true });
  impostor.approve(pairingId);
  await pollPairing(pairingId);
  await w.chrome.storage.local.remove("meridian.connection");
  await realReturns(ctx);
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(creds.token, realToken);
  assert.equal(await previous(w), undefined);
  assert.equal((await current_(w)).token, realToken);
  assert.equal((await current_(w)).replacedAt, undefined);
});

test("Disconnect forgets the fallback too: nothing of a pairing is left behind", async () => {
  const ctx = await quitRealWithImpostorOn();
  const { w, impostor } = ctx;
  setRemoteRevokeMs(300);
  const { pairingId } = await startPairing({ confirmUnknown: true });
  impostor.approve(pairingId);
  await pollPairing(pairingId);
  assert.ok(await previous(w));
  await disconnectBrowser();
  assert.equal(await current_(w), undefined);
  assert.equal(await previous(w), undefined);
});

test("without a stored credential nothing changes: the first pairing is the user's call and needs no confirmation (documented limit)", async () => {
  const w = await world({ bridges: [{}] });
  current = w;
  setWatchInterval(0);
  assert.equal((await resolveConnection({ force: true })).state.phase, "unpaired");
  const started = await startPairing();
  assert.match(started.code, /^\d{6}$/);
  await cancelPairing(started.pairingId);
});

test("the in-page panel is told 'could not prove it is Meridian' (it has no way to confirm anything); the options page sees the real state", async () => {
  const { w } = await quitRealWithImpostorOn();
  w.chrome.__addTab("https://boards.greenhouse.io/acme/jobs/1", { id: 1 });
  await setTabState(1, { active: true, since: Date.now(), origin: "https://boards.greenhouse.io", auto: false, sessions: [] });
  const page = { id: EXT_ID, tab: { id: 1, url: "https://boards.greenhouse.io/acme/jobs/1" }, frameId: 0, url: "https://boards.greenhouse.io/acme/jobs/1" };
  assert.equal((await handleMessage({ t: "conn:state", refresh: true }, options())).data.phase, "unknown_listener", "the options page is told the truth");
  const viaPage = await handleMessage({ t: "conn:state", refresh: true }, page);
  assert.equal(viaPage.data.phase, "proof_failed");
  const ctx = await handleMessage({ t: "ctx" }, page);
  assert.equal(ctx.data.connection.phase, "proof_failed");
  const refused = await handleMessage({ t: "pair:start", confirmUnknown: true }, page);
  assert.equal(refused.ok, false, "only the options page can give the confirmation: what a content script sends is not one");
  assert.equal(refused.error.code, "confirm_required");
  assert.equal(refused.error.connection.phase, "proof_failed");
  assert.equal(posts(current.servers[1]).length, 0);
  const pushed = [];
  w.chrome.tabs.sendMessage = async (tabId, message) => { pushed.push(message); };
  const { onConnectionState } = await import("../../src/background/activation.ts");
  await onConnectionState({ phase: "unknown_listener", port: 1 });
  assert.deepEqual(pushed.map((message) => message.connection.phase), ["proof_failed"]);
});

// ---- M1: a rebuild with the same version number ------------------------------------------------------------------------

const A = "a".repeat(64);
const B = "b".repeat(64);

test("isOutdated: a newer version, or the same version with a different digest on both sides; never when something is unknown", () => {
  assert.equal(isOutdated("0.2.0", "", "0.1.0", ""), true);
  assert.equal(isOutdated("0.1.0", A, "0.1.0", B), true, "a rebuild that kept the version number");
  assert.equal(isOutdated("0.1.0", A, "0.1.0", A), false);
  assert.equal(isOutdated("0.1.0", A, "0.1.0", ""), false, "this build carries no stamp (Safari, tests): nothing to compare");
  assert.equal(isOutdated("0.1.0", "", "0.1.0", B), false, "an app without a digest (older Meridian)");
  assert.equal(isOutdated("", A, "0.1.0", B), false, "no bundled extension: unknown");
  assert.equal(isOutdated("0.1.0", A, "0.2.0", B), false, "this build is newer than the app's: never an update");
});

test("hello's digest makes a rebuilt extension with the same version outdated, and the same build is not", async () => {
  const w = await world({ bridges: [{ latest: "0.1.0", latestDigest: A }] });
  current = w;
  setOwnDigest(B);
  let state = (await resolveConnection({ force: true })).state;
  assert.equal(state.latestExtensionVersion, "0.1.0");
  assert.equal(state.extensionOutdated, true);
  setOwnDigest(A);
  state = (await resolveConnection({ force: true })).state;
  assert.equal(state.extensionOutdated, false);
  setOwnDigest("");
  state = (await resolveConnection({ force: true })).state;
  assert.equal(state.extensionOutdated, false);
});

test("a malformed digest in hello is ignored rather than trusted", async () => {
  const w = await world({ bridges: [{ latest: "0.1.0", latestDigest: "NOT-A-DIGEST" }] });
  current = w;
  setOwnDigest(B);
  assert.equal((await resolveConnection({ force: true })).state.extensionOutdated, false);
});
