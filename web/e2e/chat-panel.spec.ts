// The chat panel: chat on the right of the page you are on. The Chat tab of a list opens it
// and the board stays; it survives going to Docs and a reload; a doc sits at the left so the
// panel covers none of it; the sidebar's Chat entry toggles it; a `?chat=` link opens it on a
// channel; a thread opens inside the panel; Close closes it. A phone keeps the Chat page.
import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function signedIn(page: Page, path = '/') {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
  await expect(page.getByRole('link', { name: /^Everything/ }).first()).toBeVisible();
}

test.describe('desktop', () => {
  // A desktop monitor: the doc's text column (760px) and the panel both fit beside the tree.
  // On a laptop the panel covers the doc's right edge instead; that is the deal (overlay, never shrink).
  test.use({ viewport: { width: 1920, height: 1080 } });

  test('the Chat tab opens the panel beside the board; it follows to Docs, a reload and a thread; Close closes it', async ({ page }) => {
    const section = await api<{ id: string }>('POST', '/sections', { name: 'Panel space', audience: 'work' });
    const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Panel list' });
    const doc = await api<{ id: string }>('POST', '/docs', { title: 'Wide page', list_id: list.id, body_md: '## Plan\n\nText.\n' });
    const root = await api<{ id: string }>('POST', `/channels/list:${list.id}/messages`, { body_md: 'Shelves this week?' }, seed().agent);

    await signedIn(page, `/#/list/${list.id}`);
    const panel = page.getByRole('complementary', { name: 'Chat panel' });
    await expect(panel).toBeHidden();

    await page.getByRole('tab').filter({ hasText: 'Chat' }).getByRole('link').click();
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('data-chat-panel', `list:${list.id}`);
    // The board stays: the URL is still the list, and the tab reads as on.
    await expect(page).toHaveURL(new RegExp(`#/list/${list.id}$`));
    await expect(page.getByRole('tab').filter({ hasText: 'Chat' })).toHaveAttribute('aria-selected', 'true');
    await expect(panel.getByText('Shelves this week?')).toBeVisible();

    // Post from the panel.
    const box = panel.getByRole('textbox', { name: 'Message #Panel list' });
    await box.click();
    await box.pressSequentially('Saturday morning.');
    await page.keyboard.press('Enter');
    await expect(panel.getByText('Saturday morning.')).toBeVisible();

    // Docs: the panel stays, and the page sits at the left of the main area, clear of it.
    await page.goto('/#/docs');
    await page.getByRole('tree', { name: 'Pages and files' }).getByRole('link', { name: /Wide page/ }).click();
    await expect(page.getByRole('heading', { name: 'Plan' })).toBeVisible();
    await expect(panel).toBeVisible();
    const main = await page.locator('.docs-main').boundingBox();
    const article = await page.locator('.doc-main').boundingBox();
    const side = await panel.boundingBox();
    expect(main && article && side).toBeTruthy();
    expect(article!.x - main!.x).toBeLessThan(40);
    expect(article!.x + article!.width).toBeLessThanOrEqual(side!.x + 1);

    // A reload brings it back on the same channel.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Plan' })).toBeVisible();
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('data-chat-panel', `list:${list.id}`);

    // A thread opens inside the panel, over the channel; closing it shows the channel again.
    await panel.getByText('Shelves this week?').hover();
    await panel.getByRole('button', { name: 'Reply in thread' }).first().click();
    await expect(panel.getByRole('complementary', { name: 'Thread' })).toBeVisible();
    await expect(panel.getByRole('textbox', { name: 'Reply…' })).toBeVisible();
    await expect(page).toHaveURL(/#\/docs\//); // the thread lives in the panel, not the URL
    await panel.getByRole('button', { name: 'Close thread' }).click();
    await expect(panel.getByRole('complementary', { name: 'Thread' })).toBeHidden();

    // Switch channels from the panel's picker.
    await panel.getByRole('button', { name: 'Switch chat' }).click();
    await page.getByRole('navigation', { name: 'Chats' }).getByRole('button', { name: 'General' }).click();
    await expect(panel).toHaveAttribute('data-chat-panel', 'general');

    // Close; the sidebar's Chat entry opens it again on the last channel and closes it again.
    await panel.getByRole('button', { name: 'Close chat' }).click();
    await expect(panel).toBeHidden();
    const entry = page.getByRole('link', { name: /^Chat/ }).first();
    await entry.click();
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('data-chat-panel', 'general');
    await expect(page).toHaveURL(/#\/docs\//);
    await entry.click();
    await expect(panel).toBeHidden();

    // A `?chat=` link opens the panel on that channel (and thread) over the page it names.
    await page.goto(`/#/list/${list.id}?chat=list:${list.id}&thread=${root.id}`);
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('data-chat-panel', `list:${list.id}`);
    await expect(panel.getByRole('complementary', { name: 'Thread' })).toBeVisible();

    // The section's chat icon in the sidebar opens the panel on the section (by keyboard: the
    // row's hover actions sit over the icon's right end, as in chat.spec).
    await page.locator(`[data-section-chat="${section.id}"]`).focus();
    await page.keyboard.press('Enter');
    await expect(panel).toHaveAttribute('data-chat-panel', `section:${section.id}`);
    await expect(page).toHaveURL(new RegExp(`#/list/${list.id}`));

    // The left edge is a grip: drag it out and the panel grows, in and it shrinks, and the width
    // survives a reload. Double-click puts it back. It never goes narrower than the floor.
    const before = (await panel.boundingBox())!;
    const grip = panel.getByRole('button', { name: 'Resize chat panel' });
    const g = (await grip.boundingBox())!;
    await page.mouse.move(g.x + g.width / 2, g.y + 300);
    await page.mouse.down();
    await page.mouse.move(g.x + g.width / 2 - 200, g.y + 300, { steps: 8 });
    await page.mouse.up();
    const wider = (await panel.boundingBox())!;
    expect(Math.abs(wider.width - before.width - 200)).toBeLessThanOrEqual(2);
    await page.reload();
    await expect(panel).toBeVisible();
    expect(Math.round((await panel.boundingBox())!.width)).toBe(Math.round(wider.width));
    await page.mouse.move(wider.x, g.y + 300);
    await page.mouse.down();
    await page.mouse.move(wider.x + 900, g.y + 300, { steps: 8 });
    await page.mouse.up();
    expect(Math.round((await panel.boundingBox())!.width)).toBe(320);
    await grip.dblclick();
    expect(Math.round((await panel.boundingBox())!.width)).toBe(Math.round(before.width));

    // The expand icon opens the chat as a page (no panel over it); the page's Panel button puts
    // it back in the panel and returns to where you were.
    await page.goto(`/#/docs/${doc.id}`);
    await expect(panel).toBeVisible();
    const was = (await panel.getAttribute('data-chat-panel'))!;
    await panel.getByRole('link', { name: 'Open chat as a page' }).click();
    await expect(page).toHaveURL(new RegExp(`#/chat/${was.replace(':', '(:|%3A)')}`));
    await expect(panel).toBeHidden();
    await page.getByRole('region', { name: /chat$/ }).getByRole('button', { name: 'Panel' }).click();
    await expect(page).toHaveURL(new RegExp(`#/docs/${doc.id}$`));
    await expect(panel).toBeVisible();
    await expect(panel).toHaveAttribute('data-chat-panel', was);

    // Straight to the Chat page, Panel goes to My Work with the panel open.
    await page.goto('/#/chat');
    await expect(page.getByRole('region', { name: '#General chat' })).toBeVisible();
    await expect(panel).toBeHidden();
    await page.getByRole('region', { name: '#General chat' }).getByRole('button', { name: 'Panel' }).click();
    await expect(page).toHaveURL(/#\/home$/);
    await expect(panel).toHaveAttribute('data-chat-panel', 'general');
  });
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('the Chat tab goes to the Chat page, and a ?chat= link does too', async ({ page }) => {
    const section = await api<{ id: string }>('POST', '/sections', { name: 'Phone panel space', audience: 'work' });
    const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Phone panel list' });
    await signedIn(page, `/#/list/${list.id}`);
    await page.getByRole('tab').filter({ hasText: 'Chat' }).getByRole('link').click();
    await expect(page).toHaveURL(new RegExp(`#/list/${list.id}/chat`));
    await expect(page.getByRole('complementary', { name: 'Chat panel' })).toBeHidden();

    await page.goto(`/#/home?chat=list:${list.id}`);
    await expect(page).toHaveURL(new RegExp(`#/chat/list:${list.id}`));
    await expect(page.getByRole('region', { name: '#Phone panel list chat' })).toBeVisible();
  });
});
