// Automations run on the server and are set up through the API (agents and scripts). The board
// has no rule builder: it names the rule behind a change in the task's activity, and an old
// link to the automations page lands on Everything.
import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

test('a rule made through the API fires, and the task window names it', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Robots', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Pipeline' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Ship the parser' });
  await api('POST', '/automations', alex, {
    name: 'Tag shipped work',
    list_id: list.id,
    trigger: { type: 'status_changed', to_kind: 'done' },
    actions: [{ type: 'add_tag', tag: 'shipped' }],
  });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  await expect(page.locator('.card', { hasText: 'Ship the parser' })).toBeVisible();
  // The list header has no automations link any more.
  await expect(page.getByRole('link', { name: 'List automations' })).toHaveCount(0);

  await api('PATCH', `/items/${item.id}`, alex, { status: 'Done' });
  await expect
    .poll(async () => (await api<{ tags: string[] }>('GET', `/items/${item.id}`, alex)).tags)
    .toContain('shipped');
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  await expect(page.locator('.tw')).toContainText('by automation “Tag shipped work”');
});

test('the board has no automations page; an old link opens Everything', async ({ page }) => {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto('/#/automations');
  await expect(page.locator('.list-page[data-scope="everything"]')).toBeVisible();
  await expect(page.locator('a.nav-item[href="#/automations"]')).toHaveCount(0);
  await page.keyboard.press('Control+k');
  await page.keyboard.type('automations');
  await expect(page.getByText('Go to Automations')).toHaveCount(0);
});
