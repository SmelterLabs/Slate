// Dragging cards between Kanban columns, by mouse and by finger, on the boards people use most:
// a list grouped by status, the day board (This week), and boards grouped by tag or section.
// Mouse drags go through real mouse moves (not one jump), so the browser runs its own drag.
// A column that can't take a card says why when you let go, instead of snapping back silently.
import { expect, test, type Page } from '@playwright/test';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/when';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

interface Lab {
  sectionId: string;
  listId: string;
  ids: Record<string, string>;
}

// Each test gets its own section (archived afterwards) so the shared boards stay as other specs expect.
const made: string[] = [];
test.afterAll(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/sections/${id}`, { archived: true });
});

const today = () => workspaceDay(new Date());

async function lab(cards: { title: string; due_at?: string; tags?: string[] }[], lists = 1): Promise<Lab> {
  const sectionId = (await api<{ id: string }>('POST', '/sections', { name: `Drag lab ${Date.now()}`, audience: 'personal' })).id;
  made.push(sectionId);
  let listId = '';
  for (let n = 0; n < lists; n++) {
    const id = (
      await api<{ id: string }>('POST', '/lists', {
        section_id: sectionId,
        name: n ? `Drag list ${n + 1}` : 'Drag list',
        statuses: [
          { name: 'To do', kind: 'open' },
          { name: 'Doing', kind: 'active' },
          { name: 'Done', kind: 'done' },
        ],
      })
    ).id;
    listId ||= id;
  }
  const ids: Record<string, string> = {};
  for (const c of cards) ids[c.title] = (await api<{ id: string }>('POST', '/items', { list_id: listId, ...c })).id;
  return { sectionId, listId, ids };
}

async function signedIn(page: Page, path: string) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
}

const column = (page: Page, key: string) => page.locator(`.column[data-group="${key}"]`);
const card = (page: Page, title: string) => page.locator('.card', { hasText: title });

/** A mouse drag the way a hand does it: press, wiggle past the drag threshold, glide, let go. */
async function mouseDrag(page: Page, from: ReturnType<Page['locator']>, to: ReturnType<Page['locator']>) {
  await from.scrollIntoViewIfNeeded();
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2 + 6, { steps: 4 });
  await page.mouse.move(b.x + b.width / 2, b.y + Math.min(60, b.height / 2), { steps: 20 });
  await page.mouse.up();
}

async function get<T>(id: string): Promise<T> {
  return api<T>('GET', `/items/${id}`);
}

async function groupBy(page: Page, name: string) {
  await page.getByRole('button', { name: 'Group by' }).click();
  await page.getByRole('menuitemradio', { name, exact: true }).click();
}

test.describe('mouse', () => {
  test.use({ viewport: { width: 1600, height: 900 } });

  test('a list board: drag a card from To do to Doing and on to Done', async ({ page }) => {
    const l = await lab([{ title: 'Mouse status card' }]);
    await signedIn(page, `/#/list/${l.listId}`);
    await mouseDrag(page, card(page, 'Mouse status card'), page.locator('.column[data-status="Doing"]'));
    await expect(page.locator('.column[data-status="Doing"]')).toContainText('Mouse status card');
    await expect.poll(async () => (await get<{ status: { name: string } }>(l.ids['Mouse status card'])).status.name).toBe('Doing');
    await mouseDrag(page, card(page, 'Mouse status card'), page.locator('.column[data-status="Done"]'));
    await expect.poll(async () => (await get<{ status: { name: string } }>(l.ids['Mouse status card'])).status.name).toBe('Done');
  });

  test('letting go in the empty space under a short column drops into that column', async ({ page }) => {
    const l = await lab([{ title: 'Low drop 1' }, { title: 'Low drop 2' }, { title: 'Low drop 3' }, { title: 'Low drop 4' }]);
    await signedIn(page, `/#/list/${l.listId}`);
    const doing = page.locator('.column[data-status="Doing"]');
    const from = card(page, 'Low drop 4');
    const a = (await from.boundingBox())!;
    const d = (await doing.boundingBox())!;
    // The Doing column ends well above the card: aim straight across, below the column.
    expect(a.y).toBeGreaterThan(d.y + d.height);
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2, { steps: 4 });
    await page.mouse.move(d.x + d.width / 2, a.y + a.height / 2, { steps: 20 });
    await expect(doing).toHaveClass(/over/);
    await page.mouse.up();
    await expect(doing).toContainText('Low drop 4');
    await expect.poll(async () => (await get<{ status: { name: string } }>(l.ids['Low drop 4'])).status.name).toBe('Doing');
  });

  test('the day board: drag to a folded day reschedules; Overdue says why it refuses', async ({ page }) => {
    const l = await lab([
      { title: 'Due today card', due_at: today() },
      { title: 'Late card', due_at: addDays(today(), -2) },
    ]);
    await signedIn(page, `/#/section/${l.sectionId}`);
    const tomorrow = addDays(today(), 1);
    await expect(column(page, tomorrow)).toHaveClass(/collapsed/);
    await mouseDrag(page, card(page, 'Due today card'), column(page, tomorrow));
    await expect(column(page, tomorrow)).toContainText('Due today card');
    await expect.poll(async () => (await get<{ due_at: string }>(l.ids['Due today card'])).due_at.slice(0, 10)).toBe(tomorrow);

    await mouseDrag(page, card(page, 'Late card'), column(page, 'today'));
    await expect(column(page, 'today')).toContainText('Late card');

    // Back into Overdue: refused, with the reason, and the card stays put.
    await mouseDrag(page, card(page, 'Due today card'), column(page, 'overdue'));
    await expect(page.locator('.toast')).toContainText('Overdue fills itself');
    await expect(column(page, tomorrow)).toContainText('Due today card');
  });

  test('grouped by tag: dragging swaps the tag; No tag takes it off', async ({ page }) => {
    const l = await lab([
      { title: 'Tagged card', tags: ['dragred'] },
      { title: 'Blue card', tags: ['dragblue'] },
    ]);
    await signedIn(page, `/#/section/${l.sectionId}`);
    await groupBy(page, 'Tag');
    await mouseDrag(page, card(page, 'Tagged card'), column(page, 'dragblue'));
    await expect.poll(async () => (await get<{ tags: string[] }>(l.ids['Tagged card'])).tags).toEqual(['dragblue']);
    await expect(column(page, 'dragblue')).toContainText('Tagged card');
    await mouseDrag(page, column(page, 'dragblue').locator('.card', { hasText: 'Tagged card' }), column(page, ''));
    await expect.poll(async () => (await get<{ tags: string[] }>(l.ids['Tagged card'])).tags).toEqual([]);
  });
});

