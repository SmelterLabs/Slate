/** Pass a fallback for optional data. Required writes, such as saving a sign-in, surface errors. */
export function withStorage<T>(use: (storage: Storage) => T, ...fallback: [] | [T]): T {
  if (!fallback.length) return use(localStorage);
  try {
    return use(localStorage);
  } catch {
    return fallback[0];
  }
}
