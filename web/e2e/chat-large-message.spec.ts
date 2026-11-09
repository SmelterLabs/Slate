import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api(path: string, body?: unknown, token = seed().alex) {
  const r = await fetch(`${BASE_URL}/api/v1${path}`, { method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.ok).toBe(true); return r.json();
}
async function prepare(page: Page) {
  const section = await api('/sections', { name: `Long note review ${Date.now()}`, audience: 'work' });
  const channel = `section:${section.id}`;
  const root = await api(`/channels/${channel}/messages`, { body_md: Array.from({ length: 80 }, (_, i) => `Paragraph ${i}: an intentionally lengthy project update.`).join('\n\n') }, seed().agent);
  await api(`/channels/${channel}/messages`, { body_md: 'Unopened reply must remain unread', parent_id: root.id }, seed().agent);
  await page.addInitScript(token => localStorage.setItem('slate.token', token), seed().alex);
  await page.goto(`/#/section/${section.id}/chat`);
  const article = page.locator(`.chat-main [data-message="${root.id}"]`);
  await expect(article).toBeVisible();
  await page.waitForTimeout(800); // Longer than exposure dwell: a partial long note is not a read.
  expect((await api(`/channels/${channel}`)).unread).toBe(2);
  return { article, channel };
}

test('explicitly reading a long message does not clear its unopened replies', async ({ page }) => {
  const { article, channel } = await prepare(page);
  await article.evaluate(node => node.scrollIntoView({ block: 'start' }));
  await article.hover({ position: { x: 100, y: 20 } });
  await article.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Mark this message read' }).click();
  await expect.poll(async () => (await api(`/channels/${channel}`)).unread).toBe(1);
  await expect(page.getByText('Message marked read', { exact: true })).toBeVisible();
  expect(Object.values((await api(`/channels/${channel}`)).unread_threads)).toEqual([1]);
});

test('failed explicit read says it failed and preserves the durable count', async ({ page }) => {
  const { article, channel } = await prepare(page);
  await page.route('**/api/v1/channels/**/read', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Read receipt unavailable' }) }));
  await article.evaluate(node => node.scrollIntoView({ block: 'start' }));
  await article.hover({ position: { x: 100, y: 20 } });
  await article.getByRole('button', { name: 'More actions' }).click();
  await page.getByRole('menuitem', { name: 'Mark this message read' }).click();
  await expect(page.getByText('Read receipt unavailable', { exact: true })).toBeVisible();
  expect((await api(`/channels/${channel}`)).unread).toBe(2);
});
