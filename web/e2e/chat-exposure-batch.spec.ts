import { expect, test } from '@playwright/test';
import { api, signIn } from './support';

test('messages scrolled into view are read in one call, not one call each', async ({ page }) => {
  const section = await api('POST', '/sections', { name: 'Batch Room', audience: 'work' });
  const key = `section:${section.id}`;
  const reader = await api('POST', '/accounts', { handle: 'batch-reader', display_name: 'Batch Reader', kind: 'person' });
  const { token } = await api('POST', `/accounts/${reader.id}/tokens`, { scopes: ['read', 'write'], label: 'batch test' });
  for (let i = 0; i < 12; i++) await api('POST', `/channels/${key}/messages`, { body_md: `Short note ${i}` });
  const reads: string[][] = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/channels\/[^/]+\/read$/.test(new URL(req.url()).pathname)) {
      const ids = (req.postDataJSON() as { message_ids?: string[] }).message_ids;
      if (ids) reads.push(ids);
    }
  });
  await signIn(page, token);
  await page.goto(`/#/section/${section.id}/chat`);
  await expect(page.getByText('Short note 11', { exact: true })).toBeVisible();
  await expect.poll(async () => (await api('GET', `/channels/${key}`, undefined, token)).unread).toBeLessThan(12);
  const exposed = reads.flat().length;
  expect(exposed).toBeGreaterThan(1);
  expect(reads.length).toBeLessThan(exposed);
});
