import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

const createdViews: string[] = [];
async function api(method: string, path: string, body?: unknown) {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${seed().alex}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path}: ${res.status}`).toBe(true);
  const result = await res.json();
  if (method === 'POST' && path === '/views') createdViews.push(result.id);
  return result;
}
async function open(page: Page, suffix = '') {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(`/#/section/${seed().workSectionId}${suffix}`);
  await expect(page.locator('.toolbar')).toBeVisible();
}
const filter = (page: Page) => page.getByRole('button', { name: 'Filter', exact: true });
test.beforeEach(() => api('PATCH', '/me/prefs', { last_view: null, view_drafts: null }));
test.afterEach(async () => {
  for (const id of createdViews.splice(0)) await api('POST', `/views/${id}/archive`);
});

test('filter cleanup: last unchecked assignee removes rule; fresh drafts stay editable but inactive', async ({ page }) => {
  await open(page);
  await filter(page).click();
  await page.getByRole('button', { name: '+ Rule', exact: true }).click();
  await page.getByLabel('Field', { exact: true }).selectOption('assignee');
  await expect(filter(page).locator('.pill')).toHaveCount(0);
  await expect(page.locator('.filter-chip')).toHaveCount(0);
  const people = page.getByRole('group', { name: 'Assignee', exact: true });
  const me = people.getByLabel('Me (current viewer)', { exact: true });
  await expect(people.getByLabel('Alex (specific person)', { exact: true })).toBeVisible();
  await expect(people.getByLabel(/slate/i)).toHaveCount(0);
  await me.check();
  await expect(filter(page).locator('.pill')).toHaveText('1');
  await expect(page.locator('.filter-chip')).toContainText('current viewer');
  // The checkbox disappears with the rule; click rather than checking its detached state.
  await me.click();
  await expect(page.locator('.fb-rule')).toHaveCount(0);
  await expect(filter(page).locator('.pill')).toHaveCount(0);
  await expect(page.locator('.filter-chip')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.reload();
  await expect(filter(page).locator('.pill')).toHaveCount(0);
  await expect(page.locator('.filter-chip')).toHaveCount(0);
});

test('filter cleanup: saved incomplete OR branches do not widen results or create empty chips', async ({ page }) => {
  const view = await api('POST', '/views', {
    name: 'Incomplete filter regression', scope_kind: 'section', scope_id: seed().workSectionId, shared: false,
    config: { filter: { op: 'or', rules: [
      { field: 'assignee', cmp: 'is', value: [] },
      { op: 'and', rules: [{ field: 'tag', cmp: 'any', value: [] }] },
      { field: 'priority', cmp: 'is', value: ['high'] },
    ] } },
  });
  await open(page, `/v/${view.id}`);
  await expect(filter(page).locator('.pill')).toHaveText('1');
  await expect(page.locator('.filter-chip')).toHaveCount(1);
  await expect(page.locator('.filter-chip')).toContainText('Priority is high');
  await expect(page.locator('.card', { hasText: 'Tomorrow call' })).toHaveCount(0);
  await expect(page.locator('.card', { hasText: 'Three days out' })).toBeVisible();
  await page.getByRole('button', { name: 'Remove filter', exact: true }).click();
  await expect(filter(page).locator('.pill')).toHaveCount(0);
  await expect(page.locator('.card', { hasText: 'Tomorrow call' })).toBeVisible();
  await filter(page).click();
  // Existing empty rules remain editable, but are not active filters.
  await expect(page.locator('.fb-rule[data-field="assignee"]')).toHaveCount(1);
  await page.getByRole('group', { name: 'Assignee', exact: true }).getByLabel('Me (current viewer)', { exact: true }).check();
  await expect(filter(page).locator('.pill')).toHaveText('1');
});

test('filter cleanup: disabled accounts stay visible only when already selected and are labeled', async ({ page }) => {
  const account = await api('POST', '/accounts', { handle: 'retired-filter-test', display_name: 'Retired Filter Agent', kind: 'agent' });
  await api('PATCH', `/accounts/${account.id}`, { disabled: true });
  const view = await api('POST', '/views', {
    name: 'Historical account filter', scope_kind: 'section', scope_id: seed().workSectionId, shared: false,
    config: { filter: { op: 'and', rules: [{ field: 'assignee', cmp: 'is', value: [account.handle] }] } },
  });
  await open(page, `/v/${view.id}`);
  await expect(page.locator('.filter-chip')).toContainText('disabled');
  await filter(page).click();
  const retired = page.getByRole('group', { name: 'Assignee', exact: true }).getByLabel(/Retired Filter Agent.*disabled/);
  await expect(retired).toBeChecked();
  await retired.click();
  await page.getByRole('button', { name: '+ Rule', exact: true }).click();
  await page.getByLabel('Field', { exact: true }).selectOption('assignee');
  await expect(page.getByLabel(/Retired Filter Agent/)).toHaveCount(0);
});
