import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { b64urlDecode, b64urlEncode, computeProof, parseToken, randomNonce, verifyProof } from "../../src/shared/hello-proof.ts";
import { referenceProof } from "./fake-bridge.mjs";

// The vector shared with backend/tests/test_extension_pairing.py (produced independently of this code).
const SECRET = "Zm9yLXRlc3RzLW9ubHktc2VjcmV0LXZhbHVlLTEyMzQ";
const NONCE = "AAECAwQFBgcICQoLDA0ODw";
const PORT = 47617;
const PROOF = "5Cv3Kfcb7C1Owes5OXCxiBvmlUqXV9yZJsMo1XWeFQ4";
// A second vector computed with Python's hmac/hashlib: key = sha256(secret) raw bytes.
const SECRET_2 = "Zk9xQw3m8vYpLr2sT7uH1dEaGbN4cJiK5oVxWyU0AfM";
const NONCE_2 = "dGVzdC1ub25jZS0xMjM0NQ";
const PROOF_2 = "BYCr2C3Pqr5jvSUF3hSySkjecc5Gs0yNoLtbOAOK95c";

test("proof matches the backend vector exactly", async () => {
  assert.equal(createHash("sha256").update(SECRET).digest("hex"), "7bdaec0c3990919f1f51ebf8d2c63f2712d3659eec9883d98931b6aed9a1b18e");
  assert.equal(await computeProof(SECRET, NONCE, PORT), PROOF);
  assert.equal(await computeProof(SECRET_2, NONCE_2, 47615), PROOF_2);
  assert.equal(referenceProof(SECRET, NONCE, PORT), PROOF);
});

test("verifyProof accepts the right proof and nothing else", async () => {
  assert.equal(await verifyProof(SECRET, NONCE, PORT, PROOF), true);
  assert.equal(await verifyProof(SECRET, NONCE, PORT + 1, PROOF), false, "bound to the port");
  assert.equal(await verifyProof(SECRET, NONCE.slice(0, -1) + "x", PORT, PROOF), false, "bound to the nonce");
  assert.equal(await verifyProof("another-secret-value-1234567890", NONCE, PORT, PROOF), false, "bound to the secret");
  for (const bad of [null, undefined, "", "short", PROOF.slice(0, -1), PROOF + "A", `${PROOF}=`, "!!!!", 42, {}]) {
    assert.equal(await verifyProof(SECRET, NONCE, PORT, bad), false, `rejects ${JSON.stringify(bad)}`);
  }
});

test("base64url helpers round-trip and reject garbage", () => {
  const bytes = new Uint8Array([0, 1, 2, 250, 251, 252, 253, 254, 255]);
  const text = b64urlEncode(bytes);
  assert.match(text, /^[A-Za-z0-9_-]+$/);
  assert.deepEqual([...b64urlDecode(text)], [...bytes]);
  assert.equal(b64urlDecode("a"), null);
  assert.equal(b64urlDecode("ab+/"), null);
  assert.equal(b64urlEncode(new Uint8Array(0)), "");
});

test("nonces are 22 base64url characters and different every time", () => {
  const nonces = new Set(Array.from({ length: 50 }, randomNonce));
  assert.equal(nonces.size, 50);
  for (const nonce of nonces) assert.match(nonce, /^[A-Za-z0-9_-]{22}$/);
});

test("parseToken accepts mrd1 tokens only", () => {
  const id = "5f0c2f4e-7d49-4bd3-8a41-0f3c8f6a9b11";
  assert.deepEqual(parseToken(`mrd1.${id}.${SECRET}`), { clientId: id, secret: SECRET });
  for (const bad of ["", "Bearer x", `mrd2.${id}.${SECRET}`, `mrd1.${id}`, `mrd1.${id}.${SECRET}.extra`, `mrd1..${SECRET}`, `mrd1.${id}.short`, null, 5]) {
    assert.equal(parseToken(bad), null, `rejects ${JSON.stringify(bad)}`);
  }
});
