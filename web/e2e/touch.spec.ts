// Drag by finger (lib/touchdrag.ts) on an iPhone and an iPad, and manual order by mouse on a
// desktop: real touch events through Chromium's DevTools protocol, so the browser scrolls,
// fires its own long-press menu and synthesizes clicks exactly as it would under a finger.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { api, seed, signedIn } from './support';

// SLATE_SCREENSHOTS=1 writes screenshots into docs/img/.
const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: join(shots, `${name}.png`) });
}

interface Made {
  listId: string;
  ids: Record<string, string>;
}

// Lists made here are archived afterwards, so later specs see the Everything board they expect.
const made: string[] = [];
test.afterAll(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/lists/${id}`, { archived: true });
});

/** A fresh list (To do / Doing / Done) with the given cards, so tests don't share state. */
async function makeList(name: string, titles: string[]): Promise<Made> {
  const list = await api<{ id: string }>('POST', '/lists', {
    section_id: seed().sectionId,
    name,
    statuses: [
      { name: 'To do', kind: 'open' },
      { name: 'Doing', kind: 'active' },
      { name: 'Done', kind: 'done' },
    ],
  });
  made.push(list.id);
  const ids: Record<string, string> = {};
  for (const title of titles) ids[title] = (await api<{ id: string }>('POST', '/items', { list_id: list.id, title })).id;
  return { listId: list.id, ids };
}

type Pt = { x: number; y: number };

async function center(page: Page, selector: string): Promise<Pt> {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no box for ${selector}`);
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** A finger: down, hold, then slide through `path` (each point in `steps` moves), and up. */
type Step = Pt | (() => Promise<Pt>) | number | { until: () => Promise<boolean> } | { run: () => Promise<void> };

async function finger(page: Page, from: Pt, path: Step[], hold = 500) {
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', p?: Pt) =>
    cdp.send('Input.dispatchTouchEvent', { type, touchPoints: p ? [{ x: p.x, y: p.y }] : [] });
  await touch('touchStart', from);
  await page.waitForTimeout(hold);
  let at = from;
  for (const step of path) {
    if (typeof step === 'number') {
      // Rest in place: auto-scroll keeps going while the finger is still.
      const end = Date.now() + step;
      while (Date.now() < end) {
        await touch('touchMove', at);
        await page.waitForTimeout(50);
      }
      continue;
    }
    if ('run' in step) {
      await step.run(); // mid-gesture: a screenshot, a check
      continue;
    }
    if ('until' in step) {
      // Rest in place until the page has scrolled far enough (auto-scroll at an edge).
      const end = Date.now() + 5000;
      while (!(await step.until()) && Date.now() < end) {
        await touch('touchMove', at);
        await page.waitForTimeout(16);
      }
      continue;
    }
    const to = typeof step === 'function' ? await step() : step;
    for (let i = 1; i <= 8; i++) {
      await touch('touchMove', { x: at.x + ((to.x - at.x) * i) / 8, y: at.y + ((to.y - at.y) * i) / 8 });
      await page.waitForTimeout(16);
    }
    at = to;
  }
  await touch('touchEnd');
  await cdp.detach();
}

async function statusOf(id: string) {
  return (await api<{ status: { name: string } }>('GET', `/items/${id}`)).status.name;
}

async function order(listId: string, status: string): Promise<string[]> {
  const items = await api<{ title: string; position: number; status: { name: string } }[]>(
    'GET',
    `/items?list_id=${listId}&limit=100`,
  );
  return items
    .filter((i) => i.status.name === status)
    .sort((a, b) => a.position - b.position)
    .map((i) => i.title);
}

const DAY = 86_400_000;
const atDay = (n: number) => new Date(Date.now() + n * DAY).toISOString();

/** A sideways swipe: no hold, a quick slide. */
async function swipeRow(page: Page, selector: string, dx: number) {
  const c = await center(page, selector);
  await finger(page, c, [{ x: c.x + dx, y: c.y }], 0);
}

