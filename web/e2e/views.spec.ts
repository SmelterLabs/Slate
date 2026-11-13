import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/when';
import { BASE_URL, seed } from './seed';

// The views board. Runs after board.spec.ts against the same seeded server.

const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (!shots) return;
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: join(shots, `${name}.png`) });
  await page.emulateMedia({ colorScheme: null });
}

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

async function signedIn(page: Page, path = '/') {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
}

// A monitor-sized window: the due board is ten columns wide, and a drag does not autoscroll.
test.use({ viewport: { width: 1920, height: 1080 } });
// Toolbar changes are remembered on the account until Save or Revert: start each test clean.
test.beforeEach(() => api('PATCH', '/me/prefs', seed().alex, { last_view: null, view_drafts: null }));

const today = () => workspaceDay(new Date());
const column = (page: Page, key: string) => page.locator(`.column[data-group="${key}"]`);
const card = (page: Page, title: string) => page.locator('.card', { hasText: title });
/** The shared No due date column fills up across specs: open its "Show more" so a card past the cap counts. */
async function noneColumn(page: Page) {
  const col = column(page, 'none');
  await expect(col.locator('.card').first()).toBeVisible();
  const more = col.locator('button.more');
  if (await more.isVisible()) await more.click();
  return col;
}

/** Group by through the toolbar's Group chip. */
async function groupBy(page: Page, name: string) {
  await page.getByRole('button', { name: 'Group by' }).click();
  await page.getByRole('menuitemradio', { name }).click();
}

/** The Tasks list's section, grouped by due: a scope with only the views-spec items. */
async function workBoard(page: Page, query = '') {
  await signedIn(page, `/${query}#/section/${seed().workSectionId}`);
  await expect(column(page, 'today')).toBeVisible();
}

test('Everything lands on "This week": Overdue, Today, named days, Future, No due date, Done', async ({ page }) => {
  await signedIn(page, '/');
  await expect(page.getByRole('tab', { name: /This week/ })).toHaveAttribute('aria-selected', 'true');
  const heads = page.locator('.column .column-head > span:first-of-type');
  await expect(heads.first()).toBeVisible();
  const titles = await page.locator('.column').evaluateAll((cols) => cols.map((c) => c.getAttribute('data-group')));
  expect(titles).toEqual([
    'overdue',
    'today',
    ...[1, 2, 3, 4, 5, 6].map((n) => addDays(today(), n)),
    'future',
    'none',
    'done',
  ]);
  await expect(column(page, 'overdue')).toContainText('Pay the water bill');
  await expect(column(page, 'today')).toContainText('Today errand');
  await expect(column(page, addDays(today(), 1))).toContainText('Tomorrow call');
  await expect(column(page, addDays(today(), 3))).toContainText('Three days out');
  await expect(column(page, 'future')).toContainText('Way later');
  await expect(await noneColumn(page)).toContainText('Someday maybe');
  // Wide scopes show which list a card is from.
  await expect(card(page, 'Today errand').locator('.list-chip')).toHaveText('Tasks');
  await shot(page, 'everything-due');
});

test('dragging a card from Today to a day column moves its due date, keeping the time', async ({ page }) => {
  await workBoard(page);
  const target = addDays(today(), 2);
  await card(page, 'Today errand').dragTo(column(page, target));
  await expect(column(page, target).locator('.card', { hasText: 'Today errand' })).toBeVisible();
  const { alex, todayId } = seed();
  await expect
    .poll(async () => workspaceDay(new Date((await api<{ due_at: string }>('GET', `/items/${todayId}`, alex)).due_at)))
    .toBe(target);
  const due = new Date((await api<{ due_at: string }>('GET', `/items/${todayId}`, alex)).due_at);
  const time = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Chicago',
    hour: 'numeric',
    minute: '2-digit',
    hour12: false,
  }).format(due);
  expect(time).toBe('23:59');
  // Put it back for the specs after this one.
  await api('PATCH', `/items/${todayId}`, alex, { due_at: new Date(due.getTime() - 2 * 86_400_000).toISOString() });
});

