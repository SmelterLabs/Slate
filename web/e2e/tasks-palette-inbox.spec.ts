// The palette and the inbox: Ctrl+K reaches chat channels and the trash from anywhere, a
// marked duplicate shows on its card, and the inbox names a task a chat message links instead
// of showing its id; a doc @mention reaches the inbox.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

async function newList(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} area`, audience: 'work' });
  return api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('Ctrl+K opens a chat channel, and "Open the trash" works from the Archive page', async ({ page }) => {
  const list = await newList('Palette chat');
  await page.goto('/#/archive');
  await expect(page.getByRole('tab', { name: 'Done' })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Control+k');
  await page.keyboard.type('open the trash');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('tab', { name: 'Trash' })).toHaveAttribute('aria-selected', 'true');

  await page.keyboard.press('Control+k');
  await page.keyboard.type('palette chat');
  await page.getByRole('option', { name: /Chat/ }).first().click();
  await expect(page).toHaveURL(new RegExp(`#/chat/list:${list.id}`));
});

test('a marked duplicate shows on its card', async ({ page }) => {
  const list = await newList('Dup card');
  const keep = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Clean the gutters' });
  const dup = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Gutters again' });
  await api('POST', `/items/${dup.id}/links`, { kind: 'duplicate_of', item_id: keep.id });
  await page.goto(`/#/list/${list.id}`);
  await expect(page.locator(`[data-item="${dup.id}"]`).getByText('Duplicate', { exact: true })).toBeVisible();
  await expect(page.locator(`[data-item="${keep.id}"]`).getByText('Duplicate', { exact: true })).toHaveCount(0);
});

test('the inbox names a task linked from chat', async ({ page }) => {
  const list = await newList('Inbox refs');
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Renew the permit' });
  await api('POST', '/channels/general/messages', { body_md: `@alex I took #${item.id}` }, seed().agent);
  await page.goto('/#/inbox');
  await expect(page.getByText('I took #Renew the permit')).toBeVisible();
  await expect(page.getByText(`#${item.id}`)).toHaveCount(0);
});

test('a doc mention lands in the inbox and opens the page', async ({ page }) => {
  const doc = await api<{ id: string }>(
    'POST',
    '/docs',
    { title: 'Ladder checklist', body_md: '@alex can you check the ladder?' },
    seed().agent,
  );
  await page.goto('/#/inbox');
  const row = page.locator('.inbox-main', { hasText: 'Ladder checklist' });
  await expect(row).toContainText('mentioned @alex in “Ladder checklist”');
  await row.click();
  await expect(page).toHaveURL(new RegExp(`#/docs/${doc.id}`));
  await expect.poll(async () => (await api<{ event: { type: string }; read: boolean }[]>('GET', '/inbox')).filter((e) => e.event.type === 'doc.mentioned' && !e.read).length).toBe(0);
});