test.describe('iPhone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('long-press lifts a card; dragging to the edge scrolls the board to the next column', async ({ page }) => {
    const { listId, ids } = await makeList('Touch board', ['Lift me', 'Stay put']);
    await signedIn(page, `/#/list/${listId}`);
    await expect(page.locator('.column')).toHaveCount(3);
    const start = await center(page, '.card:has-text("Lift me")');
    const before = await page.locator('.kanban').evaluate((el) => el.scrollLeft);

    await finger(page, start, [
      { x: 386, y: start.y }, // the right edge: the board scrolls along
      {
        until: async () => {
          const box = await page.locator('.column[data-status="Doing"]').boundingBox();
          return !!box && box.x < 150;
        },
      },
      { x: 195, y: start.y }, // back to the middle of the screen, over Doing
      300,
      { run: () => shot(page, 'touch-drag-phone') },
    ]);
    await expect.poll(() => statusOf(ids['Lift me'])).toBe('Doing');
    expect(await page.locator('.kanban').evaluate((el) => el.scrollLeft)).toBeGreaterThan(before);
    await expect(page.locator('.touch-ghost')).toHaveCount(0);
    // The finger lifting did not open the task underneath.
    await expect(page.locator('.tw')).toHaveCount(0);
  });

  test('a quick swipe scrolls instead of dragging, and a tap still opens the task', async ({ page }) => {
    const { listId, ids } = await makeList('Touch swipe', ['Swipe past me']);
    await signedIn(page, `/#/list/${listId}`);
    const start = await center(page, '.card:has-text("Swipe past me")');
    await finger(page, start, [{ x: start.x - 200, y: start.y }], 0);
    await page.waitForTimeout(300);
    await expect(page.locator('.touch-ghost')).toHaveCount(0);
    expect(await statusOf(ids['Swipe past me'])).toBe('To do');

    await page.locator('.kanban').evaluate((el) => (el.scrollLeft = 0));
    await page.locator('.card', { hasText: 'Swipe past me' }).tap();
    await expect(page.locator('.tw')).toBeVisible();
  });

  test('hold and let go without moving opens the card menu', async ({ page }) => {
    const { listId } = await makeList('Touch menu', ['Hold me']);
    await signedIn(page, `/#/list/${listId}`);
    const start = await center(page, '.card:has-text("Hold me")');
    await finger(page, start, [], 600);
    await expect(page.locator('.card-menu, .menu-pop, .dd-panel').first()).toBeVisible();
    await expect(page.locator('.tw')).toHaveCount(0);
    await shot(page, 'touch-hold-menu');
  });
  test('swipe a My Work row right to complete, left to snooze to tomorrow', async ({ page }) => {
    const { listId, ids } = await makeList('Touch swipes', []);
    for (const title of ['Swipe done', 'Swipe later']) {
      ids[title] = (
        await api<{ id: string }>('POST', '/items', { list_id: listId, title, assignees: ['alex'], due_at: atDay(-2) })
      ).id;
    }
    await signedIn(page, '/#/home');
    await expect(page.locator('.work-row', { hasText: 'Swipe done' })).toBeVisible();
    if (shots) {
      const c = await center(page, '.work-row:has-text("Swipe done")');
      await finger(page, c, [{ x: c.x + 120, y: c.y }, { run: () => shot(page, 'touch-swipe') }, { x: c.x, y: c.y }], 0);
    }
    await swipeRow(page, '.work-row:has-text("Swipe done")', 160);
    await expect.poll(() => statusOf(ids['Swipe done'])).toBe('Done');

    await swipeRow(page, '.work-row:has-text("Swipe later")', -160);
    await expect(page.locator('.toast')).toContainText('Snoozed');
    await expect
      .poll(async () => {
        const it = await api<{ due_at: string }>('GET', `/items/${ids['Swipe later']}`);
        return Math.round((Date.parse(it.due_at) - Date.now()) / DAY);
      })
      .toBeGreaterThanOrEqual(0);
    // A short swipe springs back and changes nothing.
    const before = await api<{ due_at: string }>('GET', `/items/${ids['Swipe later']}`);
    await swipeRow(page, '.work-row:has-text("Swipe later")', -40);
    await page.waitForTimeout(400);
    expect((await api<{ due_at: string }>('GET', `/items/${ids['Swipe later']}`)).due_at).toBe(before.due_at);
    await expect(page.locator('.swipe-under')).toHaveCount(0);
  });

  test('pull down at the top to refresh', async ({ page }) => {
    const { listId } = await makeList('Touch pull', ['Already here']);
    await signedIn(page, `/#/list/${listId}`);
    await expect(page.locator('.card', { hasText: 'Already here' })).toBeVisible();
    const reloads: string[] = [];
    page.on('request', (r) => {
      if (new URL(r.url()).pathname === '/api/v1/lists') reloads.push(r.url());
    });
    const c = await center(page, '.card:has-text("Already here")');
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: c.x, y: c.y }] });
    for (let i = 1; i <= 12; i++) {
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: c.x, y: c.y + i * 18 }] });
      await page.waitForTimeout(16);
    }
    await expect(page.locator('.ptr.armed')).toBeVisible();
    await shot(page, 'touch-pull-refresh');
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await expect.poll(() => reloads.length).toBeGreaterThan(0);
    await expect(page.locator('.ptr')).toHaveCount(0);
    await expect(page.locator('.card', { hasText: 'Already here' })).toBeVisible();
  });

  test('offline: a banner says so, and a change says it was not saved', async ({ page, context }) => {
    const { listId } = await makeList('Touch offline', ['No signal']);
    await signedIn(page, `/#/list/${listId}`);
    const card = page.locator('.card', { hasText: 'No signal' });
    await expect(card).toBeVisible();
    // Hold for the card menu first: a fresh DevTools session resets the offline emulation.
    await finger(page, await center(page, '.card:has-text("No signal")'), [], 600);
    await context.setOffline(true);
    await expect(page.locator('.banner.offline')).toBeVisible();
    await page.locator('.card-menu').getByRole('menuitem', { name: 'Complete' }).tap();
    await expect(page.locator('.toast')).toContainText('offline');
    await shot(page, 'touch-offline');
    await context.setOffline(false);
    await expect(page.locator('.banner.offline')).toHaveCount(0);
  });
});