test('a filter on tag and priority narrows the board, and chips summarise it', async ({ page }) => {
  await workBoard(page);
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  const panel = page.locator('.dd-panel');
  await panel.getByRole('button', { name: '+ Rule' }).click();
  await panel.getByRole('group', { name: 'Tag' }).getByLabel('#errand').check();
  await panel.getByRole('button', { name: '+ Rule' }).click();
  await panel.locator('.fb-rule').nth(1).getByLabel('Field').selectOption('priority');
  await panel.getByRole('group', { name: 'Priority' }).getByLabel('high').check();
  await page.keyboard.press('Escape');

  await expect(card(page, 'Today errand')).toBeVisible();
  await expect(card(page, 'Tomorrow call')).toHaveCount(0); // errand, but low
  await expect(card(page, 'Three days out')).toHaveCount(0); // high, but no tag
  await expect(page.locator('.chips')).toContainText('Tag has any of #errand');
  await expect(page.locator('.chips')).toContainText('Priority is high');
  // The view now has unsaved changes; Revert puts it back.
  await expect(page.locator('.view-tab.on .dirty')).toBeVisible();
  await page.getByRole('button', { name: 'Revert' }).click();
  await expect(card(page, 'Three days out')).toBeVisible();
});

test('grouping by priority makes priority columns, and dropping on one sets it', async ({ page }) => {
  await workBoard(page);
  await groupBy(page, 'Priority');
  await expect(page.locator('.column')).toHaveCount(6); // four priorities, No priority, Done
  await expect(column(page, 'high')).toContainText('Three days out');
  await expect(column(page, 'low')).toContainText('Tomorrow call');
  await card(page, 'Someday maybe').dragTo(column(page, 'urgent'));
  await expect(column(page, 'urgent')).toContainText('Someday maybe');
  const { alex, tasksId } = seed();
  await expect
    .poll(async () => {
      const found = await api<{ title: string; priority: string }[]>('GET', `/items?list_id=${tasksId}&q=Someday`, alex);
      return found[0]?.priority;
    })
    .toBe('urgent');
  await api('PATCH', `/items/${(await api<{ id: string }[]>('GET', `/items?list_id=${tasksId}&q=Someday`, alex))[0].id}`, alex, {
    priority: null,
  });
});

test('a change made through the API appears live on the Everything board', async ({ page }) => {
  await signedIn(page, '/#/everything');
  await expect(await noneColumn(page)).toContainText('Someday maybe');
  const { agent, alex, tasksId } = seed();
  const [item] = await api<{ id: string }[]>('GET', `/items?list_id=${tasksId}&q=Someday`, alex);
  await api('PATCH', `/items/${item.id}`, agent, { due_at: addDays(today(), 1) });
  await expect(column(page, addDays(today(), 1))).toContainText('Someday maybe', { timeout: 2000 });
  await api('PATCH', `/items/${item.id}`, agent, { due_at: null });
  await expect(column(page, 'none')).toContainText('Someday maybe', { timeout: 2000 });
});

test('monitor mode hides nav and toolbar, shows a clock, stays live', async ({ page }) => {
  await workBoard(page, '?kiosk=1');
  await expect(page.locator('.kiosk-bar .clock')).toBeVisible();
  await expect(page.locator('.conn-live')).toBeVisible();
  await expect(page.locator('.nav')).toHaveCount(0);
  await expect(page.locator('.toolbar')).toHaveCount(0);
  await expect(page.getByLabel('Quick add')).toHaveCount(0);
  const { agent, todayId } = seed();
  await api('PATCH', `/items/${todayId}`, agent, { title: 'Today errand (kiosk)' });
  await expect(card(page, 'Today errand (kiosk)')).toBeVisible({ timeout: 2000 });
  await api('PATCH', `/items/${todayId}`, agent, { title: 'Today errand' });
  await shot(page, 'kiosk');
});

