// Box view: a box per person and agent (and Unassigned) with to do, in progress, overdue and
// done counts and this week's load against capacity (set from the box's gear); drag a task onto
// another box to hand it over, or use Assign to (the same on a phone).
import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { BASE_URL, seed } from './seed';

const shots = process.env.SLATE_BOX_SHOTS ?? null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string) {
  if (!shots) return;
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(shots, `${name}.png`) });
}

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status} ${res.ok ? '' : await res.text()}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function dragTo(page: Page, from: Locator, to: Locator) {
  await to.scrollIntoViewIfNeeded();
  await from.scrollIntoViewIfNeeded();
  const a = (await from.boundingBox())!;
  const b = (await to.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width / 2, b.y + Math.min(b.height / 2, 60), { steps: 8 });
  await page.mouse.up();
}

interface ItemOut {
  id: string;
  assignees: { handle: string }[];
}

test('box view: counts per assignee, drag and Assign to, phone', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Support', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Tickets' });
  const make = (title: string, assignees: string[], extra: Record<string, unknown> = {}) =>
    api<ItemOut>('POST', '/items', { list_id: list.id, title, assignees, ...extra });
  const refund = await make('Refund order 1142', ['alex'], { estimate_minutes: 30, due_at: '2020-01-01T22:00:00Z' });
  const doing = await make('Reset password', ['alex'], { estimate_minutes: 15 });
  await api('PATCH', `/items/${doing.id}`, { status: 'In progress' });
  const shipped = await make('Close spam tickets', ['agent']);
  await api('PATCH', `/items/${shipped.id}`, { status: 'Done' });
  await make('Triage new inbox', ['agent']);
  await make('Sort out VAT question', []);

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Box', exact: true }).click();
  const box = (handle: string) => page.locator(`.box[data-box="${handle}"]`);
  const stat = (handle: string, label: string) => box(handle).locator('.box-stat', { hasText: label }).locator('b');

  await expect(box('alex').locator('.box-task')).toHaveCount(2);
  await expect(box('alex').locator('.box-title').first()).toHaveText('Reset password'); // in progress first
  await expect(stat('alex', 'to do')).toHaveText('1');
  await expect(stat('alex', 'in progress')).toHaveText('1');
  await expect(stat('alex', 'overdue')).toHaveText('1');
  await expect(stat('agent', 'done')).toHaveText('1');
  await expect(box('none').locator('.box-title')).toHaveText('Sort out VAT question');
  await shot(page, 'box-desktop');

  // The gear on a box sets that account's capacity: 3 minutes a day on weekdays is 15 minutes a
  // week, under the 45 minutes of open estimates; Use defaults puts 40 hours back.
  await expect(box('alex').locator('.box-load')).toHaveText('This week: 45m of 40h');
  await box('alex').getByRole('button', { name: 'Capacity for Alex' }).click();
  const editor = box('alex').getByRole('form', { name: 'Capacity for Alex' });
  await editor.getByLabel('Hours a day').fill('0.05');
  await editor.getByRole('button', { name: 'Save' }).click();
  await expect(editor).toHaveCount(0);
  await expect(box('alex').locator('.box-load')).toHaveText('Over this week: 45m of 15m');
  await expect(box('alex')).toHaveClass(/over/);
  await box('alex').getByRole('button', { name: 'Capacity for Alex' }).click();
  await box('alex').getByRole('button', { name: 'Use defaults' }).click();
  await expect(box('alex').locator('.box-load')).toHaveText('This week: 45m of 40h');

  // Drag the refund to the agent.
  await dragTo(page, box('alex').locator(`.box-task[data-item="${refund.id}"]`), box('agent'));
  await expect(box('agent').locator(`.box-task[data-item="${refund.id}"]`)).toBeVisible();
  await expect(stat('alex', 'overdue')).toHaveText('0');
  expect((await api<ItemOut>('GET', `/items/${refund.id}`)).assignees.map((a) => a.handle)).toEqual(['agent']);

  // Phone: same boxes, one column; Assign to hands the VAT question to Alex.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(box('none').locator('.box-task')).toHaveCount(1);
  const vat = box('none').locator('.box-task');
  await vat.getByRole('button', { name: /Assign Sort out VAT question/ }).click();
  const me = await api<{ account: { id: string } }>('GET', '/me');
  await shot(page, 'box-phone');
  await vat.getByRole('combobox', { name: 'Assign Sort out VAT question to' }).selectOption(me.account.id);
  await expect(box('alex').locator('.box-title', { hasText: 'Sort out VAT question' })).toBeVisible();
  await expect(box('none').locator('.box-task')).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
