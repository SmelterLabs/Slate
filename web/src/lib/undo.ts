// Undo: what PATCH puts an item back the way it was before `changes`. Pure, unit-tested; the
// store keeps the stack (Ctrl/Cmd+Z, and the Undo button on a toast).

import type { Item } from './types';
import { splitWhen } from './when';

/** Fields the board can put back. Anything else in a change makes it not undoable. */
const PLAIN = ['title', 'body_md', 'priority', 'due_at', 'start_at', 'acceptance_md'] as const;

/**
 * The PATCH body that reverses `changes` on `before` (the item as it was), or null when a field
 * cannot be put back from here. `list_id` comes back separately: it moves through /items/bulk.
 */
export function revertPatch(before: Item, changes: Record<string, unknown>): Record<string, unknown> | null {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(changes)) {
    if (key === 'due_at' && before.due_at && before.due_date_only) {
      out.due_at = splitWhen(before.due_at, true).date; // a bare date keeps it date-only
    } else if ((PLAIN as readonly string[]).includes(key)) {
      out[key] = (before as unknown as Record<string, unknown>)[key] ?? null;
    } else if (key === 'status' || key === 'status_kind') {
      if (!before.status) return null;
      out.status = before.status.id;
    } else if (key === 'assignees') {
      out.assignees = before.assignees.map((a) => a.handle);
    } else if (key === 'tags') {
      out.tags = [...before.tags];
    } else if (key === 'list_id') {
      out.list_id = before.list_id;
    } else {
      return null;
    }
  }
  return out;
}

/** Group per-item reverts that are identical, so a bulk undo is one call per distinct shape. */
export function groupReverts(reverts: [string, Record<string, unknown>][]): { ids: string[]; changes: Record<string, unknown> }[] {
  const groups = new Map<string, { ids: string[]; changes: Record<string, unknown> }>();
  for (const [id, changes] of reverts) {
    const key = JSON.stringify(Object.entries(changes).sort(([a], [b]) => a.localeCompare(b)));
    const g = groups.get(key);
    if (g) g.ids.push(id);
    else groups.set(key, { ids: [id], changes });
  }
  return [...groups.values()];
}

/** A short past-tense label for a change, for the toast and the "Undid …" message. */
export function changeLabel(changes: Record<string, unknown>, count = 1): string {
  const keys = Object.keys(changes);
  const what = count === 1 ? 'task' : `${count} tasks`;
  if (keys.length === 1) {
    switch (keys[0]) {
      case 'status':
      case 'status_kind':
        return `Moved ${what}`;
      case 'due_at':
        return changes.due_at ? `Rescheduled ${what}` : `Cleared the due date on ${what}`;
      case 'priority':
        return `Changed priority on ${what}`;
      case 'assignees':
        return `Reassigned ${what}`;
      case 'tags':
        return `Retagged ${what}`;
      case 'list_id':
        return `Moved ${what} to another list`;
      case 'title':
        return `Renamed ${what}`;
      case 'archive':
        return changes.archive ? `Put away ${what}` : `Brought back ${what}`;
    }
  }
  return `Changed ${what}`;
}
