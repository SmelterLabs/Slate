// The favorites bar: pin from the nav and view menu, one-click open, live from the API, drag to
// reorder, unpin, and the phone row. Every test removes its favorites so later files see none.
import { expect, test, type Page } from '@playwright/test';
import { api, screenshots, signedIn, signIn } from './support';

const shot = screenshots();

interface Fav {
  id: string;
  kind: string;
  target_id: string;
}

async function clearFavorites() {
  for (const f of await api<Fav[]>('GET', '/favorites')) await api('DELETE', `/favorites/${f.id}`);
}

async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', {
    name: `${name} section`,
    audience: 'work',
  });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  return { sectionId: section.id, listId: list.id };
}

test.beforeEach(clearFavorites);
test.afterAll(clearFavorites);

const bar = (page: Page) => page.getByRole('toolbar', { name: 'Favorites' });

test('star a list in the nav, open it from the bar in one click, unstar it', async ({ page }) => {
  const { listId } = await scratch('Backlog');
  await signedIn(page, '/', { ready: true });
  await expect(bar(page)).toContainText('Star a list');

  const row = page.locator('.nav-list[data-list="Backlog"]');
  await row.hover();
  await row.getByRole('button', { name: 'Add Backlog to favorites' }).click();
  const chip = bar(page).getByRole('link', { name: 'Backlog' });
  await expect(chip).toBeVisible();

  await page.getByRole('link', { name: /Everything/ }).click();
  await chip.click();
  await expect(page).toHaveURL(new RegExp(`#/list/${listId}$`));
  await expect(chip).toHaveAttribute('aria-current', 'page');

  await row.hover();
  await row.getByRole('button', { name: 'Remove Backlog from favorites' }).click();
  await expect(chip).toHaveCount(0);
  expect(await api<Fav[]>('GET', '/favorites')).toEqual([]);
});

test('a view pinned over the API appears live; drag reorders; × unpins', async ({ page }) => {
  const { listId } = await scratch('To read');
  const view = await api<{ id: string }>('POST', '/views', {
    name: 'Reading board',
    scope_kind: 'list',
    scope_id: listId,
    config: { layout: 'board' },
  });
  await api('POST', '/favorites', { kind: 'list', target_id: listId });
  await signedIn(page, '/', { ready: true });
  await expect(bar(page).getByRole('link', { name: 'To read' })).toBeVisible();

  // An agent pins the view: the bar shows it without a reload.
  await api('POST', '/favorites', { kind: 'view', target_id: view.id });
  const viewChip = bar(page).getByRole('link', { name: 'Reading board' });
  await expect(viewChip).toBeVisible();
  await viewChip.click();
  await expect(page).toHaveURL(new RegExp(`#/list/${listId}/v/${view.id}$`));
  await expect(page.locator('.view-tab.on .fav-mark')).toBeVisible();

  await shot(page, 'favorites-bar', { scheme: 'dark', clip: { x: 0, y: 0, width: 1280, height: 200 } });

  // Drag the view chip onto the list chip's left half: it lands first.
  const listChip = bar(page).locator('.fav-chip[data-fav="list"]');
  const box = (await listChip.boundingBox())!;
  await bar(page).locator('.fav-chip[data-fav="view"]').dragTo(listChip, {
    targetPosition: { x: 4, y: box.height / 2 },
  });
  await expect(bar(page).locator('.fav-chip').first()).toHaveAttribute('data-fav', 'view');
  await expect
    .poll(async () => (await api<Fav[]>('GET', '/favorites')).map((f) => f.kind))
    .toEqual(['view', 'list']);

  await bar(page).locator('.fav-chip[data-fav="view"]').hover();
  await page.getByRole('button', { name: 'Remove Reading board from favorites' }).click();
  await expect(viewChip).toHaveCount(0);
  await expect.poll(async () => (await api<Fav[]>('GET', '/favorites')).length).toBe(1);
});

test('the view menu pins the current view', async ({ page }) => {
  const { listId } = await scratch('Backlog views');
  const view = await api<{ id: string }>('POST', '/views', {
    name: 'Queue board',
    scope_kind: 'list',
    scope_id: listId,
    config: { layout: 'board' },
  });
  await signedIn(page, `/#/list/${listId}/v/${view.id}`, { ready: true });
  await page.locator('.view-tab.on').getByTitle('View menu').click();
  await page.getByRole('button', { name: 'Add to favorites' }).click();
  await expect(bar(page).getByRole('link', { name: 'Queue board' })).toBeVisible();
});

test('on a phone the bar is one scrolling row of 40 px targets under the top bar', async ({
  page,
}) => {
  const { listId } = await scratch('Phone pin');
  await api('POST', '/favorites', { kind: 'list', target_id: listId });
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.goto('/');
  const chip = bar(page).getByRole('link', { name: 'Phone pin' });
  await expect(chip).toBeVisible();
  expect((await chip.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  const top = (await page.locator('.topbar').boundingBox())!;
  expect((await bar(page).boundingBox())!.y).toBeGreaterThanOrEqual(top.y + top.height - 1);
  await chip.click();
  await expect(page).toHaveURL(new RegExp(`#/list/${listId}$`));
});

test('s stars the card under the pointer; its chip opens the task', async ({ page }) => {
  const { listId } = await scratch('Star tasks');
  await api('POST', '/items', { list_id: listId, title: 'Call the plumber' });
  await signedIn(page, `/#/list/${listId}`, { ready: true });
  await page.locator('.card', { hasText: 'Call the plumber' }).hover();
  await page.keyboard.press('s');
  const chip = bar(page).getByRole('link', { name: 'Call the plumber' });
  await expect(chip).toBeVisible();
  await page.getByRole('link', { name: /Everything/ }).click();
  await chip.click();
  await expect.poll(() => page.evaluate(() => location.hash)).toMatch(/^#\/everything(\?task=.*)?$/);
  await expect(page.getByLabel('Title').or(page.getByTitle('Click to rename'))).toBeVisible();
});
