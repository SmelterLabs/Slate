// Sections and lists from the nav, list settings, the drawer's editors and the
// recurrence editor, against a real server. Each test makes its own section or items so the
// order of files does not matter.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { api, BASE_URL, seed, signedIn } from './support';

const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (!shots) return;
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.screenshot({ path: join(shots, `${name}.png`) });
  await page.emulateMedia({ colorScheme: null });
}

interface ListOut {
  id: string;
  name: string;
  section_id: string;
  position: number;
  default_status_id: string;
  archived_at: string | null;
  statuses: { id: string; name: string; kind: string; color: string | null }[];
}

/** A section with one list made over the API, for tests that need their own. */
async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', {
    name: `${name} section`,
    audience: 'work',
  });
  const list = await api<ListOut>('POST', '/lists', {
    section_id: section.id,
    name,
    statuses: [
      { name: 'To do', kind: 'open' },
      { name: 'In progress', kind: 'active' },
      { name: 'Done', kind: 'done' },
    ],
  });
  return { sectionId: section.id, list };
}

test('add a section and a list from the nav; a second board sees both live', async ({
  page,
  browser,
}) => {
  const other = await browser.newPage();
  await signedIn(other, '/', { ready: true });
  await signedIn(page, '/', { ready: true });

  await page.getByRole('button', { name: 'New section' }).click();
  await page.getByLabel('New section name').fill('Example Project');
  await page.getByLabel('Audience').selectOption('family');
  await page.getByRole('button', { name: 'Add section' }).click();
  await expect(page.locator('.nav-section[data-section="Example Project"]')).toBeVisible();

  await page.getByRole('button', { name: 'Add list to Example Project', exact: true }).click();
  await page.getByLabel('New list name').fill('Groceries');
  await page.getByLabel('New list name').press('Enter');

  // The new list opens with the default columns.
  await expect(page).toHaveURL(/#\/list\//);
  await expect(page.locator('.column .column-head')).toHaveText([/To do/, /In progress/, /Done/]);

  // The other board got both through the stream, no reload.
  const otherNav = other.locator('.nav-section[data-section="Example Project"]');
  await expect(otherNav).toBeVisible({ timeout: 2000 });
  await expect(otherNav.getByRole('link', { name: /Groceries/ })).toBeVisible({ timeout: 2000 });

  const lists = await api<ListOut[]>('GET', '/lists');
  const made = lists.find((l) => l.name === 'Groceries')!;
  expect(made.statuses.map((s) => [s.name, s.kind])).toEqual([
    ['To do', 'open'],
    ['In progress', 'active'],
    ['Done', 'done'],
  ]);
  await shot(page, 'nav-structure');
  await other.close();
});

test('rename, reorder, archive and restore lists from their menus', async ({ page }) => {
  const { sectionId, list } = await scratch('Alpha');
  await api('POST', '/lists', { section_id: sectionId, name: 'Beta' });
  await signedIn(page, '/', { ready: true });
  const section = page.locator('.nav-section[data-section="Alpha section"]');
  await expect(section.locator('.nav-item')).toHaveText([/Alpha/, /Beta/]);

  // Rename.
  await section.getByRole('button', { name: 'Alpha options' }).click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  await section.getByLabel('List name').fill('Alpha one');
  await section.getByLabel('List name').press('Enter');
  await expect(section.locator('.nav-item').first()).toContainText('Alpha one');

  // Move down: Beta first now.
  await section.getByRole('button', { name: 'Alpha one options' }).click();
  await page.getByRole('menuitem', { name: 'Move down' }).click();
  await expect(section.locator('.nav-item')).toHaveText([/Beta/, /Alpha one/]);
  const positions = (await api<ListOut[]>('GET', `/lists?section_id=${sectionId}`)).map(
    (l) => l.name,
  );
  expect(positions).toEqual(['Beta', 'Alpha one']);

  // Archive asks first, then hides it.
  await section.getByRole('button', { name: 'Alpha one options' }).click();
  await page.getByRole('menuitem', { name: 'Archive…' }).click();
  await expect(section.getByRole('alertdialog')).toContainText('Archive Alpha one?');
  await section.getByRole('button', { name: 'Archive', exact: true }).click();
  await expect(section.locator('.nav-item')).toHaveText([/Beta/]);
  expect((await api<ListOut>('GET', `/lists/${list.id}`)).archived_at).not.toBeNull();

  // Restore from the Archived footer.
  await page.getByRole('button', { name: 'Archived' }).click();
  const archived = page.locator('.nav-archived');
  await expect(archived).toContainText('Alpha one');
  await archived
    .locator('.nav-row', { hasText: 'Alpha one' })
    .getByRole('button', { name: 'Restore' })
    .click();
  await expect(section.locator('.nav-item')).toHaveText([/Beta/, /Alpha one/]);
  expect((await api<ListOut>('GET', `/lists/${list.id}`)).archived_at).toBeNull();
});

test('move a list to another section by right-click menu and by dragging', async ({ page }) => {
  const { list } = await scratch('Porch');
  const home = await scratch('Garden');
  await signedIn(page, '/', { ready: true });
  const from = page.locator('.nav-section[data-section="Porch section"]');
  const to = page.locator('.nav-section[data-section="Garden section"]');

  // Right-click the row, pick "Move to Garden section": it lands last there.
  await from.locator('.nav-list', { hasText: 'Porch' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Move to Garden section' }).click();
  await expect(to.locator('.nav-item')).toHaveText([/Garden/, /Porch/]);
  await expect(from.locator('.nav-item')).toHaveCount(0);
  expect((await api<ListOut>('GET', `/lists/${list.id}`)).section_id).toBe(home.sectionId);

  // Drag it back onto the Porch heading.
  await to.locator('.nav-list', { hasText: 'Porch' }).dragTo(from.locator('.nav-heading'));
  await expect(from.locator('.nav-item')).toHaveText([/Porch/]);
  await expect(to.locator('.nav-item')).toHaveText([/Garden/]);
  expect((await api<ListOut>('GET', `/lists/${list.id}`)).section_id).not.toBe(home.sectionId);
});

test('list settings edit statuses and move items off a removed one', async ({ page }) => {
  const { list } = await scratch('Kitchen');
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Descale the kettle',
  }); // lands on "To do", the default
  await signedIn(page, `/#/list/${list.id}`, { ready: true });
  await expect(page.locator('.column')).toHaveCount(3);

  const section = page.locator('.nav-section[data-section="Kitchen section"]');
  await section.getByRole('button', { name: 'Kitchen options' }).click();
  await page.getByRole('menuitem', { name: 'List settings…' }).click();
  const dlg = page.getByRole('dialog', { name: 'List settings' });

  const rows = dlg.locator('.status-row');
  await rows.nth(1).getByLabel('Status name').fill('Doing');
  await dlg.getByRole('button', { name: '+ Add status' }).click();
  await rows.nth(3).getByLabel('Status name').fill('Blocked');
  await rows.nth(3).getByLabel('Kind of Blocked').selectOption('active');
  await dlg.getByRole('button', { name: 'Move Blocked up' }).click(); // before Done
  await rows.nth(1).getByLabel('Default status Doing').check();
  await dlg.getByRole('button', { name: 'Remove To do' }).click();

  // The removed status still holds an item: Save waits for a destination.
  await expect(dlg).toContainText('1 item is on a status you removed');
  await expect(dlg.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  await dlg.getByLabel('Move items to').selectOption('Doing');
  await shot(page, 'list-settings');
  await dlg.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(dlg).toHaveCount(0);

  await expect(page.locator('.column .column-head')).toHaveText([/Doing/, /Blocked/, /Done/]);
  const saved = await api<ListOut>('GET', `/lists/${list.id}`);
  expect(saved.statuses.map((s) => [s.name, s.kind])).toEqual([
    ['Doing', 'active'],
    ['Blocked', 'active'],
    ['Done', 'done'],
  ]);
  expect(saved.default_status_id).toBe(saved.statuses[0].id);
  const moved = await api<{ status: { name: string } }>('GET', `/items/${item.id}`);
  expect(moved.status.name).toBe('Doing');
});

test('drawer editors: due date quick pick and time, tags with autocomplete, assignee, complete, move', async ({
  page,
}) => {
  const { list } = await scratch('Garage');
  const { list: other } = await scratch('Shed');
  await api('POST', '/tags', { name: 'tools' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Sharpen the mower blade',
  });
  await signedIn(page, `/#/list/${list.id}`, { ready: true });
  await page.locator('.card', { hasText: 'Sharpen the mower blade' }).click();
  const drawer = page.locator('.tw');
  type Got = {
    due_at: string | null;
    tags: string[];
    assignees: { handle: string }[];
    status: { name: string };
    completed_at: string | null;
    list_id: string;
  };
  const got = () => api<Got>('GET', `/items/${item.id}`);

  // Due: Tomorrow, then 9:30.
  await drawer.getByRole('button', { name: /^Due:/ }).click();
  await drawer.getByRole('button', { name: 'Tomorrow' }).click();
  const tomorrow = workspaceDay(new Date(Date.now() + 86_400_000));
  await expect.poll(async () => (await got()).due_at && workspaceDay(new Date((await got()).due_at!))).toBe(tomorrow);
  await drawer.getByRole('button', { name: /^Due:/ }).click();
  await drawer.getByRole('button', { name: '+ time' }).click();
  await drawer.getByLabel('Due time').fill('09:30');
  await drawer.getByLabel('Due time').dispatchEvent('change');
  await expect
    .poll(async () => {
      const due = (await got()).due_at;
      return due
        ? new Intl.DateTimeFormat('en-GB', {
            timeZone: 'America/Chicago',
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
          }).format(new Date(due))
        : null;
    })
    .toBe('09:30');
  await expect(drawer.getByRole('button', { name: /^Due:/ })).toContainText('9:30 AM');

  // Tags: autocomplete an existing one, create a new one on Enter.
  const tagInput = drawer.getByLabel('Add tag');
  await tagInput.fill('too');
  await expect(drawer.getByRole('listbox', { name: 'Tag suggestions' })).toContainText('#tools');
  await drawer.getByRole('option', { name: '#tools' }).getByRole('button').click();
  await tagInput.fill('outdoor');
  await tagInput.press('Enter');
  await expect.poll(async () => (await got()).tags).toEqual(['outdoor', 'tools']);

  // Assign the agent.
  await drawer.getByRole('button', { name: '+ assign' }).click();
  await drawer.getByLabel('Find someone').fill('agent');
  await drawer.getByRole('checkbox', { name: /@agent(?![\w-])/ }).check();
  await expect.poll(async () => (await got()).assignees.map((a) => a.handle)).toEqual(['agent']);
  await expect(drawer.locator('.assignee-picker .chip .avatar.agent svg')).toBeVisible();
  await shot(page, 'task-window-fields');

  // One-click complete, and back.
  await drawer.getByRole('button', { name: 'Mark complete' }).click();
  await expect.poll(async () => (await got()).status.name).toBe('Done');
  expect((await got()).completed_at).not.toBeNull();
  await drawer.getByRole('button', { name: 'Reopen' }).click();
  await expect.poll(async () => (await got()).status.name).toBe('To do');

  // Move to another list.
  await drawer.getByRole('button', { name: 'Move to list' }).click();
  await drawer.getByLabel('Find a list').fill('Shed');
  await drawer.getByLabel('Find a list').press('Enter');
  await expect.poll(async () => (await got()).list_id).toBe(other.id);
  await expect(page.locator('.card', { hasText: 'Sharpen the mower blade' })).toHaveCount(0);
});

test('assignee picker: each tick saves at once, rows sit on one line, Escape and a click outside close it', async ({
  page,
}) => {
  const { list } = await scratch('Assign');
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Pick a helper' });
  const handles = async () =>
    (await api<{ assignees: { handle: string }[] }>('GET', `/items/${item.id}`)).assignees.map((a) => a.handle).sort();
  await signedIn(page, `/#/list/${list.id}`, { ready: true });
  await page.locator('.card', { hasText: 'Pick a helper' }).click();
  const drawer = page.locator('.tw');
  const pop = drawer.locator('.assignee-picker .picker-pop');

  await drawer.getByRole('button', { name: '+ assign' }).click();
  await expect(drawer.getByLabel('Find someone')).toBeFocused();
  // No Done button: ticking saves, and the popover stays open for the next tick.
  await expect(drawer.locator('.assignee-picker').getByRole('button', { name: 'Done' })).toHaveCount(0);
  await drawer.getByRole('checkbox', { name: /@agent(?![\w-])/ }).check();
  await expect.poll(handles).toEqual(['agent']);
  await drawer.getByRole('checkbox', { name: /Alex/ }).check();
  await expect.poll(handles).toEqual(['agent', 'alex']);
  await expect(pop).toBeVisible();

  // Name, @handle and "agent" share one line.
  const row = pop.locator('label', { hasText: '@agent' });
  const name = await row.locator('.ap-name').boundingBox();
  const meta = await row.locator('.ap-meta').boundingBox();
  expect(Math.abs(name!.y + name!.height / 2 - (meta!.y + meta!.height / 2))).toBeLessThan(3);
  expect(meta!.height).toBeLessThan(name!.height * 1.5);

  // Escape closes only the picker; the task window stays.
  await page.keyboard.press('Escape');
  await expect(pop).toHaveCount(0);
  await expect(drawer).toBeVisible();

  // A click elsewhere in the window closes it too.
  await drawer.getByRole('button', { name: '+ assign' }).click();
  await expect(pop).toBeVisible();
  await drawer.locator('.label', { hasText: 'Priority' }).click();
  await expect(pop).toHaveCount(0);
  await expect(drawer).toBeVisible();
  expect(await handles()).toEqual(['agent', 'alex']);

  // Off the boards again, so the Everything specs' No due date column keeps its count.
  const del = await fetch(`${BASE_URL}/api/v1/items/${item.id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${seed().alex}` },
  });
  expect(del.ok).toBe(true);
});

