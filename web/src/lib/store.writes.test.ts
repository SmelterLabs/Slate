// @vitest-environment happy-dom
// The store's writes (docs/board.md, the store's layers): what shows at once, what is put back
// when Slate refuses, and what Undo sends. Only `fetch` and `EventSource` are faked
// (board/testkit.ts); the api layer, the store and the reducer are real.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from './fixtures';
import { LEAVES } from './words';
import type { Favorite, Item, Section } from './types';
import { deferred, freshBoard, routes, settle, startLive, tearDown, writes, type Board } from './board/testkit';

let board: Board;

beforeEach(async () => {
  board = await freshBoard();
  routes.set('/items', () => [item(), item({ id: 'I2', title: 'Paint fence', position: 2 })]);
  await startLive(board);
  await settle();
});
afterEach(() => tearDown(board));

const plain = (id: string) => board.plainItems.find((i) => i.id === id);
const refuse = (detail: string) => ({ status: 409, body: { detail } });
const lastWrite = () => writes().at(-1);
/** A PATCH /items/{id} that answers with the item as the change leaves it. */
function acceptPatch(id = 'I1') {
  routes.set(`PATCH /items/${id}`, (body) => {
    const { status, ...rest } = body as Record<string, unknown>;
    const out = { ...JSON.parse(JSON.stringify(board.items[id])), ...rest } as Item;
    if (status) out.status = LIST.statuses.find((s) => s.id === status) ?? null;
    return out;
  });
}
/** POST /items/bulk answering with each item as the change leaves it. */
function acceptBulk() {
  routes.set('POST /items/bulk', (body) => {
    const { ids, changes } = body as { ids: string[]; changes: Partial<Item> };
    return ids.filter((id) => board.items[id]).map((id) => ({ ...board.items[id], ...changes }));
  });
}

describe('optimistic edits', () => {
  it('a drag to a column shows at once and goes back when Slate refuses it', async () => {
    const answer = deferred<unknown>();
    routes.set('PATCH /items/I1', () => answer.promise);
    const moved = board.moveToStatus('I1', LIST.statuses[2]);
    expect(board.items.I1.status?.id).toBe('st_done');
    expect(plain('I1')?.status?.id).toBe('st_done'); // the views read the plain copy
    answer.resolve(refuse('Blocked by another task'));
    await moved;
    expect(board.items.I1.status?.id).toBe('st_open');
    expect(plain('I1')?.status?.id).toBe('st_open');
    expect(board.toast).toBe('Blocked by another task');
    expect(board.undoDepth).toBe(0);
  });

  it('a refused priority change puts the old value back in the item and its plain copy', async () => {
    const answer = deferred<unknown>();
    routes.set('PATCH /items/I1', () => answer.promise);
    const done = board.setPriority('I1', 'high');
    expect(board.items.I1.priority).toBe('high');
    expect(plain('I1')?.priority).toBe('high');
    answer.resolve(refuse('Read-only list'));
    await done;
    expect(board.items.I1.priority).toBeNull();
    expect(plain('I1')?.priority).toBeNull();
    expect(board.toast).toBe('Read-only list');
  });

  it("an accepted change keeps the server's copy, and Undo sends the old value back", async () => {
    acceptPatch();
    await board.setPriority('I1', 'high');
    expect(lastWrite()).toEqual({ method: 'PATCH', path: '/items/I1', body: { priority: 'high' } });
    expect(board.items.I1.priority).toBe('high');
    expect(board.undoDepth).toBe(1);
    acceptBulk();
    await board.undoLast();
    expect(lastWrite()).toEqual({
      method: 'POST',
      path: '/items/bulk',
      body: { ids: ['I1'], changes: { priority: null } },
    });
    expect(board.items.I1.priority).toBeNull();
    expect(board.undoDepth).toBe(0);
    expect(board.toast).toBe('Undone: Changed priority on task');
  });

  it('an undo is not itself remembered, and an empty history says so', async () => {
    acceptPatch();
    await board.update('I1', { title: 'Fix the gutter' });
    acceptBulk();
    await board.undoLast();
    expect(board.items.I1.title).toBe('Fix gutter');
    expect(board.undoDepth).toBe(0);
    await board.undoLast();
    expect(board.toast).toBe('Nothing to undo');
  });

  it('keeps at most 50 changes to undo', async () => {
    acceptPatch();
    for (let i = 0; i < 52; i++) await board.update('I1', { title: `Title ${i}` });
    expect(board.undoDepth).toBe(50);
  });

  it('a multi-card reorder is one batch, and every position goes back if it fails', async () => {
    const answer = deferred<unknown>();
    routes.set('POST /batch', () => answer.promise);
    const done = board.reposition([
      { id: 'I1', position: 5 },
      { id: 'I2', position: 6 },
    ]);
    expect([board.items.I1.position, board.items.I2.position]).toEqual([5, 6]);
    expect([plain('I1')?.position, plain('I2')?.position]).toEqual([5, 6]);
    answer.resolve(refuse('Conflict'));
    expect(await done).toBe(false);
    expect([board.items.I1.position, board.items.I2.position]).toEqual([1, 2]);
    expect([plain('I1')?.position, plain('I2')?.position]).toEqual([1, 2]);
    const batches = writes().filter((w) => w.path === '/batch');
    expect(batches).toHaveLength(1);
    expect((batches[0].body as { ops: unknown[] }).ops).toEqual([
      { tool: 'item', action: 'update', params: { id: 'I1', position: 5 } },
      { tool: 'item', action: 'update', params: { id: 'I2', position: 6 } },
    ]);
    expect(board.toast).toBe('Conflict');
  });

  it('a single reorder is one PATCH', async () => {
    acceptPatch();
    expect(await board.reposition([{ id: 'I1', position: 3 }])).toBe(true);
    expect(lastWrite()).toEqual({ method: 'PATCH', path: '/items/I1', body: { position: 3 } });
    expect(board.items.I1.position).toBe(3);
  });

  it('rescheduling a date-only task sends a bare date; a timed one keeps its time of day', async () => {
    acceptPatch();
    await board.reschedule('I1', '2026-11-25');
    expect(lastWrite()?.body).toEqual({ due_at: '2026-11-25' });

    acceptPatch('I2');
    board.items.I2.due_at = '2026-11-20T15:30:00.000Z';
    board.items.I2.due_date_only = false;
    await board.reschedule('I2', '2026-11-25');
    expect(lastWrite()?.body).toEqual({ due_at: '2026-11-25T15:30:00.000Z' });
  });

  it('a write for an item the board does not hold sends nothing', async () => {
    const before = writes().length;
    await board.setPriority('nope', 'high');
    await board.moveToStatus('nope', LIST.statuses[1]);
    expect(writes()).toHaveLength(before);
  });
});

