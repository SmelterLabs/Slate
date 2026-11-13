import { expect, test, type Page } from '@playwright/test';
import { api, BASE_URL, countRequests, seed, signIn } from './support';

async function login(page: Page, token: string, path: string) {
  await signIn(page, token);
  await page.goto(path);
}

test('background mount preserves unread; focused viewport excludes unopened replies and old messages across devices', async ({ browser }) => {
  const section = await api('POST', '/sections', { name: 'Workshop', audience: 'work' });
  const key = `section:${section.id}`;
  const account = await api('POST', '/accounts', { handle: 'exposure-reader', display_name: 'Reader', kind: 'person' });
  const { token } = await api('POST', `/accounts/${account.id}/tokens`, { scopes: ['read', 'write'], label: 'isolated test' });
  const root = await api('POST', `/channels/${key}/messages`, { body_md: 'Earlier root' });
  const reply = await api('POST', `/channels/${key}/messages`, { body_md: 'Reply stays unread', parent_id: root.id });
  for (let i = 0; i < 52; i++) await api('POST', `/channels/${key}/messages`, { body_md: `Workshop update ${i}` });
  const context = await browser.newContext();
  const page = await context.newPage();
  // Browser lifecycle is deterministic in headless mode; model a hidden, unfocused window.
  await page.addInitScript(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.hasFocus = () => false;
  });
  // The board's clock is ours to run: past the exposure dwell and the read batch in one step.
  await page.clock.install();
  const reads = countRequests(page, 'POST', `/channels/${key}/read`);
  await login(page, token, `/#/section/${section.id}/chat`);
  await expect(page.getByText('Workshop update 51', { exact: true })).toBeVisible();
  await page.clock.runFor(2000);
  expect(reads(), 'hidden and unfocused: nothing is read').toBe(0);
  expect((await api('GET', `/channels/${key}`, undefined, token)).unread).toBe(54);
  // Either condition by itself must block both old and newly arriving messages.
  await page.evaluate(() => { document.hasFocus = () => true; window.dispatchEvent(new Event('focus')); });
  await page.clock.runFor(2000);
  expect(reads(), 'focused but hidden: nothing is read').toBe(0);
  expect((await api('GET', `/channels/${key}`, undefined, token)).unread).toBe(54);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.hasFocus = () => false;
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await api('POST', `/channels/${key}/messages`, { body_md: 'Arrived while unfocused' });
  await expect(page.getByText('Arrived while unfocused', { exact: true })).toBeVisible();
  await page.clock.runFor(2000);
  expect(reads(), 'visible but unfocused: nothing is read').toBe(0);
  expect((await api('GET', `/channels/${key}`, undefined, token)).unread).toBe(55);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.hasFocus = () => true;
    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(async () => (await api('GET', `/channels/${key}`, undefined, token)).unread).toBeLessThan(54);
  const state = await api('GET', `/channels/${key}`, undefined, token);
  expect(state.unread).toBeGreaterThan(1);
  expect(state.first_unread_id).toBe(root.id);
  expect(state.unread_threads).toEqual({ [root.id]: 1 });
  // A second independent device has the same durable boundary, and can reach the old thread.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mobile = await phone.newPage();
  await login(mobile, token, `/#/section/${section.id}/chat`);
  await mobile.bringToFront();
  await mobile.getByRole('button', { name: /First unread/ }).click();
  await expect(mobile.locator('.chat-main [data-message="' + root.id + '"]')).toBeInViewport();
  await mobile.getByRole('button', { name: /1 unread repl/ }).click();
  await expect(mobile.getByRole('complementary', { name: 'Thread' }).getByText('Reply stays unread')).toBeVisible();
  await expect.poll(async () => (await api('GET', `/channels/${key}`, undefined, token)).unread_threads).toEqual({});
  expect((await api('GET', `/channels/${key}`, undefined, token)).unread).toBeGreaterThan(0);
  expect(reply.id).toBeTruthy();
  await phone.close(); await context.close();
});

test('First unread opens the earliest unread reply, not the bottom of a long thread', async ({ page }) => {
  const section = await api('POST', '/sections', { name: 'Thread review', audience: 'work' });
  const key = `section:${section.id}`;
  const root = await api('POST', `/channels/${key}/messages`, { body_md: 'Review discussion' }, seed().agent);
  const first = await api('POST', `/channels/${key}/messages`, { body_md: 'Earliest pending reply', parent_id: root.id }, seed().agent);
  for (let i = 0; i < 24; i++) await api('POST', `/channels/${key}/messages`, { body_md: `Later reply ${i}`, parent_id: root.id }, seed().agent);
  await api('POST', `/channels/${key}/read`, { message_ids: [root.id] });
  await login(page, seed().alex, `/#/section/${section.id}/chat`);
  await page.getByRole('button', { name: /First unread/ }).click();
  await expect(page.getByRole('complementary', { name: 'Thread' }).locator(`[data-message="${first.id}"]`)).toBeInViewport();
});

test('project attention is owned and scoped; drawers stay in docs and mention reach is qualified', async ({ page }) => {
  const me = await api('GET', '/me');
  const section = await api('POST', '/sections', { name: 'Design project', audience: 'work' });
  const privateResult = await fetch(`${BASE_URL}/api/v1/sections/${section.id}`, { method: 'PATCH', headers: { Authorization: `Bearer ${seed().alex}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ private: true }) });
  expect(privateResult.ok).toBe(true);
  const list = await api('POST', '/lists', { name: 'Commitments', section_id: section.id });
  await api('POST', '/lists', { name: 'Reference drawer', section_id: section.id, kind: 'drawer' });
  await api('POST', '/items', { title: 'Owned commitment', list_id: list.id, assignees: [me.account.id] });
  await api('POST', '/items', { title: 'Exploratory idea', list_id: list.id });
  await login(page, seed().alex, `/#/section/${section.id}`);
  const attention = page.getByRole('region', { name: 'Your project attention' });
  await expect(attention.getByText('Owned commitment')).toBeVisible();
  await expect(attention.getByText('Exploratory idea')).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Project work and ideas' }).getByText('Reference drawer')).toHaveCount(0);
  await expect(page.getByTestId('hub-drawer').filter({ hasText: 'Reference drawer' })).toBeVisible();
  await page.getByRole('link', { name: 'Open chat', exact: true }).click();
  await page.getByRole('textbox', { name: 'Message #Design project' }).fill('@');
  await expect(page.getByText('Directory suggestions do not confirm access to this channel.')).toBeVisible();
});
