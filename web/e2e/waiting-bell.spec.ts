// The top-right bell on a desktop: mentions and messages waiting arrive live, its menu opens the
// card, and Notifications shows the "Tell me when" grid (rows of reasons against toast/push).
import { expect, test } from '@playwright/test';
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

test('the bell counts mentions and messages live and opens the card', async ({ page }) => {
  const { alex, agent } = seed();
  await api('POST', '/inbox/clear', alex, { all: true });
  await api('POST', '/channels/read-all', alex).catch(() => {});
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Bell', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Bell list' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Pick the paint' });
  await api('POST', `/items/${item.id}/watchers`, alex, {}).catch(() => {});

  await page.setViewportSize({ width: 1400, height: 900 });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/home`);
  const bell = page.locator('.favbar-end .waiting-bell');
  await expect(bell).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, agent, { body_md: '@alex which colour?' });
  await expect(bell.locator('[data-waiting-tags="1"]')).toBeVisible();

  await bell.click();
  const menu = page.locator('.wb-menu');
  await expect(menu.getByText('Pick the paint')).toBeVisible();
  await page.waitForTimeout(400); // the pop-in animation
  await page.screenshot({ path: 'test-results/waiting-bell.png' });
  await menu.getByText('Pick the paint').click();
  await expect(page.locator('.tw')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(bell.locator('[data-waiting-tags]')).toHaveCount(0);
});

test('Notifications shows the Tell me when grid and saves a row', async ({ page }) => {
  const { alex } = seed();
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/home`);
  await page.locator('.favbar-end .waiting-bell').click();
  await page.getByRole('button', { name: 'Notification settings' }).click();
  const grid = page.locator('.ns-grid');
  await expect(grid).toBeVisible();
  const row = grid.locator('tr[data-group="watching"] input[type="checkbox"]').first();
  const before = await row.isChecked();
  await row.click();
  await expect(row).toBeChecked({ checked: !before });
  await page.screenshot({ path: 'test-results/notify-grid.png' });
  const me = await api<{ prefs: { push_reasons?: string[] } }>('GET', '/me', alex);
  expect((me.prefs.push_reasons ?? []).includes('status')).toBe(!before);
  await row.click(); // put it back
});
