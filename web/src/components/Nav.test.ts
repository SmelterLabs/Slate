// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FakeEventSource, ME, freshBoard, routes, settle, startLive, tearDown, type Board } from '../lib/board/testkit';
import { LIST, ev, item } from '../lib/fixtures';
import type { AgentEntry, Section } from '../lib/types';
import type { InboxEntry } from '../lib/inbox.svelte';
import type { PlanOut } from '../lib/plan';

const section: Section = {
  id: 'S1', name: 'Work', slug: 'work', audience: 'work', position: 0,
  archived_at: null, created_at: '2026-10-20T00:00:00Z',
};
const agent = { id: 'A_agent', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' } as const;
const reading = { ...LIST, name: 'Reading' };
const card = item({
  title: 'File the old article',
  due_at: '2026-10-01T00:00:00Z',
  assignees: [ME.account, agent],
});
const referenceEvent = ev({
  type: 'list.updated', subject_id: reading.id, item_id: null, actor_id: ME.account.id,
  field: 'reference', before: false, after: true,
});

let board: Board;
let target: HTMLDivElement;
const mounted: (() => Promise<void>)[] = [];

beforeEach(async () => {
  board = await freshBoard();
  vi.setSystemTime(new Date('2026-10-29T12:00:00Z'));
  routes.set('/sections', () => [section]);
  routes.set('/lists', () => [reading, { ...LIST, id: 'L2', name: 'Work list' }]);
  routes.set('/items', () => [card, item({ id: 'I2', list_id: 'L2', title: 'A task' })]);
  await startLive(board);
  await settle();
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(async () => {
  for (const destroy of mounted.splice(0)) await destroy();
  target.remove();
  tearDown(board);
});

it('a live reference change updates the sidebar, project attention, Inbox and Agents page', async () => {
  const [{ default: Nav }, { default: ProjectHome }, { default: AgentsPage }, { inbox }, { flushSync, mount, unmount }] = await Promise.all([
    import('./Nav.svelte'), import('./ProjectHome.svelte'), import('./AgentsPage.svelte'),
    import('../lib/inbox.svelte'), import('svelte'),
  ]);
  let reference = false;
  routes.set('/lists', () => [{ ...reading, reference }, { ...LIST, id: 'L2', name: 'Work list' }]);
  routes.set('/sections/S1/project', () => ({ section, participants: [] }));
  routes.set('/sections/S1/documents', () => ({
    drawers: [], pages: [], needs_you: [], counts: { docs: 0, files: 0, open_threads: 0 },
  }));
  routes.set('/stats', () => ({ routes: [], live_streams: 0 }));
  routes.set('/events', () => ({ events: [] }));
  const due: InboxEntry = {
    event: ev({ type: 'item.overdue', item_id: card.id }), reason: 'due', read: false,
    item_title: card.title, list_id: reading.id,
  };
  routes.set('/inbox', () => reference ? [] : [due]);
  const presence = {
    online: false, last_seen_at: null, last_seen_via: null, live_streams: 0,
    working: [], events_24h: 0, last_event_at: null, throttled: 0,
  };
  routes.set('/agents', (): AgentEntry[] => [
    { ...presence, account: ME.account, open_assigned: 0 },
    { ...presence, account: agent, open_assigned: reference ? 0 : 1 },
  ]);
  routes.set('/agents/plan', (): PlanOut => ({
    horizon_hours: 24,
    agents: [{ ...agent, leases: [], next: reference ? [] : [
      { item_id: card.id, title: card.title, list: reading.name, due_at: card.due_at, reason: 'due', at: null },
    ] }],
  }));
  inbox.start();
  mounted.push(async () => inbox.stop());
  const nav = mount(Nav, { target });
  const project = mount(ProjectHome, { target, props: { id: section.id } });
  const agents = mount(AgentsPage, { target });
  mounted.push(() => unmount(agents), () => unmount(project), () => unmount(nav));
  flushSync();
  await settle();
  flushSync();

  const readingRow = target.querySelector('[data-list="Reading"]')!;
  const workRow = target.querySelector('[data-list="Work list"]')!;
  const everything = target.querySelector('[href="#/everything"]')!;
  const attention = target.querySelector('[aria-label="Your project attention"]')!;
  const agentsPage = target.querySelector('[aria-label="Agents"]')!;
  const assigned = () => agentsPage.querySelector('[title="Open items assigned"]')?.textContent;
  expect(readingRow.querySelector('.count')?.textContent).toBe('1');
  expect(everything.querySelector('.count.hot')?.textContent).toBe('1');
  expect(attention.textContent).toContain('1 owned tasks');
  expect(attention.textContent).toContain(card.title);
  expect(inbox.unread).toBe(1);
  expect(assigned()).toBe('1 assigned');
  expect(agentsPage.textContent).toContain(card.title);

  reference = true;
  FakeEventSource.last.emit(referenceEvent);
  await vi.advanceTimersByTimeAsync(700);
  await settle();
  flushSync();

  expect(readingRow.querySelector('[data-reference]')?.textContent).toBe('Reference');
  expect(readingRow.querySelector('.count')).toBeNull();
  expect(workRow.querySelector('.count')?.textContent).toBe('1');
  expect(everything.querySelector('.count')).toBeNull();
  expect.soft(attention.textContent).toContain('0 owned tasks');
  expect.soft(attention.textContent).not.toContain(card.title);
  expect.soft(target.querySelector('[aria-label="Project work and ideas"]')?.querySelector('[data-reference]')?.textContent).toBe('Reference');
  expect.soft(inbox.entries).toEqual([]);
  expect.soft(inbox.unread).toBe(0);
  expect.soft(assigned()).toBe('0 assigned');
  expect.soft(agentsPage.textContent).not.toContain(card.title);
});

it('retains the document count on a drawer with a stored reference flag', async () => {
  const [{ default: Nav }, { docs }, { mount, unmount }] = await Promise.all([
    import('./Nav.svelte'), import('../lib/docs.svelte'), import('svelte'),
  ]);
  board.lists.L1 = { ...reading, kind: 'drawer', reference: true };
  const at = '2026-10-20T00:00:00Z';
  docs.list = [{
    id: 'D1', kind: 'page', file: null, title: 'Reading notes', icon: null,
    parent_id: null, section_id: section.id, list_id: reading.id, position: 0,
    revision: 1, version_no: 1, created_by: ME.account.id, updated_by: ME.account.id,
    created_at: at, updated_at: at, archived_at: null,
  }];
  const nav = mount(Nav, { target });
  mounted.push(() => unmount(nav));

  const drawer = target.querySelector('[data-list="Reading"]')!;
  expect(drawer.querySelector('.count')?.textContent).toBe('1');
  expect(drawer.querySelector('[data-reference]')).toBeNull();
});
