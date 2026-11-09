import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api(path: string, body?: unknown, token = seed().alex, method = body === undefined ? 'GET' : 'POST') {
  const r = await fetch(`${BASE_URL}/api/v1${path}`, { method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  expect(r.ok).toBe(true); return r.status === 204 ? null : r.json();
}

test('phone top bar fits when Needs you, agent replies and Inbox are all populated', async ({ browser }) => {
  const s = seed();
  const agent = await api('/me', undefined, s.agent);
  const task = await api('/items', { list_id: s.listId, title: 'Review phone layout' });
  const messages: string[] = [];
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  try {
    await api(`/items/${task.id}/comments`, { body_md: 'Please review', assignee: 'alex' }, s.agent);
    for (let i = 0; i < 3; i++) {
      const message = await api(`/channels/agent:${agent.account.id}/messages`, { body_md: `@alex Agent response ${i}` }, s.agent);
      messages.push(message.id);
    }
    const channels: { key: string; unread: number }[] = await api('/channels');
    const unread = channels.filter(c => c.key.startsWith('agent:')).reduce((n, c) => n + c.unread, 0);
    expect(unread).toBeGreaterThanOrEqual(3);
    const page = await ctx.newPage();
    await page.addInitScript(t => localStorage.setItem('slate.token', t), s.alex);
    await page.goto('/#/members');
    const bar = page.locator('.topbar');
    await expect(bar.getByRole('link', { name: /^Needs you:/ })).toBeVisible();
    await expect(bar.getByRole('button', { name: `Chat with an agent, ${unread} unread` })).toBeVisible();
    await expect(bar.getByRole('link', { name: /^Inbox,/ })).toBeVisible();
    for (const width of [390, 360, 320]) {
      await page.setViewportSize({ width, height: 844 });
      const geometry = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth, bar: [...document.querySelector('.topbar')!.children].map(n => ({ cls: n.className, width: n.getBoundingClientRect().width, right: n.getBoundingClientRect().right })) }));
      expect(geometry.width, JSON.stringify(geometry)).toBeLessThanOrEqual(width);
      for (const control of await bar.locator('button,a').all()) await expect(control).toBeInViewport();
    }
  } finally {
    // Don't leave an extra Needs you section or unread replies in later shared-fixture specs.
    await ctx.close();
    await api(`/items/${task.id}/archive`, {}, s.alex);
    for (const id of messages) await api(`/messages/${id}`, undefined, s.agent, 'DELETE');
  }
});
