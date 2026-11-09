import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';
async function api(method: string, path: string, body?: unknown, token = seed().alex) {
  const r = await fetch(`${BASE_URL}/api/v1${path}`, {method, headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  expect(r.ok).toBe(true); return r.json();
}
async function open(page: Page, id: string) {
  await page.goto(`/#/docs/${id}`); await expect(page.locator('.doc-rendered')).toBeVisible();
}
test.beforeEach(async ({ page }) => {
  await page.addInitScript(t => { if (!sessionStorage.getItem('seeded')) {localStorage.setItem('slate.token', t); sessionStorage.setItem('seeded', '1');} }, seed().alex);
});

test('phone discussion and thread drafts survive close, reopen and reload', async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  const doc = await api('POST', '/docs', {title: 'Phone draft retention', body_md: 'Plan text'});
  const root = await api('POST', `/docs/${doc.id}/comments`, {body_md: 'Reply here'});
  await open(page, doc.id);
  const toggle = page.getByRole('button', {name: 'Discussion', exact: true});
  await toggle.click();
  await page.getByLabel('Discussion comment', {exact: true}).fill('Unsent phone discussion');
  await page.getByRole('button', {name: 'Close discussion'}).click(); await toggle.click();
  await expect(page.getByLabel('Discussion comment', {exact: true})).toHaveValue('Unsent phone discussion');
  await page.locator(`#thread-${root.id}`).getByRole('button', {name: 'Reply', exact: true}).click();
  await page.getByLabel('Reply', {exact: true}).fill('Unsent phone reply');
  await page.getByRole('button', {name: 'Close discussion'}).click(); await toggle.click();
  await page.locator(`#thread-${root.id}`).getByRole('button', {name: 'Reply', exact: true}).click();
  await expect(page.getByLabel('Reply', {exact: true})).toHaveValue('Unsent phone reply');
  await page.reload(); await toggle.click();
  await expect(page.getByLabel('Discussion comment', {exact: true})).toHaveValue('Unsent phone discussion');
  await page.locator(`#thread-${root.id}`).getByRole('button', {name: 'Reply', exact: true}).click();
  await expect(page.getByLabel('Reply', {exact: true})).toHaveValue('Unsent phone reply');
  // Same document/thread, different identity: neither composer inherits the other person's text.
  await page.evaluate(t => localStorage.setItem('slate.token', t), seed().agent);
  await page.reload(); await toggle.click();
  await expect(page.getByLabel('Discussion comment', {exact: true})).toHaveValue('');
  await page.locator(`#thread-${root.id}`).getByRole('button', {name: 'Reply', exact: true}).click();
  await expect(page.getByLabel('Reply', {exact: true})).toHaveValue('');
  await page.evaluate(t => localStorage.setItem('slate.token', t), seed().alex);
  await page.reload(); await toggle.click();
  await expect(page.getByLabel('Discussion comment', {exact: true})).toHaveValue('Unsent phone discussion');
  await page.locator(`#thread-${root.id}`).getByRole('button', {name: 'Reply', exact: true}).click();
  await expect(page.getByLabel('Reply', {exact: true})).toHaveValue('Unsent phone reply');
});

test('a completed send from a closed phone panel cannot erase a newer composer draft', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  const doc = await api('POST', '/docs', {title: 'Pending discussion', body_md: 'Plan'});
  await open(page, doc.id); await page.getByRole('button', {name: 'Discussion', exact: true}).click();
  let release!: () => void; const held = new Promise<void>(r => release = r);
  let seen!: () => void; const requested = new Promise<void>(r => seen = r);
  await page.route(`**/api/v1/docs/${doc.id}/comments`, async route => {seen(); await held; await route.continue();});
  await page.getByLabel('Discussion comment', {exact: true}).fill('First sent message');
  await page.getByRole('button', {name: 'Send', exact: true}).click(); await requested;
  await page.getByRole('button', {name: 'Close discussion'}).click();
  await page.getByRole('button', {name: 'Discussion', exact: true}).click();
  await page.getByLabel('Discussion comment', {exact: true}).fill('New unsent message');
  release(); await expect(page.locator('.dd-body')).toContainText('First sent message');
  await page.reload(); await page.getByRole('button', {name: 'Discussion', exact: true}).click();
  await expect(page.getByLabel('Discussion comment', {exact: true})).toHaveValue('New unsent message');
});

