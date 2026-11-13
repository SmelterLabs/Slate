import { expect, test } from '@playwright/test';
import { addRevision, api, seed, signIn } from './support';

async function drawer() {
  const section = await api('POST', '/sections', { name: `Collab ${Date.now()}`, audience: 'work' });
  return api('POST', '/lists', { name: 'Files', section_id: section.id, kind: 'drawer' });
}

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test('a phone drawer opens the document first, with discussion still reachable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const list = await drawer();
  const doc = await api('POST', '/docs', { title: 'Phone plan', list_id: list.id, body_md: 'Read this plan first.' });
  await page.goto(`/#/list/${list.id}?doc=${doc.id}`);
  await expect(page.locator('.doc-rendered')).toContainText('Read this plan first.');
  await expect(page.getByRole('complementary', { name: 'Discussion', exact: true })).not.toBeVisible();
  await page.getByRole('button', { name: 'Discussion', exact: true }).click();
  await expect(page.getByLabel('Discussion comment')).toBeVisible();
  await page.getByRole('button', { name: 'Close discussion' }).click();
  await expect(page.locator('.doc-rendered')).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('phone-content.png'), fullPage: true });
});

test('drawer name filter states its scope', async ({ page }) => {
  const list = await drawer();
  await page.goto(`/#/list/${list.id}`);
  await expect(page.getByRole('textbox', { name: 'Filter this folder by name' })).toBeVisible();
});

test('a conflicted draft survives navigation and reload and keep mine remains guarded', async ({ page }) => {
  const doc = await api('POST', '/docs', { title: 'Conflict plan', body_md: 'Original' });
  const other = await api('POST', '/docs', { title: 'Other plan', body_md: 'Other' });
  await page.goto(`/#/docs/${doc.id}`);
  await page.locator('.doc-rendered').click();
  await page.getByRole('textbox', { name: 'Page text', exact: true }).fill('My unsaved text');
  await api('PATCH', `/docs/${doc.id}`, { body_md: 'Their saved text' }, seed().agent);
  await expect(page.locator('.doc-conflict')).toBeVisible();
  await page.evaluate(id => location.hash = `#/docs/${id}`, other.id);
  await expect(page.locator('.doc-rendered')).toContainText('Other');
  await page.evaluate(id => location.hash = `#/docs/${id}`, doc.id);
  await expect(page.locator('.doc-rendered')).toContainText('My unsaved text');
  await page.reload();
  await expect(page.locator('.doc-rendered')).toContainText('My unsaved text');
  await expect(page.getByRole('button', { name: 'Download my draft' })).toBeVisible();
  let revision: unknown;
  await page.route(`**/api/v1/docs/${doc.id}`, async route => {
    if (route.request().method() === 'PATCH') revision = route.request().postDataJSON().base_revision;
    await route.continue();
  });
  await page.getByRole('button', { name: 'Keep mine', exact: true }).click();
  await expect.poll(async () => (await api('GET', `/docs/${doc.id}`)).body_md).toBe('My unsaved text');
  expect(revision).toBe(2);
});

test('a failed page save offers an immediate draft download', async ({ page }) => {
  const doc = await api('POST', '/docs', { title: 'Offline plan', body_md: 'Original' });
  await page.route(`**/api/v1/docs/${doc.id}`, async route => {
    if (route.request().method() === 'PATCH') await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Offline"}' });
    else await route.continue();
  });
  await page.goto(`/#/docs/${doc.id}`);
  await page.locator('.doc-rendered').click();
  const failedSave = page.waitForResponse(r => r.url().endsWith(`/docs/${doc.id}`) && r.request().method() === 'PATCH');
  await page.getByRole('textbox', { name: 'Page text', exact: true }).fill('My offline draft');
  expect((await failedSave).status()).toBe(503);
  await expect(page.getByRole('button', { name: 'Download my draft' })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download my draft' }).click();
  const stream = await (await download).createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks).toString()).toContain('My offline draft');
});

test('review on an old viewed file revision sends that revision, even after upload' , async ({ page }) => {
  const doc = await api('POST', '/docs', { title: 'Revision source', kind: 'file' });
  for (const n of [1, 2]) await addRevision(doc.id, 'a.txt', `Revision ${n}`, { note: `revision${n}` });
  await page.goto(`/#/docs/${doc.id}`);
  await page.getByRole('button', { name: 'Revisions', exact: true }).click();
  await page.locator('.rev-item[data-revision="1"] .rev-pick').click();
  await expect(page.locator('.file-view')).toHaveAttribute('data-revision', '1');
  await page.getByRole('button', { name: 'Discussion', exact: true }).click();
  await page.getByRole('tab', { name: /Review/ }).click();
  await page.getByRole('button', { name: 'comment on a page' }).click();
  await page.getByRole('textbox', { name: 'Page', exact: true }).fill('1');
  await page.getByLabel('Review comment', { exact: true }).fill('Check old revision');
  await page.getByRole('button', { name: 'Start thread', exact: true }).click();
  await expect.poll(async () => (await api('GET', `/docs/${doc.id}/comments`)).length).toBe(1);
  const comments = await api('GET', `/docs/${doc.id}/comments`);
  expect(comments[0].anchor_revision).toBe(1);
  expect(comments[0].outdated).toBe(true);
});

test('task from a drawer offers only task lists and keeps failed form open' , async ({ page }) => {
  const list = await drawer();
  const doc = await api('POST', '/docs', { title: 'Task source', list_id: list.id, body_md: 'Call supplier' });
  await page.goto(`/#/docs/${doc.id}`);
  await page.locator('.doc-rendered p').evaluate(el => {
    const range = document.createRange(); range.selectNodeContents(el);
    const sel = window.getSelection()!; sel.removeAllRanges(); sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  });
  await page.locator('.doc-bubble').getByRole('button', { name: /task/i }).click();
  const form = page.locator('.doc-newtask');
  await expect(form.locator(`option[value="${list.id}"]`)).toHaveCount(0);
  await page.route(`**/api/v1/docs/${doc.id}/tasks`, route => route.fulfill({ status: 422, contentType: 'application/json', body: '{"detail":"Not saved"}' }));
  await form.getByRole('button', { name: 'Create task', exact: true }).click();
  await expect(page.getByText('Not saved', { exact: true })).toBeVisible();
  await expect(form).toBeVisible();
  await expect(page.getByText('Task “Call supplier” created', { exact: true })).not.toBeVisible();
  await page.unroute(`**/api/v1/docs/${doc.id}/tasks`);
  await form.getByRole('button', { name: 'Create task', exact: true }).click();
  await expect(form).not.toBeVisible();
  await expect(page.locator('.task-embed')).toContainText('Call supplier');
});