describe('drops', () => {
  it('dropping on an assignee group shows the account at once, sends handles, and reverts', async () => {
    const answer = deferred<unknown>();
    routes.set('PATCH /items/I1', () => answer.promise);
    const done = board.applyDrop('I1', { kind: 'assignee', handle: 'alex' });
    expect(board.items.I1.assignees.map((a) => a.handle)).toEqual(['alex']);
    expect(lastWrite()?.body).toEqual({ assignees: ['alex'] });
    answer.resolve(refuse('No access'));
    await done;
    expect(board.items.I1.assignees).toEqual([]);
  });

  it('a tag column swaps the tag the card came from for the new one', async () => {
    board.items.I1.tags = ['home', 'urgent'];
    acceptPatch();
    await board.applyDrop('I1', { kind: 'tag', tag: 'garden' }, 'home');
    expect(lastWrite()?.body).toEqual({ tags: ['urgent', 'garden'] });
  });

  it('a section with several lists refuses the drop with a note and sends nothing', async () => {
    board.lists.L2 = { ...LIST, id: 'L2', section_id: 'S2', name: 'Beds' };
    board.lists.L3 = { ...LIST, id: 'L3', section_id: 'S2', name: 'Paths' };
    board.sections = [{ id: 'S2', name: 'Garden' } as Section];
    const before = writes().length;
    await board.applyDrop('I1', { kind: 'section', sectionId: 'S2' });
    expect(board.toast).toBe('Garden has 2 lists. Group by List to pick which one.');
    expect(writes()).toHaveLength(before);
  });

  it('a section with one list moves the card there, and the toast offers Undo', async () => {
    board.lists.L2 = { ...LIST, id: 'L2', section_id: 'S2', name: 'Beds' };
    board.sections = [{ id: 'S2', name: 'Garden' } as Section];
    acceptBulk();
    await board.applyDrop('I1', { kind: 'section', sectionId: 'S2' });
    expect(lastWrite()).toEqual({
      method: 'POST',
      path: '/items/bulk',
      body: { ids: ['I1'], changes: { list_id: 'L2' } },
    });
    expect(board.items.I1.list_id).toBe('L2');
    expect(board.toast).toBe('Moved task to another list');
    board.toastAction!.run();
    await settle();
    expect(lastWrite()?.body).toEqual({ ids: ['I1'], changes: { list_id: 'L1' } });
    expect(board.items.I1.list_id).toBe('L1');
  });

  it('a status kind the list does not have is refused with a note', async () => {
    const before = writes().length;
    await board.applyDrop('I1', { kind: 'status_kind', value: 'cancelled' });
    expect(board.toast).toBe('Home has no cancelled status');
    expect(writes()).toHaveLength(before);
  });

  it("dropping on Done completes the card, and the toast's Undo reopens it", async () => {
    acceptPatch();
    await board.applyDrop('I1', { kind: 'done' });
    expect(lastWrite()?.body).toEqual({ status: 'st_done' });
    expect(board.toast).toBe(`Completed “Fix gutter”. ${LEAVES.complete}`);
    board.toastAction!.run();
    await settle();
    expect(lastWrite()?.body).toEqual({ status: 'st_open' });
    expect(board.items.I1.status?.id).toBe('st_open');
  });

  it('a column gives a new card what the column means, when the list has it', () => {
    expect(board.columnDefaults({ kind: 'status', status: LIST.statuses[1] }, 'L1')).toEqual({ status: 'st_ready' });
    expect(board.columnDefaults({ kind: 'status_kind', value: 'done' }, 'L1')).toEqual({ status: 'st_done' });
    expect(board.columnDefaults({ kind: 'status_kind', value: 'cancelled' }, 'L1')).toEqual({});
    expect(board.columnDefaults({ kind: 'due', day: '2026-11-25' }, 'L1')).toEqual({ due_at: '2026-11-25' });
    expect(board.columnDefaults({ kind: 'assignee', handle: 'alex' }, 'L1')).toEqual({ assignees: ['alex'] });
    expect(board.columnDefaults(null, 'L1')).toEqual({});
  });
});

