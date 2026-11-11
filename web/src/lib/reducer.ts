// Applies one stream event to the in-memory board. Pure except that it mutates `data` in place
// (which behaves the same on a plain object and on a Svelte $state proxy). Returns what the
// payload could not supply: items to refetch, and whether the nav or everything needs reloading.

import type { Item, List, SlateEvent } from './types';

export interface BoardData {
  items: Record<string, Item>;
  lists: Record<string, List>;
}

export interface Effects {
  /** Item ids to GET again: the payload did not carry enough. */
  refetch: string[];
  /** Sections or lists changed: reload the nav. */
  structure: boolean;
  /** A list's statuses were replaced: reload lists and items. */
  reload: boolean;
  /** Anything that can move the Today view or the overdue count. */
  today: boolean;
  /** A saved view was created, changed or archived: reload the views. */
  views: boolean;
  /** A tag was created, renamed, recoloured, merged or deleted. */
  tags: boolean;
  /** Someone's favorites changed: the store reloads them if they are this board's. */
  favorites: boolean;
  /** Someone's capacity changed: reload the capacities. */
  capacities: boolean;
}

// Columns an `item.updated` event may carry that map one-to-one onto `Item`.
const SCALARS = new Set([
  'title',
  'body_md',
  'priority',
  'start_at',
  'due_at',
  'due_date_only',
  'completed_at',
  'waiting_on_account_id',
  'acceptance_md',
  'recurrence_id',
  'occurrence_at',
  'external_ref',
  'position',
  'kind',
  'archived_at',
  'list_id',
  'parent_id',
  'estimate_minutes',
]);

// Event types whose `field` / `after` are a column and its new value.
const COLUMN_CHANGES = new Set(['item.updated', 'item.archived', 'item.unarchived', 'item.moved']);

function statusOf(data: BoardData, listId: string, statusId: unknown) {
  return data.lists[listId]?.statuses.find((s) => s.id === statusId);
}

/** An `item.created` snapshot is the row's columns; tags and assignees follow as own events. */
function fromSnapshot(data: BoardData, snap: Record<string, unknown>): Item | null {
  const listId = snap.list_id as string;
  if (!data.lists[listId]) return null;
  const status = snap.status_id == null ? null : statusOf(data, listId, snap.status_id);
  if (status === undefined) return null;
  const { status_id: _, ...columns } = snap;
  return {
    ...(columns as unknown as Item),
    status: status ? { ...status } : null,
    assignees: [],
    tags: [],
    subtask_count: 0,
    blocked_by: [],
    blocking: [],
    related: [],
    blocked: false,
    comment_count: 0,
  };
}

function bumpParent(data: BoardData, parentId: unknown, by: number) {
  const parent = typeof parentId === 'string' ? data.items[parentId] : undefined;
  if (parent) parent.subtask_count = Math.max(0, parent.subtask_count + by);
}

