// List defaults: a card quick-added to a list with a default owner shows that owner on the
// board without a reload. Needs the server's list fields (note_md, default_assignee_id,
// default_tags, reference); until that merges this test fails at the list patch.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('default owner: a quick-added card shows its owner without a reload', async ({ page }) => {
  seed();
  const me = await api<{ account: { id: string } }>('GET', '/me');
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Defaults', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Owned' });
  await api('PATCH', `/lists/${list.id}`, { default_assignee_id: me.account.id });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const input = page.locator('#quick-add');
  await expect(input).toBeVisible();
  await input.fill('Card with a default owner');
  await input.press('Enter');

  const card = page.locator('.card', { hasText: 'Card with a default owner' });
  await expect(card).toBeVisible();
  // The owner arrives with the card (no reload): Alex's avatar is on it.
  await expect(card.locator('[title="Alex (@alex)"]')).toBeVisible();
  await expect
    .poll(async () => {
      const items = await api<{ title: string; assignees: { handle: string }[] }[]>('GET', `/items?list_id=${list.id}`);
      return items.find((i) => i.title === 'Card with a default owner')?.assignees.map((a) => a.handle) ?? [];
    })
    .toEqual(['alex']);

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
