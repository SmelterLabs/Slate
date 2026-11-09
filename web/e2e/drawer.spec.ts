// Drawers: a list of documents. Create one from the sidebar, add a folder and upload a file
// into it, read it with the rail and discussion, pin a revision as the one in use, see the
// drawer on the project home, and turn a page with subpages into a drawer.
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

async function upload(docId: string, token: string, name: string, note: string, body: string) {
  const res = await fetch(`${BASE_URL}/api/v1/docs/${docId}/revisions?filename=${encodeURIComponent(name)}&note=${encodeURIComponent(note)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain', Authorization: `Bearer ${token}` },
    body,
  });
  expect(res.ok).toBe(true);
}

test('a drawer from the sidebar: folder, upload, reading state, pin, move', async ({ page }) => {
  const { alex, agent } = seed();
  const name = `Drawer section ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name, audience: 'work' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/section/${section.id}`);
  const group = page.getByRole('group', { name: `Section ${name}` });
  await group.getByRole('button', { name: `Add list to ${name}` }).click();
  await page.getByRole('radio', { name: 'Drawer' }).click();
  await page.getByLabel('New drawer name').fill('Contracts');
  await page.getByLabel('New drawer name').press('Enter');
  await expect(page).toHaveURL(/#\/list\//);
  const listId = page.url().split('#/list/')[1].split(/[/?]/)[0];
  const drawer = page.getByTestId('drawer');
  await expect(drawer).toContainText('Nothing in here yet');
  await expect(page.getByRole('navigation', { name: 'Drawer tabs' })).toContainText('Files');

  // A folder, then a file uploaded into it.
  await drawer.getByRole('button', { name: 'New folder' }).click();
  await page.getByLabel('Folder name').fill('Vendors');
  await page.getByLabel('Folder name').press('Enter');
  const folderRow = page.getByTestId('drawer-table').locator('tr[data-kind="folder"]');
  await expect(folderRow).toContainText('Vendors');
  await folderRow.locator('.row-link').click();
  await expect(page.getByRole('navigation', { name: 'Folder' })).toContainText('Vendors');
  await drawer.getByRole('button', { name: 'Upload' }).click();
  const dialog = page.getByRole('dialog', { name: /Upload into Vendors/ });
  await dialog.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Hello\n') });
  await dialog.getByPlaceholder(/Corrected the totals/).fill('First draft');
  await dialog.getByRole('button', { name: 'Upload' }).click();
  const fileRow = page.getByTestId('drawer-table').locator('tr[data-kind="file"]');
  await expect(fileRow).toContainText('notes');
  await expect(fileRow).toContainText('First draft');
  // Back at the top the folder counts one entry; the sidebar counts one document.
  await page.getByRole('navigation', { name: 'Folder' }).getByRole('button', { name: 'Contracts' }).click();
  await expect(page.getByTestId('drawer-table').locator('tr[data-kind="folder"]')).toContainText('1 entry');
  await expect(page.locator('.nav-list[data-list="Contracts"] .count')).toHaveText('1');

  // Pin to top: a page added after the folder sits first once pinned, with the pin glyph.
  await api('POST', '/docs', alex, { kind: 'page', title: 'Project spec', list_id: listId });
  const table = page.getByTestId('drawer-table');
  await expect(table.locator('tbody tr').first()).toHaveAttribute('data-kind', 'folder');
  const specRow = table.locator('tr', { hasText: 'Project spec' });
  await specRow.hover();
  await specRow.getByRole('button', { name: 'Pin to top Project spec' }).click();
  await expect(table.locator('tbody tr').first()).toContainText('Project spec');
  await expect(table.locator('tr[data-pinned="true"]').getByLabel('Pinned to the top')).toBeVisible();
  await specRow.hover();
  await specRow.getByRole('button', { name: 'Unpin Project spec' }).click();
  await expect(table.locator('tbody tr').first()).toHaveAttribute('data-kind', 'folder');

  // Created: a file named with a date is created on that date.
  await api('POST', '/docs', alex, { kind: 'file', title: '2026-10-30 - Venue photos', list_id: listId });
  await expect(table.locator('tr', { hasText: 'Venue photos' }).locator('[data-created]')).toHaveText('Oct 30');

  // Reading state: click the file; the rail and discussion open; pin the revision.
  await page.getByTestId('drawer-table').locator('tr[data-kind="folder"] .row-link').click();
  await page.getByTestId('drawer-table').locator('tr[data-kind="file"] .row-link').click();
  await expect(page.getByRole('complementary', { name: 'In this drawer' })).toContainText('notes');
  await expect(page.getByRole('complementary', { name: 'Discussion' })).toBeVisible();
  await expect(page.getByTestId('file-meta')).toContainText('Revision 1');
  const docId = new URLSearchParams(page.url().split('?')[1]).get('doc')!;
  await upload(docId, agent, 'notes.txt', 'Updated text', 'Hello again\n');
  await expect(page.getByTestId('file-meta')).toContainText('Revision 2');
  await page.getByRole('button', { name: 'Revisions' }).click();
  const rev1 = page.getByRole('complementary', { name: 'Revisions' }).locator('.rev-item[data-revision="1"]');
  await rev1.getByRole('button', { name: 'Pin as in use' }).click();
  await rev1.getByLabel('Pin note').fill('The signed copy');
  await rev1.getByRole('button', { name: 'Pin', exact: true }).click();
  await expect(page.getByTestId('pinned')).toContainText('rev 1 in use: The signed copy');
  await expect(rev1).toContainText('in use');
  await page.getByRole('button', { name: 'All files' }).click();
  await expect(page.getByTestId('drawer-counts')).toContainText('3 docs · 1 folder');

  // The project home shows the drawer with its count.
  await api('PATCH', `/sections/${section.id}`, alex, { private: true });
  await page.goto(`/#/section/${section.id}`);
  await expect(page.getByTestId('hub-drawer')).toContainText('Contracts');
  await expect(page.getByTestId('hub-drawer')).toContainText('3');
  // No tasks in a drawer.
  const res = await fetch(`${BASE_URL}/api/v1/items`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${alex}` }, body: JSON.stringify({ title: 'x', list_id: listId }) });
  expect(res.status).toBe(422);
});

test('a page with subpages becomes a drawer', async ({ page }) => {
  const { alex } = seed();
  const name = `Convert ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name, audience: 'work' });
  const meetings = await api<{ id: string }>('POST', '/docs', alex, { title: 'Meetings', section_id: section.id, body_md: 'Transcripts.' });
  await api('POST', '/docs', alex, { title: '2026-10-29 - review', parent_id: meetings.id, body_md: 'a' });
  await api('POST', '/docs', alex, { title: '2026-10-30 - plan', parent_id: meetings.id, body_md: 'b' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/docs/${meetings.id}`);
  await page.getByRole('button', { name: 'Page actions' }).click();
  await page.getByRole('menuitem', { name: 'Turn into a drawer' }).click();
  await expect(page).toHaveURL(/#\/list\//);
  const table = page.getByTestId('drawer-table');
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await expect(table).toContainText('2026-10-29 - review');
  await expect(page.locator('.nav-list[data-list="Meetings"] .count')).toHaveText('3');
});
