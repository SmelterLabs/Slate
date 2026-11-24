// @vitest-environment happy-dom
// The store's stream lifecycle (docs/board.md "Live stream and catch-up"): the stream opens
// first and buffers, the REST load follows, a failed start retries, and reloads coalesce.
// `fetch` and `EventSource` are the only things faked (board/testkit.ts); the api layer and the
// reducer are real.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, ev, item } from './fixtures';
import type { Item, SlateEvent } from './types';
import {
  FakeEventSource,
  ME,
  calls,
  deferred,
  freshBoard,
  routes,
  settle,
  startLive as live,
  tearDown,
  writes,
  type Answer,
  type Board,
  type Failure,
} from './board/testkit';

let board: Board;

beforeEach(async () => {
  board = await freshBoard();
});
afterEach(() => tearDown(board));

const startLive = () => live(board);

describe('Also show', () => {
  const other = {
    ...LIST, id: 'L2', section_id: 'S2', name: 'Other',
    statuses: LIST.statuses.map((s) => ({ ...s, id: `other-${s.id}` })),
  };
  beforeEach(async () => {
    routes.set('/sections', () => ['S1', 'S2', 'S3'].map((id) => ({ id, name: id, archived_at: null })));
    routes.set('/lists', () => [LIST, other, { ...other, id: 'ref', section_id: 'S3', reference: true }]);
    routes.set('/items', () => [
      item(), item({ id: 'foreign', list_id: other.id, status: other.statuses[0], assignees: [ME.account] }),
      item({ id: 'unassigned', list_id: other.id }), item({ id: 'closed', list_id: other.id, status: other.statuses[2] }),
      item({ id: 'done-today', list_id: other.id, status: other.statuses[2], completed_at: new Date().toISOString() }),
    ]);
    await startLive();
    board.selection = { kind: 'list', id: LIST.id };
  });

  it('counts eligible open cards and removes empty entries without removing other lists', async () => {
    const keep = { sections: ['S3'], everyone: true };
    board.me!.prefs = { also_show: { L1: { sections: ['S2', 'missing'] }, L2: keep } };
    routes.set('PATCH /me/prefs', (body) => body);
    expect(board.ctx.alsoShow).toEqual({ sections: ['S2'], everyone: false });
    expect(board.alsoShowOptions(LIST.id).map(({ section, count }) => [section.id, count])).toEqual([['S2', 1]]);
    await board.setAlsoShow(LIST.id, { sections: ['S2'], everyone: true });
    // Everyone: both open cards and the one finished today (still on the board), not the older one.
    expect(board.alsoShowOptions(LIST.id)[0].count).toBe(3);
    await board.setAlsoShow(LIST.id, { sections: [], everyone: true });
    expect(writes().at(-1)?.body).toEqual({ also_show: { L2: keep } });
    await board.setAlsoShow('L2', { sections: [], everyone: true });
    expect(writes().at(-1)?.body).toEqual({ also_show: null });
  });

  it('maps a status drop to the card’s own list and refuses missing kinds', async () => {
    await board.applyDrop('foreign', { kind: 'status', status: LIST.statuses[0] });
    expect(writes()).toEqual([]);
    const active = other.statuses[1];
    routes.set('PATCH /items/foreign', () => item({ id: 'foreign', list_id: other.id, status: active }));
    await board.applyDrop('foreign', { kind: 'status', status: LIST.statuses[1] });
    expect(writes()).toEqual([{ method: 'PATCH', path: '/items/foreign', body: { status: active.id } }]);
    await board.applyDrop('foreign', { kind: 'status', status: { ...LIST.statuses[0], kind: 'cancelled' } });
    expect(board.toast).toContain('Other has no cancelled status');
    expect(writes()).toHaveLength(1);
  });

  it('restores the saved switches when the server refuses the change', async () => {
    board.me!.prefs = { also_show: { L1: { sections: ['S2'] } } };
    routes.set('PATCH /me/prefs', () => ({ status: 422, body: { detail: 'Could not save' } }));
    await board.setAlsoShow(LIST.id, { sections: [], everyone: false });
    expect(board.alsoShowFor(LIST.id)).toEqual({ sections: ['S2'], everyone: false });
    expect(board.toast).toContain('Could not save');
  });

  it('keeps the exact bulk status for local cards and uses its kind for brought-in cards', async () => {
    routes.set('POST /items/bulk', () => []);
    const status = LIST.statuses[1];
    await board.bulkStatus(['I1', 'foreign'], status);
    expect(writes().map(({ body }) => body)).toEqual([
      { ids: ['I1'], changes: { status: status.id } },
      { ids: ['foreign'], changes: { status_kind: status.kind } },
    ]);
  });

  it('never writes another list’s positions from the list page', async () => {
    await board.reposition([{ id: 'foreign', position: 2048 }]);
    expect(writes()).toEqual([]);
  });

  it('writes no positions at all when a reorder names a brought-in card', async () => {
    // A renumbering worked out over a group that mixes two lists (a brought-in card dragged
    // under manual order) must not move the page list's own cards either.
    await board.reposition([{ id: 'I1', position: 1024 }, { id: 'foreign', position: 2048 }]);
    expect(writes()).toEqual([]);
    routes.set('PATCH /items/I1', () => item({ position: 1024 }));
    await board.reposition([{ id: 'I1', position: 1024 }]);
    expect(writes()).toEqual([{ method: 'PATCH', path: '/items/I1', body: { position: 1024 } }]);
  });
});