test('undo to clean does not resurrect abandoned page recovery after reload', async ({page}) => {
  const doc = await api('POST', '/docs', {title: 'Undo recovery', body_md: 'Original'});
  const me = await api('GET', '/me');
  await open(page, doc.id); await page.locator('.doc-rendered').click();
  const text = page.getByRole('textbox', {name: 'Page text', exact: true});
  await text.fill('Abandoned draft'); await text.fill('Original');
  await expect.poll(() => page.evaluate(k => localStorage.getItem(k), `slate.doc-draft:${me.account.id}:${doc.id}`)).toBeNull();
  await page.reload(); await expect(page.locator('.doc-rendered')).toHaveText('Original');
  await expect(page.locator('.doc-conflict')).not.toBeVisible();
});

test('undo while autosave is pending persists the undo and saves it against the acknowledged revision', async ({page}) => {
  const doc = await api('POST', '/docs', {title: 'Pending undo recovery', body_md: 'Original'});
  const me = await api('GET', '/me');
  let release!: () => void; const held = new Promise<void>(r => release = r);
  let seen!: () => void; const requested = new Promise<void>(r => seen = r);
  let first = true;
  await page.route(`**/api/v1/docs/${doc.id}`, async route => {
    if (route.request().method() === 'PATCH' && first) {first = false; seen(); await held;}
    await route.continue();
  });
  await open(page, doc.id); await page.locator('.doc-rendered').click();
  const text = page.getByRole('textbox', {name: 'Page text', exact: true});
  await text.fill('Sent edit'); await requested; await text.fill('Original');
  const storageKey = `slate.doc-draft:${me.account.id}:${doc.id}`;
  expect(JSON.parse((await page.evaluate(k => localStorage.getItem(k), storageKey))!).body_md).toBe('Original');
  release();
  await expect.poll(async () => (await api('GET', `/docs/${doc.id}`)).revision).toBe(3);
  expect((await api('GET', `/docs/${doc.id}`)).body_md).toBe('Original');
  await expect.poll(() => page.evaluate(k => localStorage.getItem(k), storageKey)).toBeNull();
  await page.reload(); await expect(page.locator('.doc-rendered')).toHaveText('Original');
});

