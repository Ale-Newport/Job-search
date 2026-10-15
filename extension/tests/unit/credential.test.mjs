import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { pollPairing, setWatchInterval, startPairing, disconnectLocally } from "../../src/background/pairing.ts";
import { bridgeCall, resolveConnection } from "../../src/shared/connection.ts";
import {
  KEYS, clearConnection, credentialPlacement, getConnection, resetCredentialPlacement, setConnection,
} from "../../src/shared/storage.ts";
import { installFakeIndexedDB, removeFakeIndexedDB } from "./fake-idb.mjs";
import { world } from "./helpers.mjs";

let current = null;
afterEach(async () => {
  setWatchInterval(1500);
  resetCredentialPlacement();
  removeFakeIndexedDB();
  await current?.close();
  current = null;
});
const setup = async (options) => {
  current = await world(options);
  setWatchInterval(0);
  resetCredentialPlacement();
  return current;
};
const dump = (area) => JSON.stringify([...area.data]);
/** Where a secret would be visible to a content script: everything in storage.local unless it is restricted, and storage.session unless it is. */
const containsSecret = (area, issued) => dump(area).includes(issued.secret) || dump(area).includes(issued.token);

async function pairFreshly(w) {
  const { pairingId } = await startPairing();
  const issued = w.bridge.approve(pairingId);
  const done = await pollPairing(pairingId);
  assert.equal(done.state, "approved");
  return issued;
}

test("Chrome: storage.local is restricted to trusted contexts first, then the credential is stored as is and nowhere else", async () => {
  const w = await setup();
  const issued = await pairFreshly(w);
  assert.equal(await credentialPlacement(), "local");
  assert.equal(w.chrome.__log.accessLevel, "TRUSTED_CONTEXTS");
  assert.equal(containsSecret(w.chrome.storage.local, issued), true);
  assert.equal(containsSecret(w.chrome.storage.session, issued), false);
  assert.equal((await getConnection()).token, issued.token);
});

test("an engine that cannot restrict storage.local keeps only ciphertext there, opened by a key content scripts can never reach", async () => {
  const w = await setup();
  delete w.chrome.storage.local.setAccessLevel; // Safari today
  const idb = installFakeIndexedDB();
  const issued = await pairFreshly(w);
  assert.equal(await credentialPlacement(), "sealed");
  assert.equal(containsSecret(w.chrome.storage.local, issued), false, "no plaintext where content scripts can read");
  assert.equal(containsSecret(w.chrome.storage.session, issued), false);
  const record = (await w.chrome.storage.local.get(KEYS.connection))[KEYS.connection];
  assert.equal(record.clientId, issued.clientId);
  assert.equal(record.token, undefined);
  assert.equal(typeof record.sealed.ct, "string");
  assert.equal(idb.databases.get("meridian-vault").stores.get("keys").size, 1, "one non-extractable key");
  const key = [...idb.databases.get("meridian-vault").stores.get("keys").values()][0];
  assert.equal(key.extractable, false);
  // it still works: the worker opens it, proves the port and calls
  assert.equal((await getConnection()).token, issued.token);
  assert.equal((await bridgeCall("/v1/status")).app_version, "1.4.0");
  assert.equal((await resolveConnection({ force: true })).state.phase, "ready");
});

test("the sealed credential is useless when copied, tampered with, moved to another client or when the key is gone", async () => {
  const w = await setup();
  delete w.chrome.storage.local.setAccessLevel;
  const idb = installFakeIndexedDB();
  const issued = await pairFreshly(w);
  const good = (await w.chrome.storage.local.get(KEYS.connection))[KEYS.connection];
  const put = (record) => w.chrome.storage.local.set({ [KEYS.connection]: record });
  resetCredentialPlacement(); // as after a worker restart: nothing cached in memory
  const flipped = good.sealed.ct.slice(0, -2) + (good.sealed.ct.endsWith("AA") ? "BB" : "AA");
  await put({ ...good, sealed: { ...good.sealed, ct: flipped } });
  assert.equal(await getConnection(), null, "tampered ciphertext");
  await put({ ...good, clientId: "11111111-2222-3333-4444-555555555555" });
  assert.equal(await getConnection(), null, "bound to the client it was sealed for");
  await put(good);
  assert.equal((await getConnection()).token, issued.token, "the untouched record opens");
  idb.databases.clear(); // site data cleared: the key is gone
  resetCredentialPlacement();
  assert.equal(await getConnection(), null, "without the key it never opens");
  assert.equal((await resolveConnection({ force: true })).state.phase, "unpaired", "the user simply connects again");
});

