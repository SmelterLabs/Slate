// The comment boxes share one @mention picker and one send key: Enter sends, but not the Enter
// that confirms an input-method composition (Japanese, Chinese, Korean…).
import { expect, test, type Locator } from '@playwright/test';
import { api, countRequests, signIn } from './support';

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
  await expect(box).toHaveValue('にほんご');

  await box.fill('');
  await box.pressSequentially('hi @ale');
  await expect(page.getByRole('listbox', { name: 'Mention someone' })).toBeVisible();
  await box.press('Enter');
  await expect(box).toHaveValue('hi @alex ');
  await box.press('Enter');
  await expect(page.locator('.tw article.comment', { hasText: 'hi @alex' })).toBeVisible();
  // That comment is the only one: the composing Enter, sent before it, posted nothing.
  await expect(page.locator('.tw article.comment')).toHaveCount(1);
});

test('the doc discussion box: Enter while composing does not send', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: `IME doc ${Date.now()}`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { name: 'Files', section_id: section.id, kind: 'drawer' });
  const doc = await api<{ id: string }>('POST', '/docs', { title: 'Plan', list_id: list.id, body_md: 'Read this.' });
  await page.goto(`/#/list/${list.id}?doc=${doc.id}`);
  const discussion = page.getByRole('button', { name: 'Discussion', exact: true });
  if (await discussion.isVisible()) await discussion.click();
  const box = page.getByLabel('Discussion comment');
  const posts = countRequests(page, 'POST', `/docs/${doc.id}/comments`);
  await box.fill('かんじ');
  await composingEnter(box);
  await expect(box).toHaveValue('かんじ');
  await box.press('Enter');
  await expect(box).toHaveValue('');
  await expect(page.locator('.dd-feed, [aria-label="Discussion"]').getByText('かんじ').first()).toBeVisible();
  // One post, from the real Enter: the composing Enter before it sent nothing.
  expect(posts()).toBe(1);
});