export function applyEvent(data: BoardData, ev: SlateEvent): Effects {
  const fx: Effects = {
    refetch: [],
    structure: false,
    reload: false,
    today: false,
    views: false,
    tags: false,
    favorites: false,
    capacities: false,
  };
  const kind = ev.type.split('.')[0];

  if (kind === 'tag') {
    fx.tags = true;
    return fx;
  }
  if (kind === 'view') {
    fx.views = true;
    return fx;
  }
  if (kind === 'favorite') {
    fx.favorites = true;
    return fx;
  }
  if (kind === 'capacity') {
    fx.capacities = true;
    return fx;
  }
  if (kind === 'section' || kind === 'list') {
    fx.structure = true;
    // New statuses, or restored lists whose items the default load left out: reload all.
    fx.reload = ev.type === 'list.statuses_changed' || ev.type.endsWith('.unarchived');
    fx.today = true;
    return fx;
  }
  if (
    (ev.type === 'comment.assigned' || ev.type === 'comment.resolved' || ev.type === 'comment.deleted') &&
    ev.item_id
  ) {
    // Open Asks and the comment count live on the item row (open_asks: the card's "?" badge).
    if (data.items[ev.item_id]) fx.refetch.push(ev.item_id);
    return fx;
  }
  if (kind === 'checklist' && ev.item_id) {
    // Checklist progress lives on the item row (checklist_done / checklist_total).
    if (data.items[ev.item_id]) fx.refetch.push(ev.item_id);
    return fx;
  }
  if (kind !== 'item' || !ev.item_id) {
    // recurrence.spawned carries the new item's id; its item.created usually came first.
    if (kind === 'recurrence' && ev.item_id && !data.items[ev.item_id]) fx.refetch.push(ev.item_id);
    return fx;
  }

  const id = ev.item_id;
  const it = data.items[id];
  fx.today = true;

  switch (ev.type) {
    case 'item.created': {
      if (it) break;
      const built =
        ev.after && typeof ev.after === 'object'
          ? fromSnapshot(data, ev.after as Record<string, unknown>)
          : null;
      if (built) {
        data.items[id] = built;
        bumpParent(data, built.parent_id, 1);
      } else {
        fx.refetch.push(id);
      }
      break;
    }
    case 'item.trashed': // after = {trashed_at, archived_at}: hidden like an archived item
    case 'item.restored': {
      const after = (ev.after ?? {}) as { trashed_at?: string | null; archived_at?: string | null };
      if (it) {
        it.trashed_at = after.trashed_at ?? null;
        it.archived_at = after.archived_at ?? null;
      } else if (ev.type === 'item.restored') {
        fx.refetch.push(id);
      }
      break;
    }
    case 'item.purged':
    case 'item.deleted': {
      if (it) {
        delete data.items[id];
        bumpParent(data, it.parent_id, -1);
      } else {
        // Already gone (a replayed event) or never held: the parent's count is not ours to
        // guess at, so ask the server for it when the parent is on the board.
        const parent = (ev.before as { parent_id?: string } | null)?.parent_id;
        if (parent && data.items[parent]) fx.refetch.push(parent);
      }
      break;
    }
    case 'item.status_changed': {
      const status = it ? statusOf(data, it.list_id, ev.after) : undefined;
      if (it && status) it.status = { ...status };
      else fx.refetch.push(id);
      // Items waiting on this one hear item.blocked / item.unblocked of their own.
      break;
    }
    case 'item.linked': // blocked_by / blocking / related and the blocked flag
    case 'item.unlinked':
      fx.refetch.push(id);
      break;
    case 'item.blocked': // derived on the server when what it waits on opens or closes
    case 'item.unblocked':
      fx.today = false;
      if (it) it.blocked = ev.type === 'item.blocked';
      break;
    case 'item.assigned': // payload has handles only; avatars need names and kinds
    case 'item.split': // the parent's subtask count
      fx.refetch.push(id);
      break;
    case 'item.commented': // the payload is the comment, not a count: a replay must not add
      fx.today = false;
      if (it) fx.refetch.push(id);
      break;
    case 'item.mentioned': // a comment edited to add a mention: no new comment
    case 'item.watched':
    case 'item.unwatched':
      fx.today = false;
      break;
    case 'item.reported': {
      // An agent's report: the badge is the payload; done and idle clear it.
      fx.today = false;
      if (!it) break;
      const r = (ev.after ?? {}) as {
        state?: string;
        note?: string | null;
        progress?: number | null;
        url?: string | null;
        thread?: { ref?: string; url?: string | null } | null;
      };
      // A new or changed thread rides on the report: keep the row's agent_threads current so
      // the task window's session links live on after the badge clears (done).
      if (r.thread?.ref) {
        const rest = (it.agent_threads ?? []).filter((t) => t.account_id !== ev.actor_id);
        it.agent_threads = [...rest, { account_id: ev.actor_id, ref: r.thread.ref, url: r.thread.url ?? null }];
      }
      it.agent =
        r.state === 'working' || r.state === 'blocked' || r.state === 'review'
          ? {
              state: r.state,
              by: ev.actor_id,
              at: ev.at,
              note: r.note ? r.note.slice(0, 280) : null,
              progress: r.progress ?? null,
              url: r.url ?? null,
            }
          : null;
      break;
    }
    case 'item.run_requested': // Run now: the store shows "Requested…" (store.runRequested)
      fx.today = false;
      break;
    case 'item.due_soon': // the scheduler's notices: `field` is due_at but `after` is
    case 'item.due': // {due_at, ...}, not a new value, and nothing on the item changed
    case 'item.overdue':
      break;
    case 'item.attachment_added': // attachment_count
    case 'item.attachment_removed':
      fx.today = false;
      if (it) fx.refetch.push(id);
      break;
    default: {
      // item.updated / item.archived / item.unarchived / item.moved: one field, before ->
      // after. Any other type is refetched rather than trusted to carry a column's value.
      if (!it || !COLUMN_CHANGES.has(ev.type)) {
        fx.refetch.push(id);
      } else if (ev.field === 'tags' && Array.isArray(ev.after)) {
        it.tags = [...(ev.after as string[])];
      } else if (ev.field && SCALARS.has(ev.field)) {
        (it as unknown as Record<string, unknown>)[ev.field] = ev.after;
        if (ev.field === 'list_id' || ev.field === 'parent_id') fx.refetch.push(id);
      } else {
        fx.refetch.push(id);
      }
    }
  }
  return fx;
}
