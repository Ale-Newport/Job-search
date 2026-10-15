// Docs/EXTENSION_DESIGN.md 4.3 and 8: no credential is ever sent without a hello proof that was made for THAT request.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { afterEach, test } from "node:test";
import { fileURLToPath } from "node:url";
import { BridgeNetworkError, bridgeFetch, markProven } from "../../src/shared/bridge-client.ts";
import { bridgeCall, proveConnection, resolveConnection } from "../../src/shared/connection.ts";
import { authedRequests, world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  await current?.close();
  current = null;
});
const setup = async (options) => (current = await world(options));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const hellos = (server) => server.log.filter((entry) => entry.path === "/v1/hello");

test("every credentialed request is preceded by a hello of its own, with a fresh nonce, however soon after the last one", async () => {
  const w = await setup({ paired: {} });
  for (let i = 0; i < 6; i++) await bridgeCall("/v1/status");
  const sequence = w.bridge.log.map((entry) => (entry.path === "/v1/hello" ? "hello" : entry.auth ? "authed" : "other"));
  assert.deepEqual(sequence, ["hello", "authed", "hello", "authed", "hello", "authed", "hello", "authed", "hello", "authed", "hello", "authed"]);
  assert.equal(new Set(hellos(w.bridge).map((entry) => entry.query.nonce)).size, 6, "six different nonces");
  assert.ok(hellos(w.bridge).every((entry) => entry.auth === null), "a hello never carries the token");
});

test("requests that start together share one hello; one that starts while a hello is in flight never rides its proof", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 150 }], paired: {} });
  const a = bridgeCall("/v1/status"); // hello #1 starts now and takes 150 ms
  await sleep(40);
  const [b, c, d] = [bridgeCall("/v1/status"), bridgeCall("/v1/documents"), bridgeCall("/v1/status")]; // all asked AFTER hello #1 was sent
  await Promise.all([a, b, c, d]);
  const kinds = w.bridge.log.map((entry) => (entry.path === "/v1/hello" ? "hello" : "authed"));
  const helloAt = kinds.map((kind, index) => (kind === "hello" ? index : -1)).filter((index) => index >= 0);
  const authedAt = kinds.map((kind, index) => (kind === "authed" ? index : -1)).filter((index) => index >= 0);
  assert.equal(helloAt.length, 2, "one hello for A, ONE for the three that started together later");
  assert.equal(authedAt.length, 4);
  assert.ok(authedAt[0] > helloAt[0], "A goes out after hello #1");
  assert.ok(authedAt.slice(1).every((index) => index > helloAt[1]), "B, C and D all go out after hello #2: none of them used hello #1's proof");
  assert.notEqual(hellos(w.bridge)[0].query.nonce, hellos(w.bridge)[1].query.nonce);
});

test("a burst costs a bounded number of hellos: one running and one waiting, not one per request", async () => {
  const w = await setup({ bridges: [{ helloDelayMs: 40 }], paired: {} });
  const calls = [];
  for (let i = 0; i < 24; i++) {
    calls.push(bridgeCall("/v1/status"));
    if (i % 6 === 5) await sleep(15);
  }
  await Promise.all(calls);
  assert.equal(authedRequests(w.bridge).length, 24);
  assert.ok(hellos(w.bridge).length <= 6, `${hellos(w.bridge).length} hellos for 24 requests`);
  // and each request still went out after a hello that began after it was asked: no authed request precedes the first hello
  assert.equal(w.bridge.log[0].path, "/v1/hello");
});

test("a state read never hands out a credential, and proveConnection never accepts a cached verification", async () => {
  const w = await setup({ paired: {} });
  const first = await proveConnection({ force: true });
  assert.ok(first.creds);
  const state = await resolveConnection(); // fresh cache: no network
  assert.deepEqual(Object.keys(state), ["state"]);
  const count = hellos(w.bridge).length;
  const second = await proveConnection(); // not forced, and the cache is fresh: still a new hello
  assert.ok(second.creds);
  assert.equal(hellos(w.bridge).length, count + 1);
  assert.notEqual(second.creds, first.creds, "fresh credentials object per proof");
});

test("a takeover is caught however soon it happens: Meridian quits, a listener binds the port, the next request is refused a token", async () => {
  const w = await setup({ paired: {} });
  const port = w.bridge.port;
  await bridgeCall("/v1/status");
  await w.bridge.stop();
  const { FakeBridge } = await import("./fake-bridge.mjs");
  const squatter = new FakeBridge({ proof: "invalid" });
  squatter.clients.set(w.issued.clientId, { secret: "the-squatter-has-no-idea-of-the-secret", revoked: false });
  await squatter.start(port);
  try {
    const started = Date.now();
    await assert.rejects(bridgeCall("/v1/status"), (error) => error.detail.code === "proof_failed");
    assert.ok(Date.now() - started < 1500);
    assert.equal(authedRequests(squatter).length, 0);
  } finally {
    await squatter.stop();
  }
});

test("bridgeFetch refuses a credential nobody proved (defence in depth), and sends nothing", async () => {
  const w = await setup({ paired: {} });
  const forged = { port: w.bridge.port, clientId: w.issued.clientId, token: w.issued.token };
  await assert.rejects(bridgeFetch(w.bridge.port, "/v1/status", forged), (error) => error instanceof BridgeNetworkError && /without a verified proof/.test(error.message));
  assert.equal(w.bridge.log.length, 0);
  const marked = markProven({ ...forged });
  assert.equal((await bridgeFetch(w.bridge.port, "/v1/status", marked)).app_version, "1.4.0");
});

test("only connection.ts earns credentials: markProven is used nowhere else, and every other bridgeFetch carries none", () => {
  const root = fileURLToPath(new URL("../../src/", import.meta.url));
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(`${dir}${entry.name}/`);
      else if (/\.tsx?$/.test(entry.name)) files.push(`${dir}${entry.name}`);
    }
  };
  walk(root);
  assert.ok(files.length > 20);
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const name = file.slice(root.length);
    if (name !== "shared/connection.ts" && name !== "shared/bridge-client.ts") {
      assert.doesNotMatch(text, /\bmarkProven\b/, `${name} must not mint credentials`);
      assert.doesNotMatch(text, /bridgeFetch(?:<[^>(]+>)?\([^,]+,[^,]+,(?!\s*null\b)/, `${name} calls bridgeFetch with something other than null credentials`);
    }
  }
  const connection = readFileSync(`${root}shared/connection.ts`, "utf8");
  assert.equal((connection.match(/\bmarkProven\(/g) ?? []).length, 1, "credentials are minted in exactly one place");
  assert.doesNotMatch(connection, /VERIFY_TTL/, "the old verification window is gone");
});
