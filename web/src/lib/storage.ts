/** Optional local preferences and drafts can work in memory if storage is blocked or full. */
export function withStorage<T>(use: (storage: Storage) => T, fallback: T): T {
  try {
    return use(localStorage);
  } catch {
    return fallback;
  }
}
