import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setWorkspaceTimezone, workspaceTimezone } from './timezone';
import { workspaceDay } from './quickadd';
import { COOKIE_SESSION } from './collaboration';
import type { Me } from './types';

vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn(), removeItem: vi.fn(), length: 0 });
vi.stubGlobal('window', { addEventListener: vi.fn() });
vi.stubGlobal('document', { documentElement: { dataset: {} } });
// The store waits for the stream to open before its first REST reads: open on the next tick.
vi.stubGlobal('EventSource', class {
  onopen: ((e: unknown) => void) | null = null;
  constructor() { queueMicrotask(() => this.onopen?.({})); }
  addEventListener() {}
  close() {}
});
vi.mock('./agents.svelte', () => ({ agents: { roster: [], resetRoster: vi.fn(), refreshRoster: vi.fn(), onEvent: vi.fn() } }));
vi.mock('./api', () => ({
  configure: vi.fn(), authHeaders: vi.fn(), ApiError: class extends Error {},
  api: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(),
}));

const { board } = await import('./store.svelte');
const { api, get } = await import('./api');
const { agents } = await import('./agents.svelte');
const boundary = new Date('2030-01-01T23:30:00Z');
const me = (zone?: string): Me => ({
  account: { id: 'person', handle: 'person', display_name: 'Person', kind: 'person' } as Me['account'],
  scopes: ['read'], token_id: 'token', prefs: {}, timezone: zone,
});

beforeEach(() => {
  vi.clearAllMocks();
  agents.roster = [];
  setWorkspaceTimezone();
  vi.mocked(get).mockImplementation(async (path) => {
    if (path === '/me') return me('Asia/Tokyo');
    return [];
  });
});
afterEach(() => board.signOut());

it('loads scoped participant identities for a guest with no assigned tasks', async () => {
  board.token = 'guest-token';
  agents.roster = [{ account: { id: 'project-agent', handle: 'project-agent', display_name: 'Project agent', kind: 'agent' } }] as typeof agents.roster;
  await board.start();
  expect(board.ready).toBe(true);
  expect(board.accounts['project-agent'].handle).toBe('project-agent');
  expect(board.pickable.map((a) => a.id)).toContain('project-agent');
  expect(vi.mocked(get).mock.calls.some(([path]) => path === '/accounts')).toBe(false);
});

it('invalidates cached sidebar day counts when the workspace zone changes', async () => {
  board.token = 'saved-token';
  board.now = boundary;
  vi.mocked(get).mockImplementation(async (path) => {
    if (path === '/me') return me('Asia/Tokyo');
    if (path === '/lists') return [{ id: 'list', statuses: [] }];
    if (path === '/items') return [{ id: 'item', list_id: 'list', assignees: [], status: { kind: 'open' }, due_at: '2030-01-02T10:00:00Z' }];
    return [];
  });
  await board.start();
  expect(board.todayCount).toBe(1);
  setWorkspaceTimezone('America/Chicago');
  expect(board.todayCount).toBe(0);
});

it('applies sign-in metadata before board loading, and resets on sign-out', async () => {
  vi.mocked(api).mockResolvedValue(me('Asia/Tokyo'));
  await board.signIn('test-token');
  expect(board.ready).toBe(false);
  expect(workspaceDay(boundary)).toBe('2030-01-02');
  board.signOut();
  expect(workspaceTimezone()).toBe('UTC');
});

it.each(['saved-token', COOKIE_SESSION])('loads %s metadata before marking board data ready', async (token) => {
  board.token = token;
  let resolveMe!: (value: Me) => void;
  const pending = new Promise<Me>((resolve) => { resolveMe = resolve; });
  vi.mocked(get).mockImplementation(async (path) => {
    if (path === '/me') return pending;
    return [];
  });
  const loading = board.start();
  await Promise.resolve();
  expect(board.ready).toBe(false);
  resolveMe(me('Asia/Tokyo'));
  await loading;
  expect(board.ready).toBe(true);
  expect(workspaceDay(boundary)).toBe('2030-01-02');
  vi.mocked(get).mockImplementation(async (path) => {
    if (path === '/me') return me('Europe/London');
    return [];
  });
  await board.start();
  expect(workspaceTimezone()).toBe('Europe/London');
  expect(workspaceDay(boundary)).toBe('2030-01-01');
});