describe('bulk, trash and quick add', () => {
  it('a bulk change to several tasks offers Undo, which sends one call per distinct old value', async () => {
    board.items.I2.priority = 'low';
    acceptBulk();
    expect(await board.bulk(['I1', 'I2'], { priority: 'high' })).toBe(true);
    expect(board.toast).toBe('Changed priority on 2 tasks');
    board.toastAction!.run();
    await settle();
    const reverts = writes().slice(-2).map((w) => w.body);
    expect(reverts).toEqual([
      { ids: ['I1'], changes: { priority: null } },
      { ids: ['I2'], changes: { priority: 'low' } },
    ]);
  });

  it('a refused bulk change records no undo', async () => {
    routes.set('POST /items/bulk', () => refuse('Too many'));
    expect(await board.bulk(['I1', 'I2'], { priority: 'high' })).toBe(false);
    expect(board.undoDepth).toBe(0);
    expect(board.toast).toBe('Too many');
  });

  it('trash closes the open task, and Undo restores it', async () => {
    routes.set('POST /items/I1/trash', () => item({ trashed_at: '2026-11-13T12:00:00Z', archived_at: '2026-11-13T12:00:00Z' }));
    routes.set('POST /items/I1/restore', () => item());
    routes.set('/items/I1', () => ({ ...item(), subtasks: [], comments: [], events: [], checklists: [], attachments: [], watchers: [] }));
    await board.open('I1');
    await board.trash('I1');
    expect(board.openId).toBeNull();
    expect(board.items.I1.trashed_at).toBeTruthy();
    expect(board.toast).toBe(`Moved “Fix gutter” to the trash. ${LEAVES.trash}`);
    board.toastAction!.run();
    await settle();
    expect(lastWrite()?.path).toBe('/items/I1/restore');
    expect(board.items.I1.trashed_at).toBeFalsy();
  });

  it('trashing many sends at most 500 ids per call', async () => {
    routes.set('POST /items/bulk', () => []);
    const ids = Array.from({ length: 501 }, (_, i) => `X${i}`);
    expect(await board.trashMany(ids)).toBe(true);
    const sent = writes().filter((w) => w.path === '/items/bulk').map((w) => (w.body as { ids: string[] }).ids.length);
    expect(sent).toEqual([500, 1]);
    expect(board.toast).toBe(`Moved 501 tasks to the trash. ${LEAVES.trash}`);
  });

  it('quick add files exactly one item, the words winning over the column, and Undo deletes it', async () => {
    routes.set('POST /items', () => item({ id: 'N1', title: 'Call plumber' }));
    const ok = await board.quickAdd(
      'L1',
      { title: 'Call plumber', tags: ['home'], assignees: ['alex'], priority: 'high', due: '2026-11-20', start: null, list: null, rrule: null },
      { priority: 'low', status: 'st_ready' },
    );
    expect(ok).toBe(true);
    const posts = writes().filter((w) => w.path === '/items');
    expect(posts).toEqual([
      {
        method: 'POST',
        path: '/items',
        body: {
          priority: 'high',
          status: 'st_ready',
          list_id: 'L1',
          title: 'Call plumber',
          tags: ['home'],
          assignees: ['alex'],
          due_at: '2026-11-20',
        },
      },
    ]);
    await board.undoLast();
    expect(lastWrite()).toEqual({ method: 'DELETE', path: '/items/N1', body: undefined });
  });

  it('a quick add that repeats makes a rule instead of an item', async () => {
    routes.set('POST /recurrences', () => ({ id: 'R1' }));
    await board.quickAdd('L1', { title: 'Bins out', tags: [], assignees: [], priority: null, due: '2026-11-16', start: null, list: null, rrule: 'FREQ=WEEKLY;BYDAY=MO' });
    expect(writes().map((w) => w.path)).toEqual(['/recurrences']);
    expect(lastWrite()?.body).toEqual({ list_id: 'L1', title_template: 'Bins out', rrule: 'FREQ=WEEKLY;BYDAY=MO', dtstart: '2026-11-16' });
  });
});

