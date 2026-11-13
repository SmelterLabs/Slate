import { expect, test, type Page } from '@playwright/test';
import { workspaceDay } from '../src/lib/quickadd';
import { api, screenshots, seed, signedIn } from './support';

const shot = screenshots('SLATE_SCREENSHOTS', { scheme: 'dark' });

/** Skip the token screen: put the token where the board keeps it. */

async function openList(page: Page) {
  await signedIn(page, `/#/list/${seed().listId}`);
  await expect(page.locator('.column')).toHaveCount(3);
}

test('token screen signs in and shows the board columns', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Slate' })).toBeVisible();

  await page.getByLabel('Token').fill('slate_nope_nope');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByText('did not accept that token')).toBeVisible();

  await page.getByLabel('Token').fill(seed().alex);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('link', { name: /Everything/ })).toBeVisible();
  await expect(page.getByText('Personal', { exact: true })).toBeVisible();

  await page.getByRole('link', { name: /^Home/ }).click();
  const columns = page.locator('.column .column-head');
  await expect(columns).toHaveText([/Open/, /Ready/, /Done/]);
  await expect(page.locator('.card', { hasText: 'Drag me to Ready' })).toBeVisible();
  // Cards show the robot glyph for agents, tags, subtask counts and overdue dates.
  const live = page.locator('.card', { hasText: 'Agent renames this' });
  await expect(live.locator('.avatar.agent svg')).toBeVisible();
  await expect(live.locator('.tag-pill')).toHaveText(['chores']);
  await expect(live.locator('.count-ic[title="Subtasks"]')).toHaveText('1');
  await expect(page.locator('.card', { hasText: 'Pay the water bill' }).locator('.due')).toHaveClass(
    /overdue/,
  );
  // The title counts unread chat, not overdue tasks (lib/chatpanel windowBadge).
  await expect(page).toHaveTitle('Slate');
  await shot(page, 'board');

  // Signing out clears the token and returns to the token screen.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByLabel('Token')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('slate.token'))).toBeNull();
});

test('dragging a card to another column changes its status', async ({ page }) => {
  await openList(page);
  const card = page.locator('.card', { hasText: 'Drag me to Ready' });
  await card.dragTo(page.locator('.column[data-status="Ready"]'));
  await expect(
    page.locator('.column[data-status="Ready"] .card', { hasText: 'Drag me to Ready' }),
  ).toBeVisible();

  const { dragId } = seed();
  await expect
    .poll(async () => (await api<{ status: { name: string } }>('GET', `/items/${dragId}`)).status.name)
    .toBe('Ready');
});

test('a change made through the API appears without a reload within 2 s', async ({ page }) => {
  await openList(page);
  await page.evaluate(() => ((window as unknown as { marker: number }).marker = 42));
  const { agent, liveId } = seed();
  await api('PATCH', `/items/${liveId}`, { title: 'Agent renamed this', priority: 'high' }, agent);

  await expect(page.locator('.card', { hasText: 'Agent renamed this' })).toBeVisible({
    timeout: 2000,
  });
  await expect(page.locator('.card', { hasText: 'Agent renamed this' }).locator('.prio')).toHaveText('High');
  // Same page, no reload.
  expect(await page.evaluate(() => (window as unknown as { marker: number }).marker)).toBe(42);

  // The task window's activity column tells it in plain English, in small gray lines.
  await page.locator('.card', { hasText: 'Agent renamed this' }).click();
  await page.getByRole('tab', { name: 'History' }).click();
  const line = page.locator('.activity .ev', { hasText: 'Agent renamed this to “Agent renamed this”' });
  await expect(line).toContainText('via API');
  await expect(page.locator('.tw')).toContainText('A subtask');
  await shot(page, 'task-window');
});

