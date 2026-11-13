// ClickUp's recurrence options: "When done" with reopen and skip weekends from the task window,
// the repeat label on cards, and future repeats drawn on the calendar.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { workspaceDay } from '../src/lib/quickadd';
import { addDays } from '../src/lib/when';
import { api, signedIn } from './support';

const shots = process.env.SLATE_RECUR_SHOTS ?? null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (shots) await page.screenshot({ path: join(shots, `${name}.png`) });
}

async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} home`, audience: 'personal' });
  return api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
}

test('recur when done: reopen the same task, skip weekends, label on the card', async ({ page }) => {
  const list = await scratch('Filters');
  const today = workspaceDay(new Date());
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Change AC filter',
    due_at: today,
  });
  await api('POST', `/items/${item.id}/checklists`, { name: 'Steps', items: ['Buy filter', 'Swap it'] });
  await signedIn(page, `/#/list/${list.id}`);
  await page.locator('.card', { hasText: 'Change AC filter' }).click();
  const tw = page.locator('.tw');
  await tw.getByRole('button', { name: '↻ Make recurring' }).click();
  const editor = tw.getByRole('group', { name: 'Recurrence' });
  await editor.getByRole('radio', { name: 'When done' }).click();
  await expect(editor.getByLabel('Lead days')).toHaveCount(0); // schedule-only settings hide
  await editor.getByLabel('Repeats').selectOption('monthly_day');
  await editor.getByLabel('Reopen this task').check();
  await editor.getByLabel('Skip weekends').check();
  const preview = editor.locator('[data-rule-preview]');
  await expect(preview).toContainText('counted from when each is done');
  await expect(preview).toContainText('If you finish it today');
  await shot(page, 'recurring-when-done-editor');
  await editor.getByRole('button', { name: 'Make recurring' }).click();
  await expect(tw.locator('[data-rule-line]')).toContainText('reopens this task');

  const got = await api<{ recurrence_id: string }>('GET', `/items/${item.id}`);
  const rule = await api<{ trigger: string; reopen: boolean; skip_weekends: boolean }>(
    'GET',
    `/recurrences/${got.recurrence_id}`,
  );
  expect(rule).toMatchObject({ trigger: 'on_done', reopen: true, skip_weekends: true });

  await page.keyboard.press('Escape');
  const card = page.locator('.card', { hasText: 'Change AC filter' });
  await expect(card.locator('[data-repeat]')).toHaveText('Monthly · when done');

  // Done: the same task comes back open with next month's date, checklist unticked.
  await api('PATCH', `/items/${item.id}`, { status: 'done' });
  await expect
    .poll(async () => (await api<{ status: { kind: string } }>('GET', `/items/${item.id}`)).status.kind)
    .toBe('open');
  const back = await api<{ due_at: string }>('GET', `/items/${item.id}`);
  expect(workspaceDay(new Date(back.due_at)) > addDays(today, 20)).toBe(true);
  const weekday = new Date(`${workspaceDay(new Date(back.due_at))}T12:00:00Z`).getUTCDay();
  expect([0, 6]).not.toContain(weekday);
  await expect(card).toBeVisible();
  await shot(page, 'recurring-card-label');
});

test('the rule editor sets who each new one is assigned to', async ({ page }) => {
  const list = await scratch('Reviews');
  const today = workspaceDay(new Date());
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Friday report',
    due_at: today,
    assignees: ['alex'],
  });
  await signedIn(page, `/#/list/${list.id}`);
  await page.locator('.card', { hasText: 'Friday report' }).click();
  const tw = page.locator('.tw');
  await tw.getByRole('button', { name: '↻ Make recurring' }).click();
  const editor = tw.getByRole('group', { name: 'Recurrence' });
  // The item's assignees are the starting point; swap Alex for the agent on the rule only.
  const who = editor.getByRole('group', { name: 'Each new one is assigned to' });
  await expect(who.locator('.chip')).toHaveText(/Alex/);
  await who.getByRole('button', { name: 'Unassign alex' }).click();
  await who.getByRole('button', { name: '+ assign' }).click();
  await who.getByLabel('Find someone').fill('agent');
  await who.getByRole('checkbox', { name: /@agent(?![\w-])/ }).check();
  await expect(who.locator('.chip')).toHaveText(/Agent/);
  await editor.getByRole('button', { name: 'Make recurring' }).click();
  await expect(tw.locator('[data-rule-line]')).toBeVisible();

  const got = await api<{ recurrence_id: string; assignees: { handle: string }[] }>('GET', `/items/${item.id}`);
  expect(got.assignees.map((a) => a.handle)).toEqual(['alex']); // this one is untouched
  const rule = await api<{ assignee_ids: string[] }>('GET', `/recurrences/${got.recurrence_id}`);
  const accounts = await api<{ id: string; handle: string }[]>('GET', '/accounts');
  const agent = accounts.find((a) => a.handle === 'agent')!;
  expect(rule.assignee_ids).toEqual([agent.id]);

  // Edit shows the rule's own list; saving sends only what changed.
  await tw.locator('[data-rule-line]').locator('..').getByRole('button', { name: 'Edit' }).click();
  const again = tw.getByRole('group', { name: 'Recurrence' }).getByRole('group', { name: 'Each new one is assigned to' });
  await expect(again.locator('.chip')).toHaveText(/Agent/);
  await again.getByRole('button', { name: '+ assign' }).click();
  await again.getByLabel('Find someone').fill('alex');
  await again.getByRole('checkbox').first().check();
  await tw.getByRole('group', { name: 'Recurrence' }).getByRole('button', { name: 'Save rule' }).click();
  await expect(tw.locator('[data-rule-line]')).toBeVisible();
  const after = await api<{ assignee_ids: string[] }>('GET', `/recurrences/${got.recurrence_id}`);
  expect(after.assignee_ids.sort()).toEqual([agent.id, accounts.find((a) => a.handle === 'alex')!.id].sort());
});
