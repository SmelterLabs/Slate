import { describe, expect, it } from 'vitest';
import { connectionStatus, canManageConnections, connectionCommand, pairingExpired } from './connections';
import type { AgentConnection } from './connections';
import type { Me } from './types';

const now = new Date('2026-11-02T20:00:00Z');
const row: AgentConnection = {
  id: 'C', account_id: 'A', label: 'Laptop', state: 'ready', capabilities: ['task'],
  last_seen_at: now.toISOString(), verified_at: now.toISOString(), detail: 'Agent test passed.',
};

describe('truthful connection status', () => {
  it('requires recent contact AND a verified executor test, not an issued token or live stream', () => {
    expect(connectionStatus(row, now).label).toBe('Ready');
    expect(connectionStatus({ ...row, verified_at: null }, now).label).toBe('Connected; test not run');
    expect(connectionStatus({ ...row, last_seen_at: null }, now).label).not.toBe('Ready');
    // The explicit model test is not rerun on every heartbeat; current contact is.
    expect(connectionStatus({ ...row, verified_at: '2026-11-02T19:55:00Z' }, now).label).toBe('Ready');
    expect(connectionStatus({ ...row, last_seen_at: '2026-11-02T19:55:00Z' }, now).label).toBe('Offline');
    expect(connectionStatus({ ...row, state: 'revoked' }, now).label).toBe('Disconnected');
    expect(connectionStatus({ ...row, state: 'problem' }, now).label).toBe('Needs attention');
  });
  it('expires pending codes locally, without fetching, and treats missing expiry as unknown', () => {
    expect(pairingExpired(now.toISOString(), now)).toBe(true);
    expect(pairingExpired('invalid', now)).toBe(true);
    expect(connectionStatus({ ...row, state: 'waiting', expires_at: now.toISOString() }, now).label).toBe('Code expired');
  });
  it('shows management only for an unrestricted account manager with admin scope', () => {
    const me = { account: { role: 'owner' }, scopes: ['admin'], access: { manage: true, limited: false } } as Me;
    expect(canManageConnections(me)).toBe(true);
    expect(canManageConnections({ ...me, access: { ...me.access!, limited: true } })).toBe(false);
    expect(canManageConnections({ ...me, access: { ...me.access!, manage: false } })).toBe(false);
    expect(canManageConnections({ ...me, scopes: ['write'] })).toBe(false);
    expect(canManageConnections(null)).toBe(false);
  });
  it('shows only the origin in the local command, never a code, path, query or credentials', () => {
    expect(connectionCommand('https://slate.example')).toBe('slate-connect connect --url https://slate.example');
    expect(() => connectionCommand('https://user:secret@slate.example/path?code=secret')).toThrow();
  });
});
