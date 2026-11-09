// One search (GET /search): typed hits over tasks, docs and chat, used by the command palette
// below its local matches. The fetch is in a small helper so the grouping is testable.

import { get } from './api';

export type SearchKind = 'task' | 'doc' | 'message';

export interface SearchHit {
  kind: SearchKind;
  id: string;
  title: string;
  snippet: string | null;
  where: string | null;
  at: string;
  list_id?: string | null;
  channel?: string | null;
  thread_id?: string | null;
}

export interface SearchGroup {
  kind: SearchKind;
  label: string;
  hits: SearchHit[];
}

/** The server is asked after this long without further typing. */
export const SEARCH_DEBOUNCE_MS = 150;
export const SEARCH_MIN = 2;

const LABEL: Record<SearchKind, string> = { task: 'Tasks', doc: 'Docs', message: 'Chat' };

/** Two or more characters that are not a command (`>`), tag (`#`) or person (`@`) search. */
export function shouldSearch(query: string): boolean {
  const q = query.trim();
  return q.length >= SEARCH_MIN && !/^[>#@]/.test(q);
}

export async function searchAll(q: string, limit = 30): Promise<SearchHit[]> {
  const out = await get<{ q: string; hits: SearchHit[] }>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  return out.hits;
}

/** Hits grouped Tasks / Docs / Chat in that order, leaving out tasks and docs the palette
 * already shows from its local index (`shown`: ids per kind). Empty groups are dropped. */
export function groupHits(hits: SearchHit[], shown: { task?: Iterable<string>; doc?: Iterable<string> } = {}): SearchGroup[] {
  const skip: Record<SearchKind, Set<string>> = {
    task: new Set(shown.task ?? []),
    doc: new Set(shown.doc ?? []),
    message: new Set(),
  };
  const groups: SearchGroup[] = (['task', 'doc', 'message'] as const).map((kind) => ({ kind, label: LABEL[kind], hits: [] }));
  const seen = new Set<string>();
  for (const h of hits) {
    const key = `${h.kind}:${h.id}`;
    if (seen.has(key) || skip[h.kind].has(h.id)) continue;
    seen.add(key);
    groups.find((g) => g.kind === h.kind)?.hits.push(h);
  }
  return groups.filter((g) => g.hits.length > 0);
}

/** Where a hit opens: the task window, the doc page, or the chat channel (at its thread). */
export function hitHref(h: SearchHit): string | null {
  if (h.kind === 'doc') return `#/docs/${h.id}`;
  if (h.kind === 'message' && h.channel) return `#/chat/${h.channel}${h.thread_id ? `?thread=${h.thread_id}` : ''}`;
  return null;
}
