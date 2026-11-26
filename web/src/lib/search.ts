// One search (GET /search): typed hits over tasks, docs and chat, used by Universal Search.
// The fetch is in a small helper so the grouping and query building are testable.

import { get } from './api';

export type SearchKind = 'task' | 'doc' | 'message';

export interface SearchHit {
  kind: SearchKind;
  id: string;
  title: string;
  snippet: string | null;
  where: string | null;
  section?: string | null;
  at: string;
  list_id?: string | null;
  channel?: string | null;
  thread_id?: string | null;
  status_name?: string | null;
  status_kind?: string | null;
  status_color?: string | null;
  archived?: boolean;
  assignees?: string[];
  matched_in?: string | null;
}

export interface SearchFilters {
  kind?: SearchKind | null;
  assignee?: string | null;
  list_id?: string | null;
  section_id?: string | null;
  include_closed?: boolean;
  include_archived?: boolean;
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
const CARD_ID_RE = /^#[0-9A-Za-z]{12}$/;

/** Two or more characters that are not a command (`>`), short tag (`#tag`) or person (`@`) search.
 * A `#`-prefixed 12-char card/doc ID (`#iSV1KhGGsGv0`) always searches the server. */
export function shouldSearch(query: string): boolean {
  const q = query.trim();
  if (CARD_ID_RE.test(q)) return true;
  return q.length >= SEARCH_MIN && !/^[>#@]/.test(q);
}

export function buildSearchQuery(q: string, filters: SearchFilters = {}, limit = 30): string {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.assignee) params.set('assignee', filters.assignee);
  if (filters.list_id) params.set('list_id', filters.list_id);
  if (filters.section_id) params.set('section_id', filters.section_id);
  if (filters.include_closed === false) params.set('include_closed', 'false');
  if (filters.include_archived) params.set('include_archived', 'true');
  return params.toString();
}

export async function searchAll(q: string, filters: SearchFilters = {}, limit = 30): Promise<SearchHit[]> {
  const out = await get<{ q: string; hits: SearchHit[] }>(`/search?${buildSearchQuery(q, filters, limit)}`);
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
