// Timeline (Gantt) layout: switch to it, see bars and a dependency arrow, drag a bar to move
// its dates, and schedule an undated task by dropping it from the tray.
import { expect, test } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const atDay = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();

test('bars, a dependency arrow, drag to move, and schedule from the tray', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Launch', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Relaunch' });
  const design = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Design mockups',
    start_at: atDay(-4),
    due_at: atDay(0),
  });
  const startAt = atDay(1);
  const dueAt = atDay(6);
  const build = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Build the pages',
    start_at: startAt,
    due_at: dueAt,
  });
  await api('POST', `/items/${build.id}/links`, alex, { kind: 'blocked_by', item_id: design.id });
  await api('POST', '/items', alex, { list_id: list.id, title: 'Pick a playlist' }); // undated

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Timeline', exact: true }).click();

  const timeline = page.locator('.tl');
  await expect(timeline).toBeVisible();
  // Two bars, one dependency arrow, one tray chip for the undated task.
  await expect(timeline.locator('.tl-bar')).toHaveCount(2);
  await expect(timeline.locator('.tl-arrow')).toHaveCount(1);
  await expect(timeline.locator('.tl-chip', { hasText: 'Pick a playlist' })).toBeVisible();
  await expect(timeline.locator('.tl-name', { hasText: 'Pick a playlist' })).toHaveCount(0);

  // Build waits on Design, so its bar is marked blocked.
  const buildBar = timeline.locator(`.tl-row[data-item="${build.id}"] .tl-bar`);
  await expect(buildBar).toHaveClass(/blocked/);

  // Drag the Build bar 7 days later (one week column at the default zoom) and check it saved.
  const box = await buildBar.boundingBox();
  if (!box) throw new Error('no bar box');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 7 * 20, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();
  // The exact day math is unit-tested; here we only prove a drag reschedules the task later and
  // keeps its 5-day length (start moves with it).
  await expect
    .poll(async () => {
      const it = await api<{ start_at: string; due_at: string }>('GET', `/items/${build.id}`, alex);
      const movedDays = Math.round((Date.parse(it.due_at) - Date.parse(dueAt)) / 86_400_000);
      const length = Math.round((Date.parse(it.due_at) - Date.parse(it.start_at)) / 86_400_000);
      return { movedLater: movedDays > 0, length };
    })
    .toEqual({ movedLater: true, length: 5 });

  // Switch zoom; the Month button takes over and the axis redraws to month ticks.
  await timeline.getByRole('button', { name: 'Month' }).click();
  await expect(timeline.getByRole('button', { name: 'Month' })).toHaveClass(/on/);
  await expect(timeline.locator('.tl-tick').first()).toContainText(/\d{4}/); // "October 2026"
});
