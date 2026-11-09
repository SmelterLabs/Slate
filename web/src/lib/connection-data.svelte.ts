import { api, ApiError, get } from './api';
import type { AgentConnection } from './connections';
import type { SlateEvent } from './types';

interface ConnectionData { rows: AgentConnection[]; loaded: boolean; error: string | null }

/** Initial fetch + the board's existing SSE stream. No network timer. */
class Connections {
  byAccount = $state<Record<string, ConnectionData>>({});
  #scope = 0;
  #generation = new Map<string, number>();
  #watchers = new Map<string, number>();

  watch(id: string): () => void {
    this.#watchers.set(id, (this.#watchers.get(id) ?? 0) + 1);
    void this.load(id);
    const scope = this.#scope;
    return () => {
      if (scope !== this.#scope) return;
      const count = (this.#watchers.get(id) ?? 1) - 1;
      if (count) this.#watchers.set(id, count);
      else this.#watchers.delete(id);
    };
  }

  async load(id: string): Promise<void> {
    const scope = this.#scope;
    const generation = (this.#generation.get(id) ?? 0) + 1;
    this.#generation.set(id, generation);
    this.byAccount[id] ??= { rows: [], loaded: false, error: null };
    const current = () => scope === this.#scope && generation === this.#generation.get(id);
    try {
      const result = await get<{ connections: AgentConnection[] }>(`/agent-connections?account_id=${encodeURIComponent(id)}`);
      if (current()) this.byAccount[id] = { rows: result.connections, loaded: true, error: null };
    } catch (err) {
      if (!current()) return;
      this.byAccount[id] = { rows: [], loaded: true, error: err instanceof ApiError && [403, 404].includes(err.status)
        ? 'Connection details are not available with your access.'
        : 'Could not check the connection. Try again; its status is unknown.' };
    }
  }

  onEvent(ev: SlateEvent) {
    if (!ev.type.startsWith('agent.connection_')) return;
    const account = (ev.after as { account_id?: string } | null)?.account_id
      ?? (ev.before as { account_id?: string } | null)?.account_id;
    // Redacted events may omit account metadata; re-read only already entitled, open panels.
    for (const id of this.#watchers.keys()) if (!account || account === id) void this.load(id);
  }

  async revoke(accountId: string, id: string) {
    await api('DELETE', `/agent-connections/${encodeURIComponent(id)}`);
    await this.load(accountId);
    if (this.byAccount[accountId]?.rows.find((c) => c.id === id)?.state !== 'revoked') {
      throw new Error('Revocation was requested, but could not be confirmed. Check the connection again.');
    }
  }

  reset() {
    this.#scope++;
    this.#generation.clear();
    this.#watchers.clear();
    this.byAccount = {};
  }
}
export const connections = new Connections();
