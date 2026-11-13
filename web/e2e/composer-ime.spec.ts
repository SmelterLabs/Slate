// The comment boxes share one @mention picker and one send key: Enter sends, but not the Enter
// that confirms an input-method composition (Japanese, Chinese, Korean…).
import { expect, test, type Locator } from '@playwright/test';
import { api, signIn } from './support';

/** Press Enter as an input method does while it is still composing. */
async function composingEnter(box: Locator) {
  await box.evaluate((el) => {
    for (const type of ['keydown', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 229, isComposing: true, bubbles: true, cancelable: true }));
    }
  });
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('the task comment box: Enter while composing does not send, the picker inserts a handle', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: `IME ${Date.now()}`, audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Notes' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Write the note' });
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const box = page.getByLabel('Add comment');
  await box.fill('にほんご');
  await composingEnter(box);
  await page.waitForTimeout(300);
  await expect(box).toHaveValue('にほんご');
  await expect(page.locator('.tw article.comment')).toHaveCount(0);

  await box.fill('');
  await box.pressSequentially('hi @ale');
  await expect(page.getByRole('listbox', { name: 'Mention someone' })).toBeVisible();
  await box.press('Enter');
  await expect(box).toHaveValue('hi @alex ');
  await box.press('Enter');
  await expect(page.locator('.tw article.comment', { hasText: 'hi @alex' })).toBeVisible();
});

test('the doc discussion box: Enter while composing does not send', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: `IME doc ${Date.now()}`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { name: 'Files', section_id: section.id, kind: 'drawer' });
  const doc = await api<{ id: string }>('POST', '/docs', { title: 'Plan', list_id: list.id, body_md: 'Read this.' });
  await page.goto(`/#/list/${list.id}?doc=${doc.id}`);
  const discussion = page.getByRole('button', { name: 'Discussion', exact: true });
  if (await discussion.isVisible()) await discussion.click();
  const box = page.getByLabel('Discussion comment');
  await box.fill('かんじ');
  await composingEnter(box);
  await page.waitForTimeout(300);
  await expect(box).toHaveValue('かんじ');
  await box.press('Enter');
  await expect(box).toHaveValue('');
  await expect(page.locator('.dd-feed, [aria-label="Discussion"]').getByText('かんじ').first()).toBeVisible();
});