test('phone review draft retains its anchor and fields; failure retains and successful send clears it', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  const doc = await api('POST', '/docs', {title: 'Review draft', body_md: 'Original quote'});
  await open(page, doc.id);
  async function selectQuote() {
    await page.locator('.doc-rendered p').evaluate(el => {
      const range = document.createRange(); range.selectNodeContents(el);
      const selection = window.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
      document.dispatchEvent(new Event('selectionchange'));
    });
    await page.locator('.doc-bubble').getByRole('button', {name: /comment/i}).click();
  }
  await selectQuote();
  await page.getByLabel('Review comment', {exact: true}).fill('Check this exact quote');
  await page.getByLabel('Suggested replacement', {exact: true}).fill('Proposed quote');
  await page.getByRole('button', {name: 'Close discussion'}).click();
  await page.getByRole('button', {name: 'Discussion', exact: true}).click();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('Check this exact quote');
  await expect(page.getByLabel('Suggested replacement', {exact: true})).toHaveValue('Proposed quote');
  await page.reload(); await selectQuote();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('Check this exact quote');
  await page.evaluate(t => localStorage.setItem('slate.token', t), seed().agent);
  await page.reload(); await selectQuote();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('');
  await expect(page.getByLabel('Suggested replacement', {exact: true})).toHaveValue('');
  await page.evaluate(t => localStorage.setItem('slate.token', t), seed().alex);
  await page.reload(); await selectQuote();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('Check this exact quote');
  await expect(page.getByLabel('Suggested replacement', {exact: true})).toHaveValue('Proposed quote');
  await page.route(`**/api/v1/docs/${doc.id}/comments`, route => route.fulfill({status: 503, json: {detail: 'Retry this review'}}));
  await page.getByRole('button', {name: 'Start thread', exact: true}).click();
  await expect(page.getByText('Retry this review', {exact: true})).toBeVisible();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('Check this exact quote');
  await page.unroute(`**/api/v1/docs/${doc.id}/comments`);
  await page.getByRole('button', {name: 'Start thread', exact: true}).click();
  await expect(page.getByTestId('review-form')).not.toBeVisible();
  const comments = await api('GET', `/docs/${doc.id}/comments`);
  expect(comments).toHaveLength(1); expect(comments[0].anchor_quote).toBe('Original quote');
  expect(comments[0].suggestion_md).toBe('Proposed quote');
  await page.reload(); await selectQuote();
  await expect(page.getByLabel('Review comment', {exact: true})).toHaveValue('');
});

test('obsolete 404 cannot erase the document opened after it', async ({page}) => {
  const a = await api('POST', '/docs', {title: 'Obsolete', body_md: 'Old'});
  const b = await api('POST', '/docs', {title: 'Current', body_md: 'Current route body'});
  let release!: () => void; const held = new Promise<void>(r => release = r);
  let seen!: () => void; const requested = new Promise<void>(r => seen = r);
  await page.route(`**/api/v1/docs/${a.id}`, async route => { seen(); await held; await route.fulfill({status: 404, json: {detail: 'Obsolete missing'}}); });
  await page.goto(`/#/docs/${a.id}`); await requested;
  await page.evaluate(id => location.hash = `#/docs/${id}`, b.id);
  await expect(page.locator('.doc-rendered')).toHaveText('Current route body');
  const done = page.waitForResponse(r => r.url().endsWith(`/docs/${a.id}`)); release(); await done;
  await expect(page.locator('.doc-rendered')).toHaveText('Current route body');
});

test('401 followed by a different token on the same route clears private document state and preserves original recovery', async ({page}) => {
  const doc = await api('POST', '/docs', {title: 'Private account document', body_md: 'Private Alice body'});
  const alice = await api('GET', '/me'); const bob = await api('GET', '/me', undefined, seed().agent);
  await open(page, doc.id);
  let newIdentity = false;
  await page.route(`**/api/v1/docs/${doc.id}`, async route => {
    if (route.request().method() === 'PATCH') await route.fulfill({status: 401, json: {detail: 'Expired'}});
    else if (newIdentity) await route.fulfill({status: 403, json: {detail: 'Private document'}});
    else await route.continue();
  });
  await page.locator('.doc-rendered').click();
  await page.getByRole('textbox', {name: 'Page text', exact: true}).fill('Private Alice unsaved draft');
  await expect(page.getByRole('button', {name: 'Sign in', exact: true})).toBeVisible();
  newIdentity = true;
  await page.getByLabel('Token', {exact: true}).fill(seed().agent);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Sign in', exact: true})).not.toBeVisible();
  await expect(page.locator('.doc-rendered')).not.toBeVisible();
  await expect(page.getByText('Private Alice unsaved draft', {exact: true})).not.toBeVisible();
  expect(await page.evaluate(k => localStorage.getItem(k), `slate.doc-draft:${bob.account.id}:${doc.id}`)).toBeNull();
  expect(await page.evaluate(k => localStorage.getItem(k), `slate.doc-draft:${alice.account.id}:${doc.id}`)).toContain('Private Alice unsaved draft');
});
