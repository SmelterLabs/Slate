import type { Me } from './types';

/** Public connection metadata only. Pairing codes live solely in the open dialog. */
export interface AgentConnection {
  id: string;
  account_id: string;
  label: string;
  state: 'waiting' | 'connected' | 'ready' | 'problem' | 'offline' | 'revoked' | 'expired';
  capabilities: string[];
  last_seen_at: string | null;
  verified_at: string | null;
  detail: string;
  expires_at?: string | null;
}
export interface Pairing {
  connection: AgentConnection;
  code: string;
  expires_at: string;
}

export function canManageConnections(me: Me | null): boolean {
  return !!me?.scopes.includes('admin') && me.access?.manage === true && !me.access.limited;
}

export function pairingExpired(expiresAt: string, now: Date): boolean {
  const expires = Date.parse(expiresAt);
  return !Number.isFinite(expires) || expires <= now.getTime();
}

export function connectionCommand(origin: string): string {
  const url = new URL(origin);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('Use the Slate server origin only.');
  }
  return `slate-connect connect --url ${url.origin}`;
}

export function connectionStatus(c: AgentConnection, now: Date): { label: string; detail: string } {
  if (c.state === 'revoked') return { label: 'Disconnected', detail: 'Slate access was revoked. This does not stop a process on the other computer.' };
  if (c.state === 'expired' || (c.state === 'waiting' && c.expires_at && pairingExpired(c.expires_at, now))) {
    return { label: 'Code expired', detail: 'Create a new code and enter it in the terminal on the agent’s computer.' };
  }
  if (c.state === 'waiting') return { label: 'Waiting for your computer', detail: 'Run the connection command on the agent’s computer. Creating a code does not set up an executor.' };
  const recent = (at: string | null) => at != null && Number.isFinite(Date.parse(at)) && now.getTime() - Date.parse(at) <= 120_000;
  if (c.state === 'offline' || (c.last_seen_at && !recent(c.last_seen_at))) {
    return { label: 'Offline', detail: 'No recent connection check. Check the connection on the agent’s computer and start it again.' };
  }
  if (c.state === 'problem') return { label: 'Needs attention', detail: c.detail || 'Check the setup on the agent’s computer, then run its connection test again.' };
  if (c.state === 'ready' && recent(c.last_seen_at) && c.verified_at != null && Number.isFinite(Date.parse(c.verified_at))) return { label: 'Ready', detail: c.detail || 'Agent test passed; connection active.' };
  if (!recent(c.last_seen_at)) return { label: 'Status unknown', detail: 'No recent connection check is available.' };
  return { label: 'Connected; test not run', detail: 'Slate can be reached, but a successful agent test has not been reported. Run the connection test on the agent’s computer.' };
}
