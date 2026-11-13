// Productivity: the command palette, natural quick add, My Work, the inbox (live from an
// agent's comment), undo, j/k and g-chords.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api, seed, signedIn } from './support';

const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });

interface Item {
  id: string;
  title: string;
  due_at: string | null;
  priority: string | null;
  tags: string[];
  list_id: string;
  archived_at: string | null;
}

async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} section`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  return list.id;
}

const palette = (page: Page) => page.getByRole('dialog', { name: 'Search and commands' });

test('Ctrl+K finds a task by fuzzy title and opens it', async ({ page }) => {
  const listId = await scratch('Palette');
  await api('POST', '/items', { list_id: listId, title: 'Renew the insurance card' });
  await signedIn(page, '/', { ready: true });
  await page.keyboard.press('Control+k');
  await expect(palette(page)).toBeVisible();
  await page.keyboard.type('insurance crd');
  const row = palette(page).getByRole('option').first();
  await expect(row).toContainText('Renew the insurance card');
  if (shots) await page.screenshot({ path: join(shots, 'palette.png') });
  await page.keyboard.press('Enter');
  await expect(palette(page)).toBeHidden();
  await expect(page.getByRole('dialog', { name: /Renew the insurance card/ })).toBeVisible();
});

test('Ctrl+K also searches task descriptions and chat on the server, grouped under the local matches', async ({ page }) => {
  const listId = await scratch('Deep search');
  await api('POST', '/items', { list_id: listId, title: 'Replace the bulb', body_md: 'The lighthouse keeper has spares' });
  await api('POST', `/channels/list:${listId}/messages`, { body_md: 'Ask the lighthouse keeper first' });
  await signedIn(page, '/', { ready: true });
  await page.keyboard.press('Control+k');
  await page.keyboard.type('lighthouse keeper');
  const dialog = palette(page);
  await expect(dialog.locator('[data-search-group="task"]')).toHaveText('Tasks');
  await expect(dialog.locator('[data-search-group="message"]')).toHaveText('Chat');
  await expect(dialog.getByRole('option', { name: /Replace the bulb/ })).toBeVisible();
  await dialog.getByRole('option', { name: /Ask the lighthouse keeper first/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/chat/list:${listId}`));
  await expect(page.getByText('Ask the lighthouse keeper first')).toBeVisible();
});

test('palette commands: > narrows to commands and g-chords navigate', async ({ page }) => {
  await signedIn(page, '/', { ready: true });
  await page.keyboard.press('Control+k');
  await page.keyboard.type('>inbox');
  await expect(palette(page).getByRole('option').first()).toContainText('Go to Inbox');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/inbox/);
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  await page.keyboard.press('g');
  await page.keyboard.press('h');
  await expect(page).toHaveURL(/#\/home/);
  await expect(page.getByRole('region', { name: 'My Work' })).toBeVisible();
});

test('quick add reads a natural date, time, priority and tag', async ({ page }) => {
  const listId = await scratch('Natural');
  await signedIn(page, `/#/list/${listId}`, { ready: true });
  const input = page.getByLabel('Quick add');
  await input.fill('Call the bank tomorrow 4pm #personal !high');
  await expect(page.locator('.qa-preview')).toContainText('due');
  await expect(page.locator('.qa-preview')).toContainText('4pm');
  await input.press('Enter');
  await expect.poll(async () => (await api<Item[]>('GET', `/items?list_id=${listId}`)).length).toBe(1);
  const [item] = await api<Item[]>('GET', `/items?list_id=${listId}`);
  expect(item.title).toBe('Call the bank');
  expect(item.priority).toBe('high');
  expect(item.tags).toEqual(['personal']);
  const due = new Date(item.due_at!);
  const hm = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'America/Chicago',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(due);
  expect(hm).toBe('16:00');
});

test('quick add "every monday" makes a recurring rule', async ({ page }) => {
  const listId = await scratch('Repeats');
  await signedIn(page, `/#/list/${listId}`, { ready: true });
  await page.getByLabel('Quick add').fill('Take out recycling every monday 7am');
  await expect(page.locator('.qa-preview')).toContainText('every week on Monday');
  await page.getByLabel('Quick add').press('Enter');
  await expect(page.getByRole('alert')).toContainText('repeats every week on Monday');
  const rules = await api<{ list_id: string; title_template: string; rrule: string }[]>('GET', '/recurrences');
  const rule = rules.find((r) => r.list_id === listId)!;
  expect(rule.title_template).toBe('Take out recycling');
  expect(rule.rrule).toBe('FREQ=WEEKLY;BYDAY=MO');
});

