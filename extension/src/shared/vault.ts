// Credential vault for engines where content scripts can read storage.local (a browser without
// storage.local.setAccessLevel, e.g. Safari today). Content scripts run next to hostile pages, so the bridge credential
// must never sit in plaintext where they can read it.
//
// The credential is encrypted with AES-GCM under a NON-EXTRACTABLE key that lives in this extension's own IndexedDB.
// A content script's IndexedDB is the PAGE's, never the extension's, so what it can read from storage.local is
// ciphertext it cannot open. The key cannot be exported (not even by the worker), only used.
//
// Nothing here ever logs or returns a secret except `unseal()`, whose result goes straight to the bridge client.

import { b64urlDecode, b64urlEncode } from "./hello-proof";

const DB_NAME = "meridian-vault";
const STORE = "keys";
const KEY_ID = "credential-v1";
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export interface Sealed { v: 1; iv: string; ct: string }

/** Is everything the vault needs present in this engine? (WebCrypto and IndexedDB.) */
export function vaultSupported(): boolean {
  try {
    return typeof indexedDB !== "undefined" && indexedDB !== null && typeof crypto !== "undefined" && typeof crypto.subtle?.encrypt === "function";
  } catch {
    return false;
  }
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("indexeddb"));
  });
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(DB_NAME, 1);
    opening.onupgradeneeded = () => {
      if (!opening.result.objectStoreNames.contains(STORE)) opening.result.createObjectStore(STORE);
    };
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error ?? new Error("indexeddb"));
    opening.onblocked = () => reject(new Error("indexeddb blocked"));
  });
}

let keyLock: Promise<unknown> = Promise.resolve();

/** The vault key; created on first use when `create` is set. Creation is serialised so two callers never end with two keys. */
async function vaultKey(create: boolean): Promise<CryptoKey | null> {
  const run = keyLock.then(async () => {
    const db = await openDatabase();
    try {
      const read = (): Promise<CryptoKey | undefined> => request(db.transaction(STORE, "readonly").objectStore(STORE).get(KEY_ID));
      const existing = await read();
      if (existing) return existing;
      if (!create) return null;
      const fresh = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      try {
        await request(db.transaction(STORE, "readwrite").objectStore(STORE).add(fresh, KEY_ID));
        return fresh;
      } catch {
        return (await read()) ?? null; // another worker instance won the race: use its key
      }
    } finally {
      db.close();
    }
  });
  keyLock = run.catch(() => undefined);
  return run;
}

/** Encrypts `plain`, binding it to `context` (the client id) so a ciphertext cannot be moved to another record. */
export async function seal(plain: string, context: string): Promise<Sealed | null> {
  try {
    const key = await vaultKey(true);
    if (!key) return null;
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: encoder.encode(context) }, key, encoder.encode(plain));
    return { v: 1, iv: b64urlEncode(iv), ct: b64urlEncode(new Uint8Array(ct)) };
  } catch {
    return null;
  }
}

/** The plaintext, or null when the key is gone, the record was tampered with or belongs to another context. */
export async function unseal(sealed: Sealed, context: string): Promise<string | null> {
  try {
    const iv = b64urlDecode(sealed.iv);
    const ct = b64urlDecode(sealed.ct);
    if (!iv || !ct || iv.length !== 12) return null;
    const key = await vaultKey(false);
    if (!key) return null;
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv, additionalData: encoder.encode(context) }, key, ct);
    return decoder.decode(plain);
  } catch {
    return null;
  }
}

export function isSealed(value: unknown): value is Sealed {
  if (!value || typeof value !== "object") return false;
  const source = value as Record<string, unknown>;
  return source.v === 1 && typeof source.iv === "string" && typeof source.ct === "string"
    && source.iv.length <= 32 && source.ct.length <= 1024 && /^[A-Za-z0-9_-]*$/.test(source.iv + source.ct);
}
