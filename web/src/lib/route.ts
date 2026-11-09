// The board's URL: `#/everything[/v/<view>]`, `#/section/<id>[/v/<view>]`, `#/list/<id>[/v/<view>]`,
// or `#/agents` (the agents page; it leaves the selected scope alone).
// `#/today` opens the seeded Agenda view of Everything. `kiosk=1` (before the hash, or as a
// query on it) turns on monitor mode.

import type { Scope } from './views';

export const AGENDA_VIEW_ID = 'vwagenda0001';

export interface Route {
  scope: Scope;
  viewId: string | null;
  page?: 'agents' | 'automations';
  /** `#/list/<id>/chat`, `#/section/<id>/chat`: the scope's Channel tab instead of a view. */
  chat?: boolean;
}

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, '').split('?')[0];
  if (/^\/agents\/?$/.test(path)) return { scope: { kind: 'everything' }, viewId: null, page: 'agents' };
  if (/^\/automations\/?$/.test(path)) return { scope: { kind: 'everything' }, viewId: null, page: 'automations' };
  if (/^\/today\/?$/.test(path)) return { scope: { kind: 'everything' }, viewId: AGENDA_VIEW_ID };
  const m = /^\/(list|section)\/([^/]+)(?:\/v\/([^/]+))?/.exec(path);
  if (m) {
    const scope = { kind: m[1] as 'list' | 'section', id: decodeURIComponent(m[2]) };
    if (/^\/(list|section)\/[^/]+\/chat\/?$/.test(path)) return { scope, viewId: null, chat: true };
    return { scope, viewId: m[3] ?? null };
  }
  const e = /^\/everything(?:\/v\/([^/]+))?/.exec(path);
  return { scope: { kind: 'everything' }, viewId: e?.[1] ?? null };
}

/** The board's own pages outside any scope: `#/home` (My Work), `#/inbox`, `#/agents`,
 * `#/archive` (`?tab=archived|lists|trash`),
 * `#/automations` (`?list=<id>` narrows it to one list's rules),
 * `#/docs[/<id>]` (the page tree, or one page; `docOf` reads the id) and
 * `#/chat[/<channel>]` (chat; `chatChannelOf` in lib/chat.ts reads the channel), `#/members`
 * (roles and sharing, owners and admins), `#/audit` (the audit log) and `#/health` (pollers,
 * agent errors, slow calls and storage; owners and admins). */
export type Page =
  | 'home'
  | 'inbox'
  | 'agents'
  | 'automations'
  | 'archive'
  | 'docs'
  | 'chat'
  | 'members'
  | 'audit'
  | 'health';

export function pageOf(hash: string): Page | null {
  const path = hash.replace(/^#/, '').split('?')[0];
  if (/^\/home\/?$/.test(path)) return 'home';
  if (/^\/inbox\/?$/.test(path)) return 'inbox';
  if (/^\/agents\/?$/.test(path)) return 'agents';
  if (/^\/automations\/?$/.test(path)) return 'automations';
  if (/^\/archive\/?$/.test(path)) return 'archive';
  if (/^\/docs(\/[^/]+)?\/?$/.test(path)) return 'docs';
  if (/^\/chat(\/[^/]+)?\/?$/.test(path)) return 'chat';
  if (/^\/members\/?$/.test(path)) return 'members';
  if (/^\/audit\/?$/.test(path)) return 'audit';
  if (/^\/health\/?$/.test(path)) return 'health';
  return null;
}

/** The page id in `#/docs/<id>`, or null for `#/docs`. */
export function docOf(hash: string): string | null {
  const m = /^\/docs\/([^/?]+)/.exec(hash.replace(/^#/, ''));
  return m ? decodeURIComponent(m[1]) : null;
}

export function isKiosk(search: string, hash: string): boolean {
  const q = new URLSearchParams(search);
  const h = new URLSearchParams(hash.split('?')[1] ?? '');
  return q.get('kiosk') === '1' || h.get('kiosk') === '1';
}
