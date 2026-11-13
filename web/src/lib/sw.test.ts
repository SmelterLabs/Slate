// The service worker (public/sw.js) run in a plain context with fake caches, fetch and clients.
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { beforeEach, describe, expect, it } from 'vitest';

const ORIGIN = 'https://slate.example.com';
const SOURCE = readFileSync(new URL('../../public/sw.js', import.meta.url), 'utf8');
const BOARD = '<html><script type="module" src="/assets/index-abc.js"></script></html>';

let store: Map<string, Response>;
let listeners: Record<string, (e: unknown) => void>;
let down: boolean;
let opened: string[];
let posted: unknown[];

const key = (req: string | { url: string }) => (typeof req === 'string' ? req : new URL(req.url).pathname);

beforeEach(() => {
  store = new Map();
  listeners = {};
  down = false;
  opened = [];
  posted = [];
  const cache = {
    put: async (req: string | { url: string }, res: Response) => void store.set(key(req), res),
    match: async (req: string | { url: string }) => store.get(key(req))?.clone(),
    keys: async () => [...store.keys()].map((k) => ({ url: ORIGIN + k })),
    delete: async (req: { url: string }) => store.delete(key(req)),
    addAll: async () => {},
  };
  const bodies: Record<string, string> = {
    '/': BOARD,
    '/health': '{"status":"ok"}',
    '/docs': '<html>API docs</html>',
    '/openapi.json': '{}',
  };
  runInNewContext(SOURCE, {
    URL,
    Response,
    Promise,
    Set,
    self: {
      location: { origin: ORIGIN },
      addEventListener: (t: string, f: (e: unknown) => void) => (listeners[t] = f),
      clients: {
        matchAll: async () => [],
        openWindow: async (u: string) => void opened.push(u),
      },
    },
    caches: { open: async () => cache, keys: async () => [] },
    fetch: async (req: { url: string }) => {
      if (down) return new Response('bad gateway', { status: 502 });
      return new Response(bodies[new URL(req.url).pathname] ?? 'x', { status: 200 });
    },
  });
});

async function go(path: string): Promise<string> {
  let out: Promise<Response> | undefined;
  listeners.fetch({ request: { method: 'GET', url: ORIGIN + path, mode: 'navigate' }, respondWith: (p: Promise<Response>) => (out = p) });
  await new Promise((r) => setTimeout(r, 5));
  return out ? (await out).text() : '(passed through)';
}

describe('service worker', () => {
  it('keeps the board as the shell, whatever other page the server answers', async () => {
    await go('/');
    store.set('/assets/index-abc.js', new Response('js'));
    for (const path of ['/health', '/docs', '/redoc', '/openapi.json', '/somewhere-else']) await go(path);
    expect(await store.get('/')!.clone().text()).toBe(BOARD);
    expect(store.has('/assets/index-abc.js')).toBe(true);
    down = true;
    expect(await go('/')).toBe(BOARD);
  });

  it('lets the server pages through untouched', async () => {
    for (const path of ['/health', '/docs', '/redoc', '/openapi.json']) expect(await go(path)).toBe('(passed through)');
  });

  it('opens only its own origin from a notification', async () => {
    const click = (url: string) =>
      new Promise<void>((done) =>
        listeners.notificationclick({
          notification: { close() {}, data: { url } },
          waitUntil: (p: Promise<void>) => void p.then(done),
        }),
      );
    await click('https://evil.example.com/x');
    await click('/#/everything?task=AAAAAAAAAAAA');
    expect(opened).toEqual([`${ORIGIN}/#/inbox`, `${ORIGIN}/#/everything?task=AAAAAAAAAAAA`]);
    expect(posted).toEqual([]);
  });
});
