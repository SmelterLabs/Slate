// Compare revisions: a CSV's changed cell is painted old → new with the added row marked; a
// text file shows a side-by-side diff; a page's history has a side-by-side tab.
import { expect, test } from '@playwright/test';
import { api, BASE_URL, seed, signIn } from './support';

async function upload(docId: string, token: string, name: string, type: string, note: string, body: string) {
  const res = await fetch(`${BASE_URL}/api/v1/docs/${docId}/revisions?filename=${encodeURIComponent(name)}&note=${encodeURIComponent(note)}`, {
    method: 'POST',
    headers: { 'Content-Type': type, Authorization: `Bearer ${token}` },
    body,
  });
  expect(res.ok, `upload -> ${res.status}`).toBe(true);
}

test('a spreadsheet compare paints the changed cell and the added row', async ({ page }) => {
  const { agent, listId } = seed();
  const doc = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: 'Price sheet', list_id: listId }, agent);
  await upload(doc.id, agent, 'quote.csv', 'text/csv', 'First quote', 'item,price\ntickets,120\n');
  await upload(doc.id, agent, 'quote.csv', 'text/csv', 'Price up, flyers added', 'item,price\ntickets,135\nflyers,1\n');

  await signIn(page);
  await page.goto(`/#/docs/${doc.id}`);
  await page.getByRole('button', { name: 'Revisions' }).click();
  const panel = page.getByRole('complementary', { name: 'Revisions' });
  await panel.locator('.rev-item[data-revision="1"]').getByRole('button', { name: 'Compare with current' }).click();

  const cmp = page.getByTestId('compare');
  await expect(cmp).toContainText('1 cell changed, 1 row added');
  const cell = cmp.locator('td[data-changed]');
  await expect(cell).toHaveCount(1);
  await expect(cell.locator('del')).toHaveText('120');
  await expect(cell.locator('ins')).toHaveText('135');
  await expect(cmp.locator('tr.added')).toContainText('flyers');
  await cmp.getByRole('button', { name: 'Close compare' }).click();
  await expect(page.getByTestId('file-meta')).toContainText('Revision 2');
});

test('a text file compares side by side, and a page history has the same view', async ({ page }) => {
  const { agent, listId } = seed();
  const doc = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: 'Schedule', list_id: listId }, agent);
  await upload(doc.id, agent, 'notes.txt', 'text/plain', 'first', 'Doors 6 pm\nDinner 7 pm\n');
  await upload(doc.id, agent, 'notes.txt', 'text/plain', 'music added', 'Doors 6 pm\nDinner 8 pm\nMusic 9 pm\n');

  await signIn(page);
  await page.goto(`/#/docs/${doc.id}`);
  await page.getByRole('button', { name: 'Revisions' }).click();
  await page.getByRole('complementary', { name: 'Revisions' }).locator('.rev-item[data-revision="1"] .rev-pick').click();
  await page.getByRole('status').getByRole('button', { name: 'Compare with current' }).click();
  const cmp = page.getByTestId('compare');
  await expect(cmp.locator('.rc-row.change .left')).toContainText('Dinner 7 pm');
  await expect(cmp.locator('.rc-row.change .right')).toContainText('Dinner 8 pm');
  await expect(cmp.locator('.rc-row.add .right')).toContainText('Music 9 pm');
  await cmp.getByRole('tab', { name: 'Inline' }).click();
  await expect(cmp.locator('.dl.add')).toHaveCount(2);

  // A page: history's side-by-side tab.
  const pg = await api<{ id: string }>('POST', '/docs', { title: 'Notes', list_id: listId, body_md: 'one\ntwo\n' });
  await api('PATCH', `/docs/${pg.id}`, { body_md: 'one\nTWO\nthree\n', note: 'Agent edit' }, agent);
  await page.goto(`/#/docs/${pg.id}`);
  await page.getByRole('button', { name: 'Page history' }).click();
  await page.getByRole('tab', { name: 'Side by side' }).click();
  const side = page.getByTestId('side-by-side');
  await expect(side.locator('.rc-row.change .left')).toContainText('two');
  await expect(side.locator('.rc-row.change .right')).toContainText('TWO');
});
