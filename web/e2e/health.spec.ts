// Health: the owner sees the verdict, every check, the background jobs and calls per token
// (an agent's failed call shows up with its route and status), the same sections on a phone,
// and finds the page from the palette. An agent (not an owner or admin) is refused.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { BASE_URL, seed, signIn } from './support';

const shots = process.env.SLATE_HEALTH_SHOTS ?? null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string, fullPage = true) {
  if (!shots) return;
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(shots, `${name}.png`), fullPage });
}

async function asAgent(path: string, init: RequestInit = {}) {
  return fetch(`${BASE_URL}/api/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().agent}` },
  });
}

test('the owner reads Slate health; an agent call that failed is listed', async ({ page }) => {
  expect((await asAgent('/items/Zz0Zz0Zz0Zz0')).status).toBe(404);
  expect((await asAgent('/items', { method: 'POST', body: '{"title":"no list"}' })).status).toBe(422);
  expect((await asAgent('/health')).status).toBe(403); // not an owner or admin

  const pub = await fetch(`${BASE_URL}/health`);
  expect(pub.status).toBe(200);
  expect((await pub.json()).checks.length).toBeGreaterThan(3);

  await signIn(page);
  await page.goto('/#/health');
  const main = page.getByRole('region', { name: 'Health' });
  await expect(main.getByRole('heading', { name: 'Health', level: 1 })).toBeVisible();
  await expect(main.getByRole('status')).toContainText(/Everything is working|something to look at|broken/);
  await expect(main.locator('[data-key="agents:errors"]')).toBeVisible();
  // The scheduler is off in the e2e server: the jobs say so instead of failing.
  await expect(main.locator('[data-poller="reminders"]')).toContainText('Reminders');
  await expect(main.locator('[data-poller="reminders"]')).toContainText('Off');
  await expect(main.getByText(/Turned off or not set up:/)).toBeVisible();

  const row = main.getByRole('row').filter({ hasText: /^\s*Agent\b/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText('3 4xx'); // the 403 counts too
  await expect(main.locator('.health-calls').first()).toContainText('POST /api/v1/items');
  await expect(main.locator('.health-calls').first()).toContainText('422');
  await shot(page, 'health-desktop');
  await main.getByRole('heading', { name: /^Agents and tokens/ }).scrollIntoViewIfNeeded();
  await shot(page, 'health-desktop-agents');

  // The nav has it for an owner.
  await expect(page.locator('nav').first().getByRole('link', { name: 'Health', exact: true })).toBeVisible();
});

test('the palette opens Health, and a phone shows the same sections', async ({ page }) => {
  await signIn(page);
  await page.goto('/#/home');
  await page.keyboard.press('Control+k');
  await page.keyboard.type('health');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/health/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  const main = page.getByRole('region', { name: 'Health' });
  for (const h of ['Checks', 'Background jobs', 'Agents and tokens', 'Response time', 'Automations', 'Queues and storage']) {
    await expect(main.getByRole('heading', { name: new RegExp(`^${h}`) })).toBeVisible();
  }
  // No sideways scroll on a phone.
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(wide).toBe(false);
  await shot(page, 'health-phone');
  await shot(page, 'health-phone-top', false);
});