test('quick add parses #tag !high due:tomorrow into one item', async ({ page }) => {
  await openList(page);
  const input = page.getByLabel('Quick add');
  await input.fill('Buy gutter guards #home !high @agent due:tomorrow');
  await expect(page.locator('.qa-preview')).toContainText('#home');
  await input.press('Enter');

  const card = page.locator('.card', { hasText: 'Buy gutter guards' });
  await expect(card).toBeVisible();
  await expect(card.locator('.tag-pill')).toHaveText(['home']);
  await expect(input).toHaveValue('');

  const { listId } = seed();
  const found = await api<
    {
      title: string;
      tags: string[];
      priority: string;
      due_at: string;
      assignees: { handle: string }[];
    }[]
  >('GET', `/items?list_id=${listId}&q=gutter`);
  expect(found).toHaveLength(1);
  const item = found[0];
  expect(item.title).toBe('Buy gutter guards');
  expect(item.tags).toEqual(['home']);
  expect(item.priority).toBe('high');
  expect(item.assignees.map((a) => a.handle)).toEqual(['agent']);
  const tomorrow = workspaceDay(new Date(Date.now() + 86_400_000));
  const due = new Date(item.due_at);
  expect(workspaceDay(due)).toBe(tomorrow);
  const hour = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Chicago',
    hour: 'numeric',
    hour12: false,
  }).format(due);
  expect(hour).toBe('17');
});

test('the old Today page opens the Agenda view, which lists overdue items', async ({ page }) => {
  await signedIn(page, '/#/today');
  await expect(page.getByRole('tab', { name: /Agenda/ })).toHaveAttribute('aria-selected', 'true');
  const overdue = page.locator('[data-group="overdue"]');
  await expect(overdue).toContainText('Pay the water bill');
  await shot(page, 'today');
});

test('a refused token anywhere returns to the token screen', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('slate.token', 'slate_revoked_x'));
  await page.goto('/');
  await expect(page.getByLabel('Token')).toBeVisible();
});

test('usable at 390 px: nav drawer, horizontal board, full-screen task window', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openList(page);
  await expect(page.locator('.nav')).not.toBeInViewport();
  await page.getByRole('button', { name: 'Menu' }).click();
  await expect(page.locator('.nav')).toBeInViewport();
  await page.getByRole('link', { name: /^Home/ }).click();
  await expect(page.locator('.nav')).not.toBeInViewport();

  const kanban = page.locator('.kanban');
  const scroll = await kanban.evaluate((el) => el.scrollWidth > el.clientWidth);
  expect(scroll).toBe(true);
  await shot(page, 'mobile-board');

  await page.locator('.card').first().click();
  const box = await page.locator('.tw').boundingBox();
  expect(box?.width).toBe(390);
  await shot(page, 'mobile-task-window');
});

test('a dropped stream shows the banner, then catches up what it missed', async ({
  page,
  context,
}) => {
  await openList(page);
  // Let the stream deliver one event first, so the board holds a cursor.
  const { agent, dragId } = seed();
  await api('PATCH', `/items/${dragId}`, { title: 'Before the outage' }, agent);
  await expect(page.locator('.card', { hasText: 'Before the outage' })).toBeVisible();

  await context.setOffline(true);
  await expect(page.getByRole('status')).toContainText("You're offline");
  await api('PATCH', `/items/${dragId}`, { title: 'Changed while offline' }, agent);
  await context.setOffline(false);

  await expect(page.locator('.card', { hasText: 'Changed while offline' })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page.getByRole('status')).toHaveCount(0);
});

test('a refused drag snaps back and says why', async ({ page }) => {
  await openList(page);
  await page.route('**/api/v1/items/*', (route) =>
    route.request().method() === 'PATCH'
      ? route.fulfill({ status: 422, json: { detail: 'nope, not today' } })
      : route.continue(),
  );
  const card = page.locator('.card', { hasText: 'Pay the water bill' });
  await card.dragTo(page.locator('.column[data-status="Done"]'));
  await expect(page.getByRole('alert')).toContainText('nope, not today');
  await expect(
    page.locator('.column[data-status="Open"] .card', { hasText: 'Pay the water bill' }),
  ).toBeVisible();
});

