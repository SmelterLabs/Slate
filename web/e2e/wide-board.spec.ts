import { expect, test, type Page } from '@playwright/test';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/when';
import { BASE_URL, seed } from './seed';

// The ClickUp-style board: folded empty days, + Add Task per column, the Done column, clickable
// pills and the Customize panel. Runs last, in its own section so earlier specs are untouched.

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

test.use({ viewport: { width: 1920, height: 1080 } });

const today = () => workspaceDay(new Date());
const column = (page: Page, key: string) => page.locator(`.column[data-group="${key}"]`);
const card = (page: Page, title: string) => page.locator('.card', { hasText: title });

let sectionId = '';
let listId = '';

test.beforeAll(async () => {
  sectionId = (await api<{ id: string }>('POST', '/sections', { name: `Board lab ${Date.now()}`, audience: 'personal' })).id;
  listId = (await api<{ id: string }>('POST', '/lists', { section_id: sectionId, name: 'Lab' })).id;
  await api('POST', '/items', { list_id: listId, title: 'Lab today', due_at: `${today()}T23:00:00Z`, tags: ['lab'] });
  await api('POST', '/items', { list_id: listId, title: 'Lab undated', body_md: 'has notes', priority: 'high' });
});

async function labBoard(page: Page) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/section/${sectionId}`);
  await expect(column(page, 'today')).toBeVisible();
}

test('empty day columns fold to a strip; opening one sticks across a reload', async ({ page }) => {
  await labBoard(page);
  const day = addDays(today(), 2);
  await expect(column(page, day)).toHaveClass(/collapsed/);
  await expect(column(page, 'today')).not.toHaveClass(/collapsed/);
  await column(page, day).locator('.column-strip').click();
  await expect(column(page, day)).not.toHaveClass(/collapsed/);
  await page.reload();
  await expect(column(page, day)).not.toHaveClass(/collapsed/);
  await column(page, day).getByRole('button', { name: 'Column options' }).click();
  await page.getByRole('button', { name: 'Collapse column' }).click();
  await expect(column(page, day)).toHaveClass(/collapsed/);
});

test('folded columns and the theme follow the account to another device', async ({ page, browser }) => {
  await labBoard(page);
  const day = addDays(today(), 3);
  await column(page, day).locator('.column-strip').click();
  await expect(column(page, day)).not.toHaveClass(/collapsed/);
  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByLabel('Customize view').getByRole('button', { name: 'Dark', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  // A second device: its own, empty storage, signed in with the same account.
  const other = await browser.newContext();
  const phone = await other.newPage();
  await labBoard(phone);
  await expect(column(phone, day)).not.toHaveClass(/collapsed/);
  await expect(phone.locator('html')).toHaveAttribute('data-theme', 'dark');

  // And live: folding it back on the first device folds it on the second.
  await column(page, day).getByRole('button', { name: 'Column options' }).click();
  await page.getByRole('button', { name: 'Collapse column' }).click();
  await expect(column(phone, day)).toHaveClass(/collapsed/);
  await page.getByRole('button', { name: 'Customize' }).click();
  await page.getByLabel('Customize view').getByRole('button', { name: 'Auto', exact: true }).click();
  await expect(phone.locator('html')).not.toHaveAttribute('data-theme', /.+/);
  await other.close();
});

test('+ Add Task in a day column files a task due that day, with quick-add syntax', async ({ page }) => {
  await labBoard(page);
  const tomorrow = addDays(today(), 1);
  await column(page, 'today').getByRole('button', { name: 'Add Task', exact: true }).click();
  const box = column(page, 'today').getByLabel('New task in Today');
  await box.fill('Typed in Today #lab !urgent');
  await box.press('Enter');
  await expect(column(page, 'today')).toContainText('Typed in Today');
  const found = await api<{ due_at: string; priority: string; tags: string[]; list_id: string }[]>(
    'GET',
    `/items?list_id=${listId}&q=Typed`,
  );
  expect(found).toHaveLength(1);
  expect(workspaceDay(new Date(found[0].due_at))).toBe(today());
  expect(found[0]).toMatchObject({ priority: 'urgent', tags: ['lab'], list_id: listId });
  await box.press('Escape');

  // A folded column opens for typing from its own + button once shown.
  await column(page, tomorrow).locator('.column-strip').click();
  await column(page, tomorrow).getByRole('button', { name: 'Add task to Tomorrow' }).click();
  await column(page, tomorrow).getByLabel('New task in Tomorrow').fill('Typed in the folded column');
  await column(page, tomorrow).getByLabel('New task in Tomorrow').press('Enter');
  await expect(column(page, tomorrow)).toContainText('Typed in the folded column');
});

test('the due pill reschedules in one click; dropping on Done completes', async ({ page }) => {
  await labBoard(page);
  const tomorrow = addDays(today(), 1);
  await card(page, 'Lab today').hover();
  await card(page, 'Lab today').getByRole('button', { name: 'Due date' }).click();
  await page.getByRole('button', { name: 'Tomorrow', exact: true }).click();
  await expect(column(page, tomorrow)).toContainText('Lab today');

  await card(page, 'Lab undated').dragTo(column(page, 'done'));
  await expect(column(page, 'done')).toContainText('Lab undated');
  await expect
    .poll(async () => (await api<{ status: { kind: string } }[]>('GET', `/items?list_id=${listId}&q=undated`))[0].status.kind)
    .toBe('done');
});

test('Customize toggles card fields and title lines for the view', async ({ page }) => {
  await labBoard(page);
  await expect(card(page, 'Lab today').locator('.tag-pill')).toHaveText(['lab']);
  await page.getByRole('button', { name: 'Customize' }).click();
  const panel = page.getByLabel('Customize view');
  await panel.getByRole('switch', { name: 'Tags' }).uncheck();
  await expect(card(page, 'Lab today').locator('.tag-pill')).toHaveCount(0);
  await panel.getByRole('button', { name: 'Full' }).click();
  await expect(card(page, 'Lab today').locator('.card-title')).not.toHaveClass(/clamped/);
  // Unsaved changes, as with any toolbar edit.
  await expect(page.locator('.view-tab.on .dirty')).toBeVisible();
});

test('right-click opens the card menu; priority from there', async ({ page }) => {
  await labBoard(page);
  await card(page, 'Lab today').click({ button: 'right' });
  await page.getByRole('button', { name: 'Priority low' }).click();
  await expect(card(page, 'Lab today').locator('.prio')).toHaveText('Low');
});
