// Actual backend, SSE and local CLI. The explicitly chosen executor is a harmless local
// Python program, not a simulated Slate response or a real model/provider.
import { expect, test } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { BASE_URL, seed, signIn } from './support';

test('real pairing, explicit local test, task execution and disconnect round trip', async ({ page, request }, info) => {
  test.setTimeout(120_000);
  const headers = { Authorization: `Bearer ${seed().alex}` };
  await signIn(page);
  await page.goto('/#/agents');
  await page.getByRole('button', { name: 'Add my agent', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add my agent' });
  await dialog.getByRole('radio', { name: 'Let Slate start my agent', exact: true }).check();
  await dialog.getByLabel('Name', { exact: true }).fill('Roundtrip Proof Agent');
  await dialog.getByRole('checkbox', { name: 'Personal › Home', exact: true }).check();
  const response = page.waitForResponse((r) => r.url().endsWith('/api/v1/agent-connections/pairings') && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create connection code', exact: true }).click();
  const pairing = await (await response).json();
  expect(pairing.connection.state).toBe('waiting');
  const root = info.outputPath('local-cli');
  const python = resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const helper = resolve('e2e/helpers/local_connection.py');
  function local(action: string, extra: Record<string, unknown> = {}) {
    const result = spawnSync(python, [helper], {
      input: JSON.stringify({ action, root, url: BASE_URL, ...extra }), encoding: 'utf8', timeout: 80_000,
    });
    expect(result.status, result.stderr).toBe(0);
    return JSON.parse(result.stdout);
  }
  try {
    expect(local('connect', { code: pairing.code })).toEqual({ connected: true, probe: 'passed' });
    await expect(dialog.getByText('Ready', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Done', exact: true }).click();
    const account = await (await request.get('/api/v1/accounts', { headers })).json();
    const agent = account.find((a: { id: string }) => a.id === pairing.connection.account_id);
    const created = await request.post('/api/v1/items', { headers, data: {
      list_id: seed().listId, title: 'Harmless connection acceptance', assignees: [agent.handle],
    } });
    expect(created.status()).toBe(201);
    const task = await created.json();
    expect(local('run', { item_id: task.id })).toEqual({ run: 'finished' });
    const finished = await (await request.get(`/api/v1/items/${task.id}`, { headers })).json();
    expect(finished.status.kind).toBe('done');
    expect(finished.comments.some((c: { body_md: string }) => c.body_md.includes('Harmless executor round trip completed.'))).toBe(true);
    const card = page.getByRole('region', { name: 'Connections for Roundtrip Proof Agent' });
    await expect(card.getByRole('status')).toHaveText('Ready');
    await card.getByRole('button', { name: 'Connections', exact: true }).click();
    await card.getByRole('button', { name: 'Disconnect', exact: true }).click();
    await card.getByRole('button', { name: 'Revoke access', exact: true }).click();
    await expect(card.getByRole('status')).toHaveText('Disconnected');
    const readback = await (await request.get(`/api/v1/agent-connections?account_id=${agent.id}`, { headers })).json();
    expect(readback.connections[0].state).toBe('revoked');
    await page.screenshot({ path: info.outputPath('real-pairing-disconnected.png') });
  } finally {
    const disabled = await request.patch(`/api/v1/accounts/${pairing.connection.account_id}`, { headers, data: { disabled: true } });
    expect(disabled.ok()).toBe(true);
  }
});
