// Comment dots: a dot in a card's bottom-right corner when a comment is new to you since you last
// opened the card. Gray for an agent's, blue for a person's, pulsing blue when you are mentioned.
// It arrives live (no reload) and opening the card clears it.
import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

test('gray for an agent, blue for a person, pulsing for a mention; opening clears', async ({ page }) => {
  const { alex, agent } = seed();
  const handle = `pat${Date.now().toString(36)}`;
  const pat = await api<{ id: string }>('POST', '/accounts', alex, { handle, display_name: 'Pat', kind: 'person' });
  const patTok = (await api<{ token: string }>('POST', `/accounts/${pat.id}/tokens`, alex, { scopes: ['write'] })).token;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Dots', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Dot list' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Book the venue' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Book the venue' });
  await expect(card).toBeVisible();
  await expect(card.locator('[data-dot]')).toHaveCount(0);

  await api('POST', `/items/${item.id}/comments`, agent, { body_md: 'Started on it.' });
  await expect(card.locator('[data-dot="agent"]')).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, patTok, { body_md: 'Which date?' });
  await expect(card.locator('[data-dot="human"]')).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, agent, { body_md: '@alex need your pick' });
  const dot = card.locator('[data-dot="mention"]');
  await expect(dot).toBeVisible();
  const box = (await dot.boundingBox())!;
  const cardBox = (await card.boundingBox())!;
  // Bottom-right corner.
  expect(box.x).toBeGreaterThan(cardBox.x + cardBox.width - 30);
  expect(box.y).toBeGreaterThan(cardBox.y + cardBox.height - 30);
  expect(await dot.evaluate((el) => getComputedStyle(el).animationName)).toBe('card-dot-pulse');

  await card.getByText('Book the venue').click();
  await expect(page.locator('.tw')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card.locator('[data-dot]')).toHaveCount(0);

  // Read on the server too: a fresh load shows no dot.
  await page.reload();
  await expect(card).toBeVisible();
  await expect(card.locator('[data-dot]')).toHaveCount(0);

  // My own comment never lights it.
  await api('POST', `/items/${item.id}/comments`, alex, { body_md: 'Friday.' });
  await page.waitForTimeout(600);
  await expect(card.locator('[data-dot]')).toHaveCount(0);
});
