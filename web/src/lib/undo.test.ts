import { describe, expect, it } from 'vitest';
import { item as makeItem } from './fixtures';
import { changeLabel, groupReverts, revertPatch } from './undo';

describe('revertPatch', () => {
  const before = makeItem({
    id: 'a',
    title: 'Old',
    due_at: '2026-10-23T22:00:00Z',
    priority: null,
    tags: ['home'],
    status: { id: 'st1', name: 'To do', kind: 'open', color: null },
    assignees: [{ id: 'u1', handle: 'alex', display_name: 'Alex', kind: 'person' }],
  });

  it('puts plain fields back, null included', () => {
    expect(revertPatch(before, { title: 'New', priority: 'high', due_at: null })).toEqual({
      title: 'Old',
      priority: null,
      due_at: '2026-10-23T22:00:00Z',
    });
  });

  it('puts status, assignees, tags and list back in PATCH shape', () => {
    expect(revertPatch(before, { status: 'st2', assignees: [], tags: ['x'], list_id: 'l2' })).toEqual({
      status: 'st1',
      assignees: ['alex'],
      tags: ['home'],
      list_id: before.list_id,
    });
  });

  it('refuses what it cannot restore', () => {
    expect(revertPatch(before, { estimate_minutes: 30 })).toBeNull();
    expect(revertPatch({ ...before, status: null }, { status: 'st2' })).toBeNull();
  });

  it('a status_kind change undoes to the exact status the item had', () => {
    expect(revertPatch(before, { status_kind: 'done' })).toEqual({ status: 'st1' });
    expect(changeLabel({ status_kind: 'done' }, 3)).toBe('Moved 3 tasks');
  });
});

describe('groupReverts', () => {
  it('groups identical patches', () => {
    const g = groupReverts([
      ['a', { status: 's1' }],
      ['b', { status: 's2' }],
      ['c', { status: 's1' }],
    ]);
    expect(g).toEqual([
      { ids: ['a', 'c'], changes: { status: 's1' } },
      { ids: ['b'], changes: { status: 's2' } },
    ]);
  });
});

describe('changeLabel', () => {
  it('names the change', () => {
    expect(changeLabel({ status: 'x' })).toBe('Moved task');
    expect(changeLabel({ due_at: null }, 3)).toBe('Cleared the due date on 3 tasks');
    expect(changeLabel({ due_at: 'x', priority: 'high' })).toBe('Changed task');
  });
});
