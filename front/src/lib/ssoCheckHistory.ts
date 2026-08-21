import type { SsoCheckItem, SsoCheckStatus } from "@/lib/api";

export type SsoCheckHistoryEntry = {
  run_id: string;
  started_at: number | null;
  finished_at: number | null;
  total_count: number;
  clean_count: number;
  flagged_count: number;
  unknown_count: number;
  failed_count: number;
  items: SsoCheckItem[];
};

const DB_NAME = "grok-register-sso-check-history";
const DB_VERSION = 1;
const STORE = "sso-check-history";
let dbPromise: Promise<IDBDatabase | null> | null = null;
let memoryEntries: SsoCheckHistoryEntry[] = [];

function normalize(entries: SsoCheckHistoryEntry[]) {
  return entries
    .filter((entry) => entry && typeof entry.run_id === "string" && Array.isArray(entry.items))
    .sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0));
}

function remember(entries: SsoCheckHistoryEntry[]) {
  memoryEntries = normalize(entries);
  return memoryEntries;
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  const pending = new Promise<IDBDatabase | null>((resolve) => {
    const fail = () => {
      queueMicrotask(() => { if (dbPromise === pending) dbPromise = null; });
      resolve(null);
    };
    if (typeof indexedDB === "undefined") return fail();
    let request: IDBOpenDBRequest;
    try { request = indexedDB.open(DB_NAME, DB_VERSION); } catch { return fail(); }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: "run_id" });
    };
    request.onsuccess = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) { db.close(); fail(); return; }
      db.onversionchange = () => { db.close(); if (dbPromise === pending) dbPromise = null; };
      db.onclose = () => { if (dbPromise === pending) dbPromise = null; };
      resolve(db);
    };
    request.onerror = fail;
    request.onblocked = fail;
  });
  dbPromise = pending;
  return pending;
}

function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T | null> {
  return openDatabase().then((db) => new Promise<T | null>((resolve) => {
    if (!db) return resolve(null);
    let result: T | null = null;
    let request: IDBRequest<T>;
    try {
      const tx = db.transaction(STORE, mode);
      request = action(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => resolve(null);
      tx.onerror = () => resolve(null);
    } catch { resolve(null); return; }
    request.onsuccess = () => { result = request.result; };
    request.onerror = () => resolve(null);
  }));
}

export async function loadSsoCheckHistory() {
  const rows = await transaction<SsoCheckHistoryEntry[]>("readonly", (store) => store.getAll());
  return rows ? remember(rows) : memoryEntries;
}

export async function appendSsoCheckHistory(report: SsoCheckStatus) {
  if (!report.run_id) return loadSsoCheckHistory();
  const entry: SsoCheckHistoryEntry = {
    run_id: report.run_id,
    started_at: report.started_at ?? null,
    finished_at: report.finished_at ?? null,
    total_count: Number(report.total_count || 0),
    clean_count: Number(report.clean_count || 0),
    flagged_count: Number(report.flagged_count || 0),
    unknown_count: Number(report.unknown_count || 0),
    failed_count: Number(report.failed_count || 0),
    items: (report.items || []).map((item) => ({ ...item, error: String(item.error || "") })),
  };
  remember([entry, ...memoryEntries.filter((old) => old.run_id !== entry.run_id)]);
  await transaction<IDBValidKey>("readwrite", (store) => store.put(entry));
  return loadSsoCheckHistory();
}

export async function removeSsoCheckHistory(runId: string) {
  remember(memoryEntries.filter((entry) => entry.run_id !== runId));
  await transaction<undefined>("readwrite", (store) => store.delete(runId));
  return loadSsoCheckHistory();
}

export async function clearSsoCheckHistory() {
  remember([]);
  await transaction<undefined>("readwrite", (store) => store.clear());
  return loadSsoCheckHistory();
}
