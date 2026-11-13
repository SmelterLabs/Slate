// "Move to…": a searchable list picker, grouped by section, in the card's right-click and ⋯
// menus, the open task window, and the selection bar. Subtasks travel with their parent.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api, signIn } from './support';

// SLATE_SCREENSHOTS=1 writes screenshots into docs/img/.
const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });

const made: string[] = [];
test.afterAll(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/sections/${id}`, { archived: true });
});

const statuses = [
  { name: 'To do', kind: 'open' },
  { name: 'Doing', kind: 'active' },
  { name: 'Done', kind: 'done' },
];

/** A section with a Home list and a To Do list, both uniquely named for this run. */
async function lab(tag: string) {
  const stamp = `${tag} ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', { name: `Move lab ${stamp}`, audience: 'personal' });
  made.push(section.id);
  const home = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: `Home ${stamp}`, statuses });
  const todo = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: `To Do ${stamp}`, statuses });
  return { stamp, home: home.id, todo: todo.id };
}

const card = (page: Page, title: string) => page.locator('.card', { hasText: title });
const listOf = async (id: string) => (await api<{ list_id: string }>('GET', `/items/${id}`)).list_id;

// A wide desktop screen: the task window then sits well away from the corner, which is what
// pushed a floating picker off it before.
test.use({ viewport: { width: 1920, height: 1080 } });

/** The element really on top at the centre of `loc` is `loc` itself (not hidden or covered). */
async function onTop(loc: ReturnType<Page['locator']>) {
  await expect
    .poll(() =>
      loc.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return !!hit && (hit === el || el.contains(hit));
      }),
    )
    .toBe(true);
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('right-click a card, Move to…, search and Enter: it moves with its subtasks', async ({ page }) => {
  const { stamp, home, todo } = await lab('rc');
  const parent = await api<{ id: string }>('POST', '/items', { list_id: home, title: 'Bank follow-up' });
  const child = await api<{ id: string }>('POST', '/items', { parent_id: parent.id, title: 'Call the bank' });
  await page.goto(`/#/list/${home}`);

  await card(page, 'Bank follow-up').click({ button: 'right' });
  const menu = page.locator('.card-menu');
  await menu.getByRole('menuitem', { name: 'Move to…' }).click();
  const find = menu.getByLabel('Find a list');
  await expect(find).toBeFocused();
  await onTop(find);
  // Grouped by section; the current list is marked and cannot be picked.
  await expect(menu.locator('.menu-label', { hasText: `Move lab ${stamp}` })).toBeVisible();
  await expect(menu.getByRole('menuitem', { name: new RegExp(`Home ${stamp}.*here now`) })).toBeDisabled();
  await find.fill(`to do ${stamp}`);
  await expect(menu.getByRole('menuitem')).toHaveCount(2); // the back row and the one match
  if (shots) await page.screenshot({ path: join(shots, 'move-to-picker.png'), clip: (await menu.boundingBox())! });
  await find.press('Enter');

  await expect.poll(() => listOf(parent.id)).toBe(todo);
  await expect.poll(() => listOf(child.id)).toBe(todo);
  await expect(card(page, 'Bank follow-up')).toHaveCount(0);
});

test('the ⋯ menu has Move to…, and Back returns to the actions', async ({ page }) => {
  const { stamp, home, todo } = await lab('dots');
  const item = await api<{ id: string }>('POST', '/items', { list_id: home, title: 'Dots move me' });
  await page.goto(`/#/list/${home}`);
  const c = card(page, 'Dots move me');
  await c.hover();
  await c.getByRole('button', { name: 'Task actions' }).click();
  const menu = page.locator('.card-menu');
  await menu.getByRole('menuitem', { name: 'Move to…' }).click();
  await menu.getByRole('menuitem', { name: /Move “Dots move me” to…/ }).click();
  await expect(menu.getByRole('menuitem', { name: 'Open' })).toBeVisible();
  await menu.getByRole('menuitem', { name: 'Move to…' }).click();
  await menu.getByRole('menuitem', { name: `To Do ${stamp}` }).click();
  await expect.poll(() => listOf(item.id)).toBe(todo);
});

test('the open task window moves a task from its header and from the List field', async ({ page }) => {
  const { stamp, home, todo } = await lab('tw');
  const item = await api<{ id: string }>('POST', '/items', { list_id: home, title: 'Window move me' });
  await page.goto(`/#/list/${home}`);
  await card(page, 'Window move me').click();
  const tw = page.locator('.tw');
  await tw.getByRole('button', { name: 'Move to another list' }).click();
  await onTop(page.getByLabel('Find a list'));
  if (shots) await page.screenshot({ path: join(shots, 'move-to-window.png'), clip: (await tw.boundingBox())! });
  await page.getByLabel('Find a list').click();
  await page.getByLabel('Find a list').fill(`To Do ${stamp}`);
  await page.getByLabel('Find a list').press('Enter');
  await expect.poll(() => listOf(item.id)).toBe(todo);
  // The window stays open on the task and shows where it is now.
  await expect(tw.getByRole('button', { name: 'Move to list' })).toContainText(`To Do ${stamp}`);

  await tw.getByRole('button', { name: 'Move to list' }).click();
  await page.getByRole('menuitem', { name: `Home ${stamp}` }).click();
  await expect.poll(() => listOf(item.id)).toBe(home);
});

test('the selection bar moves several tasks with the same picker', async ({ page }) => {
  const { stamp, home, todo } = await lab('bulk');
  const a = await api<{ id: string }>('POST', '/items', { list_id: home, title: 'Bulk one' });
  const b = await api<{ id: string }>('POST', '/items', { list_id: home, title: 'Bulk two' });
  await page.goto(`/#/list/${home}`);
  for (const t of ['Bulk one', 'Bulk two']) {
    await card(page, t).click({ button: 'right' });
    await page.locator('.card-menu').getByRole('menuitem', { name: 'Select' }).click();
  }
  const bar = page.locator('.bulk-bar');
  await bar.getByRole('button', { name: 'Move to list' }).click();
  await bar.getByLabel('Find a list').fill(`to do ${stamp}`);
  await bar.getByLabel('Find a list').press('Enter');
  await expect.poll(() => listOf(a.id)).toBe(todo);
  await expect.poll(() => listOf(b.id)).toBe(todo);
});

test('in the table layout the ⋯ menu sits above the rows below it, and Move to… works', async ({ page }) => {
  const { stamp, home, todo } = await lab('table');
  const ids: string[] = [];
  for (const t of ['Table first', 'Table second', 'Table third', 'Table fourth'])
    ids.push((await api<{ id: string }>('POST', '/items', { list_id: home, title: t })).id);
  await page.goto(`/#/list/${home}`);
  // Exact: a section row's chat link is titled "Open #<section> chat", and this lab's name holds "table".
  await page.getByTitle('Table', { exact: true }).first().click();
  const row = page.locator('[data-item]', { hasText: 'Table first' });
  await row.hover();
  await row.getByRole('button', { name: 'Task actions' }).click();
  const moveItem = page.locator('.card-menu').getByRole('menuitem', { name: 'Move to…' });
  await onTop(moveItem);
  await moveItem.click();
  await onTop(page.getByLabel('Find a list'));
  await page.getByRole('menuitem', { name: `To Do ${stamp}` }).click();
  await expect.poll(() => listOf(ids[0])).toBe(todo);
  await expect.poll(() => listOf(ids[1])).toBe(home);
  // Back to the board for the specs after this one (the layout is saved per viewer).
  await page.getByTitle('Board (b)').first().click();
});