describe('start', () => {
  it('waits for the stream to open before reading anything over REST', async () => {
    const started = board.start();
    await settle();
    expect(calls).toEqual([]);
    FakeEventSource.last.open();
    await started;
    expect(calls[0]).toBe('/me');
    expect(board.ready).toBe(true);
    expect(board.items.I1.title).toBe('Fix gutter');
  });

  it('retries a failed first load with backoff instead of staying blank until a reload', async () => {
    routes.set('/me', () => ({ status: 503, body: { detail: 'restarting' } }));
    await startLive();
    expect(board.ready).toBe(false);
    expect(board.toast).toContain('restarting');
    expect(board.conn).toBe('reconnecting');
    expect(FakeEventSource.last.closed).toBe(true); // the stream from the failed start is dropped

    routes.set('/me', () => ME);
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    await settle(); // the retry checks /me first, then starts over with a new stream
    expect(FakeEventSource.all).toHaveLength(2);
    FakeEventSource.last.open();
    await vi.waitFor(() => expect(board.ready).toBe(true));
    expect(board.conn).toBe('live');
    expect(board.items.I1.title).toBe('Fix gutter');
    // Only the retry's stream buffers from here on.
    FakeEventSource.last.emit(ev({ field: 'title', after: 'Live' }));
    await vi.advanceTimersByTimeAsync(1);
    expect(board.items.I1.title).toBe('Live');
  });

  it('backs off 1 s, 2 s, 4 s while the load keeps failing, and signOut cancels the retry', async () => {
    routes.set('/items', () => ({ status: 500, body: {} }));
    await startLive();
    for (const wait of [1000, 2000, 4000]) {
      const before = FakeEventSource.all.length;
      await vi.advanceTimersByTimeAsync(wait - 1);
      await settle();
      expect(FakeEventSource.all).toHaveLength(before);
      await vi.advanceTimersByTimeAsync(1);
      await settle();
      expect(FakeEventSource.all).toHaveLength(before + 1);
      FakeEventSource.last.open();
      await settle();
      expect(board.ready).toBe(false);
    }
    const streams = FakeEventSource.all.length;
    const meCalls = calls.filter((p) => p === '/me').length;
    board.signOut();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(streams);
    expect(calls.filter((p) => p === '/me')).toHaveLength(meCalls);
  });

  it('a stream that fails before opening starts over, without a REST read in between', async () => {
    void board.start();
    FakeEventSource.last.fail();
    await settle();
    expect(calls).toEqual([]);
    expect(board.conn).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(FakeEventSource.all).toHaveLength(2);
    FakeEventSource.last.open();
    await vi.waitFor(() => expect(board.ready).toBe(true));
  });

  it('a stream the server refuses (a revoked token) signs out instead of retrying forever', async () => {
    routes.set('/me', () => ({ status: 401, body: {} }));
    void board.start();
    FakeEventSource.last.fail(); // EventSource reports a 401 as an error, never as open
    await vi.advanceTimersByTimeAsync(1000);
    await settle(); // the retry asks /me first
    expect(board.token).toBeNull();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });

  it('coming back online while a start waits to retry starts over at once, not a bare stream', async () => {
    routes.set('/me', () => ({ status: 503, body: {} }));
    await startLive();
    expect(board.conn).toBe('reconnecting');
    routes.set('/me', () => ME);
    window.dispatchEvent(new Event('online'));
    await settle(); // the reconnect's /me check, then start()
    FakeEventSource.last.open();
    await vi.waitFor(() => expect(board.ready).toBe(true));
    expect(board.items.I1.title).toBe('Fix gutter');
  });

  it('a 401 signs out and does not retry', async () => {
    routes.set('/me', () => ({ status: 401, body: {} }));
    await startLive();
    expect(board.token).toBeNull();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });
});

