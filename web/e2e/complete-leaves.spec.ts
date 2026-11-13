// On the due-date board, checking a card's circle takes the card out of Today without a
// reload, even when the board has been open since before the card's due time and so heard the
// scheduler's `item.due` / `item.overdue` for it. Those events name `due_at` as their field and
// carry `{due_at}` as `after`; the reducer must not write that object into the card's due date.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { workspaceInstant } from '../src/lib/views';
import { addDays } from '../src/lib/when';
import { api, signIn } from './support';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const dbPath = join(here, '..', '.e2e', 'slate-e2e.db'); // playwright.config.ts's dbPath

/** The scheduler's due step, for real, on the e2e database (the e2e server runs it off). */
function dueTick() {
  const out = spawnSync('uv', ['run', 'python', join(here, 'due_tick.py')], {
    cwd: root,
    env: { ...process.env, SLATE_DATABASE_PATH: dbPath },
    encoding: 'utf8',
  });
  expect(out.status, out.stderr).toBe(0);
  expect(Number(out.stdout.trim())).toBeGreaterThan(0);
}

/** Drop and restore the connection: the board reconnects and replays what it missed. */
async function reconnect(context: BrowserContext, page: Page) {
  await context.setOffline(true);
  await expect(page.locator('.banner.offline')).toBeVisible();
  await context.setOffline(false);
  await expect(page.locator('.banner.offline')).toHaveCount(0);
}

test('a card that went overdue while the board was open still leaves Today when completed', async ({
  page,
  context,
}) => {
  // The board's clock reads noon yesterday (workspace time) whatever the hour of the run, and
  // the card was due at 10 that morning: on the board it is today's, past due and an hour past
  // overdue, and the server, whose clock is later still, sends both events for it.
  const day = addDays(workspaceDay(new Date()), -1);
  const due = workspaceInstant(day, 10);
  await page.clock.install({ time: workspaceInstant(day, 12) });
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Pickups home', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Pickups' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Bulk pickup',
    due_at: due.toISOString(),
  });

  // A typical board: the list's Default view regrouped by due date.
  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Group by' }).click();
  await page.getByRole('menuitemradio', { name: 'Due date' }).click();
  const inToday = page.locator(`.column[data-group="today"] .card[data-item="${item.id}"]`);
  await expect(inToday).toBeVisible();

  dueTick(); // item.due + item.overdue, as the scheduler sends them at 10 and 11
  await reconnect(context, page);
  // The card still reads as due today, not as a broken date.
  await expect(inToday).toBeVisible();

  await inToday.hover();
  await inToday.getByRole('button', { name: 'Complete' }).click();
  await expect(page.getByText('Completed “Bulk pickup”')).toBeVisible();
  await expect(inToday).toHaveCount(0);
  await expect(page.locator(`.column[data-group="done"] .card[data-item="${item.id}"]`)).toBeVisible();
});
