// Window chrome must stay usable above chat; size changes never navigate or remount it.
import { expect, test, type Locator, type Page } from '@playwright/test';
import { api, signIn } from './support';

async function openChat(page: Page) {
  await signIn(page);
  await page.goto('/#/home?chat=general');
  const panel = page.getByRole('complementary', { name: 'Chat panel' });
  await expect(panel.getByRole('textbox', { name: 'Message #General' })).toBeVisible();
  return panel;
}

async function hitTarget(control: Locator) {
  await expect(control).toBeVisible();
  await expect.poll(() => control.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
  })).toBe(true);
}

test('long channel names and an open thread survive resizing down to the grip minimum', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  const post = (path: string, body: unknown) => api<{ id: string }>('POST', path, body);
  const section = await post('/sections', { name: 'A project with a very long descriptive name to test the chat header', audience: 'personal' });
  const root = await post(`/channels/section:${section.id}/messages`, { body_md: 'Keep this thread open' });
  await signIn(page);
  await page.goto(`/#/home?chat=section:${section.id}&thread=${root.id}`);
  const panel = page.getByRole('complementary', { name: 'Chat panel' });
  const draft = panel.getByRole('textbox', { name: 'Reply…' });
  await draft.fill('Unsent thread reply');
  const composer = await draft.elementHandle();
  const grip = panel.getByRole('button', { name: 'Resize chat panel' });
  await grip.focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight');
  expect((await panel.boundingBox())!.width).toBe(320);
  for (const control of await panel.locator('.chat-panel-head button, .chat-panel-head a').all()) await hitTarget(control);
  await panel.getByRole('button', { name: 'Full screen', exact: true }).click();
  await panel.getByRole('button', { name: 'Exit full screen', exact: true }).click();
  expect((await panel.boundingBox())!.width).toBe(320);
  expect(await composer!.evaluate((el) => el.isConnected)).toBe(true);
  await expect(draft).toHaveValue('Unsent thread reply');
  await expect(panel.getByRole('complementary', { name: 'Thread' })).toBeVisible();
});

for (const width of [1920, 1280, 900]) {
  test(`compact, expanded and full-screen chat preserve the conversation at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 620 });
    const panel = await openChat(page);
    const draft = panel.getByRole('textbox', { name: 'Message #General' });
    await draft.fill('Keep this unsent draft');
    const composer = await draft.elementHandle();
    const before = (await panel.boundingBox())!;
    await panel.getByRole('button', { name: 'Expand chat width', exact: true }).click();
    expect((await panel.boundingBox())!.width).toBeGreaterThan(before.width);
    await panel.getByRole('button', { name: 'Full screen', exact: true }).click();
    expect((await panel.boundingBox())!.width).toBe(width);
    await expect(panel.getByRole('button', { name: 'Resize chat panel' })).toBeHidden();
    await hitTarget(page.locator('.favbar').getByRole('button', { name: /^Chat with an agent/ }));
    await hitTarget(panel.getByRole('button', { name: 'Close chat', exact: true }));
    await hitTarget(draft);
    await page.screenshot({ path: `test-results/chat-full-screen-${width}.png` });
    await panel.getByRole('button', { name: 'Exit full screen', exact: true }).click();
    expect((await panel.boundingBox())!.width).toBeGreaterThan(before.width);
    await panel.getByRole('button', { name: 'Compact chat', exact: true }).click();
    expect((await panel.boundingBox())!.width).toBe(before.width);
    expect(await composer!.evaluate((el) => el.isConnected)).toBe(true);
    await expect(draft).toHaveValue('Keep this unsent draft');
    await expect(page).toHaveURL(/#\/home/);
    await panel.getByRole('button', { name: 'Full screen', exact: true }).click();
    await panel.getByRole('button', { name: 'Compact chat', exact: true }).click();
    expect((await panel.boundingBox())!.width).toBe(before.width);
    await page.screenshot({ path: `test-results/chat-compact-${width}.png` });
    await panel.getByRole('button', { name: 'Expand chat width', exact: true }).click();
    await page.reload();
    await expect(panel.getByRole('button', { name: 'Compact chat', exact: true })).toBeVisible();
    await expect(draft).toHaveValue('Keep this unsent draft');
    await panel.getByRole('button', { name: 'Full screen', exact: true }).click();
    await panel.getByRole('button', { name: 'Close chat', exact: true }).click();
    await expect(panel).toBeHidden();
    await page.locator('.nav').getByRole('link', { name: /^Chat/ }).click();
    await expect(panel.getByRole('button', { name: 'Full screen', exact: true })).toBeVisible();
    expect((await panel.boundingBox())!.width).toBeLessThan(width);
  });

  test(`chat leaves the header and larger labelled Agents button usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    const panel = await openChat(page);
    const header = page.locator('.favbar');
    const headerBox = (await header.boundingBox())!;
    expect((await panel.boundingBox())!.y).toBeGreaterThanOrEqual(headerBox.y + headerBox.height);
    const agents = header.getByRole('button', { name: /^Chat with an agent/ });
    await expect(agents.getByText('Agents', { exact: true })).toBeVisible();
    expect((await agents.boundingBox())!.height).toBeGreaterThanOrEqual(36);
    await hitTarget(agents);
    await agents.click();
    const menu = page.getByRole('navigation', { name: 'Agents to chat with' });
    await hitTarget(menu.getByRole('link').first());
    await menu.getByRole('link').first().click();
    await expect(panel).toHaveAttribute('data-chat-panel', /^agent:/);
  });
}
