// Hello proof of the Meridian bridge (docs/EXTENSION_DESIGN.md 4.3, backend/jobagent/extension/pairing.py).
//
// Before a credential is sent anywhere, the worker asks the listener to prove it holds the secret of this pairing:
//   key   = SHA-256 (32 raw bytes) of the secret, the third dot-separated part of the token, as UTF-8
//   msg   = UTF-8 of "meridian-bridge-v1|" + nonce + "|" + port        (nonce exactly as sent, NOT decoded)
//   proof = base64url(HMAC-SHA256(key, msg)) without padding
// A process that merely squats a bridge port never sees the secret, so it cannot produce a valid proof.
//
// A client that was revoked gets a second kind of statement from the real Meridian, made with the same key and a prefix of its own:
//   msg   = UTF-8 of "meridian-bridge-v1-revoked|" + nonce + "|" + port
// so "this credential was revoked" is something Meridian PROVES, and silence (no proof of either kind) never means revoked.

const encoder = new TextEncoder();

export const PROOF_PREFIX = "meridian-bridge-v1|";
export const REVOKED_PROOF_PREFIX = "meridian-bridge-v1-revoked|";

export function b64urlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(text: string): Uint8Array<ArrayBuffer> | null {
  if (!/^[A-Za-z0-9_-]*$/.test(text) || text.length % 4 === 1) return null;
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  try {
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  } catch {
    return null;
  }
}

/** 16 random bytes, base64url (22 characters), the shape the bridge accepts (16-64 characters). */
export function randomNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return b64urlEncode(bytes);
}

export interface ParsedToken { clientId: string; secret: string }

/** `mrd1.<client_id>.<secret>` -> parts. Anything else is not a credential this extension ever stored. */
export function parseToken(token: unknown): ParsedToken | null {
  if (typeof token !== "string") return null;
  const match = /^mrd1\.([A-Za-z0-9-]{8,64})\.([A-Za-z0-9_-]{16,128})$/.exec(token);
  return match ? { clientId: match[1], secret: match[2] } : null;
}

async function hmacKey(secret: string, usage: "sign" | "verify"): Promise<CryptoKey> {
  const raw = await crypto.subtle.digest("SHA-256", encoder.encode(secret));
  return crypto.subtle.importKey("raw", raw, { name: "HMAC", hash: "SHA-256" }, false, [usage]);
}

export function proofMessage(nonce: string, port: number, prefix: string = PROOF_PREFIX): Uint8Array<ArrayBuffer> {
  return encoder.encode(`${prefix}${nonce}|${port}`);
}

export async function computeProof(secret: string, nonce: string, port: number): Promise<string> {
  const mac = await crypto.subtle.sign("HMAC", await hmacKey(secret, "sign"), proofMessage(nonce, port));
  return b64urlEncode(new Uint8Array(mac));
}

async function verifyWith(prefix: string, secret: string, nonce: string, port: number, proof: unknown): Promise<boolean> {
  if (typeof proof !== "string") return false;
  const signature = b64urlDecode(proof);
  if (!signature || signature.length !== 32) return false;
  return crypto.subtle.verify("HMAC", await hmacKey(secret, "verify"), signature, proofMessage(nonce, port, prefix));
}

/** Constant-time verification (WebCrypto `verify` compares the MACs without early exit). */
export function verifyProof(secret: string, nonce: string, port: number, proof: unknown): Promise<boolean> {
  return verifyWith(PROOF_PREFIX, secret, nonce, port, proof);
}

/** Does `proof` show that the listener knows this secret AND says the client was revoked? (An active proof does not pass here.) */
export function verifyRevokedProof(secret: string, nonce: string, port: number, proof: unknown): Promise<boolean> {
  return verifyWith(REVOKED_PROOF_PREFIX, secret, nonce, port, proof);
}
