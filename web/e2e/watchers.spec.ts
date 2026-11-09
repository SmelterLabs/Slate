// Watchers (ClickUp-style): the eye in the task window shows who watches; its popover watches
// or unwatches you, adds a person or agent and removes one. Creating a task auto-watches it.
// The same popover on a phone.
import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const handles = async (id: string, token: string) =>
  (await api<{ handle: string }[]>('GET', `/items/${id}/watchers`, token)).map((w) => w.handle).sort();

async function watchersFlow(page: Page, title: string, shot: (name: string) => string) {
  const { alex, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: listId, title });
  expect(await handles(item.id, alex)).toEqual(['alex']); // the creator auto-watches

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${listId}?task=${item.id}`);
  const modal = page.getByRole('dialog', { name: title });
  await expect(modal).toBeVisible();
  const eye = modal.locator('[data-watchers]');
  await eye.scrollIntoViewIfNeeded();
  await expect(eye).toHaveAttribute('aria-label', 'Watchers: 1, including you');

  await eye.click();
  const pop = modal.getByRole('group', { name: 'Watchers' });
  await pop.getByRole('textbox', { name: 'Add a watcher' }).fill('agent');
  await pop.getByRole('button', { name: /@agent(?![\w-])/ }).click();
  await expect(pop.getByRole('list', { name: 'Watching' })).toContainText('Agent');
  await expect(eye).toHaveAttribute('aria-label', 'Watchers: 2, including you');
  await page.screenshot({ path: shot('popover') });

  await pop.getByRole('button', { name: 'Stop watching' }).click();
  await expect(eye).toHaveAttribute('aria-label', 'Watchers: 1');
  expect(await handles(item.id, alex)).toEqual(['agent']);
  await pop.getByRole('button', { name: 'Remove Agent as a watcher' }).click();
  await expect(pop).toContainText('Nobody is watching yet.');
  await pop.locator('[data-watch-me]').click();
  await expect(eye).toHaveAttribute('aria-label', 'Watchers: 1, including you');
  expect(await handles(item.id, alex)).toEqual(['alex']);
}

test('add and remove watchers from the task window', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await watchersFlow(page, 'Patch the fence', (n) => info.outputPath(`pc-${n}.png`));
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  test('the same watchers popover on a phone', async ({ page }, info) => {
    await watchersFlow(page, 'Paint the shed', (n) => info.outputPath(`phone-${n}.png`));
  });
});

test('notifications settings turn auto-watch off per kind', async ({ page }) => {
  const { alex } = seed();
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto('/#/home');
  await page.getByRole('button', { name: /Notifications/ }).first().click();
  const block = page.getByRole('region', { name: 'Watch tasks automatically' });
  await expect(block.getByLabel('Tasks I create')).toBeChecked();
  await block.getByLabel('Tasks I comment on').uncheck();
  await expect
    .poll(async () => (await api<{ prefs: { auto_watch?: Record<string, boolean> } }>('GET', '/me', alex)).prefs.auto_watch)
    .toEqual({ created: true, assigned: true, commented: false });
  await block.getByLabel('Tasks I comment on').check();
});
