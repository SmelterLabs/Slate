// The Overdue column on the day board wears a soft red wash (like Today's amber one), in light
// and dark, and the other plain columns stay grey.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/views';
import { BASE_URL, seed } from './seed';

// SLATE_SCREENSHOTS=1 writes screenshots into docs/img/.
const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

/** "rgb(r, g, b)" or "color(srgb r g b)" -> [r, g, b] in 0..255. */
function rgb(css: string): number[] {
  const n = css.match(/[\d.]+/g)!.map(Number);
  return css.startsWith('color(') ? n.slice(0, 3).map((v) => v * 255) : n.slice(0, 3);
}

test.use({ viewport: { width: 1400, height: 700 } });

test('the Overdue column is washed red in light and dark; plain columns are not', async ({ page }) => {
  const today = workspaceDay(new Date());
  const sectionId = (await api<{ id: string }>('POST', '/sections', { name: 'Overdue tint', audience: 'personal' })).id;
  const listId = (
    await api<{ id: string }>('POST', '/lists', {
      section_id: sectionId,
      name: 'Tasks',
      statuses: [
        { name: 'To do', kind: 'open' },
        { name: 'Done', kind: 'done' },
      ],
    })
  ).id;
  for (const [title, due_at] of [
    ['Renew car registration', addDays(today, -3)],
    ['Call the dentist back', addDays(today, -1)],
    ['Pay water bill', today],
  ])
    await api('POST', '/items', { list_id: listId, title, due_at });
  await api('POST', '/items', { list_id: listId, title: 'Sort the garage' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/section/${sectionId}`);
  const overdue = page.locator('.column[data-group="overdue"]');
  await expect(overdue).toContainText('Renew car registration');
  const plain = page.locator('.column[data-group="none"]');

  const bg = async (col: typeof overdue) => rgb(await col.evaluate((el) => getComputedStyle(el).backgroundColor));
  const redLean = async () => {
    const [r, g, b] = await bg(overdue);
    return r - Math.max(g, b);
  };
  for (const scheme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    // The theme switch eases colours over a moment: wait for the wash to settle.
    await expect.poll(redLean, { message: `${scheme}: overdue column leans red` }).toBeGreaterThan(8);
    const [pr, pg, pb] = await bg(plain);
    expect(Math.abs(pr - pg) + Math.abs(pg - pb), `${scheme}: plain column stays grey`).toBeLessThan(8);
    await page.waitForTimeout(500);
    if (shots) await page.screenshot({ path: join(shots, `overdue-tint-${scheme}.png`), animations: 'disabled' });
  }
  await page.emulateMedia({ colorScheme: null });
  await api('PATCH', `/sections/${sectionId}`, { archived: true });
});
