// "Agent is working…": when a person @mentions an agent in a thread, the thread shows a quiet
// line under its last message from the mention ("will answer…") through the runner's take
// ("is working…") until the turn ends with the reply. A board loaded mid-turn shows it too
// (`agent_turns` on GET /channels), and the channel itself never shows a thread's turn.
import { expect, test, type Page } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_URL, seed } from './seed';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const dbPath = join(here, '..', '.e2e', 'slate-e2e.db'); // playwright.config.ts's dbPath

/** The scheduler's chat-turn expiry, an hour ahead, on the e2e database (the server runs it off). */
function expireTurns() {
  const out = spawnSync('uv', ['run', 'python', join(here, 'turn_expire.py')], {
    cwd: root,
    env: { ...process.env, SLATE_DATABASE_PATH: dbPath },
    encoding: 'utf8',
  });
  expect(out.status, out.stderr).toBe(0);
  expect(Number(out.stdout.trim())).toBeGreaterThan(0);
}

async function api<T>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function signedIn(page: Page, path: string) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
}

test.use({ viewport: { width: 1440, height: 900 } });

test('a thread shows the agent answering from the mention to the reply', async ({ page }) => {
  const key = `list:${seed().listId}`;
  const agent = (await api<{ account: { id: string } }>('GET', '/me', undefined, seed().agent)).account.id;
  const root = await api<{ id: string }>('POST', `/channels/${key}/messages`, { body_md: 'Weekend plans' });
  await signedIn(page, `/#/chat/${key}?thread=${root.id}`);

  const thread = page.getByRole('complementary', { name: 'Thread' });
  await expect(thread.getByText('Weekend plans')).toBeVisible();
  const line = thread.locator(`[data-agent="${agent}"]`);
  await expect(line).toHaveCount(0);

  // The mention: the agent will answer.
  await api('POST', `/channels/${key}/messages`, { body_md: '@agent anything due Saturday?', parent_id: root.id });
  await expect(line).toHaveAttribute('data-agent-turn', 'waiting');
  await expect(line).toContainText('Agent will answer');

  // The runner takes the turn: the agent is working (live, then after a reload from the channel list).
  const taken = await api<{ chat: { lease: { run_id: string } } }>(
    'POST',
    '/work/next',
    { kinds: ['chat'], channel: key, thread_id: root.id },
    seed().agent,
  );
  await expect(line).toHaveAttribute('data-agent-turn', 'working');
  await expect(line).toContainText('Agent is working');
  await page.reload();
  await expect(thread.locator(`[data-agent="${agent}"]`)).toHaveAttribute('data-agent-turn', 'working');
  // A thread's turn shows in the thread, not under the channel's messages.
  await expect(page.locator('.chat-main').locator(`[data-agent="${agent}"]`)).toHaveCount(0);

  // The reply ends the turn: the line goes as the answer lands.
  await api(
    'POST',
    '/work/chat/finish',
    { channel: key, thread_id: root.id, outcome: 'done', reply: 'Nothing due Saturday.', run_id: taken.chat.lease.run_id },
    seed().agent,
  );
  await expect(thread.getByText('Nothing due Saturday.')).toBeVisible();
  await expect(thread.locator(`[data-agent="${agent}"]`)).toHaveCount(0);
});

test('a turn whose run was lost shows as stopped, and a person clears it', async ({ page }) => {
  const key = `list:${seed().listId}`;
  const agent = (await api<{ account: { id: string } }>('GET', '/me', undefined, seed().agent)).account.id;
  const root = await api<{ id: string }>('POST', `/channels/${key}/messages`, { body_md: 'Garage door' });
  await api('POST', `/channels/${key}/messages`, { body_md: '@agent who fixes these?', parent_id: root.id });
  await api('POST', '/work/next', { kinds: ['chat'], channel: key, thread_id: root.id, lease_seconds: 60 }, seed().agent);
  expireTurns();

  await signedIn(page, `/#/chat/${key}?thread=${root.id}`);
  const thread = page.getByRole('complementary', { name: 'Thread' });
  const line = thread.locator(`[data-agent="${agent}"]`);
  await expect(line).toHaveAttribute('data-agent-turn', 'expired');
  await expect(line).toContainText('Agent stopped answering');
  await line.locator('[data-clear-turn]').click();
  await expect(line).toHaveCount(0);
});