test('make an item recurring on the 2nd and 4th Wednesday with a live preview', async ({
  page,
}) => {
  const { list } = await scratch('Chores');
  // Wed Jan 9 2030 is the 2nd Wednesday of January.
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Recycling out',
    due_at: '2030-01-09',
    tags: ['home'],
  });
  await signedIn(page, `/#/list/${list.id}`, { ready: true });
  await page.locator('.card', { hasText: 'Recycling out' }).click();
  const drawer = page.locator('.tw');

  await drawer.getByRole('button', { name: '↻ Make recurring' }).click();
  const editor = drawer.getByRole('group', { name: 'Recurrence' });
  await editor.getByLabel('Repeats').selectOption('monthly_nth');
  await editor.getByRole('button', { name: '4th' }).click();
  const preview = editor.locator('[data-rule-preview]');
  await expect(preview).toContainText('Every month on the 2nd and 4th Wednesday');
  await expect(preview).toContainText('This item stays the current one (due Wed, Jan 9)');
  await expect(preview.locator('.preview-dates li')).toHaveText([
    'Wed, Jan 23',
    'Wed, Feb 13',
    'Wed, Feb 27',
    'Wed, Mar 13',
    'Wed, Mar 27',
  ]);

  // Custom shows the same rule, and a bad one is refused in plain words before saving.
  await editor.getByLabel('Repeats').selectOption('custom');
  await expect(editor.getByLabel('RRULE')).toHaveValue('FREQ=MONTHLY;BYDAY=2WE,4WE');
  await editor.getByLabel('RRULE').fill('FREQ=HOURLY');
  await expect(preview).toContainText('FREQ must be');
  await expect(editor.getByRole('button', { name: 'Make recurring' })).toBeDisabled();
  await editor.getByLabel('RRULE').fill('FREQ=MONTHLY;BYDAY=WE;BYSETPOS=2,4');
  await editor.getByLabel('Repeats').selectOption('monthly_nth');
  await expect(preview).toContainText('Every month on the 2nd and 4th Wednesday');

  await editor.getByLabel('Skip while one is open').check();
  await editor.getByLabel('Lead days').fill('7');
  await shot(page, 'recurrence-editor');
  await editor.getByRole('button', { name: 'Make recurring' }).click();

  await expect(drawer.locator('[data-rule-line]')).toContainText(
    'every month on the 2nd and 4th Wednesday',
  );
  const got = await api<{ recurrence_id: string; due_at: string }>('GET', `/items/${item.id}`);
  expect(got.recurrence_id).toBeTruthy();
  expect(got.due_at).toBe('2030-01-09T23:00:00Z');
  const rule = await api<{
    rrule: string;
    missed_policy: string;
    lead_days: number;
    tags: string[];
    title_template: string;
    next_run_at: string;
  }>('GET', `/recurrences/${got.recurrence_id}`);
  expect(rule.rrule).toBe('FREQ=MONTHLY;BYDAY=2WE,4WE');
  expect(rule.missed_policy).toBe('skip_if_open');
  expect(rule.lead_days).toBe(7);
  expect(rule.tags).toEqual(['home']);
  expect(rule.title_template).toBe('Recycling out');
  expect(rule.next_run_at.slice(0, 10)).toBe('2030-01-23');

  // Editing the rule: every 2 months now; Pause, then Resume.
  await drawer.getByRole('button', { name: 'Edit', exact: true }).first().click();
  await editor.getByLabel('Interval').fill('2');
  await expect(preview).toContainText('Every 2 months on the 2nd and 4th Wednesday');
  await editor.getByRole('button', { name: 'Save rule' }).click();
  await expect(drawer.locator('[data-rule-line]')).toContainText('every 2 months');
  await drawer.getByRole('button', { name: 'Pause' }).click();
  await expect(drawer.locator('[data-rule-line]')).toContainText('paused');
  await drawer.getByRole('button', { name: 'Resume' }).click();
  await expect(drawer.locator('[data-rule-line]')).not.toContainText('paused');
});
