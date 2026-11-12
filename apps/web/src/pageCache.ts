type CacheEntry = { data: unknown; at: number };

const cache = new Map<string, CacheEntry>();
const MAX_ENTRIES = 60;

/** Synchronous read for rendering cached data before the network answers. */
export function peekPageCache<T>(key: string): T | null {
  const entry = cache.get(key);
  return entry ? (entry.data as T) : null;
}

export function storePageCache<T>(key: string, data: T): void {
  if (!cache.has(key) && cache.size >= MAX_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
  cache.set(key, { data, at: Date.now() });
}

/** Drop every entry whose key starts with the prefix, e.g. after a write. */
export function invalidatePageCache(prefix: string): void {
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}
