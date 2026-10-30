// Attaches to `self`: shared with the service worker. pushsubscriptionchange can fire with no page and both
// subscriptions null, so the server key and old auth secret must already be stored when the user subscribes.

self.wizWebPushKey = function (base64url) {
  const padded = base64url + '='.repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

self.wizWebPushStore = (() => {
  const DB_NAME = 'wiz-web-push';
  const STORE = 'state';
  const KEY = 'subscription';

  function open() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function run(mode, action) {
    const db = await open();
    try {
      return await new Promise((resolve, reject) => {
        const request = action(db.transaction(STORE, mode).objectStore(STORE));
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    } finally {
      db.close();
    }
  }

  // Best-effort: IndexedDB can be unavailable, and none of this may fail a subscribe or a push.
  return {
    async load() {
      try {
        return (await run('readonly', (store) => store.get(KEY))) || null;
      } catch (e) {
        return null;
      }
    },
    async save(record) {
      try {
        await run('readwrite', (store) => store.put(record, KEY));
      } catch (e) { /* rotation will fall back to whatever the event carries */ }
    },
    async clear() {
      try {
        await run('readwrite', (store) => store.delete(KEY));
      } catch (e) { /* stale record is harmless: rotation authenticates on it */ }
    },
  };
})();
