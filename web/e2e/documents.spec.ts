// Word and Excel attachments open in the full-screen viewer, drawn by the board itself
// (lib/office, files/DocumentPreview): a .docx as a page, a .xlsx as sheets with tabs.
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_URL, seed } from './seed';

const here = dirname(fileURLToPath(import.meta.url));
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

async function upload(token: string, itemId: string, name: string, type: string) {
  const res = await fetch(`${BASE_URL}/api/v1/items/${itemId}/attachments?filename=${name}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': type },
    body: readFileSync(join(here, 'fixtures', name)),
  });
  expect(res.status).toBe(201);
}

test('a Word file and a workbook open in the viewer, full size', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Events', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Quotes' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Project scope review' });
  await upload(alex, item.id, 'scope.docx', DOCX);
  await upload(alex, item.id, 'quote.xlsx', XLSX);

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const files = page.locator('.tw').getByRole('region', { name: 'Attachments' });

  // The Word file: a page with its heading, bold run and bullet.
  await files.getByRole('button', { name: 'Open scope.docx' }).click();
  const viewer = page.getByRole('dialog', { name: 'Viewing scope.docx' });
  const docPage = viewer.getByRole('article', { name: 'Document' });
  await expect(docPage.getByRole('heading', { name: 'Scope of work' })).toBeVisible();
  await expect(docPage.locator('strong')).toHaveText('phase 1 deliverables');
  await expect(docPage).toContainText('Standard layout, dark theme');
  // Full size: the page is US-Letter wide and the stage is the viewer's height.
  const box = await docPage.boundingBox();
  expect(box?.width).toBeGreaterThanOrEqual(800);

  // The arrow steps to the workbook: the grid with letters and numbers, formats and tabs.
  await viewer.getByRole('button', { name: 'Next file' }).click();
  const sheets = page.getByRole('dialog', { name: 'Viewing quote.xlsx' });
  const grid = sheets.getByRole('tabpanel', { name: 'Sheet Quote' });
  await expect(grid.getByRole('columnheader', { name: 'A', exact: true })).toBeVisible();
  await expect(grid.locator('td[colspan="3"]')).toHaveText('Example Project quote');
  await expect(grid.getByRole('cell', { name: '$480.00' })).toHaveClass(/right/);
  await expect(grid.getByRole('cell', { name: 'Banner stand' })).toBeVisible();
  await sheets.getByRole('tab', { name: 'Notes' }).click();
  await expect(sheets.getByRole('tabpanel', { name: 'Sheet Notes' }).getByRole('cell', { name: '11/20/2026' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(sheets).toBeHidden();
});

test('a Word file longer than the screen stays on its white page to the last line', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Long docs', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Quotes' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Questions for the quote' });
  await upload(alex, item.id, 'long.docx', DOCX);

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  await page.locator('.tw').getByRole('region', { name: 'Attachments' }).getByRole('button', { name: 'Open long.docx' }).click();
  const docPage = page.getByRole('dialog', { name: 'Viewing long.docx' }).getByRole('article', { name: 'Document' });
  const last = docPage.getByText('The last line of the document.');
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeVisible();
  // The page grows with its text: the last line sits inside the white page, not spilled past it.
  const pageBox = await docPage.boundingBox();
  const lastBox = await last.boundingBox();
  expect(lastBox!.y + lastBox!.height).toBeLessThanOrEqual(pageBox!.y + pageBox!.height);
});
