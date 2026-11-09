// Revert from the log (POST /events/{id}/revert): which history and audit rows offer the
// button, and the call. The server writes the inverse as a new event; nothing is edited.

import { post } from './api';
import type { Item } from './types';

/** `item.updated` fields the server can put back (service/revert.py FIELDS). */
export const REVERTABLE_FIELDS = new Set([
  'title',
  'priority',
  'start_at',
  'due_at',
  'due_date_only',
  'waiting_on_account_id',
  'acceptance_md',
  'estimate_minutes',
  'kind',
  'external_ref',
  'position',
  'body_md',
  'tags',
]);

export const REVERTABLE_TYPES = new Set(['item.status_changed', 'item.assigned', 'item.moved']);

export interface Revertable {
  type: string;
  field: string | null;
  actor_id: string;
  session_ref?: string | null;
}

/** True for a change someone else made that the server knows how to put back. A row that is
 * itself a revert is left alone: reverting a revert is just the original change again. */
export function canRevert(ev: Revertable, me: string | null | undefined): boolean {
  if (!me || ev.actor_id === me) return false;
  if ((ev.session_ref ?? '').startsWith('revert:')) return false;
  if (ev.type === 'item.updated') return !!ev.field && REVERTABLE_FIELDS.has(ev.field);
  return REVERTABLE_TYPES.has(ev.type);
}

/** The one-line question before the call. */
export function revertQuestion(ev: Revertable, who: string): string {
  const what =
    ev.type === 'item.status_changed'
      ? 'the status change'
      : ev.type === 'item.assigned'
        ? 'the change to who is on it'
        : ev.type === 'item.moved'
          ? 'the move'
          : `the change to ${ev.field === 'body_md' ? 'the description' : (ev.field ?? 'it')}`;
  return `Put back ${what} ${who} made? This writes a new change as you; the history keeps both.`;
}

export function revertEvent(eventId: string): Promise<Item> {
  return post<Item>(`/events/${eventId}/revert`);
}