test.describe('iPad', () => {
  test.use({ viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true });

  test('drag a card between two others: it stays in that order', async ({ page }) => {
    const { listId } = await makeList('Touch order', ['First', 'Second', 'Third']);
    await signedIn(page, `/#/list/${listId}`);
    await expect(page.locator('.column[data-status="To do"] .card')).toHaveCount(3);
    const third = await center(page, '.card:has-text("Third")');
    const first = await page.locator('.card:has-text("First")').boundingBox();
    await finger(page, third, [{ x: third.x, y: first!.y + 4 }, 200, { run: () => shot(page, 'touch-reorder-ipad') }]);
    await expect.poll(() => order(listId, 'To do')).toEqual(['Third', 'First', 'Second']);
    await expect(page.locator('.column[data-status="To do"] .card .title, .column[data-status="To do"] .card')).not.toHaveCount(0);
  });

  test('reorder checklist entries by finger', async ({ page }) => {
    const { listId, ids } = await makeList('Touch checklist', ['Has a checklist']);
    await api('POST', `/items/${ids['Has a checklist']}/checklists`, { name: 'Steps', items: ['One', 'Two', 'Three'] });
    await signedIn(page, `/#/list/${listId}`);
    await page.locator('.card', { hasText: 'Has a checklist' }).tap();
    const three = page.locator('.checklist li', { hasText: 'Three' });
    await three.scrollIntoViewIfNeeded();
    const from = await center(page, '.checklist li:has-text("Three") .grip');
    const one = await page.locator('.checklist li', { hasText: 'One' }).boundingBox();
    await finger(page, from, [{ x: from.x, y: one!.y + 3 }, 200]);
    await expect
      .poll(async () => {
        const [cl] = await api<{ items: { title: string; position: number }[] }[]>(
          'GET',
          `/items/${ids['Has a checklist']}/checklists`,
        );
        return [...cl.items].sort((a, b) => a.position - b.position).map((e) => e.title);
      })
      .toEqual(['Three', 'One', 'Two']);
  });
});

test.describe('desktop', () => {
  test('drag a card between two others with the mouse keeps that order', async ({ page }) => {
    const { listId } = await makeList('Mouse order', ['Alpha', 'Bravo', 'Charlie']);
    await signedIn(page, `/#/list/${listId}`);
    const target = page.locator('.card', { hasText: 'Alpha' });
    await page.locator('.card', { hasText: 'Charlie' }).dragTo(target, { targetPosition: { x: 20, y: 3 } });
    await expect.poll(() => order(listId, 'To do')).toEqual(['Charlie', 'Alpha', 'Bravo']);
  });
});
