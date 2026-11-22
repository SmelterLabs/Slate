// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { deferred, freshBoard, ME, requests, routes, settle, startLive, tearDown, writes, type Board } from '../../lib/board/testkit';
import { LIST, item } from '../../lib/fixtures';
import type { Item } from '../../lib/types';

const stale = item({
  id: 'I_OLD',
  title: 'Old overdue task',
  due_at: '2026-10-01T22:00:00Z',
  due_date_only: true,
  assignees: [ME.account],
});
const cancelled = { id: 'st_cancel', name: 'Cancelled', kind: 'cancelled' as const, color: null, position: 4 };
let board: Board;
let target: HTMLDivElement;
let app: ReturnType<typeof import('svelte').mount>;
let svelte: typeof import('svelte');

beforeEach(async () => {
  board = await freshBoard();
  svelte = await import('svelte');
  routes.set('/lists', () => [{ ...LIST, statuses: [...LIST.statuses, cancelled] }]);
  let saved = stale;
  routes.set('/items', () => [saved]);
  routes.set('PATCH /items/I_OLD', (body) => {
    const { status, ...changes } = body as { status?: string } & Partial<Item>;
    saved = { ...saved, ...changes, status: status === cancelled.id ? cancelled : saved.status };
    return saved;
  });
  routes.set('POST /items/bulk', (body) => {
    const { changes } = body as { changes: Partial<Item> };
    saved = { ...saved, ...changes, due_at: `${changes.due_at}T22:00:00Z` };
    return [saved];
  });
  routes.set('/today/sweep', () => saved.due_at === stale.due_at ? [saved] : []);
  await startLive(board);
  board.now = new Date('2026-10-22T20:00:00Z');
  target = document.createElement('div');
  document.body.appendChild(target);
  const { default: DecidePanel } = await import('./DecidePanel.svelte');
  app = svelte.mount(DecidePanel, { target });
  svelte.flushSync();
  await settle();
  svelte.flushSync();
});

afterEach(async () => {
  await svelte.unmount(app);
  target.remove();
  tearDown(board);
});

describe('DecidePanel', () => {
  it('loads stale cards and moves one to the coming sweep day', async () => {
    expect(requests).toContainEqual({ method: 'GET', path: '/today/sweep', body: undefined });
    expect(target.querySelector('[data-decide-item="I_OLD"]')).not.toBeNull();
    target.querySelector<HTMLButtonElement>('[data-sweep-week]')!.click();
    await settle();
    expect(writes().at(-1)).toEqual({
      method: 'PATCH', path: '/items/I_OLD', body: { due_at: '2026-10-23' },
    });
  });

  it('uses the date picker and makes one Undo entry that restores the old date', async () => {
    target.querySelector<HTMLButtonElement>('[aria-label^="New date:"]')!.click();
    svelte.flushSync();
    const date = target.querySelector<HTMLInputElement>('[aria-label="New date date"]')!;
    date.value = '2026-11-05';
    date.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    expect(writes().at(-1)).toEqual({
      method: 'PATCH', path: '/items/I_OLD', body: { due_at: '2026-11-05' },
    });
    expect(board.undoDepth).toBe(1);
    await board.undoLast();
    expect(writes().at(-1)).toEqual({
      method: 'POST', path: '/items/bulk', body: { ids: ['I_OLD'], changes: { due_at: '2026-10-01' } },
    });
    expect(board.items.I_OLD.due_at).toBe(stale.due_at);
    expect(board.undoDepth).toBe(0);
  });

  it('Drop selects the cancelled status, or says why it cannot act', async () => {
    const original = board.lists.L1;
    board.lists.L1 = { ...LIST, statuses: [LIST.statuses[0]] };
    svelte.flushSync();
    target.querySelector<HTMLButtonElement>('[data-sweep-drop]')!.click();
    expect(writes()).toHaveLength(0);
    expect(board.toast).toContain('has no cancelled or done status');
    board.lists.L1 = original;
    svelte.flushSync();
    target.querySelector<HTMLButtonElement>('[data-sweep-drop]')!.click();
    await settle();
    expect(writes().at(-1)).toEqual({
      method: 'PATCH', path: '/items/I_OLD', body: { status: cancelled.id },
    });
  });

  it('ignores an older sweep response that would resurrect a decided card', async () => {
    const older = deferred<Item[]>();
    routes.set('/today/sweep', () => older.promise);
    board.rev++;
    svelte.flushSync();
    routes.set('/today/sweep', () => []);
    board.rev++;
    svelte.flushSync();
    await settle();
    svelte.flushSync();
    expect(target.querySelector('[data-decide-item="I_OLD"]')).toBeNull();
    older.resolve([stale]);
    await settle();
    svelte.flushSync();
    expect(target.querySelector('[data-decide-item="I_OLD"]')).toBeNull();
  });

  it('names a failed sweep refresh and keeps the last loaded cards', async () => {
    routes.set('/today/sweep', () => ({ status: 503, body: { detail: 'Temporarily unavailable' } }));
    board.rev++;
    svelte.flushSync();
    await settle();
    expect(board.toast).toBe('Could not load the weekly sweep: Temporarily unavailable');
    expect(target.querySelector('[data-decide-item="I_OLD"]')).not.toBeNull();
  });
});