describe('favorites, prefs and drafts', () => {
  const fav = (id: string, position: number): Favorite =>
    ({ id, kind: 'list', target_id: `L_${id}`, position, created_at: '', name: null, scope_kind: null, scope_id: null }) as Favorite;

  it('a refused reorder of the favorites bar puts the old order back', async () => {
    board.favorites = [fav('F1', 0), fav('F2', 1)];
    const answer = deferred<unknown>();
    routes.set('PUT /favorites/order', () => answer.promise);
    const done = board.moveFavorite('F2', 'F1');
    expect(board.favorites.map((f) => f.id)).toEqual(['F2', 'F1']);
    answer.resolve(refuse('Nope'));
    await done;
    expect(board.favorites.map((f) => f.id)).toEqual(['F1', 'F2']);
  });

  it('a refused unpin brings the favorite back', async () => {
    board.favorites = [fav('F1', 0), fav('F2', 1)];
    routes.set('DELETE /favorites/F1', () => refuse('Nope'));
    await board.removeFavorite('F1');
    expect(board.favorites.map((f) => f.id)).toEqual(['F1', 'F2']);
  });

  it('prefs show at once and then take what the server saved', async () => {
    const answer = deferred<unknown>();
    routes.set('PATCH /me/prefs', () => answer.promise);
    const done = board.setPrefs({ theme: 'dark' });
    expect(board.prefs.theme).toBe('dark');
    answer.resolve({ theme: 'dark', collapsed: { v: { c: true } } });
    await done;
    expect(board.prefs).toEqual({ theme: 'dark', collapsed: { v: { c: true } } });
  });

  it('view changes are one draft write after the last change, and Revert clears it', async () => {
    routes.set('PATCH /me/prefs', (body) => ({ ...board.prefs, ...(body as object) }));
    board.setConfig({ show_closed: true });
    board.setConfig({ show_subtasks: true });
    expect(board.active.dirty).toBe(true);
    expect(writes().filter((w) => w.path === '/me/prefs')).toHaveLength(0);
    await settle();
    await vi.advanceTimersByTimeAsync(400);
    const sent = writes().filter((w) => w.path === '/me/prefs');
    expect(sent).toHaveLength(1);
    const drafts = (sent[0].body as { view_drafts: Record<string, unknown> }).view_drafts;
    expect(drafts['default:everything']).toEqual({ show_closed: true, show_subtasks: true });
    board.revert();
    expect(board.active.dirty).toBe(false);
    await vi.advanceTimersByTimeAsync(400);
    expect(writes().filter((w) => w.path === '/me/prefs')).toHaveLength(2);
  });
});

describe('selection', () => {
  it('shift-click selects the range from the last card toggled, in screen order', () => {
    board.order = ['I1', 'I2', 'I3', 'I4'];
    board.toggleSelect('I2');
    board.toggleSelect('I4', true);
    expect([...board.selected].sort()).toEqual(['I2', 'I3', 'I4']);
    board.toggleSelect('I3');
    expect(board.selected.has('I3')).toBe(false);
    board.clearSelection();
    expect(board.selected.size).toBe(0);
  });
});
