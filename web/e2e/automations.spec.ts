// Automations run on the server and are set up through the API (agents and scripts). The board
// has no rule builder: it names the rule behind a change in the task's activity, and an old
// link to the automations page lands on Everything.
import { expect, test } from '@playwright/test';
import { api, signIn } from './support';

test('a rule made through the API fires, and the task window names it', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Robots', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Pipeline' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Ship the parser' });
  await api('POST','/automations',{
    name: 'Tag shipped work',
    list_id: list.id,
    trigger: { type: 'status_changed', to_kind: 'done' },
    actions: [{ type: 'add_tag', tag: 'shipped' }],
  });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await expect(page.locator('.card', { hasText: 'Ship the parser' })).toBeVisible();
  // The list header has no automations link any more.
  await expect(page.getByRole('link', { name: 'List automations' })).toHaveCount(0);

  await api('PATCH', `/items/${item.id}`, { status: 'Done' });
  await expect
    .poll(async () => (await api<{ tags: string[] }>('GET', `/items/${item.id}`)).tags)
    .toContain('shipped');
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  await expect(page.locator('.tw')).toContainText('by automation “Tag shipped work”');
});

test('the board has no automations page; an old link opens Everything', async ({ page }) => {
  await signIn(page);
  await page.goto('/#/automations');
  await expect(page.locator('.list-page[data-scope="everything"]')).toBeVisible();
  await expect(page.locator('a.nav-item[href="#/automations"]')).toHaveCount(0);
  await page.keyboard.press('Control+k');
  await page.keyboard.type('automations');
  await expect(page.getByText('Go to Automations')).toHaveCount(0);
});
