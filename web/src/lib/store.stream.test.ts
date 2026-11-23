// @vitest-environment happy-dom
// The live stream once the board is up (docs/live-stream.md): a dropped stream resumes from its
// cursor with backoff, the browser's offline and online events drive it, and what an event
// cannot supply is fetched again in one batch. Only `fetch` and `EventSource` are faked
// (board/testkit.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ev, item } from './fixtures';
import { FakeEventSource, ME, calls, deferred, freshBoard, requests, routes, settle, startLive, tearDown, type Board } from './board/testkit';

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

  it('coming online while a reconnect is checking the token opens one stream, not two', async () => {
    await emit(ev({ field: 'priority', after: 'high' })); // a cursor: the reconnect resumes
    const me = deferred<unknown>();
    routes.set('/me', () => me.promise);
    FakeEventSource.last.fail();
    await vi.advanceTimersByTimeAsync(1000); // the backoff timer's reconnect now waits on /me
    window.dispatchEvent(new Event('online')); // the browser says it is back meanwhile
    me.resolve(ME);
    await settle();
    expect(FakeEventSource.all.filter((s) => !s.closed)).toHaveLength(1);
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

  it('a refetch that fails is asked again until it answers', async () => {
    let down = true;
    routes.set('/items', () => (down ? { status: 503, body: { detail: 'busy' } } : [item({ title: 'Fresh' })]));
    await emit(ev({ type: 'item.assigned' }));
    await vi.advanceTimersByTimeAsync(50);
    await settle();
    expect(board.items.I1.title).not.toBe('Fresh');
    down = false;
    await vi.advanceTimersByTimeAsync(2000);
    await settle();
    expect(board.items.I1.title).toBe('Fresh');
  });

  it('a refetch the server refuses is not asked again', async () => {
    routes.set('/items', () => ({ status: 422, body: { detail: 'bad ids' } }));
    await emit(ev({ type: 'item.assigned' }));
    await vi.advanceTimersByTimeAsync(50);
    await settle();
    const asked = requests.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(requests.filter((r) => r.path === '/items').length).toBe(requests.slice(0, asked).filter((r) => r.path === '/items').length);
  });

  it('a Later card is asked about when its start time comes, not only at midnight', async () => {
    const start = new Date(Date.now() + 45_000).toISOString();
    board.items.I1.later = true;
    board.items.I1.start_at = start;
    routes.set('/items', () => [item({ later: false, start_at: start })]);
    await vi.advanceTimersByTimeAsync(30_000);
    await settle();
    expect(board.items.I1.later).toBe(true);
    await vi.advanceTimersByTimeAsync(30_000);
    await settle();
    expect(board.items.I1.later).toBe(false);
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
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(lists()).toBe(l0 + 1);
    expect(items()).toBe(i0);
  });

  it.each([503, 403])('applies the failed-read rule to a structure reload returning %i', async (status) => {
    const before = board.sections;
    routes.set('/sections', () => ({ status, body: { detail: 'Unavailable' } }));
    await emit(ev({ type: 'list.updated', item_id: null, subject_id: 'L1' }));
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(board.toast).toBe(status === 503 ? 'Could not refresh board structure: Unavailable' : null);
    expect(board.sections).toBe(before);
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

  it('a tag created or recoloured elsewhere reloads the tag list with the manager closed', async () => {
    expect(board.tagManagerOpen).toBe(false);
    routes.set('/tags', () => [{ id: 'T1', name: 'garden', color: '#C8102E' }]);
    const tagLoads = () => calls.filter((p) => p === '/tags').length;
    const before = tagLoads();
    await emit(
      ev({ type: 'tag.created', item_id: null, subject_id: 'T1' }),
      ev({ type: 'tag.updated', item_id: null, subject_id: 'T1', field: 'color', after: '#C8102E' }),
    );
    await vi.advanceTimersByTimeAsync(50);
    await settle();
    expect(tagLoads()).toBe(before + 1); // a burst of tag events is one reload
    expect(board.tags.find((t) => t.name === 'garden')?.color).toBe('#C8102E');
  });

  it('an item event in a project section schedules one debounced structure reload', async () => {
    board.sections = [
      { id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work', created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true, next_step: null },
    ];
    const sections = () => calls.filter((p) => p === '/sections').length;
    const before = sections();
    await emit(ev({ field: 'due_at', after: '2026-11-01T22:00:00Z' }), ev({ field: 'title', after: 'Again' }));
    await vi.advanceTimersByTimeAsync(50);
    expect(sections()).toBe(before);
    await vi.advanceTimersByTimeAsync(100);
    await settle();
    expect(sections()).toBe(before + 1); // a burst is one reload
  });

  it('an item event outside a project section schedules no structure reload', async () => {
    board.sections = [
      { id: 'S1', name: 'Plain', slug: 'plain', position: 0, audience: 'work', created_at: '2026-10-20T00:00:00Z', archived_at: null, project: false, next_step: null },
    ];
    const sections = () => calls.filter((p) => p === '/sections').length;
    const before = sections();
    await emit(ev({ field: 'due_at', after: '2026-11-01T22:00:00Z' }));
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    expect(sections()).toBe(before);
  });

  it('moving the next step out of its project refreshes the source section', async () => {
    const project = { id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work' as const, created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true };
    const step = { item_id: 'I1', title: 'Launch', assignee_ids: [ME.account.id], due_at: '2026-11-01' };
    board.sections = [{ ...project, next_step: step }];
    board.lists.L2 = { ...board.lists.L1, id: 'L2', section_id: 'S2' };
    routes.set('/sections', () => [{ ...project, next_step: null }]);
    routes.set('/items', () => [item({ list_id: 'L2' })]);

    await emit(ev({ type: 'item.moved', field: 'list_id', before: 'L1', after: 'L2' }));
    await vi.advanceTimersByTimeAsync(400);
    expect(board.items.I1.list_id).toBe('L2');
    expect(board.sections[0].next_step).toBeNull();
    expect(board.projectNeeds.map((need) => need.section.id)).toEqual(['S1']);
  });

  it('refreshes a project when an uncached restored card returns to it', async () => {
    const project = { id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work' as const, created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true };
    board.sections = [{ ...project, next_step: null }];
    const step = { item_id: 'I3', title: 'Send the draft', assignee_ids: [ME.account.id], due_at: '2026-11-01' };
    routes.set('/items', () => [item({ id: 'I3', title: step.title })]);
    routes.set('/sections', () => [{ ...project, next_step: step }]);
    await emit(ev({ type: 'item.restored', item_id: 'I3', after: { archived_at: null, trashed_at: null } }));
    await vi.advanceTimersByTimeAsync(400);
    expect(board.items.I3.title).toBe(step.title);
    expect(board.sections[0].next_step).toEqual(step);
  });

  it('signing out cancels a queued project refresh', async () => {
    board.sections = [{ id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work', created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true }];
    const before = calls.filter((path) => path === '/sections').length;
    await emit(ev({ field: 'title', after: 'Rename' }));
    board.signOut();
    await vi.advanceTimersByTimeAsync(400);
    expect(calls.filter((path) => path === '/sections')).toHaveLength(before);
  });

  it('a slow project refresh cannot replace a newer next step', async () => {
    const project = { id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work' as const, created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true };
    board.sections = [{ ...project, next_step: null }];
    const older = deferred<unknown>();
    routes.set('/sections', () => older.promise);
    await emit(ev({ field: 'title', after: 'Old next step' }));
    await vi.advanceTimersByTimeAsync(400);
    const step = { item_id: 'I1', title: 'New next step', assignee_ids: [ME.account.id], due_at: '2026-11-01' };
    routes.set('/sections', () => [{ ...project, next_step: step }]);
    await emit(ev({ field: 'title', after: step.title }));
    await vi.advanceTimersByTimeAsync(400);
    expect(board.sections[0].next_step).toEqual(step);
    older.resolve([{ ...project, next_step: null }]);
    await settle();
    expect(board.sections[0].next_step).toEqual(step);
  });

  it('a lead-days change from another device refetches held cards', async () => {
    routes.set('/items', () => [item({ later: true }), item({ id: 'I2' })]);
    await emit(ev({ type: 'account.prefs_changed', item_id: null, subject_id: ME.account.id, after: { lead_days: 2 } }));
    await vi.advanceTimersByTimeAsync(400);
    expect(board.prefs.lead_days).toBe(2);
    expect(board.items.I1.later).toBe(true);
  });

  it('when the board day rolls over, later cards are refetched so they can come back', async () => {
    board.items.I1 = { ...board.items.I1, later: true, due_at: '2026-10-24T22:00:00Z' };
    routes.set('/items', () => [item({ later: false, due_at: '2026-10-24T22:00:00Z' })]);
    // 2026-10-22 23:59:50 in Chicago (UTC-5): the next 30 s clock tick crosses midnight.
    vi.setSystemTime(new Date('2026-10-23T04:59:50Z'));
    board.now = new Date('2026-10-23T04:59:50Z');
    const before = requests.length;
    await vi.advanceTimersByTimeAsync(30_000);
    await settle();
    const refetches = requests.slice(before).filter((r) => r.path === '/items');
    expect(refetches).toHaveLength(1); // one batch for the Later cards, nothing else
    expect(board.items.I1.later).toBe(false); // the server's recomputed flag replaced the stale one
    expect(board.items.I2.title).toBe('Paint fence');
  });

  it('the needs total counts item needs and project needs together', async () => {
    board.sections = [
      { id: 'S1', name: 'Launch', slug: 'launch', position: 0, audience: 'work', created_at: '2026-10-20T00:00:00Z', archived_at: null, project: true, next_step: null },
    ];
    expect(board.projectNeeds.map((p) => p.section.id)).toEqual(['S1']);
    expect(board.needsTotal).toBe(board.needsMe.length + 1);
  });

  it('a lone event applies on the next tick, with no batching wait', async () => {
    FakeEventSource.last.emit(ev({ field: 'title', after: 'One' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(board.items.I1.title).toBe('One');
  });
});