test('the task window edits title, autosaves the description, adds subtasks and comments', async ({
  page,
}) => {
  await openList(page);
  await page.locator('.card', { hasText: 'Pay the water bill' }).click();
  const tw = page.locator('.tw');
  const desc = tw.getByRole('region', { name: 'Description' });
  await expect(desc.locator('.md strong')).toHaveText('4411');
  await expect(desc.locator('.md a')).toHaveAttribute('href', 'https://example.com');

  await tw.getByLabel('Title').fill('Pay the water bill now');
  await tw.getByLabel('Title').press('Enter');
  await expect(page.locator('.card', { hasText: 'Pay the water bill now' })).toBeVisible();

  // No Edit button: click into the box, type, and it saves itself.
  await desc.locator('.md').click();
  await desc.getByLabel('Description').fill('Paid *online*.');
  await expect(desc).toContainText('Saved', { timeout: 5000 });
  await desc.getByLabel('Description').press('Escape');
  await expect(desc.locator('.md em')).toHaveText('online');
  await expect(page.locator('.tw')).toBeVisible(); // Esc left the box, not the window

  await tw.getByRole('button', { name: 'Subtask', exact: true }).click();
  await tw.getByLabel('Add subtask').fill('Find the account number');
  await tw.getByLabel('Add subtask').press('Enter');
  const subs = tw.getByRole('region', { name: 'Subtasks' });
  await expect(subs).toContainText('Find the account number');
  await subs.getByRole('button', { name: 'Complete Find the account number' }).click();
  await expect(subs.locator('li.done')).toContainText('Find the account number');
  await expect(
    page.locator('.card', { hasText: 'Pay the water bill now' }).locator('.count-ic[title="Subtasks"]'),
  ).toHaveText('1');

  // Subtasks nest four deep: open the subtask, give it one of its own; the crumbs climb the
  // whole trail, and back in the parent its row shows the nested count.
  await subs.getByRole('button', { name: 'Find the account number', exact: true }).click();
  await expect(tw.getByLabel('Title')).toHaveValue('Find the account number');
  await tw.getByRole('button', { name: 'Subtask', exact: true }).click();
  await tw.getByLabel('Add subtask').fill('Check the last statement');
  await tw.getByLabel('Add subtask').press('Enter');
  await expect(tw.getByRole('region', { name: 'Subtasks' })).toContainText('Check the last statement');
  await tw.getByRole('region', { name: 'Subtasks' }).getByRole('button', { name: 'Check the last statement', exact: true }).click();
  const crumbs = tw.getByRole('navigation', { name: 'Where this task lives' });
  await expect(crumbs.getByRole('button', { name: 'Pay the water bill now' })).toBeVisible();
  await expect(crumbs.getByRole('button', { name: 'Find the account number' })).toBeVisible();
  await crumbs.getByRole('button', { name: 'Pay the water bill now' }).click();
  await expect(tw.getByLabel('Title')).toHaveValue('Pay the water bill now');
  await expect(subs.locator('li', { hasText: 'Find the account number' }).locator('.nested')).toHaveText('1');

  await tw.getByLabel('Add comment').fill('Done at the portal, @agent');
  await tw.getByRole('listbox', { name: 'Mention someone' }).getByRole('option', { name: /@agent(?![\w-])/ }).click();
  await tw.getByLabel('Add comment').press('Enter');
  const comment = tw.locator('.activity .comment', { hasText: 'Done at the portal' });
  await expect(comment).toContainText('Alex');
  await expect(comment.locator('.mention')).toHaveText('@agent');

  await comment.hover();
  await comment.getByRole('button', { name: 'Add reaction' }).click();
  await comment.getByRole('menuitem', { name: 'React 👍' }).click();
  await expect(comment.getByRole('button', { name: /👍 1/ })).toBeVisible();

  await expect(tw.locator('.activity')).toContainText('You edited the description');
  await expect(tw.locator('.activity')).toContainText('You renamed this to “Pay the water bill now”');
});
