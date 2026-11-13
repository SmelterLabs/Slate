import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api(path: string, body?: unknown, token = seed().alex) {
  const r = await fetch(`${BASE_URL}/api/v1${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(r.ok).toBe(true);
  return r.json();
}

test('messages scrolled into view are read in one call, not one call each', async ({ page }) => {
  const section = await api('/sections', { name: 'Batch Room', audience: 'work' });
  const key = `section:${section.id}`;
  const reader = await api('/accounts', { handle: 'batch-reader', display_name: 'Batch Reader', kind: 'person' });
  const { token } = await api(`/accounts/${reader.id}/tokens`, { scopes: ['read', 'write'], label: 'batch test' });
  for (let i = 0; i < 12; i++) await api(`/channels/${key}/messages`, { body_md: `Short note ${i}` });
  const reads: string[][] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/channels\/[^/]+\/read$/.test(new URL(req.url()).pathname)) {
      const ids = (req.postDataJSON() as { message_ids?: string[] }).message_ids;
      if (ids) reads.push(ids);
    }
  });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), token);
  await page.goto(`/#/section/${section.id}/chat`);
  await expect(page.getByText('Short note 11', { exact: true })).toBeVisible();
  await expect.poll(async () => (await api(`/channels/${key}`, undefined, token)).unread).toBeLessThan(12);
  const exposed = reads.flat().length;
  expect(exposed).toBeGreaterThan(1);
  expect(reads.length).toBeLessThan(exposed);
});
