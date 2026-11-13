// Test harness for the board store's unit tests: a fake EventSource and a `fetch` stub that
// answers per route. Only `fetch` and `EventSource` are faked; the api layer, the store and the
// reducer are the real ones. Not a test file itself (vitest runs `*.test.ts` only).
import { vi } from 'vitest';
import { LIST, item } from '../fixtures';
import type { Me, SlateEvent } from '../types';

export class FakeEventSource {
  static all: FakeEventSource[] = [];
  static get last() {
    return FakeEventSource.all.at(-1)!;
  }
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((m: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;
  #listeners = new Map<string, Set<(m: MessageEvent) => void>>();
  constructor(public url: string) {
    FakeEventSource.all.push(this);
  }
  addEventListener(type: string, fn: (m: MessageEvent) => void) {
    (this.#listeners.get(type) ?? this.#listeners.set(type, new Set()).get(type)!).add(fn);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.(new Event('open'));
  }
  fail() {
    this.onerror?.(new Event('error'));
  }
  emit(e: SlateEvent) {
    for (const fn of this.#listeners.get(e.type) ?? []) fn({ data: JSON.stringify(e) } as MessageEvent);
  }
}

export const ME: Me = {
  account: { id: 'A_alex', handle: 'alex', display_name: 'Alex', kind: 'person' },
  scopes: ['read', 'write'],
  token_id: 'T1',
  prefs: {},
  timezone: 'America/Chicago',
};

/** A route's answer: the JSON body (or a promise of it), or `{status, body}` for an error. */
export type Answer = unknown;
export type Failure = { status: number; body?: unknown };
const failed = (a: Answer): a is Failure =>
  !!a && typeof a === 'object' && typeof (a as Failure).status === 'number';

/** Answers per `/api/v1` path, or per `METHOD path` (which wins); anything else is an empty
 * list. The handler gets the request's JSON body. */
export const routes = new Map<string, (body: unknown) => Answer>();
/** Every path asked for, in order. */
export const calls: string[] = [];
/** Every request with its method and body, in order. */
export const requests: { method: string; path: string; body: unknown }[] = [];

async function fetchStub(url: string, init: RequestInit = {}): Promise<Response> {
  const path = url.replace(/^\/api\/v1/, '').split('?')[0];
  const method = init.method ?? 'GET';
  const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push(path);
  requests.push({ method, path, body });
  const route = routes.get(`${method} ${path}`) ?? routes.get(path);
  const answer = await (route?.(body) ?? []);
  const status = failed(answer) ? answer.status : 200;
  const data = failed(answer) ? answer.body : answer;
  return { ok: status < 300, status, statusText: '', json: async () => data } as unknown as Response;
}

export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((yes) => (resolve = yes));
  return { promise, resolve };
}

/** Enough microtask turns for a chain of fetches to settle; no real time passes. */
export async function settle() {
  for (let i = 0; i < 80; i++) await Promise.resolve();
}

export type Board = typeof import('../store.svelte').board;

/** A fresh store module with fake timers and the default routes (one list, one item). */
export async function freshBoard(): Promise<Board> {
  vi.resetModules();
  vi.useFakeTimers();
  FakeEventSource.all = [];
  routes.clear();
  calls.length = 0;
  requests.length = 0;
  routes.set('/me', () => ME);
  routes.set('/lists', () => [LIST]);
  routes.set('/items', () => [item()]);
  localStorage.setItem('slate.token', 'tok');
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('fetch', fetchStub);
  return (await import('../store.svelte')).board;
}

export function tearDown(board: Board) {
  board.signOut();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
}

/** start(), with the stream opening at once, as the server does. */
export async function startLive(board: Board) {
  const started = board.start();
  FakeEventSource.last.open();
  await started;
}

/** Requests other than reads: what a write sent. */
export const writes = () => requests.filter((r) => r.method !== 'GET');
