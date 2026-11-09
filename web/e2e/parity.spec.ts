import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays, dayOf, weekday } from '../src/lib/views';
import { BASE_URL, seed } from './seed';

// ClickUp parity: Table and Calendar layouts, estimates, dependencies and the tag manager.
// Builds its own section and list so it does not disturb the other specs' data.

const shots = process.env.SLATE_PARITY_SHOTS ?? null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: join(shots, `${name}.png`), fullPage: false });
}

async function api<T>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status} ${res.ok ? '' : await res.text()}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

interface Built {
  listId: string;
  ids: Record<string, string>;
}

let built: Built | null = null;
async function setup(): Promise<Built> {
  if (built) return built;
  // A failed test restarts the worker, losing `built`; the server's data survives, so find it.
  const lists = await api<{ id: string; name: string }[]>('GET', '/lists');
  const existing = lists.find((l) => l.name === 'Projects');
  if (existing) {
    const items = await api<{ id: string; title: string }[]>('GET', `/items?list_id=${existing.id}`);
    const ids: Record<string, string> = {};
    for (const i of items) ids[i.title] = i.id;
    built = { listId: existing.id, ids };
    return built;
  }
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Parity', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Projects' });
  const today = workspaceDay(new Date());
  const ids: Record<string, string> = {};
  for (const [title, extra] of [
    ['Plan the deck', { due_at: today, tags: ['parity-a'], estimate_minutes: 60 }],
    ['Build the deck', { due_at: addDays(today, 2), tags: ['parity-b'] }],
    ['Paint the shed', {}],
  ] as const) {
    ids[title] = (await api<{ id: string }>('POST', '/items', { list_id: list.id, title, ...extra })).id;
  }
  built = { listId: list.id, ids };
  return built;
}

