// Parked: parking a card in the task window removes it from My Work and shows
// "Waiting on <note> until <date>" in its list. Needs s6-server merged to pass.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('parked: parking a card removes it from My Work and shows the line in its list', async ({ page }) => {
  const { listId } = seed();
  const created = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Disputed charge',
    assignees: ['alex'],
    due_at: new Date().toISOString().slice(0, 10),
  });

  await signIn(page);
  await page.goto('/#/home');
  await expect(page.locator(`[data-item="${created.id}"]`)).toBeVisible();

  // Open the card and park it waiting on an outside party until a future date.
  await page.locator(`[data-item="${created.id}"]`).click();
  const future = new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10);
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
