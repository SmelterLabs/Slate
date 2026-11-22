// Parked: parking a card in the task window removes it from My Work and shows
// "Waiting on <note> until <date>" in its list.
import { expect } from '@playwright/test';
import { addDays } from '../src/lib/when';
import { today } from './seed';
import { api, calendarTest as test, seed, signIn } from './support';

test('parked: parking a card removes it from My Work and shows the line in its list', async ({ page }) => {
  const { listId } = seed();
  const created = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Disputed charge',
    assignees: ['alex'],
    due_at: today(),
  });

  await signIn(page);
  await page.goto('/#/home');
  await expect(page.locator(`[data-item="${created.id}"]`)).toBeVisible();

  // Open the card and park it waiting on an outside party until a future date.
  await page.locator(`[data-item="${created.id}"]`).click();
  const future = addDays(today(), 10);
  await page.getByLabel('Waiting on note').fill('Bank dispute desk');
  await page.getByLabel('Waiting until date').fill(future);
  await page.locator('[data-park-save]').click();
  await page.keyboard.press('Escape');

  // Removed from My Work.
  await expect(page.locator(`[data-item="${created.id}"]`)).toHaveCount(0);

  // Shows "Waiting on <note> until <date>" in its list.
  await page.goto(`/#/list/${listId}`);
  const card = page.locator(`[data-item="${created.id}"]`);
  await expect(card.locator('[data-parked]')).toContainText('Waiting on Bank dispute desk until');
});

test('Run now unparks a card and makes it available for pickup', async ({ page }) => {
  const { listId, agent } = seed();
  const { id } = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Review the draft',
    assignees: ['agent'],
  });
  const future = addDays(today(), 10);
  await api('PATCH', `/items/${id}`, { parked_until: future, parked_note: 'Review window' });
  const take = () => api<{ item: { id: string } | null }>('POST', '/work/next', { item_id: id }, agent);
  expect((await take()).item).toBeNull();

  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const card = page.locator(`[data-item="${id}"]`);
  await expect(card.locator('[data-parked]')).toBeVisible();
  await card.click();
  const dialog = page.getByRole('dialog', { name: 'Task: Review the draft' });
  const run = dialog.getByRole('button', { name: 'Run now', exact: true });
  await run.click();
  await page.keyboard.press('Escape');
  await expect(card.locator('[data-parked]')).toHaveCount(0);
  expect((await take()).item?.id).toBe(id);
});
