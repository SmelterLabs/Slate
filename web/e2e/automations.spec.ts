// Automations: build a rule in the builder, watch it fire live when the API changes a task, read
// the run log, and pause it with the switch.
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

test('build a rule, it fires live, the run log shows it, the switch pauses it', async ({ page }) => {
  const { alex } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Robots', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Pipeline' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Ship the parser' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  await page.getByRole('link', { name: 'List automations' }).click();
  await expect(page.getByRole('heading', { name: /Automations.·.Pipeline/ })).toBeVisible();

  // A preset opens the builder filled in; switch it to our own rule.
  await page.getByRole('button', { name: /Tidy up finished work/ }).click();
  const dialog = page.getByRole('dialog', { name: 'New automation' });
  await expect(dialog.getByLabel('Scope')).toHaveValue(`list:${list.id}`);
  await dialog.getByLabel('Automation name').fill('Tag shipped work');
  await dialog.getByRole('button', { name: 'Remove action 2' }).click();
  await dialog.locator('#action-0-tag').fill('shipped');
  await expect(dialog.locator('.words')).toHaveText('When status changes to a done status, then add tag shipped.');
  await dialog.getByRole('button', { name: 'Create automation' }).click();
  await expect(dialog).toHaveCount(0);

  const rule = page.locator('article.rule', { hasText: 'Tag shipped work' });
  await expect(rule).toContainText('When status changes');
  await expect(rule).toContainText('Not run yet');

  // An agent (here the API) finishes the task: the rule fires and the counters update live.
  await api('PATCH', `/items/${item.id}`, alex, { status: 'Done' });
  await expect(rule).toContainText('Ran 1 time');
  await page.getByRole('tab', { name: /Run log/ }).click();
  const run = page.locator('.log .run').first();
  await expect(run).toContainText('Tag shipped work');
  await expect(run).toContainText('Ship the parser');
  await expect(run).toContainText('add tag shipped');

  // The task window says the tag came from the automation.
  await run.getByRole('button', { name: 'Ship the parser' }).click();
  await expect(page.locator('.tw')).toContainText('by automation “Tag shipped work”');
  await page.keyboard.press('Escape');

  // The switch pauses it: the next change does not fire.
  await page.getByRole('tab', { name: /Rules/ }).click();
  await rule.getByLabel('Tag shipped work on').uncheck({ force: true });
  await expect(rule).toHaveClass(/off/);
  // The class flips before the save lands: wait for the server.
  await expect
    .poll(async () => (await api<{ enabled: boolean }[]>('GET', `/automations?list_id=${list.id}`, alex))[0].enabled)
    .toBe(false);
});
