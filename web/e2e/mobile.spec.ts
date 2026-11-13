// Phone and installed-app (PWA) checks: manifest, icons, service worker shell cache, and a
// layout that fits a 390 px touch screen without sideways scrolling or iOS focus zoom.
import { expect, test } from '@playwright/test';
import { BASE_URL, seed, signedIn } from './support';

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

test('installable: manifest, icons and theme are served', async ({ page, request }) => {
  await page.goto('/');
  const href = await page.locator('link[rel="manifest"]').getAttribute('href');
  const res = await request.get(href!);
  expect(res.ok()).toBe(true);
  expect(res.headers()['content-type']).toContain('manifest+json');
  const manifest = await res.json();
  expect(manifest).toMatchObject({ name: 'Slate', display: 'standalone', start_url: '/' });
  const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes);
  expect(sizes).toEqual(expect.arrayContaining(['192x192', '512x512']));
  expect(manifest.icons.some((i: { purpose: string }) => i.purpose === 'maskable')).toBe(true);
  // The share sheet target: a GET to the app's root with the shared title, text and url.
  expect(manifest.share_target).toMatchObject({
    action: '/',
    method: 'GET',
    params: { title: 'title', text: 'text', url: 'url' },
  });
  for (const icon of manifest.icons) expect((await request.get(icon.src)).ok()).toBe(true);
  const apple = await page.locator('link[rel="apple-touch-icon"]').getAttribute('href');
  expect((await request.get(apple!)).ok()).toBe(true);
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute('content', /viewport-fit=cover/);
});

test('a page shared from the phone opens quick add prefilled, with the link in the description', async ({ page }) => {
  // How the share sheet launches the installed app (manifest share_target, GET).
  await signedIn(page, '/?title=How%20to%20file%20expenses&text=Worth%20a%20read&url=https%3A%2F%2Fexample.com%2Fexpenses');
  await expect(page).toHaveURL(/#\/everything$/); // the params leave the address at once
  const input = page.getByLabel('Quick add');
  await expect(input).toHaveValue('How to file expenses');
  await expect(page.locator('.qa-preview')).toContainText('example.com/expenses');
  await input.press('Enter');
  await expect(input).toHaveValue('');
  const headers = { Authorization: `Bearer ${seed().alex}` };
  await expect
    .poll(async () => {
      const items = (await (await fetch(`${BASE_URL}/api/v1/items?q=file%20expenses`, { headers })).json()) as { id: string }[];
      if (!items.length) return null;
      const item = (await (await fetch(`${BASE_URL}/api/v1/items/${items[0].id}`, { headers })).json()) as { body_md: string | null };
      return item.body_md;
    })
    .toContain('https://example.com/expenses');
  // A bare link becomes a Read item, through the in-app route too.
  await page.goto('/#/share?url=https%3A%2F%2Fexample.com%2Fnotes%2F7');
  await expect(page).not.toHaveURL(/share/);
  await expect(input).toHaveValue('Read example.com/notes/7');
  await page.getByRole('button', { name: 'drop the note' }).click();
  await expect(page.locator('.qa-preview')).not.toContainText('example.com');
});

test('the service worker caches the shell, so the app opens offline', async ({ page, context }) => {
  await page.goto('/');
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload(); // now controlled by the worker
  await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);

  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Slate' })).toBeVisible();
  await context.setOffline(false);
});

test('the service worker never answers API calls', async ({ page }) => {
  await signedIn(page, `/#/list/${seed().listId}`);
  await page.evaluate(() => navigator.serviceWorker.ready);
  const api: boolean[] = [];
  page.on('response', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api/')) api.push(r.fromServiceWorker());
  });
  await page.reload();
  await expect(page.locator('.column')).toHaveCount(3);
  expect(api.length).toBeGreaterThan(0);
  const fromWorker = api.some(Boolean);
  expect(fromWorker).toBe(false);
});

test('fits a phone: no sideways page scroll, 16 px inputs, menus stay on screen', async ({ page }) => {
  await signedIn(page, `/#/list/${seed().listId}`);
  await expect(page.locator('.column')).toHaveCount(3);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);

  const size = await page.locator('#quick-add').evaluate((el) => getComputedStyle(el).fontSize);
  expect(parseFloat(size)).toBeGreaterThanOrEqual(16);

  await page.getByRole('button', { name: 'Customize' }).click();
  const panel = await page.locator('.dd-panel').boundingBox();
  expect(panel!.x).toBeGreaterThanOrEqual(0);
  expect(panel!.x + panel!.width).toBeLessThanOrEqual(390);
  expect(panel!.y + panel!.height).toBeLessThanOrEqual(844);

  // Customize is a tall sheet on a phone; Escape closes it (a tap outside does too).
  await page.keyboard.press('Escape');
  await page.locator('.card').first().click();
  await expect(page.locator('.tw')).toBeVisible();
  const overflow = await page.locator('.tw-body').evaluate((el) => el.scrollWidth > el.clientWidth);
  expect(overflow).toBe(false);
});
