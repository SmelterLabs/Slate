// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../lib/fixtures';
import { defaultConfig } from '../lib/views';

const CFG = defaultConfig({ kind: 'list', id: 'L1' });

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
import TableRow from './TableRow.svelte';

beforeEach(() => {
  board.me = {
    account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    token_id: 'T1',
    scopes: ['admin'],
    prefs: {},
  };
  board.accounts = {
    me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' },
    sc: { id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' },
  };
  board.lists = { L1: LIST };
  board.now = new Date('2026-10-29T12:00:00Z');
  board.runRequested = {};
});

describe('Standing line and stalled mark on Card, ItemRow, and TableRow', () => {
  const standing = {
    line: 'Sent the invoice. Nothing needed from you.',
    by_id: 'sc',
    at: '2026-10-29T11:00:00Z',
    move_id: 'me',
  };

  it('keeps the agent note in review tooltips on the card, list and table', async () => {
    const review = item({
      standing,
      agent: { state: 'review', by: 'sc', at: standing.at, note: 'The report is ready.' },
    });
    const card = document.createElement('div');
    const list = document.createElement('div');
    const table = document.createElement('tbody');
    const apps = [
      mount(Card, { target: card, props: { item: review, config: CFG } }),
      mount(ItemRow, { target: list, props: { item: review, config: CFG } }),
      mount(TableRow, { target: table, props: { item: review, columns: [] } }),
    ];
    try {
      for (const target of [card, list, table]) {
        const tip = target.querySelector('.agent-badge')?.getAttribute('title');
        expect(tip).toContain(review.agent!.note);
        expect(tip).not.toContain(standing.line);
      }
    } finally {
      await Promise.all(apps.map((app) => unmount(app)));
    }
  });

  it('Card shows standing line with full text on title, Your move / account name / nothing, and neutral Stalled with Run now', async () => {
    const target = document.createElement('div');
    const cardItem = item({
      id: 'I1',
      title: 'Invoice client',
      due_at: '2026-10-28T10:00:00Z',
      assignees: [{ id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' }],
      standing,
      stalled: true,
    });
    const app = mount(Card, { target, props: { item: cardItem, config: CFG } });

    const line = target.querySelector<HTMLElement>('[data-standing-line]');
    expect(line?.textContent?.trim()).toBe(standing.line);
    expect(line?.getAttribute('title')).toBe(standing.line);
    expect(target.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('Your move');

    const stalled = target.querySelector('[data-stalled]');
    expect(stalled?.textContent).toContain('Stalled');
    const runBtn = stalled?.querySelector<HTMLButtonElement>('[data-stalled-run] button.run-now');
    expect(runBtn?.textContent).toContain('Run now');
    await unmount(app);

    // Other account's move shows their name; null move_id shows no move mark.
    const t2 = document.createElement('div');
    const app2 = mount(Card, {
      target: t2,
      props: { item: item({ standing: { ...standing, move_id: 'sc' } }), config: CFG },
    });
    expect(t2.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('My Agent');
    await unmount(app2);

    const t3 = document.createElement('div');
    const app3 = mount(Card, {
      target: t3,
      props: { item: item({ standing: { ...standing, move_id: null } }), config: CFG },
    });
    expect(t3.querySelector('[data-standing-move]')).toBeNull();
    await unmount(app3);
  });

  it('ItemRow shows standing line under title and Stalled mark with Run now', async () => {
    const target = document.createElement('div');
    const rowItem = item({
      id: 'I2',
      title: 'Prepare report',
      assignees: [{ id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' }],
      standing,
      stalled: true,
    });
    const app = mount(ItemRow, { target, props: { item: rowItem, config: CFG } });
    expect(target.querySelector('[data-standing-line]')?.textContent?.trim()).toBe(standing.line);
    expect(target.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('Your move');
    expect(target.querySelector('[data-stalled]')?.textContent).toContain('Stalled');
    expect(target.querySelector('[data-stalled-run]')?.textContent).toContain('Run now');
    await unmount(app);
  });

  it('TableRow shows standing line inside the title cell (no new column) and Stalled mark with Run now', async () => {
    const table = document.createElement('table');
    const tbody = document.createElement('tbody');
    table.appendChild(tbody);
    const tableItem = item({
      id: 'I3',
      title: 'Check draft',
      assignees: [{ id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' }],
      standing,
      stalled: true,
    });
    const app = mount(TableRow, { target: tbody, props: { item: tableItem, columns: [{ key: 'status', kind: 'status', label: 'Status', sort: null }] } });
    expect(tbody.querySelectorAll('td')).toHaveLength(2); // title cell + 1 column, no extra standing column
    const titleCell = tbody.querySelector('td.t-title');
    expect(titleCell?.querySelector('[data-standing-line]')?.textContent?.trim()).toBe(standing.line);
    expect(titleCell?.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('Your move');
    expect(titleCell?.querySelector('[data-stalled]')?.textContent).toContain('Stalled');
    expect(titleCell?.querySelector('[data-stalled-run]')?.textContent).toContain('Run now');
    await unmount(app);
  });
});
