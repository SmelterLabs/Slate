// Roles, sharing levels and the audit log, as the board shows them. Pure helpers: the server
// (service/access.py) decides what anyone can do; these only decide what the board offers.

import { workspaceDate, workspaceFormatter } from './timezone';
import type { AuditEntry, Level, Me, MyAccess, Role, SlateEvent } from './types';

export const ROLES: Role[] = ['owner', 'admin', 'member', 'guest'];
export const LEVELS: Level[] = ['view', 'comment', 'edit'];

export const ROLE_LABEL: Record<Role, string> = {
  owner: 'Owner',
  admin: 'Admin',
  member: 'Member',
  guest: 'Guest',
};

export const ROLE_HINT: Record<Role, string> = {
  owner: 'Everything, including who is an admin. There is one owner.',
  admin: 'Everything: members, sharing, private lists.',
  member: 'Every section and list that is not private.',
  guest: 'Only the sections and lists shared with them.',
};

export const LEVEL_LABEL: Record<Level, string> = {
  view: 'Can view',
  comment: 'Can comment',
  edit: 'Can edit',
};

const RANK: Record<Level, number> = { view: 1, comment: 2, edit: 3 };

export function atLeast(have: Level | null | undefined, need: Level): boolean {
  return !!have && RANK[have] >= RANK[need];
}

/** This token's level on a list, or null when it cannot see it. No access info = everything. */
export function listLevel(access: MyAccess | undefined, listId: string | null | undefined): Level | null {
  if (!listId) return null;
  if (!access || access.all) return access?.level ?? 'edit';
  return access.lists[listId] ?? null;
}

export function sectionLevel(access: MyAccess | undefined, sectionId: string): Level | null {
  if (!access || access.all) return access?.level ?? 'edit';
  return access.sections[sectionId] ?? null;
}

/** Can this token create sections (members and up, unlimited tokens)? */
export function canCreateSections(me: Me | null): boolean {
  if ((me?.account.role ?? 'member') === 'guest') return false;
  return !me?.access?.limited && atLeast(me?.access?.level ?? 'edit', 'edit');
}

/** A live event that may change what this board can see: reload /me and the board. */
export function affectsMyAccess(ev: SlateEvent, me: Me | null): boolean {
  if (!me) return false;
  const mine = me.account.id;
  if (ev.type.startsWith('share.')) {
    const row = (ev.after ?? ev.before) as { account_id?: string } | null;
    return row?.account_id === mine;
  }
  if (ev.type === 'account.role_changed') return ev.subject_id === mine;
  if (ev.field === 'private' && (ev.type === 'section.updated' || ev.type === 'list.updated')) {
    return !me.access?.all;
  }
  return !!me.access && !me.access.all && (ev.type === 'list.created' || ev.type === 'section.created');
}

export interface AuditFilters {
  actor: string;
  kind: '' | 'person' | 'agent';
  source: '' | 'ui' | 'api' | 'mcp' | 'system';
  type: string;
  section: string;
  list: string;
  since: string; // YYYY-MM-DD (local)
  until: string; // YYYY-MM-DD (local, inclusive)
  q: string;
}

export const NO_FILTERS: AuditFilters = {
  actor: '',
  kind: '',
  source: '',
  type: '',
  section: '',
  list: '',
  since: '',
  until: '',
  q: '',
};

/** A local date at midnight as a UTC ISO instant; `next` moves to the following midnight. */
function dayStart(day: string, next = false): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d + (next ? 1 : 0)).toISOString();
}

/** Query string for GET /audit and /audit.csv. */
export function auditQuery(f: AuditFilters, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams();
  if (f.actor) q.set('actor', f.actor);
  if (f.kind) q.set('actor_kind', f.kind);
  if (f.source) q.set('source', f.source);
  if (f.type) q.set('type', f.type);
  if (f.list) q.set('list_id', f.list);
  else if (f.section) q.set('section_id', f.section);
  if (f.since) q.set('since', dayStart(f.since));
  if (f.until) q.set('until', dayStart(f.until, true));
  if (f.q.trim()) q.set('q', f.q.trim());
  for (const [k, v] of Object.entries(extra)) q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : '';
}

export function activeFilters(f: AuditFilters): number {
  return (Object.keys(f) as (keyof AuditFilters)[]).filter((k) => f[k] !== '').length;
}

/** Entries grouped by workspace day, newest first (entries arrive newest first). */
export function byDay(entries: AuditEntry[], now = new Date()): { day: string; label: string; entries: AuditEntry[] }[] {
  const out: { day: string; label: string; entries: AuditEntry[] }[] = [];
  const key = (d: Date) => workspaceDate(d);
  const today = key(now);
  const yesterday = key(new Date(now.getTime() - 86_400_000));
  const fmt = workspaceFormatter(undefined as unknown as string, { weekday: 'long', month: 'short', day: 'numeric' });
  for (const e of entries) {
    const d = new Date(e.at);
    const k = key(d);
    let group = out[out.length - 1];
    if (!group || group.day !== k) {
      const label = k === today ? 'Today' : k === yesterday ? 'Yesterday' : fmt.format(d);
      group = { day: k, label, entries: [] };
      out.push(group);
    }
    group.entries.push(e);
  }
  return out;
}

/** The kind of change, for the colored dot in the log: create, delete, access, or change. */
export function auditTone(type: string): 'create' | 'remove' | 'access' | 'change' {
  if (type.startsWith('share.') || type === 'account.role_changed' || type.startsWith('token.')) return 'access';
  if (/\.(created|posted|logged|granted|started)$/.test(type)) return 'create';
  if (/\.(deleted|archived|trashed|purged|revoked|disabled)$/.test(type)) return 'remove';
  return 'change';
}

/** A short before -> after line for a field change; null when there is nothing to show. */
export function changeLine(e: AuditEntry): string | null {
  if (!e.field) return null;
  const show = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'string') return v.length > 60 ? `${v.slice(0, 57)}…` : v;
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (typeof v === 'number') return String(v);
    const s = JSON.stringify(v);
    return s.length > 60 ? `${s.slice(0, 57)}…` : s;
  };
  return `${show(e.before)} → ${show(e.after)}`;
}

/** "Everything", or "2 sections, 3 lists" for an account's reach. */
export function reachLabel(access: { all: boolean; sections: Record<string, unknown>; lists: Record<string, unknown> }): string {
  if (access.all) return 'Everything';
  const s = Object.keys(access.sections).length;
  const l = Object.keys(access.lists).length;
  if (!s && !l) return 'Nothing shared yet';
  const parts = [];
  if (s) parts.push(`${s} section${s === 1 ? '' : 's'}`);
  if (l) parts.push(`${l} list${l === 1 ? '' : 's'}`);
  return parts.join(', ');
}

/** The workspace's agent deny-list (`GET /settings/agents`, service/agent_rules.py). */
export interface AgentRules {
  denied: string[];
  default: boolean;
  actions: { key: string; label: string }[];
}

/** `denied` with `key` turned on or off, kept in the vocabulary's order (what PUT expects). */
export function toggleDenied(rules: AgentRules, key: string, deny: boolean): string[] {
  const next = new Set(rules.denied);
  if (deny) next.add(key);
  else next.delete(key);
  return rules.actions.map((a) => a.key).filter((k) => next.has(k));
}

/** Whether a live event changed the agent rules (the Members page reloads them). */
export function changesAgentRules(ev: Pick<SlateEvent, 'type' | 'field'>): boolean {
  return ev.type === 'settings.changed' && ev.field === 'agents.denied';
}
