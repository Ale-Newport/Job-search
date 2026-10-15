// The first pairing is unauthenticated by nature (no secret exists yet). What the extension can still do without an app
// change: ignore listeners whose hello is not Meridian's, pair only when exactly one listener answers like Meridian, show
// the user which port answered, and say that Meridian itself must open the code window.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cancelPairing, pollPairing, setWatchInterval, startPairing } from "../../src/background/pairing.ts";
import { isRecognisedHello, probeHello } from "../../src/shared/bridge-client.ts";
import { proveConnection, resolveConnection } from "../../src/shared/connection.ts";
import { handleMessage } from "../../src/background/router.ts";
import { EXT_ID } from "./chrome-mock.mjs";
import { world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  setWatchInterval(1500);
  await current?.close();
  current = null;
});
const setup = async (options) => {
  current = await world(options);
  setWatchInterval(0);
  return current;
};
const sloppy = (body) => ({ app: "Meridian", bridge: 1, port: body.port }); // echoes the name, the protocol and the port: no versions
const posts = (server) => server.log.filter((entry) => entry.method === "POST");
const options = () => ({ id: EXT_ID, tab: { id: 900 }, frameId: 0, url: `chrome-extension://${EXT_ID}/options.html` });

test("what counts as Meridian's hello: every field present and well formed, on the port it was asked on", () => {
  const good = { app: "Meridian", bridge: 1, app_version: "0.1.0", latest_extension_version: "0.0.0", port: 47615, proof: null };
  assert.equal(isRecognisedHello(good, 47615), true);
  assert.equal(isRecognisedHello({ ...good, app_version: "1.2.3-beta.4+sha" }, 47615), true);
  assert.equal(isRecognisedHello({ ...good, app_version: "2" }, 47615), true, "a bare number is a version too");
  assert.equal(isRecognisedHello({ ...good, latest_extension_version: "" }, 47615), true, "no bundled extension is a normal answer");
  for (const broken of [
    null, "Meridian", {}, { app: "Meridian" }, { ...good, app: "meridian" }, { ...good, bridge: "1" }, { ...good, bridge: 0 }, { ...good, bridge: 1.5 },
    { ...good, app_version: "" }, { ...good, app_version: "latest" }, { ...good, app_version: "1.2.3 <script>" }, { ...good, app_version: 7 },
    { ...good, latest_extension_version: undefined }, { ...good, latest_extension_version: "x".repeat(60) }, { ...good, latest_extension_version: null }, { ...good, port: 47616 }, { ...good, port: "47615" },
  ]) assert.equal(isRecognisedHello(broken, 47615), false, JSON.stringify(broken));
});

test("a listener that only echoes the name is not Meridian: it is never chosen, never asked to pair", async () => {
  const w = await setup({ bridges: [{ helloBody: sloppy }, {}] });
  const [impostor, real] = w.servers;
  const probe = await probeHello(impostor.port, null, 2000, "0.1.0");
  assert.equal(probe.kind, "foreign");
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "unpaired");
  assert.equal(state.port, real.port, "pairing goes to the listener whose hello looks like Meridian's, though it is on the later port");
  assert.deepEqual(state.meridianPorts, [real.port]);
  const started = await startPairing();
  assert.match(started.code, /^\d{6}$/);
  assert.equal(posts(impostor).length, 0, "the impostor was never asked to pair");
  assert.equal(posts(real).length, 1);
  assert.equal(impostor.log.every((entry) => entry.path === "/v1/hello"), true);
});

