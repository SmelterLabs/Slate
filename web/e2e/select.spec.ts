// Multi-select shows what is selected: every selected card or row has a ticked box and a
// selected outline while you pick more, in Board, List and Table, on a PC and on a phone.
import { expect, test, type Page } from '@playwright/test';
import { api, screenshots, signIn } from './support';

const shot = screenshots('SLATE_SHOTS_DIR');

// SLATE_SHOTS_DIR=<dir> writes screenshots there.

// Each test's section is archived after it, so its tasks stay off later tests' Everything board.
const made: string[] = [];
test.afterEach(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/sections/${id}`, { archived: true });
});

async function makeList(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name, audience: 'personal' });
  made.push(section.id);
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  const ids: string[] = [];
  for (const title of ['Pick me', 'And me', 'Not me']) {
    ids.push((await api<{ id: string }>('POST', '/items', { list_id: list.id, title })).id);
  }
  return { listId: list.id, ids };
}

async function open(page: Page, listId: string) {
  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  await expect(page.locator('.list-page')).toBeVisible();
}

test.describe('PC', () => {
  test.use({ viewport: { width: 1400, height: 900 } });

  for (const layout of ['Board', 'List', 'Table'] as const) {
    test(`${layout}: selected tasks show a ticked box while you pick more`, async ({ page }) => {
      const { listId, ids } = await makeList(`Select ${layout}`);
      await open(page, listId);
      if (layout !== 'Board') await page.getByRole('button', { name: layout, exact: true }).click();
      const el = (id: string) => page.locator(`[data-item="${id}"]`).first();
      await expect(el(ids[0])).toBeVisible();

      // First pick from the box, second by clicking the task while selecting.
      await el(ids[0]).hover();
      await el(ids[0]).getByLabel('Select').click();
      await el(ids[1]).getByLabel('Select').click();
      await page.mouse.move(5, 890);

      for (const id of ids.slice(0, 2)) {
        await expect(el(id)).toHaveClass(/selected/);
        const box = el(id).getByLabel('Select');
        await expect(box).toBeChecked();
        await expect(box).toBeVisible();
        expect(Number(await box.evaluate((b) => getComputedStyle(b).opacity))).toBe(1);
      }
      // The unselected one shows an empty box, so you can see where to tick next.
      const other = el(ids[2]).getByLabel('Select');
      await expect(other).not.toBeChecked();
      expect(Number(await other.evaluate((b) => getComputedStyle(b).opacity))).toBe(1);
      await expect(page.getByRole('region', { name: 'Bulk edit' })).toContainText('2 selected');
      await shot(page, `select-${layout.toLowerCase()}-pc`);

      // Untick one: the box clears and the bar counts one.
      await el(ids[0]).getByLabel('Select').click();
      await expect(el(ids[0]).getByLabel('Select')).not.toBeChecked();
      await expect(el(ids[0])).not.toHaveClass(/selected/);
      await expect(page.getByRole('region', { name: 'Bulk edit' })).toContainText('1 selected');
    });
  }
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('Board: selected cards show a ticked box on a phone too', async ({ page }) => {
    const { listId, ids } = await makeList('Select phone');
    await open(page, listId);
    const el = (id: string) => page.locator(`.card[data-item="${id}"]`);
    // A phone has no hover: hold a card (its menu) and pick Select; then the boxes show.
    await expect(el(ids[0]).getByLabel('Select')).toBeHidden();
    await el(ids[0]).dispatchEvent('contextmenu');
    await page.getByRole('menuitem', { name: 'Select', exact: true }).tap();
    // While selecting, a tap on a card adds it.
    await el(ids[1]).tap();
    for (const id of ids.slice(0, 2)) {
      await expect(el(id)).toHaveClass(/selected/);
      await expect(el(id).getByLabel('Select')).toBeChecked();
      await expect(el(id).getByLabel('Select')).toBeVisible();
    }
    await expect(el(ids[2]).getByLabel('Select')).not.toBeChecked();
    await expect(el(ids[2]).getByLabel('Select')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Bulk edit' })).toContainText('2 selected');
    await shot(page, 'select-board-phone');
    // Ticking the third box adds it, and the tap stays on the box: the card's menu arms on that
    // tap, and must not shift Complete under the finger.
    await el(ids[2]).getByLabel('Select').tap();
    await expect(el(ids[2]).getByLabel('Select')).toBeChecked();
    await expect(el(ids[2])).not.toHaveClass(/closed/);
    await expect(page.getByRole('region', { name: 'Bulk edit' })).toContainText('3 selected');
  });

  test('List: selected rows show a ticked box on a phone', async ({ page }) => {
    const { listId, ids } = await makeList('Select phone list');
    await open(page, listId);
    await page.getByRole('button', { name: 'List', exact: true }).click();
    const el = (id: string) => page.locator(`.row[data-item="${id}"]`);
    await el(ids[0]).getByLabel('Select').tap();
    await el(ids[1]).tap();
    for (const id of ids.slice(0, 2)) {
      await expect(el(id)).toHaveClass(/selected/);
      await expect(el(id).getByLabel('Select')).toBeChecked();
    }
    await shot(page, 'select-list-phone');
  });
});
