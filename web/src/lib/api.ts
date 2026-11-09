// Thin fetch wrapper for /api/v1. Every call sends the bearer token; a 401 anywhere calls
// `onUnauthorized` (the store signs out, which shows the token screen).

import { COOKIE_SESSION } from './collaboration';
export function authHeaders(token: string | null): Record<string, string> {
  return { 'X-Slate-Client': 'browser', 'X-Slate-Source': 'ui', ...(token && token !== COOKIE_SESSION ? { Authorization: `Bearer ${token}` } : {}) };
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

export interface ApiConfig {
  token: () => string | null;
  onUnauthorized: () => void;
}

let config: ApiConfig = { token: () => null, onUnauthorized: () => {} };

export function configure(c: ApiConfig) {
  config = c;
}

// The browser's offline/online events (navigator.onLine alone lags behind them in some browsers).
let offline = typeof navigator !== 'undefined' && navigator.onLine === false;
if (typeof window !== 'undefined') {
  window.addEventListener('offline', () => (offline = true));
  window.addEventListener('online', () => (offline = false));
}

/** What a call that never reached Slate says. Status 0 marks it (ApiError.status). */
export const OFFLINE_DETAIL = (method = 'GET') =>
  offline || (typeof navigator !== 'undefined' && navigator.onLine === false)
    ? method === 'GET'
      ? 'You are offline: showing what was already loaded'
      : 'You are offline: that change was not saved. Try again once you are back online.'
    : method === 'GET'
      ? 'Could not reach Slate (weak connection?)'
      : 'Could not reach Slate, so that change was not saved (weak connection?). Try again.';

function detailOf(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join('.')}: ${e.msg}`)
      .join('; ');
  }
  return fallback;
}

export async function api<T>(
  method: string,
  path: string,
  body?: unknown,
  token: string | null = config.token(),
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...authHeaders(token) };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // No answer at all: no signal, a dropped tunnel, the server restarting.
    throw new ApiError(0, OFFLINE_DETAIL(method));
  }
  if (res.status === 401) {
    config.onUnauthorized();
    throw new ApiError(401, 'Signed out: the token was refused');
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailOf(data, `${res.status} ${res.statusText}`));
  return data as T;
}

export const get = <T>(path: string) => api<T>('GET', path);
export const post = <T>(path: string, body?: unknown) => api<T>('POST', path, body ?? {});
export const patch = <T>(path: string, body: unknown) => api<T>('PATCH', path, body);
export const put = <T>(path: string, body: unknown) => api<T>('PUT', path, body);
