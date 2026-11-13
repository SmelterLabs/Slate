// Reminders and notification settings: set a reminder from the task window, see it on the chip
// and in the history, remove it; choose the digest hour and push reasons (account prefs).
import { expect, test } from '@playwright/test';
import { api, countRequests, signIn } from './support';

test('remind me from the task window, then remove it', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Example Project', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Bills' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Pay the water bill',
    due_at: '2030-01-09T23:00:00Z',
  });

  const loads = countRequests(page, 'GET', `/items/${item.id}/reminders`);
  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const modal = page.getByRole('dialog', { name: /Pay the water bill/ });
  await expect(modal).toBeVisible();

  await modal.getByRole('button', { name: 'Remind me' }).click();
  await page.getByLabel('Reminder note').fill('Autopay is off');
  await page.getByRole('button', { name: '1 day before due', exact: true }).click();
  const chip = modal.locator('.rem-chip');
  await expect(chip).toHaveText(/1 day before due · Autopay is off/);
  await expect(page.getByRole('alert')).toContainText('Reminder set');
  await page.keyboard.press('Escape'); // before-due toggles keep the menu open for more

  const saved = await api<{ before_minutes: number; note: string }[]>('GET', `/items/${item.id}/reminders`);
  expect(saved.map((r) => [r.before_minutes, r.note])).toEqual([[1440, 'Autopay is off']]);
  await expect(modal).toContainText('set a reminder 1 day before due');

  // A reminder set elsewhere (an agent, another device) shows up live.
  await api('POST', `/items/${item.id}/reminders`, { at: '2030-01-08T15:00:00Z' });
  await expect(chip).toHaveCount(2);
  // The chips load once per change, not in a loop: while a later change (a comment) travels to
  // the open window, at most its own reload and one still due from the reminder before it go out;
  // a load that fed itself would keep going.
  const settled = loads();
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Autopay stays off this month' });
  await expect(modal.getByText('Autopay stays off this month')).toBeVisible();
  expect(loads() - settled, 'the reminder chips load once per change, not in a loop').toBeLessThanOrEqual(2);

  await modal.getByRole('button', { name: /Remove reminder 1 day before due/ }).click();
  await expect(chip).toHaveCount(1);
  await expect.poll(async () => (await api<unknown[]>('GET', `/items/${item.id}/reminders`)).length).toBe(1);
});

test('several alerts before due from the toggles, and a custom one', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Home', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Chores' });
  const undated = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Someday' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Dentist',
    due_at: '2030-01-16T15:00:00Z',
  });
  await signIn(page);

  await page.goto(`/#/list/${list.id}?task=${undated.id}`);
  const plain = page.getByRole('dialog', { name: /Someday/ });
  await plain.getByRole('button', { name: 'Remind me' }).click();
  await expect(page.getByText('Give the task a due date to set alerts before it.')).toBeVisible();
  await page.keyboard.press('Escape');

  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const modal = page.getByRole('dialog', { name: /Dentist/ });
  await expect(modal).toBeVisible();
  await expect(modal.locator('.rem-chip')).toHaveCount(0); // none by default
  await modal.getByRole('button', { name: 'Remind me' }).click();
  const before = page.getByRole('group', { name: 'Before due' });
  for (const name of ['15 min before due', '1 hour before due', '1 week before due']) {
    await before.getByRole('button', { name, exact: true }).click();
    await expect(before.getByRole('button', { name, exact: true })).toHaveAttribute('aria-pressed', 'true');
  }
  await page.getByLabel('Alert amount').fill('3');
  await page.getByLabel('Alert unit').selectOption('day');
  await before.getByRole('button', { name: 'Add' }).click();
  await expect(modal.locator('.rem-chip')).toHaveCount(4);
  await expect.poll(async () =>
    (await api<{ before_minutes: number }[]>('GET', `/items/${item.id}/reminders`))
      .map((r) => r.before_minutes)
      .sort((a, b) => a - b),
  ).toEqual([15, 60, 4320, 10080]);

  // Pressing a lit toggle takes that alert off again.
  await before.getByRole('button', { name: '1 hour before due', exact: true }).click();
  await expect(before.getByRole('button', { name: '1 hour before due', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await expect(modal.locator('.rem-chip')).toHaveCount(3);
});

test('notification settings follow the account', async ({ page }) => {
  await signIn(page);
  await page.goto('/#/home');
  await page.getByRole('button', { name: 'Notifications' }).click();
  const panel = page.getByRole('dialog', { name: 'Notifications' });
  await expect(panel).toBeVisible();
  await expect(panel.locator('.ns-state')).not.toHaveText('Checking…');

  await panel.getByLabel('Digest hour').selectOption('6');
  // Each control saves asynchronously. This checks persistence, not simultaneous preference
  // writes: wait for one save before editing the next field (cold Windows runs overlap them).
  await expect.poll(async () => (await api<{ prefs: Record<string, unknown> }>('GET', '/me')).prefs.digest_hour).toBe(6);
  // "Replies, comments and chat" stands for reply, comment and chat; ticking it chooses all three.
  await panel.getByLabel('Replies, comments and chat: toast / push').check();
  await expect.poll(async () => (await api<{ prefs: { push_reasons?: string[] } }>('GET', '/me')).prefs.push_reasons).toContain('comment');
  await panel.getByLabel("I'm mentioned or asked something: toast / push").uncheck();
  await expect
    .poll(async () => (await api<{ prefs: Record<string, unknown> }>('GET', '/me')).prefs)
    .toMatchObject({
      digest_hour: 6,
      push_reasons: ['reply', 'chat', 'assigned', 'waiting', 'due', 'unblocked', 'nudge', 'reminder', 'digest', 'health', 'access', 'comment', 'watching'],
    });
  await expect(panel.getByText('Upcoming reminders')).toBeVisible();
  await panel.getByRole('button', { name: 'Close' }).click();
  await expect(panel).toHaveCount(0);
});
