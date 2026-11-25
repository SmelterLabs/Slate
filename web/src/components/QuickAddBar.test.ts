// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { item, LIST } from '../lib/fixtures';

const BASE_ITEM = item({ id: 'I1', list_id: 'L1', title: 'Base' });
const apiMetaMock = vi.fn(async () => ({ data: BASE_ITEM, headers: new Headers() }));
const postMock = vi.fn(async () => [BASE_ITEM]);

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return {
    ...actual,
    api: vi.fn(async () => undefined),
    apiMeta: (...args: unknown[]) => apiMetaMock(...(args as [])),
    get: vi.fn(async () => []),
    post: (...args: unknown[]) => postMock(...(args as [])),
    patch: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
  };
});

import { board } from '../lib/store.svelte';
import AiReceipt from './AiReceipt.svelte';
import QuickAddBar from './QuickAddBar.svelte';

beforeEach(() => {
  vi.clearAllMocks();
  board.dismissAiReceipt();
  board.me = {
    account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    token_id: 'T1',
    scopes: ['admin'],
    prefs: {},
    ai_add: false,
    inbox_list_id: 'L2',
  };
  board.accounts = {};
  board.sections = [
    { id: 'S1', name: 'Work', slug: 'work', audience: 'work', position: 0, archived_at: null, created_at: '2026-10-20T00:00:00Z' },
  ];
  board.lists = {
    L1: { ...LIST, id: 'L1', name: 'Repairs', position: 0, note_md: '# Reading links worth keeping\nFile the whole article.' },
    L2: { ...LIST, id: 'L2', name: 'Todo', position: 1, note_md: null },
  };
  board.items = {};
  board.setAiAdd(true);
});

describe('QuickAddBar list note help and AI routing', () => {
  it("shows the list note's first line as the placeholder in plain quick add", async () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(QuickAddBar, { target, props: { scope: { kind: 'list', id: 'L1' }, viewKey: 'list:L1' } });
    expect(target.querySelector<HTMLInputElement>('#quick-add')?.placeholder).toBe('Reading links worth keeping');
    await unmount(app);
    target.remove();
  });

  it('defaults to workspace inbox list in wide scope plain quick add', async () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(QuickAddBar, { target, props: { scope: { kind: 'everything' }, viewKey: 'everything' } });
    const select = target.querySelector<HTMLSelectElement>('select[aria-label="Add to list"]');
    expect(select?.value).toBe('L2');
    await unmount(app);
    target.remove();
  });

  it('defaults to Auto routing even inside a specific list when AI add is on, and pops the receipt bubble', async () => {
    board.me = { ...board.me!, ai_add: true };
    const created = item({ id: 'N9', list_id: 'L2', title: 'Take medication' });
    apiMetaMock.mockResolvedValueOnce({ data: created, headers: new Headers() });

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(QuickAddBar, { target, props: { scope: { kind: 'list', id: 'L1' }, viewKey: 'list:L1' } });
    const select = target.querySelector<HTMLSelectElement>('select[aria-label="Add to list"]');
    expect(select?.value).toBe('');
    expect(select?.options[0]?.textContent).toBe('Auto (Todo)');

    const input = target.querySelector<HTMLInputElement>('#quick-add')!;
    input.value = 'Every Tuesday remind me to take medication';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    target.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await vi.waitFor(() =>
      expect(board.aiReceipt).toEqual({ itemId: 'N9', title: 'Take medication', listId: 'L2', fallback: false }),
    );
    expect(apiMetaMock).toHaveBeenCalledWith('POST', '/items/ai', {
      text: 'Every Tuesday remind me to take medication',
      defaults: {},
      route: true,
    });

    await unmount(app);
    target.remove();
  });

  it('AiReceipt bubble shows the routed list and moves the item when the user picks another list', async () => {
    const created = item({ id: 'N9', list_id: 'L2', title: 'Take medication' });
    board.items = { N9: created };
    board.showAiReceipt({ itemId: 'N9', title: 'Take medication', listId: 'L2', fallback: false });
    const moved = { ...created, list_id: 'L1' };
    postMock.mockResolvedValueOnce([moved]);

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(AiReceipt, { target });
    const sel = target.querySelector<HTMLSelectElement>('select[aria-label="Routed list (change to move)"]')!;
    expect(sel.value).toBe('L2');

    sel.value = 'L1';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(board.aiReceipt?.listId).toBe('L1'));
    flushSync();

    expect(postMock).toHaveBeenCalledWith('/items/bulk', { ids: ['N9'], changes: { list_id: 'L1' } });

    await unmount(app);
    target.remove();
  });
});
