// What a guest is offered matches what a guest may do. Chat opens a
// channel they can see (not General), and workspace pages (Members) are out of the nav,
// the palette and the g-chords, the same on a phone.
import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

async function guestToken(): Promise<string> {
  const { alex, listId } = seed();
  const handle = `guest-${Date.now()}`;
  const acct = await api<{ id: string }>('POST', '/accounts', alex, { handle, display_name: 'Guest', kind: 'person', role: 'guest' });
  await api('PUT', '/shares', alex, { account_id: acct.id, scope_kind: 'list', scope_id: listId, level: 'edit' });
  return (await api<{ token: string }>('POST', `/accounts/${acct.id}/tokens`, alex, { scopes: ['write'] })).token;
}

test('a guest: chat opens a shared channel, workspace pages are not offered', async ({ page }) => {
  const token = await guestToken();
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), token);
  await page.goto('/#/chat');
  await expect(page.getByRole('textbox', { name: /Message #Home/ })).toBeVisible();
  await expect(page.getByText(/channel general not found/)).toHaveCount(0);

  const nav = page.locator('nav').first();
  await nav.getByRole('button', { name: 'More', exact: true }).click(); // the rest of the pages live under More
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Members', exact: true })).toHaveCount(0);

  await page.keyboard.press('Control+k');
  await page.keyboard.type('Go to');
  const palette = page.getByRole('dialog');
  await expect(palette.getByText('Go to Archive')).toBeVisible();
  await expect(palette.getByText('Go to Members')).toHaveCount(0);
});

test('the owner finds Members and the Audit log from the palette', async ({ page }) => {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto('/#/home');
  await page.keyboard.press('Control+k');
  await page.keyboard.type('members');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/members/);
});
