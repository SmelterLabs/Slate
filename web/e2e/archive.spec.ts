// The trash and the Archive page: move a task to the trash from its card menu (Undo puts it
// back), find it in the trash, restore it, delete another for good, see done and archived
// tasks, and download an export.
import { expect, test } from '@playwright/test';
import { api, BASE_URL, openMore, seed, signIn } from './support';

test('trash, restore and the archive page', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Chores', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'House' });
  const make = (title: string) => api<{ id: string }>('POST', '/items', { list_id: list.id, title });
  const bin = await make('Clean the gutters');
  const gone = await make('Old idea');
  const done = await make('Fix the tap');
  const shelved = await make('Paint the fence');
  await api('PATCH', `/items/${done.id}`, { status: 'Done' });
  await api('POST', `/items/${shelved.id}/archive`);
  await api('POST', `/items/${gone.id}/trash`);

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator(`[data-item="${bin.id}"]`).first();
  await expect(card).toBeVisible();
  await expect(page.locator(`[data-item="${gone.id}"]`)).toHaveCount(0);

  // Move to trash from the card menu; Undo brings it straight back.
  await card.hover();
  await card.getByRole('button', { name: /actions|options|more/i }).first().click();
  await page.getByRole('menuitem', { name: 'Move to trash' }).click();
  await expect(page.locator(`[data-item="${bin.id}"]`)).toHaveCount(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.locator(`[data-item="${bin.id}"]`).first()).toBeVisible();

  // Trash it for real (from another client: the board follows live).
  await api('POST', `/items/${bin.id}/trash`);
  await expect(page.locator(`[data-item="${bin.id}"]`)).toHaveCount(0);

  // The Archive page: Done first.
  await openMore(page); // Archive lives under More
  await page.getByRole('link', { name: 'Archive' }).first().click();
  await expect(page.getByRole('heading', { name: 'Archive' })).toBeVisible();
  const rows = page.locator('.arch-row');
  await expect(page.locator(`.arch-row[data-item="${done.id}"]`)).toContainText('Fix the tap');
  await expect(page.locator(`.arch-row[data-item="${done.id}"]`)).toContainText('Completed');

  await page.getByRole('tab', { name: 'Put away' }).click();
  await expect(page.locator(`.arch-row[data-item="${shelved.id}"]`)).toContainText('Paint the fence');
  await expect(rows).toHaveCount(1);

  await page.getByRole('tab', { name: 'Trash' }).click();
  await expect(page).toHaveURL(/#\/archive\?tab=trash/);
  const binned = page.locator(`.arch-row[data-item="${bin.id}"]`);
  await expect(binned).toContainText('Deleted for good in 30 days');
  await expect(rows).toHaveCount(2);

  // Restore one.
  await binned.getByRole('button', { name: 'Restore' }).click();
  await expect(binned).toHaveCount(0);
  const back = await api<{ trashed_at: string | null }>('GET', `/items/${bin.id}`);
  expect(back.trashed_at).toBeNull();

  // Delete the other for good (asks first).
  const old = page.locator(`.arch-row[data-item="${gone.id}"]`);
  await old.getByRole('button', { name: 'Delete forever' }).click();
  await old.getByRole('button', { name: 'Delete for good?' }).click();
  await expect(page.getByText('The trash is empty')).toBeVisible();
  const res = await fetch(`${BASE_URL}/api/v1/items/${gone.id}`, { headers: { Authorization: `Bearer ${alex}` } });
  expect(res.status).toBe(404);

  // Export everything as CSV.
  const [file] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Export CSV' }).click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/^slate-everything-\d{4}-\d{2}-\d{2}\.csv$/);
});

test('a trashed task opens with a restore banner', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Misc', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Stuff' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Binned thing' });
  await api('POST', `/items/${item.id}/trash`);

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const modal = page.getByRole('dialog', { name: /Binned thing/ });
  await expect(modal.locator('.banner.trashed')).toContainText('in the trash');
  await modal.locator('.banner.trashed').getByRole('button', { name: 'Restore' }).click();
  await expect(modal.locator('.banner.trashed')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator(`[data-item="${item.id}"]`).first()).toBeVisible();
});