test("two listeners that both answer like Meridian: pairing is refused, nothing is posted to either, and the user is told which ports", async () => {
  const w = await setup({ bridges: [{}, {}] });
  const [a, b] = w.servers;
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "unpaired");
  assert.deepEqual(state.meridianPorts, [a.port, b.port]);
  assert.equal(state.port, undefined, "no port is picked");
  assert.match(state.detail, new RegExp(`${a.port}, ${b.port}`));
  await assert.rejects(startPairing(), (error) => {
    assert.equal(error.detail.code, "ambiguous");
    assert.deepEqual(error.detail.connection.meridianPorts, [a.port, b.port]);
    assert.match(error.detail.detail, /More than one program/);
    return true;
  });
  assert.equal(posts(a).length + posts(b).length, 0, "no pairing request reached either");
  const viaMessage = await handleMessage({ t: "pair:start" }, options());
  assert.equal(viaMessage.ok, false);
  assert.equal(viaMessage.error.code, "ambiguous");
  assert.equal(viaMessage.error.connection.meridianPorts.length, 2, "the page/panel gets the state it needs to explain");
  assert.equal(posts(a).length + posts(b).length, 0);
});

test("the same refusal applies to a browser whose credential was revoked (it cannot tell the listeners apart either)", async () => {
  const w = await setup({ bridges: [{}, {}], paired: {} });
  w.servers[0].revoke(w.issued.clientId);
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "revoked");
  assert.equal(state.meridianPorts.length, 2);
  await assert.rejects(startPairing(), (error) => error.detail.code === "ambiguous");
});

test("one listener that answers like Meridian next to others that do not: pairing proceeds with the one", async () => {
  const w = await setup({ bridges: [{ app: "SomethingElse" }, { helloBody: sloppy }, {}] });
  const real = w.servers[2];
  const { state } = await resolveConnection({ force: true });
  assert.equal(state.phase, "unpaired");
  assert.deepEqual(state.meridianPorts, [real.port]);
  const started = await startPairing();
  assert.ok(started.pairingId);
  assert.equal(posts(real).length, 1);
  assert.equal(posts(w.servers[0]).length + posts(w.servers[1]).length, 0);
  assert.equal((await resolveConnection({ force: true })).state.phase, "pairing");
  assert.equal((await resolveConnection()).state.port, real.port, "the pairing state names the port that answered (shown to the user)");
  await cancelPairing(started.pairingId);
});

test("with a credential, a listener that PROVES itself wins over any number of look-alikes: ready, not ambiguous", async () => {
  const w = await setup({ bridges: [{ proof: "invalid" }, {}], paired: { on: 1 } });
  const { state, creds } = await proveConnection({ force: true });
  assert.equal(state.phase, "ready");
  assert.equal(state.port, w.servers[1].port);
  assert.equal(state.meridianPorts, undefined, "the list is only for the unpaired case");
  assert.ok(creds);
  assert.equal(w.servers[0].log.filter((entry) => entry.auth).length, 0);
});

test("a pairing already under way is not disturbed when a second program starts answering", async () => {
  const w = await setup({ bridges: [{}] });
  const started = await startPairing();
  const { FakeBridge } = await import("./fake-bridge.mjs");
  const late = new FakeBridge();
  await late.start();
  try {
    await w.chrome.storage.local.set({ "meridian.e2e.ports": [...w.ports, late.port] });
    const { state } = await resolveConnection({ force: true });
    assert.equal(state.phase, "pairing");
    assert.equal(state.pairing.pairingId, started.pairingId);
    assert.equal(state.port, w.bridge.port);
    const issued = w.bridge.approve(started.pairingId);
    assert.equal((await pollPairing(started.pairingId)).state, "approved");
    assert.ok(issued.token);
  } finally {
    await late.stop();
  }
});

test("diagnostics list the ports that answered like Meridian (no secret in them)", async () => {
  const w = await setup({ bridges: [{}, {}] });
  const answer = await handleMessage({ t: "diagnostics" }, options());
  assert.equal(answer.ok, true);
  assert.deepEqual(answer.data.meridianPorts, w.ports);
  assert.equal(JSON.stringify(answer).includes("mrd1"), false);
});

test("diagnostics of a connected browser list the port that proved itself", async () => {
  const w = await setup({ paired: {} });
  const answer = await handleMessage({ t: "diagnostics" }, options());
  assert.equal(answer.data.phase, "ready");
  assert.deepEqual(answer.data.meridianPorts, [w.bridge.port]);
});
