// Isolated fixture metadata: exercise changing workspace configuration without restarting a server.
import { expect, test } from '@playwright/test';
import { api, signIn } from './support';

test('workspace metadata changes home, cards and new rules; stored rule zones survive editing', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const section = await api('POST', '/sections', { name: 'Timezone fixture', audience: 'personal' });
  const list = await api('POST', '/lists', { section_id: section.id, name: 'Timezone fixture' });
  const item = await api('POST', '/items', {
    list_id: list.id, title: 'Timezone boundary', due_at: '2030-01-01T23:30:00Z',
  });
  await page.clock.setFixedTime(new Date('2030-01-01T23:30:00Z'));
  await signIn(page);
  let zone = 'Asia/Tokyo';
  await page.route('**/api/v1/me', async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), timezone: zone } });
  });
  await page.goto('/#/home');
  await expect(page.locator('.work-head h1')).toContainText('Good morning');
  await expect(page.locator('.work-head')).toContainText('Wednesday, January 2');
  await page.screenshot({ path: info.outputPath('tokyo-home.png') });
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Timezone boundary' });
  await expect(card).toContainText('8:30 am');
  await card.getByText('Timezone boundary', { exact: true }).click();
  const task = page.locator('.tw');
  await task.getByRole('button', { name: '↻ Make recurring' }).click();
  const editor = task.getByRole('group', { name: 'Recurrence' });
  await expect(editor.locator('[data-rule-preview]')).toBeVisible();
  await editor.getByRole('button', { name: 'Make recurring' }).click();
  await expect(task.locator('[data-rule-line]')).toBeVisible();
  const made = await api('GET', `/items/${item.id}`);
  const rule = await api('GET', `/recurrences/${made.recurrence_id}`);
  expect(rule.timezone).toBe('Asia/Tokyo');

  // Simulate a new server configuration on reload; stored rule remains Tokyo.
  zone = 'America/Chicago';
  await page.goto('/#/home');
  await page.reload();
  await expect(page.locator('.work-head h1')).toContainText('Good evening');
  await expect(page.locator('.work-head')).toContainText('Tuesday, January 1');
  await page.goto(`/#/list/${list.id}`);
  await expect(card).toContainText('5:30 pm');
  await card.getByText('Timezone boundary', { exact: true }).click();
  const previewRequest = page.waitForRequest((request) => request.url().includes('/recurrences/preview'));
  await task.locator('[data-rule-line]').locator('..').getByRole('button', { name: 'Edit' }).click();
  const request = await previewRequest;
  expect(new URL(request.url()).searchParams.get('timezone')).toBe('Asia/Tokyo');
  await expect(editor.locator('[data-rule-preview]')).toBeVisible();
  await editor.getByLabel('Title for each one').fill('Timezone rule edited');
  await editor.getByRole('button', { name: 'Save rule' }).click();
  await expect(editor).not.toBeVisible();
  const after = await api('GET', `/recurrences/${rule.id}`);
  expect(after.title_template).toBe('Timezone rule edited');
  expect(after.timezone).toBe(rule.timezone);
  expect(after.dtstart).toBe(rule.dtstart);
  expect(errors).toEqual([]);
});
