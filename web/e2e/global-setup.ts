// Bootstrap Alex, add an agent, seed one section, one list and a few items. Writes
// the tokens and ids to .e2e/seed.json for the specs.
import { writeFileSync } from 'node:fs';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays, workspaceInstant } from '../src/lib/views';
import { BASE_URL, SEED_FILE, type Seed } from './seed';

async function call<T>(method: string, path: string, token: string | null, body?: unknown) {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export default async function globalSetup() {
  const boot = await call<{ token: string }>('POST', '/bootstrap', null, {
    handle: 'alex',
    display_name: 'Alex',
  });
  const alex = boot.token;
  const agentAcct = await call<{ id: string }>('POST', '/accounts', alex, {
    handle: 'agent',
    display_name: 'Agent',
    kind: 'agent',
  });
  const agent = (
    await call<{ token: string }>('POST', `/accounts/${agentAcct.id}/tokens`, alex, {
      scopes: ['write'],
      label: 'e2e',
    })
  ).token;
  const section = await call<{ id: string }>('POST', '/sections', alex, {
    name: 'Personal',
    audience: 'personal',
  });
  const list = await call<{ id: string }>('POST', '/lists', alex, {
    section_id: section.id,
    name: 'Home',
    statuses: [
      { name: 'Open', kind: 'open' },
      { name: 'Ready', kind: 'active' },
      { name: 'Done', kind: 'done' },
    ],
  });
  const drag = await call<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Drag me to Ready',
    priority: 'normal',
  });
  const live = await call<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Agent renames this',
    assignees: ['agent'],
    tags: ['chores'],
  });
  await call('POST', '/items', alex, {
    list_id: list.id,
    title: 'Pay the water bill',
    priority: 'urgent',
    due_at: '2026-01-05',
    body_md: 'Account **4411**. See [the portal](https://example.com).',
  });
  await call('POST', '/items', alex, { parent_id: live.id, title: 'A subtask' });

  // A second section for the views specs: items spread over the due board's columns.
  const work = await call<{ id: string }>('POST', '/sections', alex, { name: 'Work', audience: 'work' });
  const tasks = await call<{ id: string }>('POST', '/lists', alex, {
    section_id: work.id,
    name: 'Tasks',
    statuses: [
      { name: 'To do', kind: 'open' },
      { name: 'Doing', kind: 'active' },
      { name: 'Finished', kind: 'done' },
    ],
  });
  const today = workspaceDay(new Date());
  const at = (days: number, h: number, m = 0) => workspaceInstant(addDays(today, days), h, m).toISOString();
  const errand = (body: Record<string, unknown>) =>
    call<{ id: string }>('POST', '/items', alex, { list_id: tasks.id, ...body });
  const todayItem = await errand({ title: 'Today errand', due_at: at(0, 23, 59), tags: ['errand'], priority: 'high' });
  await errand({ title: 'Tomorrow call', due_at: at(1, 12), tags: ['errand'], priority: 'low' });
  await errand({ title: 'Three days out', due_at: at(3, 10), priority: 'high' });
  await errand({ title: 'Way later', due_at: at(20, 9) });
  await errand({ title: 'Someday maybe' });
  const bulk1 = await errand({ title: 'Bulk one', tags: ['batch'] });
  const bulk2 = await errand({ title: 'Bulk two', tags: ['batch'] });

  const seed: Seed = {
    alex,
    agent,
    sectionId: section.id,
    listId: list.id,
    dragId: drag.id,
    liveId: live.id,
    tasksId: tasks.id,
    workSectionId: work.id,
    todayId: todayItem.id,
    bulkIds: [bulk1.id, bulk2.id],
  };
  writeFileSync(SEED_FILE, JSON.stringify(seed));
}
