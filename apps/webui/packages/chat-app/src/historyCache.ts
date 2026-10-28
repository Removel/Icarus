import type { Transcript } from './updates';

const databaseName = 'icarus.webui.history';
const maxEntries = 12;
const maxAge = 7 * 24 * 60 * 60 * 1000;
let failures = 0;

// Optional cache operations never sit in the RPC/hydration critical path.
async function access<T>(
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T | undefined> {
  if (failures >= 3 || !globalThis.indexedDB) return;
  try {
    const result = await new Promise<T>((resolve, reject) => {
      let database: IDBDatabase | undefined;
      let transaction: IDBTransaction | undefined;
      let expired = false;
      const timer = setTimeout(() => {
        expired = true;
        transaction?.abort();
        database?.close();
        reject(new Error('History cache timeout'));
      }, 3000);
      const fail = () => {
        expired = true;
        clearTimeout(timer);
        database?.close();
        reject(new Error('History cache unavailable'));
      };
      let opening: IDBOpenDBRequest;
      try {
        opening = indexedDB.open(databaseName, 1);
      } catch {
        fail();
        return;
      }
      opening.onupgradeneeded = () => {
        const store = opening.result.createObjectStore('sessions', { keyPath: 'key' });
        store.createIndex('updated', 'updated');
      };
      opening.onerror = fail;
      opening.onblocked = fail;
      opening.onsuccess = () => {
        database = opening.result;
        if (expired) {
          database.close();
          return;
        }
        try {
          transaction = database.transaction('sessions', mode);
          const request = operation(transaction.objectStore('sessions'));
          transaction.oncomplete = () => {
            clearTimeout(timer);
            database!.close();
            resolve(request.result);
          };
          transaction.onabort = fail;
          transaction.onerror = fail;
        } catch {
          fail();
        }
      };
    });
    failures = 0;
    return result;
  } catch {
    failures += 1;
    return;
  }
}

export async function readHistory(key: string): Promise<Transcript | undefined> {
  const cached = await access('readonly', (store) => store.get(key));
  const state = cached?.transcript as Transcript | undefined;
  if (
    !state ||
    Date.now() - cached.updated > maxAge ||
    !(state.seen instanceof Set) ||
    !(state.finished instanceof Set) ||
    !Array.isArray(state.items) ||
    !state.items.every((item) => typeof item.id === 'string' && typeof item.text === 'string')
  )
    return;
  return { ...state, tasks: [] };
}

export async function saveHistory(key: string, transcript: Transcript) {
  if (
    !transcript.items.length ||
    transcript.items.reduce(
      (size, item) => size + item.text.length + (item.detail?.length ?? 0),
      0,
    ) > 2_000_000
  )
    return;
  const snapshot = {
    ...transcript,
    tasks: [],
    items: transcript.items
      .filter((item) => !item.pending)
      .map((item) => ({ ...item, live: false })),
  };
  await access('readwrite', (store) => {
    const request = store.put({ key, updated: Date.now(), transcript: snapshot });
    const count = store.count();
    count.onsuccess = () => {
      let excess = count.result - maxEntries;
      if (excess <= 0) return;
      const oldest = store.index('updated').openKeyCursor();
      oldest.onsuccess = () => {
        const cursor = oldest.result;
        if (!cursor || excess-- <= 0) return;
        store.delete(cursor.primaryKey);
        cursor.continue();
      };
    };
    return request;
  });
}

export async function deleteHistory(key: string) {
  await access('readwrite', (store) => store.delete(key));
}
