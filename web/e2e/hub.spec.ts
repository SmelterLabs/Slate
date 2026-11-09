// The documents hub on a project home (drawers, pages, Needs you), the tree's kind
// filter and sort, and tags on a doc.
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

async function upload(docId: string, token: string, note: string) {
  const res = await fetch(`${BASE_URL}/api/v1/docs/${docId}/revisions?filename=spec.txt&note=${encodeURIComponent(note)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain', Authorization: `Bearer ${token}` },
    body: 'hello',
  });
  expect(res.ok).toBe(true);
}

test('the project home shows drawers, pages and what needs you', async ({ page }) => {
  const { alex, agent } = seed();
  const name = `Hub ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name, audience: 'work' });
  await api('PATCH', `/sections/${section.id}`, alex, { private: true }); // a private section has a project home
  const me = await api<{ account: { id: string } }>('GET', '/me', agent);
  await api('PUT', '/shares', alex, { account_id: me.account.id, scope_kind: 'section', scope_id: section.id, level: 'edit' });
  const drawer = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Hub contracts', kind: 'drawer' });
  const spec = await api<{ id: string }>('POST', '/docs', agent, { kind: 'file', title: 'Hub spec', list_id: drawer.id });
  await upload(spec.id, agent, 'First draft for Alex');
  const plan = await api<{ id: string }>('POST', '/docs', alex, { title: 'Hub plan', section_id: section.id, body_md: 'Seat 12 guests.' });
  await api('POST', `/docs/${spec.id}/comments`, agent, { body_md: 'Is this the rev the venue signed?', assignee: 'alex' });
  await api('POST', `/docs/${plan.id}/comments`, agent, { body_md: 'Should be 14', anchor: { quote: 'Seat 12 guests.' } });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/section/${section.id}`);
  const hub = page.getByTestId('docs-hub');
  await expect(hub.getByTestId('hub-drawer')).toContainText('Hub contracts');
  await expect(hub.getByTestId('hub-drawer')).toContainText('1 needs you');
  await expect(hub).toContainText('Hub plan');
  const needs = page.getByTestId('hub-needs');
  await expect(needs.getByRole('link')).toHaveCount(2);
  await expect(needs).toContainText('Asked');
  await expect(needs).toContainText('Review');

  // Clicking an ask opens the doc on that thread.
  await needs.getByRole('link').filter({ hasText: 'Asked' }).click();
  await expect(page).toHaveURL(new RegExp(`#/docs/${spec.id}`));
  await expect(page.getByRole('complementary', { name: 'Discussion' }).locator('[data-comment]', { hasText: 'Is this the rev the venue signed?' })).toBeVisible();
});

test('the tree filters by kind and sorts, and a doc takes tags', async ({ page }) => {
  const { alex, listId } = seed();
  const stamp = Date.now();
  const pg = await api<{ id: string }>('POST', '/docs', alex, { title: `Filter page ${stamp}`, list_id: listId });
  const file = await api<{ id: string }>('POST', '/docs', alex, { kind: 'file', title: `Filter file ${stamp}`, list_id: listId });
  await upload(file.id, alex, 'first');

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto('/#/docs');
  const tree = page.getByRole('tree', { name: 'Pages and files' });
  await expect(tree.getByRole('link', { name: new RegExp(`Filter page ${stamp}`) })).toBeVisible();
  await page.getByRole('group', { name: 'Filter and sort' }).getByRole('button', { name: 'Files' }).click();
  await expect(tree.getByRole('link', { name: new RegExp(`Filter file ${stamp}`) })).toBeVisible();
  await expect(tree.getByRole('link', { name: new RegExp(`Filter page ${stamp}`) })).toHaveCount(0);
  await page.getByRole('group', { name: 'Filter and sort' }).getByRole('button', { name: 'Text' }).click();
  await expect(tree.getByRole('link', { name: new RegExp(`Filter file ${stamp}`) })).toBeVisible();
  await page.getByRole('group', { name: 'Filter and sort' }).getByRole('button', { name: 'All' }).click();
  await page.getByLabel('Sort', { exact: true }).selectOption('name');
  await expect(tree.getByRole('link', { name: new RegExp(`Filter page ${stamp}`) })).toBeVisible();

  // Tags on the page, then the tag chip filters the tree.
  await page.goto(`/#/docs/${pg.id}`);
  const editor = page.getByTestId('doc-tags');
  await editor.getByRole('textbox').fill(`example${stamp}`);
  await editor.getByRole('textbox').press('Enter');
  await expect(editor).toContainText(`example${stamp}`);
  await expect.poll(async () => (await api<{ tags: string[] }>('GET', `/docs/${pg.id}`, alex)).tags).toEqual([`example${stamp}`]);
  await page.getByRole('group', { name: 'Filter by tag' }).getByRole('button', { name: new RegExp(`#example${stamp}`) }).click();
  await expect(tree.getByRole('link', { name: new RegExp(`Filter file ${stamp}`) })).toHaveCount(0);
  await expect(tree.getByRole('link', { name: new RegExp(`Filter page ${stamp}`) })).toBeVisible();
});
