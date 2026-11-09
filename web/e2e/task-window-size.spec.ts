// The task window's size and place: the corner grip resizes it and it stays centered; the
// activity column widens leftward from its grip; the dock button parks it on the right third or
// quarter (no scrim, the board usable) and back in the center; all of it survives a reload.
import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

test.use({ viewport: { width: 1600, height: 1000 } });

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

async function openCard(page: Page, id: string) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/list/${seed().listId}?task=${id}`);
  const win = page.locator('.tw');
  await expect(win.locator('.title')).toBeVisible();
  return win;
}

async function box(page: Page, sel: string) {
  return (await page.locator(sel).boundingBox())!;
}

test('the corner grip resizes the centered window about its center, and the size is remembered', async ({ page }) => {
  const item = await api<{ id: string }>('POST', '/items', { list_id: seed().listId, title: 'Sizeable' });
  await openCard(page, item.id);
  const before = await box(page, '.tw');
  expect(Math.round(before.x + before.width / 2)).toBe(800);
  expect(before.width).toBe(1280);

  const grip = await box(page, '.tw-corner-grip');
  await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
  await page.mouse.down();
  await page.mouse.move(grip.x - 100, grip.y - 60, { steps: 5 });
  await page.mouse.up();
  const after = await box(page, '.tw');
  expect(after.width).toBeLessThan(before.width - 150);
  expect(after.height).toBeLessThan(before.height - 80);
  // Still centered.
  expect(Math.abs(after.x + after.width / 2 - 800)).toBeLessThan(2);
  expect(Math.abs(after.y + after.height / 2 - 500)).toBeLessThan(2);

  await page.reload();
  await expect(page.locator('.tw .title')).toBeVisible();
  const again = await box(page, '.tw');
  expect(Math.round(again.width)).toBe(Math.round(after.width));
  expect(Math.round(again.height)).toBe(Math.round(after.height));

  // Double-click puts the default back.
  await page.locator('.tw-corner-grip').dblclick();
  expect((await box(page, '.tw')).width).toBe(1280);
});

test('the activity column widens leftward from its grip and remembers its width', async ({ page }) => {
  const item = await api<{ id: string }>('POST', '/items', { list_id: seed().listId, title: 'Wide activity' });
  await openCard(page, item.id);
  const act = await box(page, '.tw .activity');
  expect(Math.round(act.width)).toBe(420);

  const grip = await box(page, '.tw-activity-grip');
  await page.mouse.move(grip.x + grip.width / 2, grip.y + 200);
  await page.mouse.down();
  await page.mouse.move(grip.x + grip.width / 2 - 200, grip.y + 200, { steps: 5 });
  await page.mouse.up();
  const wider = await box(page, '.tw .activity');
  // The window's 1px border sits between the pointer's reference edge and the column.
  expect(Math.abs(wider.width - 620)).toBeLessThanOrEqual(2);
  // The window itself did not move or grow.
  expect((await box(page, '.tw')).width).toBe(1280);

  await page.reload();
  await expect(page.locator('.tw .title')).toBeVisible();
  expect(Math.abs((await box(page, '.tw .activity')).width - 620)).toBeLessThanOrEqual(2);

  await page.locator('.tw-activity-grip').dblclick();
  expect(Math.round((await box(page, '.tw .activity')).width)).toBe(420);
});

test('the dock button parks the window on the right third or quarter, with the board live beside it, and back', async ({ page }) => {
  const item = await api<{ id: string }>('POST', '/items', { list_id: seed().listId, title: 'Dockable' });
  const win = await openCard(page, item.id);
  await expect(page.locator('.tw-scrim')).toHaveCount(1);
  await expect(win).toHaveAttribute('aria-modal', 'true');

  await win.getByTitle('Where the window sits').click();
  await page.getByRole('menuitemradio', { name: 'Right third' }).click();
  let b = await box(page, '.tw');
  expect(Math.round(b.width)).toBe(Math.round(1600 / 3));
  expect(Math.round(b.x + b.width)).toBe(1600);
  expect(b.y).toBe(0);
  expect(Math.round(b.height)).toBe(1000);
  await expect(page.locator('.tw-scrim')).toHaveCount(0);
  await expect(win).toHaveAttribute('aria-modal', 'false');
  // A third of 1600 is under 760: the activity stacks under the task, no grips to drag.
  await expect(win).toHaveClass(/stacked/);
  await expect(page.locator('.tw-corner-grip')).toHaveCount(0);
  await expect(page.locator('.tw-activity-grip')).toHaveCount(0);
  // The board beside it is usable: another card opens from it.
  const other = await api<{ id: string }>('POST', '/items', { list_id: seed().listId, title: 'Beside the dock' });
  await page.locator('.card', { hasText: 'Beside the dock' }).first().click();
  await expect(win.locator('.title')).toHaveValue('Beside the dock');
  expect(other.id).toBeTruthy();

  await page.reload();
  await expect(page.locator('.tw .title')).toBeVisible();
  b = await box(page, '.tw');
  expect(Math.round(b.width)).toBe(Math.round(1600 / 3));

  await win.getByTitle('Where the window sits').click();
  await page.getByRole('menuitemradio', { name: 'Right quarter' }).click();
  b = await box(page, '.tw');
  expect(Math.round(b.width)).toBe(400);
  expect(Math.round(b.x + b.width)).toBe(1600);

  await win.getByTitle('Where the window sits').click();
  await page.getByRole('menuitemradio', { name: 'Centered' }).click();
  b = await box(page, '.tw');
  expect(b.width).toBe(1280);
  expect(Math.round(b.x + b.width / 2)).toBe(800);
  await expect(page.locator('.tw-scrim')).toHaveCount(1);
  await expect(page.locator('.tw-corner-grip')).toHaveCount(1);
});
