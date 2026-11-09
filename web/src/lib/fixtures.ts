// Test builders shared by the unit tests.
import type { Item, List, SlateEvent } from './types';

export const LIST: List = {
  id: 'L1',
  section_id: 'S1',
  name: 'Home',
  slug: 'home',
  position: 0,
  default_status_id: 'st_open',
  created_at: '2026-10-01T00:00:00Z',
  archived_at: null,
  statuses: [
    { id: 'st_open', name: 'Open', kind: 'open', color: null, position: 0 },
    { id: 'st_ready', name: 'Ready', kind: 'active', color: null, position: 1 },
    { id: 'st_done', name: 'Done', kind: 'done', color: null, position: 2 },
  ],
};

export function item(over: Partial<Item> = {}): Item {
  return {
    id: 'I1',
    list_id: 'L1',
    parent_id: null,
    kind: 'task',
    title: 'Fix gutter',
    body_md: '',
    status: LIST.statuses[0],
    priority: null,
    start_at: null,
    due_at: null,
    completed_at: null,
    waiting_on_account_id: null,
    acceptance_md: null,
    recurrence_id: null,
    occurrence_at: null,
    external_ref: null,
    position: 1,
    created_by: 'A_alex',
    created_at: '2026-10-20T00:00:00Z',
    updated_at: '2026-10-20T00:00:00Z',
    archived_at: null,
    assignees: [],
    tags: [],
    subtask_count: 0,
    comment_count: 0,
    ...over,
  };
}

let seq = 0;
export function ev(over: Partial<SlateEvent>): SlateEvent {
  seq += 1;
  return {
    id: `E${String(seq).padStart(6, '0')}`,
    type: 'item.updated',
    item_id: 'I1',
    subject_id: 'I1',
    actor_id: 'A_agent',
    source: 'mcp',
    field: null,
    before: null,
    after: null,
    session_ref: null,
    at: '2026-10-22T20:00:00Z',
    ...over,
  };
}