test('a saved view round-trips: Save as new, survives a reload, rename, delete', async ({ page }) => {
  await workBoard(page);
  await groupBy(page, 'Priority');
  await page.getByRole('button', { name: 'Save view' }).click();
  await page.getByLabel('New view name').fill('By priority');
  await page.getByLabel('New view name').press('Enter');
  await expect(page.getByRole('tab', { name: /By priority/ })).toHaveAttribute('aria-selected', 'true');
  await expect(page).toHaveURL(/#\/section\/[^/]+\/v\/[^/]+$/);
  const viewId = page.url().split('/v/')[1];

  const { alex, workSectionId } = seed();
  const views = await api<{ id: string; name: string; config: { group_by: string } }[]>(
    'GET',
    `/views?scope=section:${workSectionId}`,
    alex,
  );
  expect(views.find((v) => v.id === viewId)).toMatchObject({ name: 'By priority', config: { group_by: 'priority' } });

  await page.reload();
  await expect(page.getByRole('button', { name: 'Group by' })).toHaveText(/Group: Priority/);
  await expect(page.locator('.column')).toHaveCount(6);

  await page.getByRole('button', { name: 'View menu' }).click();
  await page.getByRole('button', { name: 'Rename' }).click();
  await page.getByLabel('View name').fill('Priorities');
  await page.getByLabel('View name').press('Enter');
  await expect(page.getByRole('tab', { name: /Priorities/ })).toBeVisible();

  await page.getByRole('button', { name: 'View menu' }).click();
  await page.getByRole('button', { name: 'Delete view' }).click();
  await page.getByRole('button', { name: /Really delete/ }).click();
  await expect(page.getByRole('tab', { name: /Priorities/ })).toHaveCount(0);
});

test('bulk: select two cards and reschedule them to tomorrow in one go', async ({ page }) => {
  await workBoard(page);
  for (const title of ['Bulk one', 'Bulk two']) {
    await card(page, title).hover();
    await card(page, title).getByLabel('Select').click();
  }
  const bar = page.getByRole('region', { name: 'Bulk edit' });
  await expect(bar).toContainText('2 selected');
  const tomorrow = addDays(today(), 1);
  await bar.getByRole('button', { name: 'Set due date' }).click();
  await bar.getByRole('button', { name: 'Tomorrow' }).click();
  await expect(column(page, tomorrow)).toContainText('Bulk one');
  await expect(column(page, tomorrow)).toContainText('Bulk two');
  const { alex, bulkIds } = seed();
  for (const id of bulkIds) {
    const it = await api<{ due_at: string }>('GET', `/items/${id}`, alex);
    expect(workspaceDay(new Date(it.due_at))).toBe(tomorrow);
  }
  await page.keyboard.press('Escape');
  await expect(bar).toHaveCount(0);
});

test('bulk: status kind across two lists is one call and lands each in its own Done', async ({ page }) => {
  const { alex, workSectionId, tasksId } = seed();
  const other = await api<{ id: string; statuses: { id: string; kind: string }[] }>('POST', '/lists', alex, {
    section_id: workSectionId,
    name: 'Kind tasks',
  });
  const due = `${today()}T22:00:00Z`;
  const a = await api<{ id: string }>('POST', '/items', alex, { list_id: tasksId, title: 'Kind one', due_at: due });
  const b = await api<{ id: string }>('POST', '/items', alex, { list_id: other.id, title: 'Kind two', due_at: due });
  const calls: string[] = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/items/bulk')) calls.push(r.postData() ?? '');
  });
  await workBoard(page);
  for (const title of ['Kind one', 'Kind two']) {
    await card(page, title).hover();
    await card(page, title).getByLabel('Select').click();
  }
  const bar = page.getByRole('region', { name: 'Bulk edit' });
  await bar.getByRole('button', { name: 'Set status' }).click();
  await bar.getByRole('menuitem', { name: 'Done' }).click();
  await expect(column(page, 'done')).toContainText('Kind one');
  await expect(column(page, 'done')).toContainText('Kind two');
  expect(calls).toHaveLength(1);
  expect(JSON.parse(calls[0]).changes).toEqual({ status_kind: 'done' });
  const doneIn = (l: { statuses: { id: string; kind: string }[] }) => l.statuses.find((s) => s.kind === 'done')!.id;
  const tasks = await api<{ statuses: { id: string; kind: string }[] }>('GET', `/lists/${tasksId}`, alex);
  expect((await api<{ status: { id: string } }>('GET', `/items/${a.id}`, alex)).status.id).toBe(doneIn(tasks));
  expect((await api<{ status: { id: string } }>('GET', `/items/${b.id}`, alex)).status.id).toBe(doneIn(other));
  await page.keyboard.press('Escape');
});

