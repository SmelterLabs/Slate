// Chat: a list's Chat tab, live messages from an agent, threads, reactions, turning a
// message into a task, task links as cards, unread badges in the sidebar, and the same Chat
// page on a phone.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_URL, seed } from './seed';

const shots = process.env.SLATE_SCREENSHOTS ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img') : null;
if (shots) mkdirSync(shots, { recursive: true });

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

async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} space`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  return list.id;
}

interface Msg {
  id: string;
}

test.describe('desktop', () => {
  test('a list Chat tab: post, agent replies in a thread live, react, make a task', async ({ page }) => {
    const listId = await scratch('Garage');
    // The Chat tab page itself (on a wide screen the tab opens the chat panel instead: chat-panel.spec).
    await signedIn(page, `/#/list/${listId}/chat`);
    await expect(page).toHaveURL(new RegExp(`#/list/${listId}/chat`));
    const view = page.getByRole('region', { name: '#Garage chat' });
    await expect(view.getByText('This is the start of #Garage')).toBeVisible();

    const box = view.getByRole('textbox', { name: 'Message #Garage' });
    await box.click();
    await box.pressSequentially('Can someone clear the shelves @agent');
    await expect(page.getByRole('listbox', { name: 'Mention someone' })).toBeVisible();
    await page.keyboard.press('Enter');
    await box.pressSequentially('before Saturday?');
    await page.keyboard.press('Enter');
    // The channel's own list of messages: an open thread pane shows the root message again.
    const mine = view.locator('.chat-main .chat-msg', { hasText: 'Can someone clear the shelves' });
    await expect(mine).toBeVisible();
    await expect(mine.locator('.mention')).toHaveText('@agent');

    // The agent answers in the thread over the API; the reply count shows up live.
    const [root] = await api<Msg[]>('GET', `/channels/list:${listId}/messages`);
    await api('POST', `/channels/list:${listId}/messages`, { body_md: 'On it, Saturday morning', parent_id: root.id }, seed().agent);
    await expect(mine.getByRole('button', { name: /1 reply/ })).toBeVisible();
    await mine.getByRole('button', { name: /1 reply/ }).click();
    const thread = page.getByRole('complementary', { name: 'Thread' });
    await expect(thread.getByText('On it, Saturday morning')).toBeVisible();
    await expect(thread.locator('.chat-agent')).toBeVisible();
    await thread.getByRole('textbox', { name: 'Reply…' }).fill('Thanks!');
    await page.keyboard.press('Enter');
    await expect(thread.locator('.chat-msg', { hasText: 'Thanks!' })).toBeVisible();
    await thread.getByRole('button', { name: 'Close thread' }).click();
    // Closing goes through the URL (so Back reopens it); the pane leaves on the next route.
    await expect(thread).toBeHidden();

    await mine.hover();
    await mine.getByRole('button', { name: 'React 👍' }).click();
    await expect(mine.getByRole('button', { name: /^👍 1/ })).toBeVisible();

    await mine.getByRole('button', { name: 'Turn into task' }).click();
    const dialog = page.getByRole('dialog', { name: 'Turn into task' });
    await expect(dialog.getByRole('textbox', { name: 'Task title' })).toHaveValue(/Can someone clear the shelves/);
    await dialog.getByRole('textbox', { name: 'Task title' }).fill('Clear the garage shelves');
    if (shots) await page.screenshot({ path: join(shots, 'chat-make-task.png') });
    await dialog.getByRole('button', { name: 'Create task' }).click();
    const card = mine.locator('.chat-task-card');
    await expect(card).toContainText('Clear the garage shelves');
    await expect(card).toContainText('Made from this message');
    if (shots) await page.screenshot({ path: join(shots, 'chat-channel.png') });
    await card.click();
    await expect(page.getByRole('dialog', { name: /Clear the garage shelves/ })).toBeVisible();
  });

  test('unread badges follow agents live, and opening the channel reads it', async ({ page }) => {
    const listId = await scratch('Pantry');
    await signedIn(page, '/#/home');
    await api('POST', `/channels/list:${listId}/messages`, { body_md: 'Restocked the rice' }, seed().agent);
    await api('POST', `/channels/list:${listId}/messages`, { body_md: '@alex we are out of beans' }, seed().agent);
    const nav = page.getByRole('navigation', { name: 'Lists' });
    const badge = nav.getByRole('link', { name: '1 unread mentions in #Pantry chat' });
    await expect(badge).toBeVisible();
    await expect(nav.getByRole('link', { name: /^Chat/ }).locator('.count')).toHaveText(/@\d+/);
    // The newest list sits under the sticky sidebar foot here, so follow the badge by keyboard.
    await badge.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('region', { name: '#Pantry chat' }).getByText('we are out of beans')).toBeVisible();
    await expect(badge).toBeHidden();
    // The mention reached the inbox too, and reading the channel read it there, even when the
    // inbox heard of it only after the channel opened.
    const mention = async (text: string) => {
      const inbox = await api<{ reason: string; read: boolean; channel: string; event: { after: { body_md?: string } } }[]>(
        'GET',
        '/inbox',
      );
      const e = inbox.find((e) => e.channel === `list:${listId}` && e.event.after?.body_md?.includes(text));
      return e && { reason: e.reason, read: e.read };
    };
    await expect.poll(() => mention('out of beans')).toEqual({ reason: 'mention', read: true });
    // A mention that arrives while you are looking at the channel is read as well.
    await api('POST', `/channels/list:${listId}/messages`, { body_md: '@alex and out of lentils' }, seed().agent);
    await expect(page.getByRole('region', { name: '#Pantry chat' }).getByText('and out of lentils')).toBeVisible();
    await expect.poll(() => mention('out of lentils')).toEqual({ reason: 'mention', read: true });
    await expect(badge).toBeHidden();
  });

  test('a section row always has a chat icon: it opens the section chat, and unread counts land on it live', async ({ page }) => {
    const section = await api<{ id: string }>('POST', '/sections', { name: 'Studio', audience: 'work' });
    await signedIn(page, '/#/home');
    const nav = page.getByRole('navigation', { name: 'Lists' });
    // Nothing unread: the icon is there all the same.
    const icon = nav.getByRole('link', { name: 'Open #Studio chat' });
    await expect(icon).toBeVisible();
    // The icon is its own width: the section name keeps the row (it once read "Stu…").
    const name = nav.locator('.nav-heading', { hasText: 'Studio' }).locator('.space-name');
    expect(await name.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    expect(await icon.evaluate((el) => el.getBoundingClientRect().width)).toBeLessThan(30);
    await icon.focus();
    await page.keyboard.press('Enter');
    // On a wide screen it opens the chat panel on the section, and the page underneath stays.
    const panel = page.getByRole('complementary', { name: 'Chat panel' });
    await expect(panel).toHaveAttribute('data-chat-panel', `section:${section.id}`);
    await expect(panel.getByRole('region', { name: '#Studio chat' })).toBeVisible();
    await expect(page).toHaveURL(/#\/home$/);

    // Away from the chat (the panel closed), a message by someone else puts its count on the same icon, live.
    await panel.getByRole('button', { name: 'Close chat' }).click();
    await expect(panel).toBeHidden();
    await page.getByRole('link', { name: /^Everything/ }).first().click();
    await api('POST', `/channels/section:${section.id}/messages`, { body_md: 'Mood board is up' }, seed().agent);
    const counted = nav.getByRole('link', { name: '1 unread in #Studio chat' });
    await expect(counted).toBeVisible();
    await expect(counted).toHaveText(/1/);
    await expect(icon).toHaveCount(0);
  });

  test('the Chat page: General, a # task picker and task cards', async ({ page }) => {
    const listId = await scratch('Dry cleaning');
    await api('POST', '/items', { list_id: listId, title: 'Pick up dry cleaning' });
    await signedIn(page, '/#/chat');
    await expect(page.getByRole('region', { name: '#General chat' })).toBeVisible();
    await expect(page.getByRole('navigation').locator('[data-channel="general"]')).toHaveClass(/active/);
    const box = page.getByRole('textbox', { name: 'Message #General' });
    await box.click();
    await box.pressSequentially('Reminder: #dry');
    const picker = page.getByRole('listbox', { name: 'Link a task, page or list' });
    await expect(picker.getByText('Pick up dry cleaning')).toBeVisible();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    const msg = page.locator('.chat-msg', { hasText: 'Reminder:' });
    await expect(msg.locator('.task-ref')).toHaveText('#Pick up dry cleaning');
    await expect(msg.locator('.chat-task-card')).toContainText('Pick up dry cleaning');
    if (shots) await page.screenshot({ path: join(shots, 'chat-page.png') });
  });

  test('# links a page and a list; a reader without access gets the gone screen, not the title', async ({ page, browser }) => {
    const section = await api<{ id: string }>('POST', '/sections', { name: 'Cooking space', audience: 'work' });
    const kitchen = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Kitchen' });
    const recipes = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Recipes', private: true });
    const doc = await api<{ id: string }>('POST', '/docs', { title: 'Secret sauce', icon: '🥫', list_id: recipes.id });
    await signedIn(page, `/#/list/${kitchen.id}/chat`);
    const view = page.getByRole('region', { name: '#Kitchen chat' });
    const box = view.getByRole('textbox', { name: 'Message #Kitchen' });
    await box.click();
    await box.pressSequentially('Read #secret');
    const picker = page.getByRole('listbox', { name: 'Link a task, page or list' });
    await expect(picker.getByText('Pages')).toBeVisible();
    await picker.getByRole('option', { name: /Secret sauce/ }).click();
    await box.pressSequentially('then cook in #kitch');
    await expect(picker.getByText('Lists')).toBeVisible();
    await picker.locator('[data-kind="list"]', { hasText: 'Kitchen' }).getByRole('button').click();
    await expect(box).toHaveValue(`Read [[doc:${doc.id}]] then cook in [[list:${kitchen.id}]] `);
    await page.keyboard.press('Enter');
    const msg = view.locator('.chat-main .chat-msg', { hasText: 'Read' });
    await expect(msg.locator('a.doc-ref')).toHaveText('🥫 Secret sauce');
    await expect(msg.locator('a.list-ref')).toHaveText('Kitchen');
    await msg.locator('a.doc-ref').click();
    await expect(page).toHaveURL(new RegExp(`#/docs/${doc.id}`));
    await expect(page.getByRole('textbox', { name: 'Page title' })).toHaveValue('Secret sauce');

    // A guest shared on Kitchen only reads the message but cannot see the Recipes page: the
    // link says "a page" and opens the existing gone screen; the title appears nowhere.
    const acct = await api<{ id: string }>('POST', '/accounts', { handle: 'chat-guest', display_name: 'Chat Guest', kind: 'person', role: 'guest' });
    const token = (await api<{ token: string }>('POST', `/accounts/${acct.id}/tokens`, { scopes: ['write'], label: 'e2e' })).token;
    await api('PUT', '/shares', { account_id: acct.id, scope_kind: 'list', scope_id: kitchen.id, level: 'comment' });
    const guest = await browser.newPage();
    await guest.addInitScript((t) => localStorage.setItem('slate.token', t), token);
    await guest.goto(`/#/list/${kitchen.id}/chat`);
    const theirs = guest.getByRole('region', { name: '#Kitchen chat' }).locator('.chat-msg', { hasText: 'Read' });
    await expect(theirs.locator('a.doc-ref')).toHaveText('📄 a page');
    await expect(theirs.locator('a.list-ref')).toHaveText('Kitchen');
    await theirs.locator('a.doc-ref').click();
    await expect(guest.getByText('That page is gone')).toBeVisible();
    await expect(guest.getByText('Secret sauce')).toHaveCount(0);
    await guest.close();
  });
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('the same Chat page, one pane at a time, with every action', async ({ page }) => {
    const listId = await scratch('Yard');
    const msg = await api<Msg>('POST', `/channels/list:${listId}/messages`, { body_md: 'Mow on Sunday?' }, seed().agent);
    await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
    await page.goto('/#/chat');
    await expect(page.getByRole('heading', { name: 'Chat' })).toBeVisible();
    await page.locator(`[data-channel="list:${listId}"]`).click();
    const view = page.getByRole('region', { name: '#Yard chat' });
    const row = view.locator('.chat-msg', { hasText: 'Mow on Sunday?' });
    await expect(row).toBeVisible();
    // Touch screens show the actions without hovering.
    await expect(row.getByRole('button', { name: 'Reply in thread' })).toBeVisible();
    await expect(row.getByRole('button', { name: 'Turn into task' })).toBeVisible();
    await row.getByRole('button', { name: 'Reply in thread' }).click();
    const thread = page.getByRole('complementary', { name: 'Thread' });
    await thread.getByRole('textbox', { name: 'Reply…' }).fill('Yes, after 10');
    await thread.getByRole('button', { name: 'Send' }).click();
    await expect(thread.locator('.chat-msg', { hasText: 'Yes, after 10' })).toBeVisible();
    if (shots) await page.screenshot({ path: join(shots, 'chat-phone-thread.png') });
    await thread.getByRole('button', { name: 'Close thread' }).click();
    await expect(thread).toBeHidden();
    await expect(view.getByRole('button', { name: /1 reply/ })).toBeVisible();
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(390);
    if (shots) await page.screenshot({ path: join(shots, 'chat-phone.png') });
    await view.getByRole('button', { name: 'All chats' }).click();
    await expect(page.locator(`[data-channel="list:${listId}"]`)).toBeVisible();
    expect(msg.id).toBeTruthy();
  });
});
