// Connection endpoints and SSE are MOCKED to the shared connection contract.
// Accounts, list shares, token creation and the rest of Slate use the disposable real server.
import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';
import type { AgentConnection } from '../src/lib/connections';

// These agents share Home while under test. Remove their availability afterward so later
// guest-chat tests don't inherit our fixtures as their first permitted conversation.
test.afterEach(async ({ request }) => {
  const headers = { Authorization: `Bearer ${seed().alex}` };
  const response = await request.get('/api/v1/accounts', { headers });
  const ours = new Set(['local-test-agent', 'retry-test-agent', 'manual-test-agent', 'existing-test-agent', 'readback-test-agent', 'mobile-pairing-agent']);
  const accounts = await response.json() as { id: string; handle: string; disabled_at: string | null }[];
  const enabled = accounts.filter((a) => ours.has(a.handle) && !a.disabled_at);
  for (const account of enabled) {
    const result = await request.patch(`/api/v1/accounts/${account.id}`, { headers, data: { disabled: true } });
    expect(result.ok()).toBe(true);
  }
  const readback = await (await request.get('/api/v1/accounts', { headers })).json() as typeof accounts;
  for (const account of enabled) expect(readback.find((a) => a.id === account.id)?.disabled_at).toBeTruthy();
});

async function fixture(page: Page, expiresIn = 600_000) {
  const state = { rows: [] as AgentConnection[], reads: 0, creates: 0, fail: false, readError: false };
  await page.addInitScript((token) => {
    localStorage.setItem('slate.token', token);
    class MockEvents extends EventTarget {
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror = null;
      constructor() { super(); (window as any).connectionEvents = this; setTimeout(() => this.onopen?.(), 10); }
      close() {}
    }
    (window as any).EventSource = MockEvents;
  }, seed().alex);
  await page.route('**/api/v1/agent-connections**', async (route) => {
    const req = route.request();
    if (req.method() === 'GET') {
      state.reads++;
      if (state.readError) return route.fulfill({ status: 403, json: { detail: 'Not authorized' } });
      const account = new URL(req.url()).searchParams.get('account_id');
      return route.fulfill({ json: { connections: state.rows.filter((c) => c.account_id === account) } });
    }
    if (req.method() === 'DELETE') {
      const c = state.rows.find((c) => req.url().endsWith(`/${c.id}`))!;
      c.state = 'revoked';
      return route.fulfill({ status: 204 });
    }
    state.creates++;
    expect(new URL(req.url()).pathname).toBe('/api/v1/agent-connections/pairings');
    if (state.fail) return route.fulfill({ status: 503, json: { detail: 'Connection setup unavailable. Try again.' } });
    const body = req.postDataJSON();
    expect(body.list_ids).toEqual([seed().listId]);
    const expires_at = new Date(Date.now() + expiresIn).toISOString();
    const connection: AgentConnection = { id: `mock-${state.creates}`, account_id: body.account_id, label: body.label,
      state: 'waiting', capabilities: [], last_seen_at: null, verified_at: null, detail: 'Run the connection command on your computer.', expires_at };
    state.rows.push(connection);
    return route.fulfill({ json: { connection, code: 'fixture-single-use-code-not-a-real-secret', expires_at } });
  });
  await page.goto('/#/agents');
  return state;
}
async function start(page: Page, name: string, method = 'Let Slate start my agent') {
  await page.getByRole('button', { name: 'Add my agent', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add my agent' });
  await dialog.getByRole('radio', { name: method, exact: true }).check();
  await dialog.getByLabel('Name', { exact: true }).fill(name);
  await dialog.getByRole('checkbox', { name: 'Personal › Home', exact: true }).check();
  return dialog;
}
async function emit(page: Page, account: string) {
  await page.evaluate((account_id) => {
    (window as any).connectionEvents.dispatchEvent(new MessageEvent('agent.connection_checked', { data: JSON.stringify({
      id: `mock-event-${Date.now()}`, type: 'agent.connection_checked', at: new Date().toISOString(), actor_id: account_id,
      subject_id: account_id, item_id: null, source: 'api', field: null, before: null, after: { account_id }, session_ref: null,
    }) }));
  }, account);
}

test('mocked pairing: least privilege, no ready on create, SSE health, revoke readback and no polling', async ({ page }, info) => {
  const state = await fixture(page);
  const dialog = await start(page, 'Local Test Agent');
  const created = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/accounts'));
  await dialog.getByRole('button', { name: 'Create connection code' }).click();
  const account = await (await created).json();
  expect(account.role).toBe('guest');
  expect(account.wake).toBe('manual');
  await expect(dialog.getByText('Waiting for your computer', { exact: true })).toBeVisible();
  await expect.poll(() => dialog.evaluate((el) => el.scrollTop)).toBe(0);
  await expect(dialog).toContainText('Code refused or already used?');
  await expect(dialog.getByLabel('Connection code')).toHaveAttribute('type', 'password');
  await expect(dialog).toContainText(`slate-connect connect --url ${BASE_URL}`);
  await expect(dialog).not.toContainText('--code');
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('fixture-single-use-code');
  expect(page.url()).not.toContain('fixture-single-use-code');
  await page.screenshot({ path: info.outputPath('pairing-desktop.png'), fullPage: true });
  const c = state.rows.at(-1)!;
  c.state = 'connected'; c.last_seen_at = new Date().toISOString();
  await emit(page, c.account_id);
  await expect(dialog).toContainText('Connected; test not run');
  c.state = 'problem'; c.detail = 'Agent runtime unavailable. Install it on the agent computer.';
  await emit(page, c.account_id);
  await expect(dialog).toContainText('Agent runtime unavailable');
  c.state = 'ready'; c.verified_at = c.last_seen_at = new Date().toISOString();
  await emit(page, c.account_id);
  await expect(dialog.getByText('Ready', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  const card = page.locator('article.agent', { hasText: 'Local Test Agent' });
  await card.getByRole('button', { name: 'Connections', exact: true }).click();
  await card.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await card.getByRole('button', { name: 'Revoke access', exact: true }).click();
  await expect(card).toContainText('Disconnected');
  await expect(card).toContainText('does not stop a process');
  await expect.poll(() => state.reads).toBeGreaterThan(1);
  const reads = state.reads;
  await page.clock.install();
  await page.clock.fastForward(300_000);
  expect(state.reads).toBe(reads);
});

test('mocked expired code has a new-code remedy; pairing failure retries the same account', async ({ page }) => {
  const state = await fixture(page, -1);
  state.fail = true;
  const dialog = await start(page, 'Retry Test Agent');
  let accounts = 0;
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/accounts')) accounts++; });
  await dialog.getByRole('button', { name: 'Create connection code' }).click();
  await expect(dialog.getByRole('alert')).toContainText('Connection setup unavailable');
  expect(accounts).toBe(1);
  state.fail = false;
  await dialog.getByRole('button', { name: 'Create connection code' }).click();
  await expect(dialog).toContainText('Code expired');
  await expect(dialog.getByLabel('Connection code')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Create a new code' }).click();
  expect(accounts).toBe(1);
  expect(state.creates).toBe(3);
});

test('manual creates no token or connection, and mobile flow stays within the viewport', async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  let tokens = 0;
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/tokens')) tokens++; });
  const dialog = await start(page, 'Manual Test Agent', 'I will pass tasks to it myself');
  await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
  await expect(dialog).toContainText('No local executor was connected');
  expect(tokens).toBe(0); expect(state.creates).toBe(0);
  expect(await dialog.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('manual-mobile.png'), fullPage: true });
});

test('limited admin cannot manage connections and hidden presence is unknown, not offline', async ({ page }) => {
  const state = await fixture(page);
  await page.route('**/api/v1/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.access = { ...me.access, all: false, manage: false, limited: true };
    return route.fulfill({ json: me });
  });
  state.reads = 0;
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Agents', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add my agent' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Manage', exact: true })).toHaveCount(0);
  await expect(page.locator('article.agent').first()).toContainText('Connection status is not shared with your access');
  await expect(page.locator('article.agent').first()).not.toContainText('Never seen');
  expect(state.reads).toBe(0);
});

test('existing agent manual choice changes delivery, does not mint credentials or grant new shares', async ({ page }) => {
  const created = await page.request.post('/api/v1/accounts', {
    headers: { Authorization: `Bearer ${seed().alex}` },
    data: { handle: 'existing-test-agent', display_name: 'Existing Test Agent', kind: 'agent', role: 'guest', wake: 'push' },
  });
  expect(created.ok()).toBe(true);
  const state = await fixture(page);
  let writes = 0;
  page.on('request', (r) => {
    if (r.method() !== 'GET' && /\/(accounts|tokens|shares)$/.test(new URL(r.url()).pathname)) writes++;
  });
  await page.getByRole('button', { name: 'Add my agent', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add my agent' });
  await dialog.getByRole('radio', { name: 'I will pass tasks to it myself', exact: true }).check();
  await dialog.getByRole('radio', { name: 'Existing agent', exact: true }).check();
  await dialog.getByRole('combobox', { name: 'Agent', exact: true }).selectOption({ label: 'Existing Test Agent (@existing-test-agent)' });
  await expect(dialog.getByRole('button', { name: 'Add agent', exact: true })).toBeDisabled();
  await dialog.getByRole('checkbox', { name: 'Personal › Home', exact: true }).check();
  await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  const card = page.locator('article.agent', { has: page.locator('.names b', { hasText: /^Existing Test Agent$/ }) });
  await expect(card.getByLabel('How Existing Test Agent is woken')).toHaveValue('manual');
  expect(writes).toBe(0); expect(state.creates).toBe(0);
});

test('mocked revocation cannot claim success when readback is forbidden; recover explicitly', async ({ page }) => {
  const state = await fixture(page);
  const dialog = await start(page, 'Readback Test Agent');
  await dialog.getByRole('button', { name: 'Create connection code' }).click();
  await expect(dialog).toContainText('Waiting for your computer');
  await dialog.getByRole('button', { name: 'Done', exact: true }).click();
  const card = page.locator('article.agent', { hasText: 'Readback Test Agent' });
  await card.getByRole('button', { name: 'Connections', exact: true }).click();
  await card.getByRole('button', { name: 'Disconnect', exact: true }).click();
  state.readError = true;
  await card.getByRole('button', { name: 'Revoke access', exact: true }).click();
  await expect(card).toContainText('could not be confirmed');
  await expect(card).toContainText('Status unknown');
  await expect(card).not.toContainText('Disconnected');
  state.readError = false;
  await card.getByRole('button', { name: 'Check again', exact: true }).click();
  await expect(card).toContainText('Disconnected');
  await expect(card).not.toContainText('could not be confirmed');
});

test('mobile pairing keeps status and close visible, traps focus, and expires without a request', async ({ page }, info) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await start(page, 'Mobile Pairing Agent');
  await dialog.getByRole('button', { name: 'Create connection code' }).click();
  await expect(dialog).toContainText('Waiting for your computer');
  await expect.poll(() => dialog.evaluate((el) => el.scrollTop)).toBe(0);
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('pairing-mobile-top.png'), fullPage: true });
  await expect(dialog.getByRole('heading', { name: 'Add my agent', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: 'Done', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Close', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: 'Done', exact: true })).toBeFocused();
  const reads = state.reads;
  await page.clock.install();
  await page.clock.fastForward(601_000);
  await expect(dialog).toContainText('Code expired');
  await expect(dialog.getByLabel('Connection code')).toHaveCount(0);
  expect(state.reads).toBe(reads);
  await page.screenshot({ path: info.outputPath('pairing-mobile-expired.png'), fullPage: true });
});
