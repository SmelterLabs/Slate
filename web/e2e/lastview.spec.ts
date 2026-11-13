import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { api, seed, signedIn } from './support';

// Coming back to a list shows it the way you left it: the last view tab, and its unsaved
// layout, grouping and filters. They live on the account, so a reload and another device agree.

const shots = process.env.SLATE_LASTVIEW_SHOTS ?? null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: join(shots, `${name}.png`) });
}

const forget = () => api('PATCH', '/me/prefs', { last_view: null, view_drafts: null });

test.use({ viewport: { width: 1440, height: 900 } });
test.beforeEach(forget);
test.afterEach(forget);

test('layout, grouping and filters stay after leaving the list and after a reload', async ({ page, browser }) => {
  const list = `#/list/${seed().tasksId}`;
  await signedIn(page, `/${list}`);
  await expect(page.locator('.column').first()).toBeVisible();

  await page.getByRole('button', { name: 'Table', exact: true }).click();
  await page.getByRole('button', { name: 'Group by' }).click();
  await page.getByRole('menuitemradio', { name: 'Priority' }).click();
  await page.getByRole('button', { name: 'Filter', exact: true }).click();
  const panel = page.locator('.dd-panel');
  await panel.getByRole('button', { name: '+ Rule' }).click();
  await panel.getByRole('group', { name: 'Tag' }).getByLabel('#errand').check();
  await page.keyboard.press('Escape');
  await expect(page.locator('table.grid-table')).toBeVisible();
  await expect(page.locator('.chips')).toContainText('Tag has any of #errand');

  // Somewhere else, then back by the sidebar link (no view in the URL).
  await page.goto(`/#/everything`);
  await expect(page.locator('.column').first()).toBeVisible();
  await page.goto(`/${list}`);
  await expect(page.locator('table.grid-table')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Group by' })).toHaveText(/Priority/);
  await expect(page.locator('.chips')).toContainText('Tag has any of #errand');
  await expect(page.locator('.view-tab.on .dirty')).toBeVisible();

  // Saved shortly after the change; then a reload, and a second device, show the same.
  await expect
    .poll(async () => JSON.stringify((await api<{ prefs: object }>('GET', '/me')).prefs))
    .toContain('errand');
  await page.reload();
  await expect(page.locator('table.grid-table')).toBeVisible();
  await expect(page.locator('.chips')).toContainText('Tag has any of #errand');
  await shot(page, 'desktop-after-reload');

  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await signedIn(phone, `/${list}`);
  await expect(phone.locator('table.grid-table')).toBeVisible();
  await expect(phone.locator('.chips')).toContainText('Tag has any of #errand');
  await shot(phone, 'phone-same-view');

  // Revert here puts the other device back too, live.
  await page.getByRole('button', { name: 'Revert' }).click();
  await expect(page.locator('.column').first()).toBeVisible();
  await expect(phone.locator('.column').first()).toBeVisible();
  await phone.close();
  await page.reload();
  await expect(page.locator('.column').first()).toBeVisible();
  await expect(page.locator('.view-tab.on .dirty')).toHaveCount(0);
});

test('the view tab you last opened on a list is the one you come back to', async ({ page }) => {
  const id = seed().tasksId;
  const made: string[] = [];
  for (const [name, layout] of [
    ['Tasks board', 'board'],
    ['Tasks table', 'table'],
  ]) {
    const v = await api<{ id: string }>('POST', '/views', {
      name,
      scope_kind: 'list',
      scope_id: id,
      config: { layout, group_by: 'status' },
    });
    made.push(v.id);
  }
  try {
    await signedIn(page, `/#/list/${id}`);
    await expect(page.getByRole('tab', { name: /Tasks board/ })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('tab', { name: /Tasks table/ }).getByRole('link').click();
    await expect(page.locator('table.grid-table')).toBeVisible();

    await page.goto('/#/everything');
    await expect(page.locator('.column').first()).toBeVisible();
    await page.goto(`/#/list/${id}`);
    await expect(page.getByRole('tab', { name: /Tasks table/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('table.grid-table')).toBeVisible();
    await page.reload();
    await expect(page.getByRole('tab', { name: /Tasks table/ })).toHaveAttribute('aria-selected', 'true');
    await shot(page, 'desktop-last-tab');
  } finally {
    for (const v of made) await api('POST', `/views/${v}/archive`);
  }
});
