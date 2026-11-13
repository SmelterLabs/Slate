// The top-right bell on a desktop: mentions and messages waiting arrive live, its menu opens the
// card, and Notifications shows the "Tell me when" grid (rows of reasons against toast/push).
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('the bell counts mentions and messages live and opens the card', async ({ page }) => {
  const { agent } = seed();
  await api('POST', '/inbox/clear', { all: true });
  await api('POST', '/channels/read-all').catch(() => {});
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Bell', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Bell list' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Pick the paint' });
  await api('POST', `/items/${item.id}/watchers`, {}).catch(() => {});

  await page.setViewportSize({ width: 1400, height: 900 });
  await signIn(page);
  await page.goto(`/#/home`);
  const bell = page.locator('.favbar-end .waiting-bell');
  await expect(bell).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, { body_md: '@alex which colour?' }, agent);
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
  await page.setViewportSize({ width: 1400, height: 900 });
  await signIn(page);
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
  // The box flips before the save lands: wait for the server to have it.
  await expect
    .poll(async () => {
      const me = await api<{ prefs: { push_reasons?: string[] } }>('GET', '/me');
      return (me.prefs.push_reasons ?? []).includes('status');
    })
    .toBe(!before);
  await row.click(); // put it back
});
