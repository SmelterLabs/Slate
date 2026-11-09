// Pure helpers for managing structure from the board: reordering sections and
// lists, and the statuses editor's guard. Unit-tested; the store does the writes.

import type { Item, StatusKind } from './types';

export interface Positioned {
  id: string;
  position: number;
}

/**
 * Move `id` one step up (-1) or down (+1) among `rows` (any order). Returns the PATCHes that
 * make positions 0..n-1 in the new order, skipping rows already in place; [] at an edge.
 */
export function reorder(rows: Positioned[], id: string, dir: -1 | 1): Positioned[] {
  const order = [...rows].sort((a, b) => a.position - b.position);
  const at = order.findIndex((r) => r.id === id);
  const to = at + dir;
  if (at < 0 || to < 0 || to >= order.length) return [];
  [order[at], order[to]] = [order[to], order[at]];
  return order
    .map((r, i) => ({ id: r.id, position: i, was: r.position }))
    .filter((r) => r.position !== r.was)
    .map(({ id, position }) => ({ id, position }));
}

/** Move a section around another section, never move any list between sections. */
export function placeSection(rows: Positioned[], id: string, target: string, after: boolean): Positioned[] {
  if (id === target || !rows.some(r => r.id === id) || !rows.some(r => r.id === target)) return [];
  const order = [...rows].sort((a, b) => a.position - b.position).filter(r => r.id !== id);
  order.splice(order.findIndex(r => r.id === target) + Number(after), 0, rows.find(r => r.id === id)!);
  return order.map((r, position) => ({ id: r.id, position })).filter(r => rows.find(s => s.id === r.id)?.position !== r.position);
}

export interface Placed {
  id: string;
  position: number;
  /** Set only for the moved row, and only when it changes section. */
  section_id?: string;
}

/**
 * Put list `id` into `sectionId` before `beforeId` (or last). `target` is that section's lists
 * in any order (the moved list may be among them). Returns the PATCHes: the moved list always
 * (with `section_id` when it changes section), the others only where their position changes.
 */
export function place(
  target: Positioned[],
  id: string,
  fromSectionId: string,
  sectionId: string,
  beforeId: string | null,
): Placed[] {
  const order = [...target].filter((r) => r.id !== id).sort((a, b) => a.position - b.position);
  const at = beforeId ? order.findIndex((r) => r.id === beforeId) : -1;
  const index = at < 0 ? order.length : at;
  const was = new Map(target.map((r) => [r.id, r.position]));
  const ids = [...order.map((r) => r.id)];
  ids.splice(index, 0, id);
  const moved = fromSectionId !== sectionId;
  const out: Placed[] = [];
  ids.forEach((rid, position) => {
    if (rid === id) {
      if (moved || was.get(id) !== position) {
        out.push(moved ? { id, position, section_id: sectionId } : { id, position });
      }
    } else if (was.get(rid) !== position) {
      out.push({ id: rid, position });
    }
  });
  // The moved list first, so it is in its new section before its neighbours shuffle.
  return out.sort((a, b) => Number(b.id === id) - Number(a.id === id));
}

export interface StatusDraft {
  /** Existing status id; absent for a new one. */
  id?: string;
  name: string;
  kind: StatusKind;
  color: string | null;
}

/** Items (top-level and subtasks, not archived) sitting on statuses the draft removes. */
export function strandedCount(
  items: Iterable<Item>,
  listId: string,
  existingIds: string[],
  draft: StatusDraft[],
): number {
  const kept = new Set(draft.map((s) => s.id).filter(Boolean));
  const removed = new Set(existingIds.filter((id) => !kept.has(id)));
  let n = 0;
  for (const i of items) {
    if (i.list_id === listId && !i.archived_at && i.status && removed.has(i.status.id)) n++;
  }
  return n;
}

/** Why a draft cannot be saved, in plain words, or null when it can. */
export function draftProblem(draft: StatusDraft[]): string | null {
  if (!draft.length) return 'A list needs at least one status.';
  const seen = new Set<string>();
  for (const s of draft) {
    const name = s.name.trim();
    if (!name) return 'Every status needs a name.';
    if (seen.has(name.toLowerCase())) return `"${name}" appears twice.`;
    seen.add(name.toLowerCase());
  }
  return null;
}

/** A soft note: allowed, but the Complete button and done counts need a Done-kind status. */
export function draftWarning(draft: StatusDraft[]): string | null {
  return draft.some((s) => s.kind === 'done')
    ? null
    : 'No Done-kind status: items in this list cannot be completed with one click.';
}

/** Status colours offered by the editor (null = the kind's colour). */
export const SWATCHES = [
  '#8b929c',
  '#6aa2ff',
  '#a78bfa',
  '#f472b6',
  '#ff9f43',
  '#facc15',
  '#3ecf8e',
  '#ff5c5c',
];