async function openList(page: Page, listId: string) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/list/${listId}`);
  await expect(page.locator('.list-page')).toBeVisible();
}

test.use({ viewport: { width: 1600, height: 1000 } });

test('Table: edit an estimate and the title in place; totals add up', async ({ page }) => {
  const b = await setup();
  await openList(page, b.listId);
  await page.getByRole('button', { name: 'Table', exact: true }).click();
  const row = page.locator('tr.t-row', { hasText: 'Build the deck' });
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByLabel('Edit time estimate').click();
  await row.getByLabel('Time estimate').fill('1h 30m');
  await row.getByLabel('Time estimate').press('Enter');
  await expect
    .poll(async () => (await api<{ estimate_minutes: number }>('GET', `/items/${b.ids['Build the deck']}`)).estimate_minutes)
    .toBe(90);

  // The name opens the task window, as on the board; the pencil renames in place.
  await row.getByRole('button', { name: 'Build the deck', exact: true }).click();
  await expect(page.locator('.tw')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.tw')).toHaveCount(0);
  await row.hover();
  await row.getByRole('button', { name: 'Rename Build the deck' }).click();
  // While editing, the title is an input's value, not text, so find the row by id.
  const editing = page.locator(`tr.t-row[data-item="${b.ids['Build the deck']}"]`);
  await editing.getByLabel('Title').fill('Build the big deck');
  await editing.getByLabel('Title').press('Enter');
  await expect(page.locator('tr.t-row', { hasText: 'Build the big deck' })).toBeVisible();

  const totals = page.locator('tr.t-total').first();
  await expect(totals.locator('td[data-col="estimate"]')).toHaveText('2h 30m');

  // An agent's write lands in the table live.
  await api('PATCH', `/items/${b.ids['Paint the shed']}`, { estimate_minutes: 45 }, seed().agent);
  await expect(page.locator('tr.t-row', { hasText: 'Paint the shed' }).locator('td[data-col="estimate"]')).toHaveText('45m');
  await expect(totals.locator('td[data-col="estimate"]')).toHaveText('3h 15m');
  await shot(page, 'table');
});

test('Calendar: items sit on their due day; drag reschedules, Unscheduled clears', async ({ page }) => {
  const b = await setup();
  await openList(page, b.listId);
  await page.getByRole('button', { name: 'Calendar', exact: true }).click();
  const today = workspaceDay(new Date());
  const todayCell = page.locator(`.cal-day[data-day="${today}"]`);
  await expect(todayCell.locator('.cal-chip', { hasText: 'Plan the deck' })).toBeVisible();
  await expect(page.locator('.cal-side .cal-chip', { hasText: 'Paint the shed' })).toBeVisible();
  await shot(page, 'calendar');

  // Into the week view; the other day is tomorrow, or yesterday on a Saturday (weeks start on
  // Sunday, so Saturday's tomorrow is on the next page).
  await page.getByRole('button', { name: 'Week', exact: true }).click();
  const tomorrow = addDays(today, weekday(today) === 6 ? -1 : 1);
  await todayCell.locator('.cal-chip', { hasText: 'Plan the deck' }).dragTo(page.locator(`.cal-day[data-day="${tomorrow}"]`));
  await expect
    .poll(async () => dayOf((await api<{ due_at: string }>('GET', `/items/${b.ids['Plan the deck']}`)).due_at))
    .toBe(tomorrow);

  await page.locator('.cal-side .cal-chip', { hasText: 'Paint the shed' }).dragTo(page.locator(`.cal-day[data-day="${tomorrow}"]`));
  await expect
    .poll(async () => (await api<{ due_at: string | null }>('GET', `/items/${b.ids['Paint the shed']}`)).due_at)
    .not.toBeNull();
  await page.locator(`.cal-day[data-day="${tomorrow}"] .cal-chip`, { hasText: 'Paint the shed' }).dragTo(page.locator('.cal-side'));
  await expect
    .poll(async () => (await api<{ due_at: string | null }>('GET', `/items/${b.ids['Paint the shed']}`)).due_at)
    .toBeNull();
  await shot(page, 'calendar-week');
});

test('Item panels: estimate and a dependency', async ({ page }) => {
  const b = await setup();
  await openList(page, b.listId);
  await page.getByRole('button', { name: 'List', exact: true }).click();
  await page.locator('.row', { hasText: 'Paint the shed' }).getByText('Paint the shed').click();
  const panel = page.locator('.estimate-panel');
  await panel.getByLabel('Edit time estimate').click();
  await panel.getByLabel('Time estimate').fill('2h');
  await panel.getByLabel('Time estimate').press('Enter');
  await expect
    .poll(async () => (await api<{ estimate_minutes: number }>('GET', `/items/${b.ids['Paint the shed']}`)).estimate_minutes)
    .toBe(120);

  await page.getByRole('button', { name: '+ Add dependency' }).click();
  await page.getByLabel('Find item').fill('Plan the');
  await page.getByRole('listbox', { name: 'Items' }).getByRole('button', { name: /Plan the deck/ }).click();
  await expect(page.locator('.deps .blocked-badge')).toBeVisible();
  const links = await api<{ blocked_by: string[]; blocked: boolean }>('GET', `/items/${b.ids['Paint the shed']}/links`);
  expect(links.blocked_by).toEqual([b.ids['Plan the deck']]);
  await shot(page, 'item-panels');
});

test('Tag manager: recolour, merge and delete', async ({ page }) => {
  const b = await setup();
  await openList(page, b.listId);
  await page.locator('.nav-foot').getByRole('button', { name: 'Tags' }).click();
  const dialog = page.getByRole('dialog', { name: 'Tags' });
  const rowA = dialog.locator('.tag-row[data-tag="parity-a"]');
  await expect(rowA).toContainText('1 item');
  await rowA.getByRole('button', { name: 'Merge' }).click();
  await rowA.getByLabel('Merge into').selectOption('parity-b');
  await rowA.getByRole('button', { name: 'Merge', exact: true }).last().click();
  await expect(dialog.locator('.tag-row[data-tag="parity-a"]')).toHaveCount(0);
  await expect(dialog.locator('.tag-row[data-tag="parity-b"]')).toContainText('2 items');
  const plan = await api<{ tags: string[] }>('GET', `/items/${b.ids['Plan the deck']}`);
  expect(plan.tags).toEqual(['parity-b']);
  await shot(page, 'tag-manager');

  const rowB = dialog.locator('.tag-row[data-tag="parity-b"]');
  await rowB.getByLabel('Colour of parity-b').click();
  await rowB.getByLabel('Colour #a78bfa').click();
  await expect.poll(async () => (await api<{ name: string; color: string }[]>('GET', '/tags')).find((t) => t.name === 'parity-b')?.color).toBe('#a78bfa');
  await rowB.getByRole('button', { name: 'Delete' }).click();
  await rowB.getByRole('button', { name: 'Delete' }).last().click();
  await expect(dialog.locator('.tag-row[data-tag="parity-b"]')).toHaveCount(0);
});
