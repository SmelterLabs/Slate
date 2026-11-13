// @vitest-environment happy-dom
// The live stream once the board is up (docs/live-stream.md): a dropped stream resumes from its
// cursor with backoff, the browser's offline and online events drive it, and what an event
// cannot supply is fetched again in one batch. Only `fetch` and `EventSource` are faked
// (board/testkit.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ev, item } from './fixtures';
import { FakeEventSource, ME, calls, freshBoard, requests, routes, settle, startLive, tearDown, type Board } from './board/testkit';

let board: Board;

beforeEach(async () => {
  board = await freshBoard();
  routes.set('/items', () => [item(), item({ id: 'I2', title: 'Paint fence' })]);
  await startLive(board);
  await settle();
});
afterEach(() => tearDown(board));

const emit = async (...events: ReturnType<typeof ev>[]) => {
  for (const e of events) FakeEventSource.last.emit(e);
  await vi.advanceTimersByTimeAsync(1);
};

describe('resume', () => {
  it('a dropped stream reconnects from its cursor after 1 s, then 2 s, without a full load', async () => {
    await emit(ev({ field: 'title', after: 'Fixed' }));
    const cursor = board.lastEventId;
    const loads = () => calls.filter((p) => p === '/items').length;
    expect(loads()).toBe(1);

    FakeEventSource.last.fail();
    expect(board.conn).toBe('reconnecting');
    expect(FakeEventSource.all.at(-1)!.closed).toBe(true);
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    await settle(); // the reconnect's /me check, then the stream
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.last.url).toContain(`since=${cursor}`);

    FakeEventSource.last.fail(); // fails again before opening: the wait doubles
    await vi.advanceTimersByTimeAsync(1999);
    await settle();
    expect(FakeEventSource.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    await settle();
    expect(FakeEventSource.all).toHaveLength(3);
    FakeEventSource.last.open();
    expect(board.conn).toBe('live');
    expect(loads()).toBe(1); // resumed, not reloaded
  });

  it('events replayed after a resume that the board already applied are dropped by id', async () => {
    const rename = ev({ field: 'title', after: 'Fixed' });
    const seen: string[] = [];
    board.onEvent((e) => seen.push(e.id));
    await emit(rename);
    FakeEventSource.last.fail();
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    FakeEventSource.last.open();
    await emit(rename, ev({ field: 'title', after: 'Fixed again' }));
    expect(seen).toHaveLength(2);
    expect(board.items.I1.title).toBe('Fixed again');
  });

  it('going offline drops the stream at once; coming back online resumes without waiting', async () => {
    await emit(ev({ field: 'priority', after: 'high' }));
    window.dispatchEvent(new Event('offline'));
    expect(board.online).toBe(false);
    expect(board.conn).toBe('reconnecting');
    expect(FakeEventSource.last.closed).toBe(true);
    window.dispatchEvent(new Event('online'));
    expect(board.online).toBe(true);
    await settle();
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.last.url).toContain('since=');
  });

  it('a reconnect whose /me check fails keeps backing off; one that is refused signs out', async () => {
    routes.set('/me', () => ({ status: 503, body: {} }));
    FakeEventSource.last.fail();
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(FakeEventSource.all).toHaveLength(1); // no stream until /me answers
    routes.set('/me', () => ({ status: 401, body: {} }));
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(board.token).toBeNull();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });

  it('signing out closes the stream and stops every timer', async () => {
    const stream = FakeEventSource.last;
    board.signOut();
    expect(stream.closed).toBe(true);
    expect(board.token).toBeNull();
    stream.fail();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
  });
});

describe('applying events', () => {
  it('items an event names but cannot fill in are fetched again, together, once', async () => {
    routes.set('/items', () => [item({ assignees: [{ id: 'A_alex', handle: 'alex', display_name: 'Alex', kind: 'person' }] }), item({ id: 'I2' })]);
    const before = requests.length;
    await emit(ev({ type: 'item.assigned' }), ev({ type: 'item.assigned', item_id: 'I2', subject_id: 'I2' }), ev({ type: 'item.assigned' }));
    expect(requests.length).toBe(before); // batched behind a short wait
    await vi.advanceTimersByTimeAsync(50);
    await settle();
    const refetches = requests.slice(before).filter((r) => r.path === '/items');
    expect(refetches).toHaveLength(1);
    expect(board.items.I1.assignees.map((a) => a.handle)).toEqual(['alex']);
  });

  it('a column change shows on the card and in the plain copy the views read', async () => {
    await emit(ev({ field: 'priority', before: null, after: 'urgent' }));
    expect(board.items.I1.priority).toBe('urgent');
    expect(board.plainItems.find((i) => i.id === 'I1')?.priority).toBe('urgent');
  });

  it("this account's prefs changed on another device: the board shows the same", async () => {
    await emit(ev({ type: 'account.prefs_changed', item_id: null, subject_id: ME.account.id, after: { theme: 'light' } }));
    expect(board.prefs.theme).toBe('light');
    await emit(ev({ type: 'account.prefs_changed', item_id: null, subject_id: 'A_other', after: { theme: 'dark' } }));
    expect(board.prefs.theme).toBe('light');
  });

  it('a structure event reloads the nav, not the items', async () => {
    const items = () => calls.filter((p) => p === '/items').length;
    const lists = () => calls.filter((p) => p === '/lists').length;
    const [i0, l0] = [items(), lists()];
    await emit(ev({ type: 'list.updated', item_id: null, subject_id: 'L1' }));
    await settle();
    expect(lists()).toBe(l0 + 1);
    expect(items()).toBe(i0);
  });

  it("an event about the open task refreshes the task window's detail", async () => {
    routes.set('/items/I1', () => ({ ...item(), subtasks: [], comments: [], events: [], checklists: [], attachments: [], watchers: [] }));
    await board.open('I1');
    const detail = () => calls.filter((p) => p === '/items/I1').length;
    const before = detail();
    await emit(ev({ type: 'item.commented', after: { body_md: 'Done?' } }));
    await settle();
    expect(detail()).toBe(before + 1);
  });

  it('a lone event applies on the next tick, with no batching wait', async () => {
    FakeEventSource.last.emit(ev({ field: 'title', after: 'One' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(board.items.I1.title).toBe('One');
  });
});
