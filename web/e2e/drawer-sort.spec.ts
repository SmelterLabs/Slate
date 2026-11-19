// The drawer's Files table sorts by a column header: ascending, descending, then the drawer's
// own order. Once sorted, folders stay on top, and the choice is remembered for the drawer after a reload
// and inside its folders.
import { expect, test } from '@playwright/test';
import { api, signIn } from './support';

test('a column header sorts the Files table and the sort is remembered', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: `Sort drawer ${Date.now()}`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Minutes', kind: 'drawer' });
  // Drawer order is the order of filing: Bravo, the folder, Charlie, Alpha.
  await api('POST', '/docs', { kind: 'page', title: 'Bravo', list_id: list.id });
  const folder = await api<{ id: string }>('POST', '/docs', { kind: 'folder', title: 'Zulu folder', list_id: list.id });
  await api('POST', '/docs', { kind: 'page', title: 'Charlie', list_id: list.id });
  await api('POST', '/docs', { kind: 'page', title: 'Alpha', list_id: list.id });
  await api('POST', '/docs', { kind: 'page', title: 'Xray', parent_id: folder.id });
  await api('POST', '/docs', { kind: 'page', title: 'Yankee', parent_id: folder.id });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const table = page.getByTestId('drawer-table');
  const names = () => table.locator('tbody .c-name .row-link').allInnerTexts().then((ts) => ts.map((t) => t.replace(/^[^A-Za-z]+/, '').replace(/\s*\d+ entr(y|ies)$/, '').trim()));
  const nameHeader = table.locator('thead th', { hasText: 'Name' });
  const nameButton = nameHeader.getByRole('button');

  await expect.poll(names).toEqual(['Bravo', 'Zulu folder', 'Charlie', 'Alpha']);
  await expect(nameHeader).toHaveAttribute('aria-sort', 'none');

  await nameButton.click();
  await expect.poll(names).toEqual(['Zulu folder', 'Alpha', 'Bravo', 'Charlie']);
  await expect(nameHeader).toHaveAttribute('aria-sort', 'ascending');
  await nameButton.click();
  await expect.poll(names).toEqual(['Zulu folder', 'Charlie', 'Bravo', 'Alpha']);
  await expect(nameHeader).toHaveAttribute('aria-sort', 'descending');

  // Remembered after a reload, and inside the drawer's folders.
  await page.reload();
  await expect.poll(names).toEqual(['Zulu folder', 'Charlie', 'Bravo', 'Alpha']);
  await table.locator('tr[data-kind="folder"] .row-link').click();
  await expect.poll(names).toEqual(['Yankee', 'Xray']);

  // A third click goes back to the drawer's own order.
  await nameButton.click();
  await expect.poll(names).toEqual(['Xray', 'Yankee']);
  await page.getByRole('navigation', { name: 'Folder' }).getByRole('button', { name: 'Minutes' }).click();
  await expect.poll(names).toEqual(['Bravo', 'Zulu folder', 'Charlie', 'Alpha']);
  await expect(nameHeader).toHaveAttribute('aria-sort', 'none');
});
