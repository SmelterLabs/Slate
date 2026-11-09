// The task menu works the same in every layout, and the Timeline stays usable on a phone.
import { expect, test } from '@playwright/test';
import { workspaceDay } from '../src/lib/quickadd';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const atDay = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();

async function newList(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} area`, audience: 'work' });
  return api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
}

test('right-click a Calendar chip opens the task menu', async ({ page }) => {
  const list = await newList('Cal menu');
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Menu on a chip', due_at: atDay(0) });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Calendar', exact: true }).click();
  const today = workspaceDay(new Date());
  await page.locator(`.cal-day[data-day="${today}"] .cal-chip`, { hasText: 'Menu on a chip' }).click({ button: 'right' });
  await page.getByRole('button', { name: 'Priority Urgent' }).click();
  await expect.poll(async () => (await api<{ priority: string }>('GET', `/items/${item.id}`)).priority).toBe('urgent');
  await expect(page.locator('.card-menu')).toHaveCount(0);
});

test('right-click a Timeline row opens the task menu', async ({ page }) => {
  const list = await newList('Gantt menu');
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Menu on a bar',
    start_at: atDay(-1),
    due_at: atDay(2),
  });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('button', { name: 'Timeline', exact: true }).click();
  await page.locator(`.tl-row[data-item="${item.id}"] .tl-bar`).click({ button: 'right' });
  await page.getByRole('button', { name: 'Priority High' }).click();
  await expect.poll(async () => (await api<{ priority: string }>('GET', `/items/${item.id}`)).priority).toBe('high');
  // The name still opens the task.
  await page.locator(`.tl-row[data-item="${item.id}"] .tl-open`).click();
  await expect(page.getByRole('dialog')).toBeVisible();
});

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('the Timeline keeps a narrow name column so bars stay in view', async ({ page }) => {
    const list = await newList('Phone gantt');
    await api('POST', '/items', { list_id: list.id, title: 'A bar on the phone', start_at: atDay(0), due_at: atDay(3) });
    await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
    await page.goto(`/#/list/${list.id}`);
    await page.getByRole('button', { name: 'Timeline', exact: true }).click();
    const name = await page.locator('.tl-name').first().boundingBox();
    expect(name!.width).toBeLessThanOrEqual(140);
    await expect(page.locator('.tl-bar').first()).toBeInViewport();
  });
});