describe('buffer', () => {
  it('applies events that arrived during the load exactly once, after it', async () => {
    const items = deferred<Item[]>();
    routes.set('/items', () => items.promise);
    const seen: SlateEvent[] = [];
    board.onEvent((e) => seen.push(e));
    const started = board.start();
    FakeEventSource.last.open();
    await settle(); // /me answered; /items still pending
    const renamed = ev({ field: 'title', before: 'Fix gutter', after: 'Fixed' });
    FakeEventSource.last.emit(renamed);
    FakeEventSource.last.emit(renamed); // the server's replay may repeat an id: dropped by cursor
    expect(seen).toEqual([]);
    expect(board.lastEventId).toBe(renamed.id);
    items.resolve([item()]);
    await started;
    expect(board.ready).toBe(true);
    expect(seen).toEqual([renamed]);
    expect(board.items.I1.title).toBe('Fixed');
    // Live now: the next event is applied straight away, not buffered.
    FakeEventSource.last.emit(ev({ field: 'title', after: 'Live' }));
    await vi.advanceTimersByTimeAsync(1);
    expect(seen).toHaveLength(2);
    expect(board.items.I1.title).toBe('Live');
  });
});

describe('refresh', () => {
  it('a pull to refresh whose load fails starts over, so events buffered during it are not lost', async () => {
    await startLive();
    expect(board.ready).toBe(true);
    const items = deferred<Item[] | Failure>();
    routes.set('/items', () => items.promise);
    const refreshed = board.refresh();
    FakeEventSource.last.open();
    await settle(); // /me answered; /items pending
    const renamed = ev({ field: 'title', before: 'Fix gutter', after: 'Fixed' });
    FakeEventSource.last.emit(renamed); // buffered, and the cursor moves past it
    items.resolve({ status: 502, body: { detail: 'bad gateway' } });
    await refreshed;
    expect(board.conn).toBe('reconnecting');

    // What the server holds now; only a full load (not a resumed stream) brings it in.
    routes.set('/items', () => [item({ title: 'Fixed' })]);
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(FakeEventSource.last.url).not.toContain('since=');
    FakeEventSource.last.open();
    await vi.waitFor(() => expect(board.items.I1.title).toBe('Fixed'));
  });
});

describe('reload', () => {
  it('coalesces a burst of events that each ask for a full reload into one', async () => {
    await startLive();
    const loads = () => calls.filter((p) => p === '/lists').length;
    expect(loads()).toBe(1);
    for (let i = 0; i < 3; i++) FakeEventSource.last.emit(ev({ type: 'list.statuses_changed', item_id: null, subject_id: 'L1' }));
    await vi.advanceTimersByTimeAsync(1); // the drain applies the batch
    expect(loads()).toBe(1);
    await vi.advanceTimersByTimeAsync(100);
    expect(loads()).toBe(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(loads()).toBe(2);
  });

  it('an older load that answers late never overwrites a newer one', async () => {
    await startLive();
    const slow = deferred<Item[]>();
    let answers: (() => Answer)[] = [() => slow.promise, () => [item({ title: 'Newer' })]];
    routes.set('/items', () => (answers.shift() ?? (() => []))());
    FakeEventSource.last.emit(ev({ type: 'list.unarchived', item_id: null, subject_id: 'L1' }));
    await vi.advanceTimersByTimeAsync(101); // the reload is under way, waiting on /items
    const refreshed = board.refresh(); // pull to refresh: a second full load behind a new stream
    FakeEventSource.last.open();
    await refreshed;
    expect(board.items.I1.title).toBe('Newer');
    slow.resolve([item({ title: 'Older' })]);
    await settle();
    expect(board.items.I1.title).toBe('Newer');
    answers = [];
  });
});