test("a plaintext credential found where content scripts can read it is moved into the vault and scrubbed", async () => {
  const w = await setup({ paired: {} });
  delete w.chrome.storage.local.setAccessLevel;
  installFakeIndexedDB();
  assert.equal(containsSecret(w.chrome.storage.local, w.issued), true, "as written by an older build");
  assert.equal((await getConnection()).token, w.issued.token);
  assert.equal(containsSecret(w.chrome.storage.local, w.issued), false, "scrubbed on first read");
  assert.equal((await getConnection()).token, w.issued.token, "and still readable from the vault");
});

test("with neither restriction nor vault the credential lives in storage.session only, and a browser restart means connecting again", async () => {
  const w = await setup();
  delete w.chrome.storage.local.setAccessLevel;
  removeFakeIndexedDB();
  const issued = await pairFreshly(w);
  assert.equal(await credentialPlacement(), "session");
  assert.equal(containsSecret(w.chrome.storage.local, issued), false, "never in storage.local");
  assert.equal(containsSecret(w.chrome.storage.session, issued), true);
  assert.equal((await bridgeCall("/v1/status")).app_version, "1.4.0");
  w.chrome.storage.session.data.clear(); // the browser quit
  assert.equal(await getConnection(), null);
});

test("a setAccessLevel that refuses is treated like one that does not exist", async () => {
  const w = await setup();
  w.chrome.storage.local.setAccessLevel = async () => {
    throw new Error("not supported in this context");
  };
  installFakeIndexedDB();
  const issued = await pairFreshly(w);
  assert.equal(await credentialPlacement(), "sealed");
  assert.equal(containsSecret(w.chrome.storage.local, issued), false);
  w.chrome.storage.local.setAccessLevel = () => {
    throw new Error("thrown synchronously");
  };
  resetCredentialPlacement();
  assert.equal(await credentialPlacement(), "sealed");
});

test("disconnecting wipes the credential from every place it could be", async () => {
  for (const mode of ["local", "sealed", "session"]) {
    const w = await setup();
    if (mode !== "local") delete w.chrome.storage.local.setAccessLevel;
    if (mode === "sealed") installFakeIndexedDB();
    else removeFakeIndexedDB();
    const issued = await pairFreshly(w);
    assert.ok(await getConnection(), mode);
    await disconnectLocally();
    assert.equal(await getConnection(), null, mode);
    assert.equal(dump(w.chrome.storage.local).includes(issued.secret), false, mode);
    assert.equal(dump(w.chrome.storage.session).includes(issued.secret), false, mode);
    assert.equal(w.chrome.storage.local.data.has(KEYS.connection), false, mode);
    assert.equal(w.chrome.storage.session.data.has(KEYS.connectionSession), false, mode);
    await current.close();
    current = null;
    resetCredentialPlacement();
  }
});

test("nothing secret is ever written next to the credential: the connection cache, pairing record, errors and tab state hold no token", async () => {
  for (const mode of ["local", "sealed", "session"]) {
    const w = await setup();
    if (mode !== "local") delete w.chrome.storage.local.setAccessLevel;
    if (mode === "sealed") installFakeIndexedDB();
    else removeFakeIndexedDB();
    const issued = await pairFreshly(w);
    await resolveConnection({ force: true });
    await bridgeCall("/v1/status");
    w.bridge.revoke(issued.clientId);
    await bridgeCall("/v1/status").catch(() => undefined); // refused before anything is sent (no proof): the connection cache is written
    // the credential itself stays where it belongs (an unproven refusal forgets nothing); NOTHING ELSE may hold any part of it
    const elsewhere = (area, slot) => JSON.stringify([...area.data].filter(([key]) => key !== slot));
    const others = elsewhere(w.chrome.storage.local, KEYS.connection) + elsewhere(w.chrome.storage.session, KEYS.connectionSession);
    assert.equal(others.includes(issued.secret) || others.includes(issued.token), false, `${mode}: the secret leaked next to the credential`);
    assert.ok(w.chrome.storage.session.data.has(KEYS.conn), `${mode}: the connection cache was written`);
    await w.chrome.storage.session.set({ "meridian.tab.9": { active: true, since: 1, origin: null, auto: false, sessions: [] } });
    await current.close();
    current = null;
    resetCredentialPlacement();
  }
});

test("setConnection refuses anything that is not a whole credential", async () => {
  await setup();
  await assert.rejects(setConnection({ port: 1, clientId: "x", token: "mrd1.bad", clientName: "", pairedAt: "" }), /invalid credential/);
  await clearConnection(); // clearing nothing is fine
  assert.equal(await getConnection(), null);
});
