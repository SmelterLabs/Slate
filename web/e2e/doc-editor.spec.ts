// The page editor is what you see: clicking into a page never shows Markdown, the toolbar
// formats in place, and what is saved is still the Markdown agents read. Tables size their
// columns to the words in them. Phone and PC get the same editor.
import { expect, test, type Page } from '@playwright/test';
import { api, seed, signIn } from './support';

interface Doc { id: string; body_md: string }

const STAGES = [
  '## Stages',
  '',
  '| Stage | What has to be true to move on | Who |',
  '| --- | --- | --- |',
  '| 4 Scheduled | Date, room, agenda | Alex |',
  '| 3 Prepared | 3A materials gathered, 3B equipment and rentals booked, 3C venue and safety checked, travel and timezone confirmed. 4A open items settled, each with an owner and due date; 4B approval on file (signed order, changes, budget, deposit) | My Agent gathers; Alex plans |',
  '',
  'Each event also carries **event type** and _budget basis_.',
  '',
  '- [ ] Confirm the caterer',
].join('\n');

async function open(page: Page, body = STAGES) {
  const doc = await api<Doc>('POST', '/docs', { title: 'Editor page', list_id: seed().listId, body_md: body });
  await signIn(page);
  await page.goto(`/#/docs/${doc.id}`);
  await expect(page.locator('.doc-rendered table')).toBeVisible();
  return doc;
}

const saved = (id: string) => async () => (await api<Doc>('GET', `/docs/${id}`)).body_md;

test('clicking into the page keeps it formatted and typing saves Markdown', async ({ page }) => {
  const doc = await open(page);
  const editor = page.getByRole('textbox', { name: 'Page text', exact: true });
  await page.getByText('Each event also carries').click();
  await expect(editor).toBeFocused();
  // Still the page, not its source.
  await expect(page.locator('.doc-rendered table')).toBeVisible();
  await expect(page.locator('.doc-rendered strong', { hasText: 'event type' })).toBeVisible();
  await expect(editor).not.toContainText('**');
  await expect(editor).not.toContainText('| --- |');

  await page.keyboard.press('End');
  await page.keyboard.type(' Done.');
  await expect.poll(saved(doc.id)).toContain('Each event also carries **event type** and _budget basis_. Done.');
  // The table nobody touched keeps its exact source.
  expect(await saved(doc.id)()).toContain('| --- | --- | --- |\n| 4 Scheduled | Date, room, agenda | Alex |');
});

test('the toolbar formats in place', async ({ page }) => {
  const doc = await open(page, '## Stages\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nPlain words here');
  const editor = page.getByRole('textbox', { name: 'Page text', exact: true });
  await page.getByText('Plain words here').click();
  await page.keyboard.press('End');
  await page.keyboard.press('Shift+Home');
  await page.getByRole('button', { name: 'B', exact: true }).first().click();
  await expect(editor.locator('b, strong', { hasText: 'Plain words here' })).toBeVisible();
  await expect.poll(saved(doc.id)).toContain('**Plain words here**');

  await page.getByRole('toolbar', { name: 'Formatting' }).getByRole('button', { name: 'H2' }).click();
  await expect(editor.locator('h2', { hasText: 'Plain words here' })).toBeVisible();
  await expect.poll(saved(doc.id)).toContain('## **Plain words here**');

  // A new line as a checklist, ticked off with a click.
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await page.getByRole('toolbar', { name: 'Formatting' }).getByTitle('Checklist').click();
  await page.keyboard.type('Call the venue');
  await expect.poll(saved(doc.id)).toMatch(/- \[ \] (\*\*)?Call the venue/);
  await editor.locator('li.md-li-check input').click();
  await expect.poll(saved(doc.id)).toMatch(/- \[x\] (\*\*)?Call the venue/);

  // Typing "# " at the start of a line makes a heading.
  await page.getByText('Call the venue').click();
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await page.keyboard.type('# Big title');
  await expect(editor.locator('h1', { hasText: 'Big title' })).toBeVisible();
  await expect.poll(saved(doc.id)).toContain('# Big title');

  // A table from the toolbar; Tab moves between cells.
  await page.keyboard.press('Enter');
  await page.getByRole('toolbar', { name: 'Formatting' }).getByTitle('Table').click();
  await page.keyboard.type('Stage');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Who');
  await expect.poll(saved(doc.id)).toContain('| Stage | Who | Column |');

  // Leaving the page shows the same thing.
  await page.locator('.doc-title').click();
  await expect(editor.locator('h1', { hasText: 'Big title' })).toBeVisible();
  await expect(editor).not.toContainText('#');
});

test('table columns fit their words on a PC and a phone', async ({ browser }) => {
  for (const viewport of [{ width: 1600, height: 1000 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, hasTouch: viewport.width < 500, isMobile: viewport.width < 500 });
    const page = await context.newPage();
    await open(page);
    const first = page.locator('.doc-rendered td').filter({ hasText: '4 Scheduled' });
    // "Scheduled" sits on one line: the cell is wider than the word.
    const fits = await first.evaluate((td) => {
      const range = document.createRange();
      range.selectNodeContents(td);
      const lines = new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top)));
      return lines.size <= 2;
    });
    expect(fits).toBe(true);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(viewport.width);
    await context.close();
  }
});
