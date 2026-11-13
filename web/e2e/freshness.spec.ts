import { expect, test } from '@playwright/test';
import { signIn } from './support';

// A board left open across a deploy reloads into the new build by itself, but never while
// someone is typing. The server names its bundle in /healthz `board`.

test.use({ serviceWorkers: 'block' });

async function open(page: import('@playwright/test').Page) {
  await signIn(page);
  // Count the board's reloads: the navigate event fires as location.reload() starts one, and
  // sessionStorage outlives it.
  await page.addInitScript(() => {
    const nav = (window as unknown as { navigation?: EventTarget }).navigation;
    nav?.addEventListener('navigate', (e) => {
      if ((e as Event & { navigationType?: string }).navigationType !== 'reload') return;
      sessionStorage.setItem('reloads', String(Number(sessionStorage.getItem('reloads') ?? 0) + 1));
    });
  });
  // The SSE connection starts a build check too. Let it finish before simulating
  // visibility: freshness intentionally coalesces a check while one is in flight.
  const initialCheck = page.waitForResponse((r) => new URL(r.url()).pathname === '/healthz');
  await page.goto('/#/everything');
  const response = await initialCheck;
  expect(response.ok()).toBe(true);
  await response.finished();
  await expect(page.getByRole('toolbar', { name: 'View options' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'My Work' })).toBeVisible();
}

const reloads = (page: import('@playwright/test').Page) =>
  page.evaluate(() => Number(sessionStorage.getItem('reloads') ?? 0));

/** The next check gets a /healthz that names `board` (the real one when omitted) and a marked
 * rev, so the test sees in the corner label that the board has read the answer. A reload, if
 * the answer calls for one, starts in the same turn as that read. */
async function serveHealth(page: import('@playwright/test').Page, rev: string, board?: string) {
  await page.route('**/healthz', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, rev, board: board ?? body.board } });
  }, { times: 1 });
}
const answered = (page: import('@playwright/test').Page, rev: string) =>
  expect(page.locator('.brand-rev').first()).toHaveText(rev);

/** The next check sees the server name a different bundle, as after a deploy. */
async function deployNewBuild(page: import('@playwright/test').Page) {
  let served = false;
  await page.route('**/healthz', async (route) => {
    // Once: after the reload the page runs "the new build", so later checks see the real one.
    if (served) return route.continue();
    served = true;
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), board: '/assets/index-NEWBUILD.js' } });
  });
}

const show = (page: import('@playwright/test').Page) =>
  page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));

test('an open board reloads into a new deploy when the tab is shown again', async ({ page }) => {
  await open(page);
  await page.evaluate(() => ((window as unknown as { stale: boolean }).stale = true));
  await deployNewBuild(page);
  const reloaded = page.waitForEvent('load');
  await show(page);
  await reloaded;
  await expect(page.getByRole('link', { name: 'My Work' })).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { stale?: boolean }).stale)).toBeUndefined();
  expect(await reloads(page), 'the reload counter sees a reload').toBe(1);
});

test('no reload when the deployed build is the one running', async ({ page }) => {
  await open(page);
  await page.evaluate(() => ((window as unknown as { stale: boolean }).stale = true));
  await serveHealth(page, 'same-build');
  await show(page);
  await answered(page, 'same-build');
  expect(await reloads(page)).toBe(0);
  expect(await page.evaluate(() => (window as unknown as { stale?: boolean }).stale)).toBe(true);
});

test('waits for typing to finish before reloading', async ({ page }) => {
  await open(page);
  await page.evaluate(() => ((window as unknown as { stale: boolean }).stale = true));
  const search = page.getByRole('searchbox').first();
  await search.click();
  await expect(search).toBeFocused();
  await serveHealth(page, 'new-build', '/assets/index-NEWBUILD.js');
  await show(page);
  await answered(page, 'new-build');
  expect(await reloads(page), 'no reload while typing').toBe(0);
  expect(await page.evaluate(() => (window as unknown as { stale?: boolean }).stale)).toBe(true);
  const reloaded = page.waitForEvent('load');
  await search.blur();
  await reloaded;
  await expect(page.getByRole('link', { name: 'My Work' })).toBeVisible();
});