// --- by finger ---------------------------------------------------------------------------------

type Pt = { x: number; y: number };

async function centerOf(page: Page, loc: ReturnType<Page['locator']>): Promise<Pt> {
  const box = (await loc.boundingBox())!;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** A finger through Chromium's DevTools protocol: down, hold, slide to `to`, up. `during` runs
 * while the finger is held still, before it moves. */
async function fingerDrag(page: Page, from: Pt, to: Pt, during?: () => Promise<void>) {
  const cdp = await page.context().newCDPSession(page);
  const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', p?: Pt) =>
    cdp.send('Input.dispatchTouchEvent', { type, touchPoints: p ? [{ x: p.x, y: p.y }] : [] });
  await touch('touchStart', from);
  await page.waitForTimeout(500);
  await during?.();
  for (let i = 1; i <= 12; i++) {
    await touch('touchMove', { x: from.x + ((to.x - from.x) * i) / 12, y: from.y + ((to.y - from.y) * i) / 12 });
    await page.waitForTimeout(16);
  }
  await page.waitForTimeout(150);
  await touch('touchEnd');
  await cdp.detach();
}

test.describe('finger', () => {
  test.use({ viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true });

  test('hold a card and drag it to another status column', async ({ page }) => {
    const l = await lab([{ title: 'Finger status card' }]);
    await signedIn(page, `/#/list/${l.listId}`);
    const from = await centerOf(page, card(page, 'Finger status card'));
    const to = await centerOf(page, page.locator('.column[data-status="Doing"]'));
    await fingerDrag(page, from, { x: to.x, y: from.y + 30 }, async () => {
      // While held, the browser's own drag is off (it would cancel ours on iOS and Android).
      await expect(card(page, 'Finger status card').first()).toHaveAttribute('draggable', 'false');
    });
    await expect.poll(async () => (await get<{ status: { name: string } }>(l.ids['Finger status card'])).status.name).toBe('Doing');
    await expect(card(page, 'Finger status card')).toHaveAttribute('draggable', 'true');
  });

  test('hold a card on the day board and drag it to a folded day', async ({ page }) => {
    const l = await lab([{ title: 'Finger day card', due_at: today() }]);
    await signedIn(page, `/#/section/${l.sectionId}`);
    const tomorrow = addDays(today(), 1);
    const from = await centerOf(page, card(page, 'Finger day card'));
    const to = await centerOf(page, column(page, tomorrow));
    await fingerDrag(page, from, to);
    await expect.poll(async () => (await get<{ due_at: string }>(l.ids['Finger day card'])).due_at.slice(0, 10)).toBe(tomorrow);
  });
});
