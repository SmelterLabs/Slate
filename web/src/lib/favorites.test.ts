import { describe, expect, it } from 'vitest';
import { chipFor, moveBefore, type FavCtx } from './favorites';
import { item, LIST } from './fixtures';
import type { Favorite, SavedView, Section } from './types';

const SECTION: Section = {
  id: 'S1',
  name: 'Personal',
  slug: 'personal',
  position: 0,
  audience: 'personal',
  created_at: '2026-10-01T00:00:00.000000Z',
  archived_at: null,
};

const VIEW: SavedView = {
  id: 'V1',
  name: 'Backlog',
  scope_kind: 'list',
  scope_id: LIST.id,
  config: {},
  position: 0,
  owner_id: 'A1',
  shared: true,
  created_at: '2026-10-01T00:00:00.000000Z',
  updated_at: '2026-10-01T00:00:00.000000Z',
  archived_at: null,
};

function fav(kind: Favorite['kind'], target_id: string, over: Partial<Favorite> = {}): Favorite {
  return {
    id: `F-${target_id}`,
    kind,
    target_id,
    position: 0,
    created_at: '2026-10-01T00:00:00.000000Z',
    name: null,
    scope_kind: null,
    scope_id: null,
    available: true,
    ...over,
  };
}

function ctx(over: Partial<FavCtx> = {}): FavCtx {
  return {
    lists: { [LIST.id]: { ...LIST, section_id: 'S1' } },
    sections: [SECTION],
    views: { V1: VIEW },
    items: { I1: item() },
    selection: { kind: 'everything' },
    viewId: null,
    openId: null,
    ...over,
  };
}

describe('chipFor', () => {
  it('links a list, a section and a view to their pages', () => {
    const c = ctx();
    expect(chipFor(fav('list', LIST.id), c)).toMatchObject({
      label: LIST.name,
      href: `#/list/${LIST.id}`,
      hint: 'List in Personal',
    });
    expect(chipFor(fav('section', 'S1'), c)).toMatchObject({ label: 'Personal', href: '#/section/S1' });
    expect(chipFor(fav('view', 'V1'), c)).toMatchObject({
      label: 'Backlog',
      href: `#/list/${LIST.id}/v/V1`,
      hint: `View of ${LIST.name}`,
    });
  });

  it('an item opens the task window instead of a page', () => {
    const chip = chipFor(fav('item', 'I1'), ctx({ openId: 'I1' }));
    expect(chip).toMatchObject({ label: item().title, href: null, active: true });
  });

  it('marks the chip for where the board is', () => {
    const onList = ctx({ selection: { kind: 'list', id: LIST.id } });
    expect(chipFor(fav('list', LIST.id), onList)?.active).toBe(true);
    const onView = ctx({ selection: { kind: 'list', id: LIST.id }, viewId: 'V1' });
    expect(chipFor(fav('view', 'V1'), onView)?.active).toBe(true);
    expect(chipFor(fav('list', LIST.id), onView)?.active).toBe(false);
  });

  it('hides a chip while its target is archived or gone', () => {
    const c = ctx({
      lists: {},
      views: { V1: { ...VIEW, archived_at: '2026-10-02T00:00:00.000000Z' } },
      items: {},
    });
    expect(chipFor(fav('list', LIST.id), c)).toBeNull();
    expect(chipFor(fav('view', 'V1'), c)).toBeNull();
    expect(chipFor(fav('item', 'I9', { available: false }), c)).toBeNull();
    // An item the board has not loaded (finished long ago) still shows by its server name.
    expect(chipFor(fav('item', 'I9', { name: 'Old thing' }), c)?.label).toBe('Old thing');
  });
});

describe('moveBefore', () => {
  it('moves an id before another, or to the end', () => {
    expect(moveBefore(['a', 'b', 'c'], 'c', 'a')).toEqual(['c', 'a', 'b']);
    expect(moveBefore(['a', 'b', 'c'], 'a', null)).toEqual(['b', 'c', 'a']);
    expect(moveBefore(['a', 'b', 'c'], 'b', 'b')).toEqual(['a', 'b', 'c']);
    expect(moveBefore(['a', 'b', 'c'], 'a', 'zz')).toEqual(['a', 'b', 'c']);
  });
});
