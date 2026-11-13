// With "Subtasks" on, a subtask card says whose it is and a thin curve joins it to its parent,
// in the same column (an elbow down the indent) or across columns.
import { expect, test } from '@playwright/test';
import { api, screenshots, signIn } from './support';

const shot = screenshots('SLATE_SCREENSHOTS', { scheme: 'dark', selector: '.kanban' });
const selectShot = screenshots('SLATE_SHOTS_DIR', { selector: '.kanban' });

test('subtask cards are labelled and linked to their parent', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Linked', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Kitchen' });
  const parent = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Redo the kitchen' });
  const same = await api<{ id: string }>('POST', '/items', { parent_id: parent.id, title: 'Pick tiles' });
  const moved = await api<{ id: string }>('POST', '/items', { parent_id: parent.id, title: 'Call the plumber' });
  await api('PATCH', `/items/${moved.id}`, { status: 'In progress' });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const ids: Record<string, string> = { 'Redo the kitchen': parent.id, 'Pick tiles': same.id, 'Call the plumber': moved.id };
  const card = (t: string) => page.locator(`.card[data-item="${ids[t]}"]`);
  await expect(card('Redo the kitchen')).toBeVisible();
  await expect(card('Pick tiles')).toHaveCount(0);

  const toggle = page.getByRole('button', { name: 'Subtasks', exact: true });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');

  // Labelled, tinted and indented.
  await expect(card('Pick tiles')).toHaveClass(/subtask/);
  await expect(card('Pick tiles').locator('.card-parent')).toContainText('Subtask of Redo the kitchen');
  await expect(card('Redo the kitchen')).not.toHaveClass(/subtask/);

  // One curve per subtask, both ending at the parent.
  const links = page.locator(`.subtask-links g[data-parent="${parent.id}"]`);
  await expect(links).toHaveCount(2);
  await expect(page.locator(`.subtask-links g[data-child="${same.id}"] path`)).toHaveAttribute('d', /^M.+C/);
  await expect(page.locator(`.subtask-links g[data-child="${moved.id}"]`)).toHaveCount(1);

  await page.mouse.move(900, 600);
  await shot(page, 'subtask-links-rest');

  // Hovering the parent lights up its lines.
  await card('Redo the kitchen').hover();
  await expect(page.locator('.subtask-links g.hot')).toHaveCount(2);
  await shot(page, 'subtask-links');

  // The label opens the parent.
  await card('Pick tiles').locator('.card-parent').click();
  await expect(page.locator('.tw')).toHaveAttribute('aria-label', 'Task: Redo the kitchen');
  await page.keyboard.press('Escape');

  // Subtasks off: the lines go with them.
  await toggle.click();
  await expect(card('Pick tiles')).toHaveCount(0);
  await expect(page.locator('.subtask-links g')).toHaveCount(0);
});

// The select box sits on the card's top-left corner; Complete and ⋯ stay top right. It must
// not cover the title, the "Subtask of" line, or the link that meets the subtask's left side.
test('the select box is top left, clear of the title and the subtask line', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Corner', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Garden' });
  const parent = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Plant the beds' });
  const kid = await api<{ id: string }>('POST', '/items', { parent_id: parent.id, title: 'Buy compost' });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Subtasks', exact: true }).click();

  type R = { x: number; y: number; width: number; height: number };
  const apart = (a: R, b: R) =>
    a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;

  for (const id of [parent.id, kid.id]) {
    const card = page.locator(`.card[data-item="${id}"]`);
    await card.hover();
    const box = card.locator('.card-select');
    await expect(box.getByLabel('Select')).toBeVisible();
    const c = (await card.boundingBox())!;
    const b = (await box.boundingBox())!;
    const tools = (await card.locator('.card-tools').boundingBox())!;
    const title = (await card.locator('.card-title').boundingBox())!;
    // Top-left corner, with Complete and the menu still on the right.
    expect(b.x).toBeLessThan(c.x + 4);
    expect(b.y).toBeLessThan(c.y + 4);
    expect(tools.x).toBeGreaterThan(c.x + c.width / 2);
    await expect(card.locator('.card-tools').getByRole('button', { name: 'Complete' })).toBeVisible();
    expect(apart(b, title)).toBe(true);
    if (id === kid.id) {
      expect(apart(b, (await card.locator('.card-parent').boundingBox())!)).toBe(true);
      // The link meets the card's left side 16px down; the box ends above that.
      expect(b.y + b.height).toBeLessThan(c.y + 16);
    }
    // Not clipped by the column: fully inside the scrolling cards area.
    const area = (await card.locator('xpath=..').boundingBox())!;
    expect(b.y).toBeGreaterThanOrEqual(area.y);
    expect(b.x).toBeGreaterThanOrEqual(area.x);
  }
  await page.mouse.move(900, 600);

  // Ticking it still selects.
  const first = page.locator(`.card[data-item="${parent.id}"]`);
  await first.hover();
  await first.getByLabel('Select').click();
  await expect(first).toHaveClass(/selected/);
  await page.mouse.move(900, 600);
  await expect(first.getByLabel('Select')).toBeVisible();
  await selectShot(page, 'select-corner');
  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

// A subtask with a long parent title must not stretch its column: every column keeps the same
// width, and the subtask card is narrower than a regular card (indented), never wider.
test('a subtask with a long parent keeps its column the same width', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Widths', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Calls' });
  const parent = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Call Sam at the venue: what do they need from us for the spring event? Pricing, the timeline, and who signs off',
  });
  const sub = await api<{ id: string }>('POST', '/items', {
    parent_id: parent.id,
    title: 'Schedule the call with Sam (for today or tomorrow)',
  });
  await api('PATCH', `/items/${sub.id}`, { status: 'In progress' });
  const regular = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'A plain card' });
  await api('PATCH', `/items/${regular.id}`, { status: 'In progress' });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Subtasks', exact: true }).click();
  const subCard = page.locator(`.card[data-item="${sub.id}"]`);
  await expect(subCard).toBeVisible();

  const widths = await page
    .locator('.cu-board .column:not(.collapsed)')
    .evaluateAll((cols) => cols.map((c) => Math.round(c.getBoundingClientRect().width)));
  expect(widths.length).toBeGreaterThan(1);
  expect(new Set(widths).size, `column widths ${widths}`).toBe(1);

  const subBox = (await subCard.boundingBox())!;
  const regBox = (await page.locator(`.card[data-item="${regular.id}"]`).boundingBox())!;
  expect(subBox.width).toBeLessThan(regBox.width);
  expect(subBox.x).toBeGreaterThan(regBox.x);

  // The long parent name is cut with an ellipsis, inside the card.
  const name = subCard.locator('.card-parent-name');
  const fit = await name.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
  expect(fit.sw).toBeGreaterThan(fit.cw);
  const nameBox = (await name.boundingBox())!;
  expect(nameBox.x + nameBox.width).toBeLessThanOrEqual(subBox.x + subBox.width);
});
