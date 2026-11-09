// The build's revision sits by the Slate name: `r<count>` from a checkout or the image.
import { expect, test } from '@playwright/test';
import { seed } from './seed';

test('the sidebar shows the build revision from /healthz', async ({ page, request }) => {
  const health = (await (await request.get('/healthz')).json()) as { rev: string; commit: string | null };
  expect(health.rev).toMatch(/^(\d+|dev)$/); // `dev` only from a shallow clone (CI)
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto('/#/home');
  const rev = page.locator('.nav-brand .brand-rev');
  await expect(rev).toHaveText(health.rev === 'dev' ? 'dev' : `r${health.rev}`);
  await expect(rev).toHaveAttribute('title', `Build ${health.commit}`);
});
