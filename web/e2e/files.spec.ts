// File docs: an agent files a PDF as a document over the API; the board shows it in the tree
// with its revision, opens it in the viewer, Alex uploads the next revision with a note, the
// Revisions panel lists both, and an older revision can be looked at and made current again.
import { expect, test } from '@playwright/test';
import { addRevision, api, seed, signIn } from './support';

test('a file doc shows its revisions and takes a new one with a note', async ({ page }) => {
  const { agent, listId } = seed();
  const doc = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: 'Quote', list_id: listId, body_md: 'The quote from the printer.' }, agent);
  await addRevision(doc.id, 'quote.csv', 'item,price\ntickets,120\n', { note: 'First quote', type: 'text/csv', token: agent });

  await signIn(page);
  await page.goto('/#/docs');
  await expect(page.getByRole('heading', { name: 'Docs & Files', level: 1 })).toBeVisible();
  const row = page.getByRole('tree', { name: 'Pages and files' }).getByRole('treeitem').filter({ hasText: 'Quote' });
  await expect(row.locator('.tree-rev')).toHaveText('r1');
  await row.getByRole('link', { name: /Quote/ }).click();

  // The current revision in the viewer (a CSV as a table), its note, and the notes below.
  await expect(page.getByTestId('file-meta')).toContainText('Revision 1 · quote.csv');
  await expect(page.getByTestId('current-note')).toContainText('First quote');
  await expect(page.locator('.file-view[data-kind="table"] td', { hasText: 'tickets' })).toBeVisible();
  await expect(page.getByText('The quote from the printer.')).toBeVisible();

  // Upload the next revision from the board: a file and the required note.
  await page.getByRole('button', { name: 'Upload new revision' }).click();
  const dialog = page.getByRole('dialog', { name: 'New revision of Quote' });
  await expect(dialog.getByRole('button', { name: 'Upload' })).toBeDisabled();
  await dialog.locator('input[type=file]').setInputFiles({ name: 'quote-v2.csv', mimeType: 'text/csv', buffer: Buffer.from('item,price\ntickets,135\n') });
  await expect(dialog.getByRole('button', { name: 'Upload' })).toBeDisabled(); // still no note
  await dialog.getByPlaceholder(/Corrected the totals/).fill('Price up to 135');
  await dialog.getByRole('button', { name: 'Upload' }).click();
  await expect(page.getByTestId('file-meta')).toContainText('Revision 2 · quote-v2.csv');
  await expect(page.locator('.file-view[data-kind="table"] td', { hasText: '135' })).toBeVisible();
  await expect(row.locator('.tree-rev')).toHaveText('r2');

  // Revisions: both listed, the note folded; look at rev 1, then make it current again.
  await page.getByRole('button', { name: 'Revisions' }).click();
  const panel = page.getByRole('complementary', { name: 'Revisions' });
  await expect(panel.locator('.rev-item')).toHaveCount(2);
  await expect(panel.locator('.rev-item[data-revision="1"] .rev-note summary')).toContainText('First quote');
  await panel.locator('.rev-item[data-revision="1"] .rev-pick').click();
  await expect(page.getByRole('status')).toContainText('Viewing revision 1 of 2');
  await expect(page.locator('.file-view[data-kind="table"] td', { hasText: '120' })).toBeVisible();
  await panel.locator('.rev-item[data-revision="1"]').getByRole('button', { name: 'Make current' }).click();
  await expect(page.getByTestId('file-meta')).toContainText('Revision 3 · quote.csv');
  await expect(panel.locator('.rev-item')).toHaveCount(3);
  await expect(panel.locator('.rev-item[data-revision="3"]')).toContainText('restored from 1');

  // A revision an agent adds elsewhere arrives live.
  await addRevision(doc.id, 'quote.csv', 'item,price\ntickets,135\n', { note: 'Approved', type: 'text/csv', token: agent });
  await expect(page.getByTestId('file-meta')).toContainText('Revision 4');
});

test('upload a file from the Docs & Files page', async ({ page }) => {
  await signIn(page);
  await page.goto('/#/docs');
  await page.getByRole('button', { name: 'Upload', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Upload a file' });
  await dialog.locator('input[type=file]').setInputFiles({ name: 'floor-plan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%fake\n') });
  await expect(dialog.getByPlaceholder(/Corrected the totals/)).toHaveValue('First upload');
  await dialog.getByRole('button', { name: 'Upload' }).click();
  await expect(page).toHaveURL(/#\/docs\/[A-Za-z0-9]+$/);
  await expect(page.getByLabel('Page title')).toHaveValue('floor-plan');
  await expect(page.getByTestId('file-meta')).toContainText('Revision 1 · floor-plan.pdf');
  await expect(page.locator('.file-view[data-kind="pdf"] iframe')).toBeVisible();
});