test('bulk: delete asks first, moves the cards to the trash, and Undo brings them back', async ({ page }) => {
  const { alex, tasksId } = seed();
  const due = `${today()}T22:00:00Z`;
  const a = await api<{ id: string }>('POST', '/items', alex, { list_id: tasksId, title: 'Trash one', due_at: due });
  const b = await api<{ id: string }>('POST', '/items', alex, { list_id: tasksId, title: 'Trash two', due_at: due });
  await workBoard(page);
  for (const title of ['Trash one', 'Trash two']) {
    await card(page, title).hover();
    await card(page, title).getByLabel('Select').click();
  }
  const bar = page.getByRole('region', { name: 'Bulk edit' });
  await bar.getByRole('button', { name: 'Trash' }).click();
  await expect(bar).toContainText('Trash 2 tasks?');
  await bar.getByRole('button', { name: 'Cancel' }).click();
  await expect(card(page, 'Trash one')).toBeVisible();
  await bar.getByRole('button', { name: 'Trash' }).click();
  await bar.getByRole('group', { name: 'Confirm' }).getByRole('button', { name: 'Trash' }).click();
  await expect(bar).toHaveCount(0);
  await expect(card(page, 'Trash one')).toHaveCount(0);
  await expect(card(page, 'Trash two')).toHaveCount(0);
  for (const it of [a, b]) {
    expect((await api<{ trashed_at: string | null }>('GET', `/items/${it.id}`, alex)).trashed_at).not.toBeNull();
  }
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(card(page, 'Trash one')).toBeVisible();
  await expect(card(page, 'Trash two')).toBeVisible();
  for (const it of [a, b]) {
    await expect
      .poll(async () => (await api<{ trashed_at: string | null }>('GET', `/items/${it.id}`, alex)).trashed_at)
      .toBeNull();
  }
});

test('one-click complete moves the card to the Done column, with Undo', async ({ page }) => {
  await workBoard(page);
  await card(page, 'Bulk one').hover();
  await card(page, 'Bulk one').getByRole('button', { name: 'Complete' }).click();
  await expect(column(page, 'done')).toContainText('Bulk one');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(column(page, 'done')).not.toContainText('Bulk one');
  await expect(card(page, 'Bulk one')).toBeVisible();
  const { alex, bulkIds } = seed();
  await expect
    .poll(async () => (await api<{ status: { kind: string } }>('GET', `/items/${bulkIds[0]}`, alex)).status.kind)
    .toBe('open');
});

test('keyboard: / searches, b flips layout, ? shows the sheet', async ({ page }) => {
  await workBoard(page);
  await page.keyboard.press('/');
  await page.keyboard.type('Bulk');
  await expect(page.locator('.card')).toHaveCount(2);
  await page.keyboard.press('Escape');
  await page.keyboard.press('b');
  await expect(page.locator('.table')).toBeVisible();
  await expect(page.locator('.list-group[data-group="none"]')).toBeVisible();
  await page.keyboard.press('?');
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toHaveCount(0);
  // With a task open, board keys leave the board behind it alone.
  await page.locator('.list-group .row-title').first().click();
  await expect(page.locator('.tw')).toBeVisible();
  await page.locator('.tw-top').click();
  await page.keyboard.press('b');
  await expect(page.locator('.table')).toBeVisible();
  await page.keyboard.press('Escape');
});

test('quick add in a wide scope files into the picked list', async ({ page }) => {
  await signedIn(page, '/#/everything');
  await page.getByLabel('Add to list').selectOption({ label: 'Tasks' });
  await page.getByLabel('Quick add').fill('Filed from everything due:tomorrow');
  await page.getByLabel('Quick add').press('Enter');
  await expect(column(page, addDays(today(), 1))).toContainText('Filed from everything');
  const { alex, tasksId } = seed();
  const found = await api<unknown[]>('GET', `/items?list_id=${tasksId}&q=Filed`, alex);
  expect(found).toHaveLength(1);
  // The pick is remembered for this view.
  await page.reload();
  await expect(page.getByLabel('Add to list')).toHaveValue(tasksId);
});
