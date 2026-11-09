// The board in a real Chromium against a running Slate: first load, regroup, live update.
//
//   node web/scripts/board-perf.mjs <base url> <token>     (scripts/load_test.py --browser runs it)
//
// Prints one JSON object: first_load_ms (navigation to the first card on Everything),
// regroup_ms per grouping (menu click to the new columns painted), live_ms (an API PATCH to
// the new title on screen, median of 10), js_heap_mb. Set SLATE_E2E_CHROMIUM to use a
// preinstalled Chromium.
import { chromium } from '@playwright/test';

const [base, token] = process.argv.slice(2);
if (!base || !token) {
  console.error('usage: board-perf.mjs <base url> <token>');
  process.exit(2);
}

const median = (xs) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
const painted = (page) =>
  page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));

const browser = await chromium.launch(
  process.env.SLATE_E2E_CHROMIUM ? { executablePath: process.env.SLATE_E2E_CHROMIUM } : {},
);
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
await page.addInitScript((t) => localStorage.setItem('slate.token', t), token);

const out = { regroup_ms: {} };
let t = Date.now();
await page.goto(`${base}/#/everything`);
await page.locator('.card').first().waitFor({ timeout: 120_000 });
await painted(page);
out.first_load_ms = Date.now() - t;

for (const [name, key] of [
  ['Priority', 'urgent'],
  ['List', null],
  ['Status kind', 'open'],
  ['Due date', 'today'],
]) {
  await page.getByRole('button', { name: 'Group by' }).click();
  t = Date.now();
  await page.getByRole('menuitemradio', { name }).click();
  if (key) await page.locator(`.column[data-group="${key}"]`).first().waitFor();
  await painted(page);
  out.regroup_ms[name] = Date.now() - t;
}

// Live: change a visible card's title through the API and wait for it on screen.
const id = await page.locator('.card').first().getAttribute('data-item');
const xs = [];
for (let i = 0; i < 10; i++) {
  const title = `Live ${Date.now()} ${i}`;
  t = Date.now();
  const res = await fetch(`${base}/api/v1/items/${id}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`PATCH ${res.status}`);
  await page.locator(`.card[data-item="${id}"]`, { hasText: title }).waitFor();
  xs.push(Date.now() - t);
}
out.live_ms = median(xs);
out.js_heap_mb = await page.evaluate(() =>
  Math.round((performance.memory?.usedJSHeapSize ?? 0) / 1048576),
);
await browser.close();
console.log(JSON.stringify(out));