test('an agent mention lands in the inbox live; opening it marks it read', async ({ page }) => {
  const listId = await scratch('Inbox');
  const item = await api<Item>('POST', '/items', { list_id: listId, title: 'Check the gutter quote' });
  await signedIn(page, '/#/inbox', { ready: true });
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Quote is in, @alex' }, seed().agent);
  const row = page.locator(`.inbox-row[data-item="${item.id}"]`);
  await expect(row).toBeVisible({ timeout: 3000 });
  await expect(row).toHaveClass(/unread/);
  await expect(row).toContainText('commented');
  await expect(page.getByRole('link', { name: /^Inbox \d+ unread/ })).toBeVisible();
  if (shots) await page.screenshot({ path: join(shots, 'inbox.png') });
  await row.locator('.inbox-main').click();
  await expect(page.getByRole('dialog', { name: /Check the gutter quote/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(row).not.toHaveClass(/unread/);
  await row.getByRole('button', { name: 'Clear' }).click();
  await expect(row).toBeHidden();
});

test('My Work shows my tasks by due date', async ({ page }) => {
  const listId = await scratch('Mine');
  await api('POST', '/items', { list_id: listId, title: 'Mine today', assignees: ['alex'], due_at: new Date(Date.now() + 3600_000).toISOString() });
  await api('POST', '/items', { list_id: listId, title: 'Mine late', assignees: ['alex'], due_at: '2026-01-02' });
  await api('POST', '/items', { list_id: listId, title: 'Not mine', assignees: ['agent'] });
  await signedIn(page, '/#/home', { ready: true });
  const region = page.getByRole('region', { name: 'My Work' });
  await expect(region.locator('[data-bucket="overdue"]')).toContainText('Mine late');
  await expect(region.locator('.work-main')).not.toContainText('Not mine');
  await expect(region.locator('.work-side')).toContainText('Not mine'); // delegated
  if (shots) await page.screenshot({ path: join(shots, 'my-work.png') });
  // j moves the cursor, e completes it, Ctrl+Z undoes that.
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  await page.keyboard.press('j');
  const focused = page.locator('[data-item].kb-focus');
  await expect(focused).toHaveCount(1);
  const id = await focused.getAttribute('data-item');
  // Undo is remembered when the board gets the PATCH answer, which can come after the server
  // has already saved: wait for the answer, not just the saved state, before Ctrl+Z.
  const saved = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().endsWith(`/items/${id}`));
  await page.keyboard.press('e');
  await saved;
  await expect.poll(async () => (await api<{ status: { kind: string } }>('GET', `/items/${id}`)).status.kind).toBe('done');
  await page.keyboard.press('Control+z');
  await expect.poll(async () => (await api<{ status: { kind: string } }>('GET', `/items/${id}`)).status.kind).not.toBe('done');
});

test('put away from the task window can be undone from the toast', async ({ page }) => {
  const listId = await scratch('Undo');
  const item = await api<Item>('POST', '/items', { list_id: listId, title: 'Keep me' });
  await signedIn(page, `/#/list/${listId}?task=${item.id}`, { ready: true });
  const modal = page.getByRole('dialog', { name: /Keep me/ });
  await expect(modal).toBeVisible();
  await modal.getByRole('button', { name: 'Task actions' }).click();
  await page.getByRole('menuitem', { name: 'Put away' }).click();
  const toast = page.getByRole('alert');
  await expect(toast).toContainText('Put away “Keep me”');
  await toast.getByRole('button', { name: 'Undo' }).click();
  await expect.poll(async () => (await api<Item>('GET', `/items/${item.id}`)).archived_at).toBeNull();
});

test('phone: My Work, the inbox bell and search are in the top bar', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signedIn(page, '/#/home', { ready: true });
  await expect(page.getByRole('region', { name: 'My Work' })).toBeVisible();
  await expect(page.locator('.topbar .where')).toHaveText('My Work');
  if (shots) await page.screenshot({ path: join(shots, 'mobile-my-work.png') });
  await page.locator('.topbar').getByRole('button', { name: 'Search' }).click();
  await expect(palette(page)).toBeVisible();
  if (shots) await page.screenshot({ path: join(shots, 'mobile-palette.png') });
  await page.keyboard.press('Escape');
  await page.locator('.topbar .top-inbox').click();
  await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();
});
