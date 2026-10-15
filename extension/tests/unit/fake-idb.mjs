// A minimal in-memory IndexedDB: just what shared/vault.ts uses (open with upgrade, one object store, get/add).

export function installFakeIndexedDB() {
  const databases = new Map();
  const asyncRequest = (work) => {
    const request = { result: undefined, error: null, onsuccess: null, onerror: null };
    setTimeout(() => {
      try {
        request.result = work();
        request.onsuccess?.();
      } catch (error) {
        request.error = error;
        request.onerror?.();
      }
    }, 0);
    return request;
  };
  const fake = {
    databases,
    open(name) {
      const request = { result: null, error: null, onsuccess: null, onerror: null, onupgradeneeded: null, onblocked: null };
      setTimeout(() => {
        let db = databases.get(name);
        const created = !db;
        if (!db) {
          db = { stores: new Map() };
          databases.set(name, db);
        }
        request.result = {
          objectStoreNames: { contains: (store) => db.stores.has(store) },
          createObjectStore: (store) => db.stores.set(store, new Map()),
          close() {},
          transaction(storeName) {
            const store = db.stores.get(storeName);
            return {
              objectStore: () => ({
                get: (key) => asyncRequest(() => store.get(key)),
                add: (value, key) => asyncRequest(() => {
                  if (store.has(key)) throw new DOMException("key exists", "ConstraintError");
                  store.set(key, value);
                  return key;
                }),
              }),
            };
          },
        };
        if (created) request.onupgradeneeded?.();
        request.onsuccess?.();
      }, 0);
      return request;
    },
  };
  globalThis.indexedDB = fake;
  return fake;
}

export function removeFakeIndexedDB() {
  delete globalThis.indexedDB;
}
