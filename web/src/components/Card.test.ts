// @vitest-environment happy-dom
// Section colours on cards and rows (docs/design-system.md "Section colour"): which pages mix
// sections, and where a card then shows its section's wash and pill.
import { mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../lib/fixtures';
import type { Section } from '../lib/types';
import { defaultConfig } from '../lib/views';

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return {
    ...actual,
    api: vi.fn(async () => undefined),
    get: vi.fn(async () => []),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
  };
});

import { board } from '../lib/store.svelte';
import Card from './Card.svelte';
import ItemRow from './ItemRow.svelte';

const section = (id: string, name: string, color: string | null = null): Section => ({
  id, name, slug: name.toLowerCase(), audience: 'work', position: 0,
  archived_at: null, created_at: '2026-10-20T00:00:00Z', color,
});
const HOME = section('S1', 'Personal', '#e5484d');
const WORK = section('S2', 'Work', '#0090ff');
const OTHER = { ...LIST, id: 'L2', section_id: 'S2', name: 'Work list' };

beforeEach(() => {
  board.me = {
    account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    token_id: 'T1',
    scopes: ['admin'],
    prefs: {},
  };
  board.accounts = { me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' } };
  board.sections = [HOME, WORK];
  board.lists = { L1: LIST, L2: OTHER };
  board.now = new Date('2026-10-29T12:00:00Z');
  board.selection = { kind: 'everything' };
});

describe('which pages mix sections', () => {
  it('Everything does; a section page does not', () => {
    expect(board.sectionMix).not.toBeNull();
    board.selection = { kind: 'section', id: 'S1' };
    expect(board.sectionMix).toBeNull();
  });

  it('a list page only with a section the store holds switched on', () => {
    board.selection = { kind: 'list', id: 'L1' };
    expect(board.sectionMix).toBeNull();
    board.me!.prefs = { also_show: { L1: { sections: ['S_gone'] } } };
    expect(board.sectionMix).toBeNull();
    board.me!.prefs = { also_show: { L1: { sections: ['S_gone', 'S2'] } } };
    expect(board.sectionMix?.S2).toEqual({ name: 'Work', color: '#0090ff', brought: true });
    expect(board.sectionMix?.S1.brought).toBe(false);
    // Another list's switches say nothing about this page.
    board.me!.prefs = { also_show: { L9: { sections: ['S2'] } } };
    expect(board.sectionMix).toBeNull();
  });
});

describe('a card on a page that mixes sections', () => {
  const show = (Component: typeof Card | typeof ItemRow, config = defaultConfig({ kind: 'everything' }), over = {}) => {
    const target = document.createElement('div');
    const app = mount(Component, { target, props: { item: item(over), config } });
    const el = target.querySelector<HTMLElement>('[data-item="I1"]')!;
    return { el, pill: el.querySelector('[data-section-pill]'), done: () => unmount(app) };
  };

  it('wears its section colour and a pill naming the section on Everything', async () => {
    for (const Component of [Card, ItemRow]) {
      const { el, pill, done } = show(Component);
      expect(el.classList.contains('section-tint')).toBe(true);
      expect(el.style.getPropertyValue('--section-color')).toBe('#e5484d');
      expect(pill?.textContent).toContain('Personal');
      expect(pill?.querySelector('.section-dot')).not.toBeNull();
      await done();
    }
  });

  it('shows neither on a section page, and no pill when the list field is off', async () => {
    board.selection = { kind: 'section', id: 'S1' };
    let card = show(Card);
    expect(card.el.classList.contains('section-tint')).toBe(false);
    expect(card.el.style.getPropertyValue('--section-color')).toBe('');
    expect(card.pill).toBeNull();
    await card.done();
    board.selection = { kind: 'everything' };
    const config = { ...defaultConfig({ kind: 'everything' }), card_fields: [] };
    card = show(Card, config);
    expect(card.el.classList.contains('section-tint')).toBe(true);
    expect(card.pill).toBeNull();
    await card.done();
  });

  it('ignores a stored colour that is not a colour', async () => {
    board.sections = [{ ...HOME, color: 'red;background:url(x)' }, WORK];
    for (const Component of [Card, ItemRow]) {
      const { el, pill, done } = show(Component);
      expect(el.classList.contains('section-tint')).toBe(false);
      expect(el.getAttribute('style') ?? '').not.toContain('url');
      expect(pill).toBeNull();
      await done();
    }
  });

  it('on a list page, a card brought in from another section always shows its pill, colour or not', async () => {
    board.selection = { kind: 'list', id: 'L1' };
    board.me!.prefs = { also_show: { L1: { sections: ['S2'] } } };
    board.sections = [HOME, { ...WORK, color: null }];
    const config = defaultConfig({ kind: 'list', id: 'L1' });
    for (const Component of [Card, ItemRow]) {
      const own = show(Component, config);
      expect(own.pill).toBeNull();
      expect(own.el.classList.contains('section-tint')).toBe(true);
      await own.done();
      const brought = show(Component, config, { list_id: 'L2' });
      expect(brought.pill?.textContent).toContain('Work');
      expect(brought.pill?.querySelector('.section-dot')).toBeNull();
      expect(brought.el.classList.contains('section-tint')).toBe(false);
      await brought.done();
    }
  });
});
