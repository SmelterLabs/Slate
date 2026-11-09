import { afterEach, expect, it, vi } from 'vitest';
import { connections } from './connection-data.svelte';
import { api, get, ApiError } from './api';
import { ev } from './fixtures';
vi.mock('./api', async (load) => ({ ...await load<typeof import('./api')>(), get: vi.fn(), api: vi.fn() }));
afterEach(() => { connections.reset(); vi.clearAllMocks(); vi.useRealTimers(); });

it('initial fetch unwraps connections, then only a visible connection event refreshes watched accounts', async () => {
  vi.useFakeTimers();
  vi.mocked(get).mockResolvedValue({ connections: [] });
  const stop = connections.watch('A');
  await vi.waitFor(() => expect(connections.byAccount.A?.loaded).toBe(true));
  expect(get).toHaveBeenCalledWith('/agent-connections?account_id=A');
  await vi.advanceTimersByTimeAsync(300_000);
  expect(get).toHaveBeenCalledTimes(1);
  connections.onEvent(ev({ type: 'item.updated' }));
  expect(get).toHaveBeenCalledTimes(1);
  connections.onEvent(ev({ type: 'agent.connection_ready', after: { account_id: 'B' } }));
  expect(get).toHaveBeenCalledTimes(1);
  connections.onEvent(ev({ type: 'agent.connection_ready', after: { account_id: 'A' } }));
  await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  stop();
  connections.onEvent(ev({ type: 'agent.connection_revoked', after: { account_id: 'A' } }));
  expect(get).toHaveBeenCalledTimes(2);
});

it('a late response cannot restore private connections after logout or scope reset', async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(get).mockImplementation(() => new Promise((done) => { resolve = done; }));
  const loading = connections.load('A');
  connections.reset();
  resolve({ connections: [{ id: 'private', account_id: 'A' }] });
  await loading;
  expect(connections.byAccount.A).toBeUndefined();
});

it('permission failure clears formerly known health instead of claiming offline or retaining ready', async () => {
  vi.mocked(get).mockResolvedValueOnce({ connections: [{ id: 'C', state: 'ready' }] });
  await connections.load('A');
  vi.mocked(get).mockRejectedValueOnce(new ApiError(403, 'Forbidden'));
  await connections.load('A');
  expect(connections.byAccount.A.rows).toEqual([]);
  expect(connections.byAccount.A.error).toMatch(/not available.*access/i);
});

it('revoke reads back the exact account before claiming disconnection', async () => {
  vi.mocked(api).mockResolvedValue(undefined);
  vi.mocked(get).mockResolvedValue({ connections: [{ id: 'C', state: 'revoked' }] });
  await connections.revoke('A', 'C');
  expect(api).toHaveBeenCalledWith('DELETE', '/agent-connections/C');
  expect(get).toHaveBeenCalledWith('/agent-connections?account_id=A');
  expect(connections.byAccount.A.rows[0].state).toBe('revoked');
});
