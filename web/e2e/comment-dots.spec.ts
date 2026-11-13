// Comment dots: a dot in a card's bottom-right corner when a comment is new to you since you last
// opened the card. Gray for an agent's, blue for a person's, pulsing blue when you are mentioned.
// It arrives live (no reload) and opening the card clears it.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('gray for an agent, blue for a person, pulsing for a mention; opening clears', async ({ page }) => {
  const { agent } = seed();
  const handle = `pat${Date.now().toString(36)}`;
  const pat = await api<{ id: string }>('POST', '/accounts', { handle, display_name: 'Pat', kind: 'person' });
  const patTok = (await api<{ token: string }>('POST', `/accounts/${pat.id}/tokens`, { scopes: ['write'] })).token;
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Dots', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Dot list' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Book the venue' });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Book the venue' });
  await expect(card).toBeVisible();
  await expect(card.locator('[data-dot]')).toHaveCount(0);

  await api('POST', `/items/${item.id}/comments`, { body_md: 'Started on it.' }, agent);
  await expect(card.locator('[data-dot="agent"]')).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, { body_md: 'Which date?' }, patTok);
  await expect(card.locator('[data-dot="human"]')).toBeVisible();

  await api('POST', `/items/${item.id}/comments`, { body_md: '@alex need your pick' }, agent);
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

  // My own comment never lights it. An agent's comment on another card, made after it, does:
  // once that dot shows, the board has reloaded the dots since my comment.
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Friday.' });
  const other = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Order the cake' });
  const otherCard = page.locator('.card', { hasText: 'Order the cake' });
  await expect(otherCard).toBeVisible();
  await api('POST', `/items/${other.id}/comments`, { body_md: 'Chocolate?' }, agent);
  await expect(otherCard.locator('[data-dot="agent"]')).toBeVisible();
  await expect(card.locator('[data-dot]')).toHaveCount(0);
});
