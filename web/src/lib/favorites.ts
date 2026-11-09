// The favorites bar's pure parts: what each pinned target shows and where it goes, and the order
// after a drag. The store owns the data and the API calls (store.svelte.ts "favorites").

import type { Favorite, Item, List, SavedView, Section } from './types';
import type { Scope } from './views';

export interface FavCtx {
  lists: Record<string, List>;
  sections: Section[];
  views: Record<string, SavedView>;
  items: Record<string, Item>;
  selection: Scope;
  viewId: string | null;
  openId: string | null;
  /** The docs index and the page on screen (docs.svelte.ts); absent before it loads. */
  docs?: Record<string, { title: string; icon: string | null; archived_at: string | null }>;
  docId?: string | null;
}

export interface Chip {
  fav: Favorite;
  label: string;
  /** Where the chip sits: "List in Personal", "View of Todo". The tooltip. */
  hint: string;
  /** A hash link, or null for an item (it opens the task window instead). */
  href: string | null;
  icon: string;
  active: boolean;
}

export const ICONS: Record<Favorite['kind'], string> = {
  list: '☰',
  section: '▤',
  view: '◫',
  item: '◉',
  doc: '📄',
};

function hashFor(kind: 'everything' | 'section' | 'list', id: string | null, viewId?: string) {
  const base = kind === 'everything' || !id ? '#/everything' : `#/${kind}/${id}`;
  return viewId ? `${base}/v/${viewId}` : base;
}

function scopeName(c: FavCtx, kind: string | null, id: string | null): string {
  if (kind === 'list' && id) return c.lists[id]?.name ?? 'a list';
  if (kind === 'section' && id) return c.sections.find((s) => s.id === id)?.name ?? 'a section';
  return 'Everything';
}

function sameScope(s: Scope, kind: string | null, id: string | null): boolean {
  if (s.kind === 'everything') return kind === 'everything';
  return s.kind === kind && s.id === id;
}

/** The chip for one favorite, or null while its target is archived or not loaded. */
export function chipFor(fav: Favorite, c: FavCtx): Chip | null {
  const icon = ICONS[fav.kind];
  const viewPinned = !!c.viewId && sameScope(c.selection, c.views[c.viewId]?.scope_kind ?? null, c.views[c.viewId]?.scope_id ?? null);
  switch (fav.kind) {
    case 'list': {
      const list = c.lists[fav.target_id];
      if (!list || list.archived_at) return null;
      const section = c.sections.find((s) => s.id === list.section_id);
      return {
        fav,
        label: list.name,
        hint: section ? `List in ${section.name}` : 'List',
        href: hashFor('list', list.id),
        icon,
        active: sameScope(c.selection, 'list', list.id) && !viewPinned,
      };
    }
    case 'section': {
      const section = c.sections.find((s) => s.id === fav.target_id);
      if (!section || section.archived_at) return null;
      return {
        fav,
        label: section.name,
        hint: 'Section',
        href: hashFor('section', section.id),
        icon,
        active: sameScope(c.selection, 'section', section.id) && !viewPinned,
      };
    }
    case 'view': {
      const view = c.views[fav.target_id];
      if (!view || view.archived_at) return null;
      return {
        fav,
        label: view.name,
        hint: `View of ${scopeName(c, view.scope_kind, view.scope_id)}`,
        href: hashFor(view.scope_kind, view.scope_id, view.id),
        icon,
        active: c.viewId === view.id && sameScope(c.selection, view.scope_kind, view.scope_id),
      };
    }
    case 'doc':
      return docChip(fav, c);
    case 'item': {
      const item = c.items[fav.target_id];
      if (item?.archived_at || (!item && !fav.available)) return null;
      const listId = item?.list_id ?? fav.scope_id;
      return {
        fav,
        label: item?.title ?? fav.name ?? 'Task',
        hint: `Task in ${scopeName(c, 'list', listId)}`,
        href: null,
        icon,
        active: c.openId === fav.target_id,
      };
    }
  }
}

/** The chip for a pinned doc page: its live title and emoji when the index has it. */
function docChip(fav: Favorite, c: FavCtx): Chip | null {
  const d = c.docs?.[fav.target_id];
  if (d?.archived_at || (!d && !fav.available)) return null;
  return {
    fav,
    label: d?.title ?? fav.name ?? 'Page',
    hint: 'Doc page',
    href: `#/docs/${fav.target_id}`,
    icon: d?.icon || ICONS.doc,
    active: c.docId === fav.target_id,
  };
}

/** Move `id` so it lands just before `beforeId` (null: at the end). Returns the new order. */
export function moveBefore(ids: string[], id: string, beforeId: string | null): string[] {
  if (id === beforeId) return ids;
  const rest = ids.filter((x) => x !== id);
  const at = beforeId === null ? rest.length : rest.indexOf(beforeId);
  if (at < 0) return ids;
  rest.splice(at, 0, id);
  return rest;
}
