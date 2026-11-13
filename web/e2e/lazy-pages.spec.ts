// Heavy pages load on demand: the board's first load does not carry Docs, Agents, Members,
// Audit, Health, Archive or Chat. A page whose file can't be fetched says so and can try again.
import { expect, test } from '@playwright/test';
import { signIn } from './support';

// The service worker would fetch the chunk itself, out of reach of page.route.
test.use({ serviceWorkers: 'block' });

const PAGES = ['AgentsPage', 'DocsPage', 'MembersPage', 'AuditPage', 'HealthPage', 'ArchivePage', 'ChatPage'];

test('the first load names no page chunk; the board works without one', async ({ page, request }) => {
  const html = await (await request.get('/')).text();
  for (const name of PAGES) expect(html).not.toContain(`${name}-`);

  // Docs can't be fetched at all: the board still opens. Its one background fetch of the page
  // (lib/lazy.svelte.ts) is refused too before Docs is opened; one that ran later, after the
  // file is back, would bring the page in by itself and take Try again away before the click.
  let refused = 0;
  await page.route(/\/assets\/DocsPage-[^/]+\.js$/, (r) => {
    refused++;
    return r.abort();
  });
  await signIn(page);
  await page.goto('/#/everything');
  await expect(page.locator('.column').first()).toBeVisible();
  await expect.poll(() => refused).toBeGreaterThan(0);

  // Opening it says what went wrong; once the file is there again, Try again opens the page.
  await page.goto('/#/docs');
  const failed = page.getByRole('alert').filter({ hasText: "This page didn't load" });
  await expect(failed).toBeVisible();
  await page.unroute(/\/assets\/DocsPage-[^/]+\.js$/);
  await failed.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('heading', { name: 'Docs & Files', level: 1 })).toBeVisible();
});

test('a page opened first, by its link, loads on its own', async ({ page }) => {
  const loaded: string[] = [];
  page.on('request', (r) => {
    const m = /\/assets\/([A-Za-z]+Page)-[^/]+\.js$/.exec(r.url());
    if (m) loaded.push(m[1]);
  });
  await signIn(page);
  await page.goto('/#/agents');
  await expect(page.getByRole('heading', { name: 'Agents', level: 1 })).toBeVisible();
  expect(loaded[0]).toBe('AgentsPage');
});
