// The card's ⋯ menu Due row (Today, Tomorrow, a date picker) and the copy-ID chip in each card's
// bottom-right corner: it copies the ID, says "Copied", and never opens or drags the card.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/when';
import { api, signIn } from './support';

// SLATE_SCREENSHOTS=1 writes screenshots into docs/img/.
const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });
/** A close-up around the given region (x, y, width, height), padded. */
async function shot(page: Page, name: string, box: { x: number; y: number; width: number; height: number }) {
  if (!shots) return;
  const pad = 16;
  const clip = { x: Math.max(0, box.x - pad), y: Math.max(0, box.y - pad), width: box.width + 2 * pad, height: box.height + 2 * pad };
  await page.screenshot({ path: join(shots, `${name}.png`), clip });
}

/** The smallest box around several elements. */
async function around(...locs: ReturnType<Page['locator']>[]) {
  const bs = await Promise.all(locs.map(async (l) => (await l.boundingBox())!));
  const x = Math.min(...bs.map((b) => b.x));
  const y = Math.min(...bs.map((b) => b.y));
  return {
    x,
    y,
    width: Math.max(...bs.map((b) => b.x + b.width)) - x,
    height: Math.max(...bs.map((b) => b.y + b.height)) - y,
  };
}

const made: string[] = [];
test.afterAll(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/sections/${id}`, { archived: true });
});

async function lab(titles: string[]) {
  const sectionId = (await api<{ id: string }>('POST', '/sections', { name: `Card quick ${Date.now()}`, audience: 'personal' })).id;
  made.push(sectionId);
  const listId = (
    await api<{ id: string }>('POST', '/lists', {
      section_id: sectionId,
      name: 'Card quick',
      statuses: [
        { name: 'To do', kind: 'open' },
        { name: 'Doing', kind: 'active' },
        { name: 'Done', kind: 'done' },
      ],
    })
  ).id;
  const ids: Record<string, string> = {};
  for (const title of titles) ids[title] = (await api<{ id: string }>('POST', '/items', { list_id: listId, title })).id;
  return { listId, ids };
}

const card = (page: Page, title: string) => page.locator('.card', { hasText: title });
const dueDay = async (id: string) => {
  const it = await api<{ due_at: string | null }>('GET', `/items/${id}`);
  return it.due_at ? workspaceDay(new Date(it.due_at)) : null;
};

test.use({ viewport: { width: 1400, height: 900 } });

test('the Due row offers Today, Tomorrow and a date picker, and marks the current one', async ({ page }) => {
  const { listId, ids } = await lab(['Pick my date']);
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const c = card(page, 'Pick my date');
  await c.hover();
  await c.getByRole('button', { name: 'Task actions' }).click();
  const menu = page.locator('.card-menu');
  await expect(menu.getByRole('button', { name: 'Next wk' })).toHaveCount(0);
  await expect(menu.getByRole('button', { name: 'Today', exact: true })).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Tomorrow', exact: true })).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Pick a date' })).toBeVisible();

  // Due and priority chips line up: one height for every chip in both rows.
  const heights = await page
    .locator('.card-menu .menu-row.chips button')
    .evaluateAll((els) => [...new Set(els.map((e) => Math.round(e.getBoundingClientRect().height)))]);
  expect(heights).toHaveLength(1);
  await shot(page, 'card-menu-due', await around(c, menu));

  // A picked date lands on the task, and the menu shows it as the current choice.
  const target = addDays(workspaceDay(new Date()), 9);
  await menu.getByLabel('Due date').fill(target);
  await expect.poll(() => dueDay(ids['Pick my date'])).toBe(target);
  await expect(menu).toHaveCount(0);

  await c.hover();
  await c.getByRole('button', { name: 'Task actions' }).click();
  await expect(page.locator('.card-menu').getByRole('button', { name: 'Pick a date' })).toHaveClass(/\bon\b/);
  await shot(page, 'card-menu-picked', await around(c, page.locator('.card-menu')));
  await page.locator('.card-menu').getByRole('button', { name: 'Today', exact: true }).click();
  await expect.poll(() => dueDay(ids['Pick my date'])).toBe(workspaceDay(new Date()));
  await c.hover();
  await c.getByRole('button', { name: 'Task actions' }).click();
  await expect(page.locator('.card-menu').getByRole('button', { name: 'Today', exact: true })).toHaveClass(/\bon\b/);
});

test('the copy-ID chip copies the ID and says so, without opening the card', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const { listId, ids } = await lab(['Copy my id']);
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const c = card(page, 'Copy my id');
  await c.hover();
  const chip = c.getByRole('button', { name: 'Copy task ID' });
  await expect(chip).toBeVisible();

  // It sits in the card's bottom-right corner.
  const cb = (await c.boundingBox())!;
  const bb = (await chip.boundingBox())!;
  expect(cb.x + cb.width - (bb.x + bb.width)).toBeLessThan(12);
  expect(cb.y + cb.height - (bb.y + bb.height)).toBeLessThan(12);

  await chip.click();
  await expect(c.locator('.card-copy-tip')).toHaveText('Copied');
  await page.waitForTimeout(300);
  await shot(page, 'card-copy-id', await around(c, c.locator('.card-copy-tip')));
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(ids['Copy my id']);
  await expect(page.locator('.tw')).toHaveCount(0);
  await expect(c.locator('.card-copy-tip')).toHaveCount(0, { timeout: 3000 });
});

test('dragging from the copy-ID chip does not move the card', async ({ page }) => {
  const { listId, ids } = await lab(['Stay put']);
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const c = card(page, 'Stay put');
  await c.hover();
  const chip = c.getByRole('button', { name: 'Copy task ID' });
  const a = (await chip.boundingBox())!;
  const b = (await page.locator('.column[data-status="Doing"]').boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + 14, a.y + 8, { steps: 4 });
  await page.mouse.move(b.x + b.width / 2, b.y + 60, { steps: 20 });
  await page.mouse.up();
  await expect(page.locator('.column[data-status="To do"]')).toContainText('Stay put');
  const it = await api<{ status: { name: string } }>('GET', `/items/${ids['Stay put']}`);
  expect(it.status.name).toBe('To do');
  await expect(page.locator('.tw')).toHaveCount(0);
});

/** True when every sampled point inside the panel hits the panel itself, not a card over it. */
async function onTop(panel: ReturnType<Page['locator']>) {
  return panel.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const pts = [0.2, 0.5, 0.8].flatMap((fx) => [0.2, 0.5, 0.8].map((fy) => [r.left + r.width * fx, r.top + r.height * fy]));
    const miss = pts.map(([x, y]) => document.elementFromPoint(x, y)).find((h) => !el.contains(h));
    return !miss;
  });
}

test('the column ⋯ menu and the card ⋯ menu sit on top of the cards', async ({ page }) => {
  const { listId } = await lab(['Under the menu one', 'Under the menu two', 'Under the menu three']);
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const col = page.locator('.column[data-status="To do"]');
  await col.hover();
  await col.getByRole('button', { name: 'Column options' }).click();
  const colMenu = page.locator('.dd-panel', { hasText: 'Collapse column' });
  await expect(colMenu).toBeVisible();
  expect(await onTop(colMenu)).toBe(true);
  await page.waitForTimeout(300);
  await shot(page, 'column-menu-on-top', await around(col, colMenu));
  await page.keyboard.press('Escape');

  const c = card(page, 'Under the menu one');
  await c.hover();
  await c.getByRole('button', { name: 'Task actions' }).click();
  const cardMenu = page.locator('.dd-panel', { has: page.locator('.card-menu') });
  await expect(cardMenu).toBeVisible();
  expect(await onTop(cardMenu)).toBe(true);
});
