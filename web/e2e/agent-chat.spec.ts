// Chat with an agent outside any card: the coloured robot at the top lists each agent's own
// chat (`agent:<id>`). On a desktop it opens the chat panel; a message there reaches the agent
// with no @mention, and the agent's answer lands in the same chat with an unread badge on the
// robot. A phone has the robot in its top bar and opens the Chat page.
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

async function agentId(): Promise<string> {
  const me = await api<{ account: { id: string } }>('GET', '/me', undefined, seed().agent);
  return me.account.id;
}

test.describe('desktop', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test('the robot opens an agent chat in the panel; a message reaches the agent with no mention', async ({ page }) => {
    const agent = await agentId();
    const key = `agent:${agent}`;
    await signedIn(page, '/#/home');

    const robot = page.locator('.favbar').getByRole('button', { name: /^Chat with an agent/ });
    await expect(robot).toBeVisible();
    await robot.click();
    await page.locator(`[data-agent-chat="${agent}"]`).click();

    const panel = page.getByRole('complementary', { name: 'Chat panel' });
    await expect(panel).toHaveAttribute('data-chat-panel', key);
    // The page underneath stays.
    await expect(page).toHaveURL(/#\/home$/);

    const box = panel.getByRole('textbox', { name: 'Message Agent' });
    await box.click();
    await box.pressSequentially('What is on my plate this week?');
    await page.keyboard.press('Enter');
    await expect(panel.getByText('What is on my plate this week?')).toBeVisible();

    // The agent heard it as a mention, typed or not.
    const msgs = await api<{ body_md: string; mentions: string[] }[]>('GET', `/channels/${key}/messages`);
    expect(msgs.at(-1)?.mentions).toContain('agent');

    // The agent answers in its chat; with the panel closed the robot shows the unread answer.
    await panel.getByRole('button', { name: 'Close chat' }).click();
    await api('POST', `/channels/${key}/messages`, { body_md: 'Three tasks, one due Friday.' }, seed().agent);
    await expect(page.locator('.favbar').getByRole('button', { name: 'Chat with an agent, 1 unread' })).toBeVisible();
    await robot.click();
    await page.locator(`[data-agent-chat="${agent}"]`).click();
    await expect(panel.getByText('Three tasks, one due Friday.')).toBeVisible();
    await expect(page.locator('.favbar').getByRole('button', { name: 'Chat with an agent' })).toBeVisible();
  });
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the robot in the top bar opens the agent chat page', async ({ page }) => {
    const agent = await agentId();
    await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
    await page.goto('/#/home');
    const robot = page.locator('.topbar').getByRole('button', { name: /^Chat with an agent/ });
    await expect(robot).toBeVisible();
    await robot.click();
    await page.locator(`[data-agent-chat="${agent}"]`).click();
    await expect(page).toHaveURL(new RegExp(`#/chat/agent:${agent}$`));
    await expect(page.getByRole('textbox', { name: 'Message Agent' })).toBeVisible();
  });
});

test.describe('guest', () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a project's guest sees and talks in the project agent's chat, and no other agent's", async ({ page }) => {
    const stamp = Date.now().toString(36);
    const section = await api<{ id: string }>('POST', '/sections', { name: `Example Project ${stamp}`, audience: 'work' });
    await api('PATCH', `/sections/${section.id}`, { private: true });
    await api('POST', '/lists', { section_id: section.id, name: 'Work' });
    const guestAgent = await api<{ id: string }>('POST', '/accounts', { handle: `guest-agent-${stamp}`, display_name: 'Guest Agent', kind: 'agent', role: 'guest' }); // a project's own agent is a guest; a member agent is hidden from guests
    const guestAgentTok = await api<{ token: string }>('POST', `/accounts/${guestAgent.id}/tokens`, { scopes: ['write'] });
    const guest = await api<{ id: string }>('POST', '/accounts', { handle: `guest-${stamp}`, display_name: 'Guest', kind: 'person', role: 'guest' });
    const guestTok = await api<{ token: string }>('POST', `/accounts/${guest.id}/tokens`, { scopes: ['write'] });
    for (const who of [guestAgent.id, guest.id]) {
      await api('PUT', '/shares', { account_id: who, scope_kind: 'section', scope_id: section.id, level: 'edit' });
    }


    await page.addInitScript((t) => localStorage.setItem('slate.token', t), guestTok.token);
    await page.goto('/#/home');
    const robot = page.locator('.favbar').getByRole('button', { name: /^Chat with an agent/ });
    await expect(robot).toBeVisible();
    await robot.click();
    const menu = page.getByRole('navigation', { name: 'Agents to chat with' });
    await expect(menu.locator(`[data-agent-chat="${guestAgent.id}"]`)).toBeVisible();
    // The workspace agent is not in the guest's project.
    await expect(menu.locator('a')).toHaveCount(1);
    await menu.locator(`[data-agent-chat="${guestAgent.id}"]`).click();

    const panel = page.getByRole('complementary', { name: 'Chat panel' });
    await expect(panel.locator('.chat-start').getByText(`Mention @guest-agent-${stamp} to ask Guest Agent to answer.`, { exact: false })).toBeVisible();
    const box = panel.getByRole('textbox', { name: 'Message Guest Agent' });
    await box.click();
    await box.pressSequentially('Which draft is current?');
    await page.keyboard.press('Enter');
    await expect(panel.getByText('Which draft is current?')).toBeVisible();
    const key = `agent:${guestAgent.id}`;
    const status = panel.locator('[data-agent-turn]');
    const state = () => api<{ agent_turns: unknown[] }>('GET', `/channels/${key}`);
    expect((await state()).agent_turns).toEqual([]);
    await expect(status).toHaveCount(0);
    await box.fill('@alex can you check?');
    await box.press('Enter');
    await expect(panel.getByText('@alex can you check?', { exact: false })).toBeVisible();
    expect((await state()).agent_turns).toEqual([]);
    await expect(status).toHaveCount(0);

    await box.fill(`@guest-agent-${stamp} please check`);
    await box.press('Enter');
    await expect(panel.getByText('Guest Agent will answer', { exact: false })).toBeVisible();
    const next = await api<{ chat: { lease: { run_id: string } } }>('POST', '/work/next', { channel: key }, guestAgentTok.token);
    await expect(panel.getByText('Guest Agent is working', { exact: false })).toBeVisible();
    await api('POST', '/work/chat/finish', { channel: key, run_id: next.chat.lease.run_id, outcome: 'done', reply: 'Draft B.' }, guestAgentTok.token);
    await expect(panel.getByText('Draft B.', { exact: true })).toBeVisible();
    await box.fill('And what about the next one?');
    await box.press('Enter');
    await expect(panel.getByText('And what about the next one?', { exact: true })).toBeVisible();
    expect((await state()).agent_turns).toEqual([]);
    await expect(panel.getByText(/Guest Agent (will answer|is working)/)).toHaveCount(0);
    await page.reload();
    await expect(panel.getByText('And what about the next one?', { exact: true })).toBeVisible();
    await expect(panel.getByText(/Guest Agent (will answer|is working)/)).toHaveCount(0);
  });
});
