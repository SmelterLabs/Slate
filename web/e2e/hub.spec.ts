// The documents hub on a project home (drawers, pages, Needs you), the tree's kind
// filter and sort, and tags on a doc.
import { expect, test } from '@playwright/test';
import { addRevision, api, seed, signIn } from './support';

test('the project home shows drawers, pages and what needs you', async ({ page }) => {
  const { agent } = seed();
  const name = `Hub ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', { name, audience: 'work' });
  await api('PATCH', `/sections/${section.id}`, { private: true }); // a private section has a project home
  const me = await api<{ account: { id: string } }>('GET', '/me', undefined, agent);
  await api('PUT', '/shares', { account_id: me.account.id, scope_kind: 'section', scope_id: section.id, level: 'edit' });
  const drawer = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Hub contracts', kind: 'drawer' });
  const spec = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: 'Hub spec', list_id: drawer.id }, agent);
  await addRevision(spec.id, 'spec.txt', 'hello', { note: 'First draft for Alex', token: agent });
  const plan = await api<{ id: string }>('POST', '/docs', { title: 'Hub plan', section_id: section.id, body_md: 'Seat 12 guests.' });
  await api('POST', `/docs/${spec.id}/comments`, { body_md: 'Is this the rev the venue signed?', assignee: 'alex' }, agent);
  await api('POST', `/docs/${plan.id}/comments`, { body_md: 'Should be 14', anchor: { quote: 'Seat 12 guests.' } }, agent);

  await signIn(page);
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
  const { listId } = seed();
  const stamp = Date.now();
  const pg = await api<{ id: string }>('POST', '/docs', { title: `Filter page ${stamp}`, list_id: listId });
  const file = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: `Filter file ${stamp}`, list_id: listId });
  await addRevision(file.id, 'spec.txt', 'hello', { note: 'first' });

  await signIn(page);
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
  await expect.poll(async () => (await api<{ tags: string[] }>('GET', `/docs/${pg.id}`)).tags).toEqual([`example${stamp}`]);
  await page.getByRole('group', { name: 'Filter by tag' }).getByRole('button', { name: new RegExp(`#example${stamp}`) }).click();
  await expect(tree.getByRole('link', { name: new RegExp(`Filter file ${stamp}`) })).toHaveCount(0);
  await expect(tree.getByRole('link', { name: new RegExp(`Filter page ${stamp}`) })).toBeVisible();
});
