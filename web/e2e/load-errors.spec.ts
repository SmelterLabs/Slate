import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('load failures tell you which board data could not be refreshed', async ({ page }) => {
  const { listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Visible while tags fail',
  });
  await page.route('**/api/v1/tags', (route) =>
    route.fulfill({ status: 503, json: { detail: 'Tags temporarily unavailable' } }),
  );
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const card = page.locator('.card').filter({ hasText: 'Visible while tags fail' });
  await expect(card).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Could not load tags');

  await page.route(`**/api/v1/items/${item.id}`, (route) =>
    route.fulfill({ status: 503, json: { detail: 'Task temporarily unavailable' } }),
  );
  await card.click();
  await expect(page.getByRole('alert')).toContainText('Could not load task details');
});
