import { expect, test, type Page } from '@playwright/test';
import { api, countRequests, seed } from './support';

async function login(page: Page, path: string, state = 'active') {
  await page.addInitScript(({ token, state }) => {
    localStorage.setItem('slate.token', token);
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state === 'hidden' ? 'hidden' : 'visible' });
    document.hasFocus = () => state !== 'unfocused';
  }, { token: seed().alex, state });
  await page.goto(path);
}

test('opening a channel Inbox group acknowledges only the exposed thread, not another root', async ({ page }) => {
  const section = await api('POST', '/sections', { name: 'Inbox exact thread review', audience: 'work' });
  const key = `section:${section.id}`;
  const first = await api('POST', `/channels/${key}/messages`, { body_md: 'First root' });
  const second = await api('POST', `/channels/${key}/messages`, { body_md: 'Second root' });
  const unseen = await api('POST', `/channels/${key}/messages`, { body_md: '@alex Unopened thread notification', parent_id: first.id }, seed().agent);
  const opened = await api('POST', `/channels/${key}/messages`, { body_md: '@alex Selected thread notification', parent_id: second.id }, seed().agent);
  const entries = async () => (await api('GET', '/inbox?limit=200')).filter((e: { channel: string }) => e.channel === key);
  expect((await entries()).filter((e: { read: boolean }) => !e.read)).toHaveLength(2);
  await login(page, '/#/inbox');
  await page.locator('.inbox-main').filter({ hasText: section.name }).click();
  const thread = page.getByRole('complementary', { name: 'Thread' });
  await expect(thread.locator(`[data-message="${opened.id}"]`)).toBeInViewport();
  await expect(thread.locator(`[data-message="${unseen.id}"]`)).toHaveCount(0);
  await expect.poll(async () => (await api('GET', `/channels/${key}`)).unread_threads).toEqual({ [first.id]: 1 });
  await expect.poll(async () => (await entries()).map((e: { event: { subject_id: string }; read: boolean }) => ({ id: e.event.subject_id, read: e.read }))).toEqual([
    { id: opened.id, read: true }, { id: unseen.id, read: false },
  ]);
  // Intentional bulk acknowledgement remains available, independent of chat navigation.
  await page.goto('/#/inbox');
  await page.getByRole('button', { name: 'Mark all read', exact: true }).click();
  await expect.poll(async () => (await entries()).every((e: { read: boolean }) => e.read)).toBe(true);
});

for (const state of ['active', 'hidden', 'unfocused']) {
  test(`task overlay dismissal rechecks unchanged message geometry (${state})`, async ({ page }) => {
    const section = await api('POST', '/sections', { name: `Overlay review ${state}`, audience: 'work' });
    const key = `section:${section.id}`;
    const item = await api('POST', '/items', { title: `Cover chat ${state}`, list_id: seed().listId });
    const message = await api('POST', `/channels/${key}/messages`, { body_md: 'Visible rectangle behind the task dialog' }, seed().agent);
    // The board's clock is ours to run: past the exposure dwell and the read batch in one step.
    await page.clock.install();
    const reads = countRequests(page, 'POST', `/channels/${key}/read`);
    await login(page, `/#/section/${section.id}/chat?task=${item.id}`, state);
    await expect(page.getByRole('button', { name: 'Close task', exact: true })).toBeVisible();
    const article = page.locator(`.chat-main [data-message="${message.id}"]`);
    await expect(article).toBeInViewport({ ratio: 1 });
    const before = await article.boundingBox();
    // Actual task scrim covers the hit-test samples, not a synthetic overlay.
    expect(await article.evaluate(node => {
      const r = node.getBoundingClientRect();
      return [0.2, 0.8].every(y => !node.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height * y)));
    })).toBe(true);
    await page.clock.runFor(2000);
    expect(reads(), 'covered by the task window: nothing is read').toBe(0);
    expect((await api('GET', `/channels/${key}`)).unread).toBe(1);
    await page.getByRole('button', { name: 'Close task', exact: true }).click({ position: { x: 5, y: 5 } });
    await expect(page.getByRole('button', { name: 'Close task', exact: true })).toHaveCount(0);
    expect(await article.boundingBox()).toEqual(before);
    if (state !== 'active') {
      await page.clock.runFor(2000);
      expect(reads(), `uncovered but ${state}: nothing is read`).toBe(0);
      expect((await api('GET', `/channels/${key}`)).unread).toBe(1);
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
        document.hasFocus = () => true;
        document.dispatchEvent(new Event('visibilitychange'));
        window.dispatchEvent(new Event('focus'));
      });
    }
    await expect.poll(async () => (await api('GET', `/channels/${key}`)).unread).toBe(0);
  });
}
