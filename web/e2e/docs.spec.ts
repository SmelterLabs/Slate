// Docs: an agent writes a page over the API and the open board shows it live, with the task it
// embeds as a live card; Alex edits the page and it autosaves; the slash menu adds a checklist;
// page history lists the versions; and a phone gets the same page and tools.
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

interface Doc {
  id: string;
  title: string;
  body_md: string;
  revision: number;
}

test('an agent writes a page, the board follows live, and Alex edits it', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const task = await api<{ id: string }>('POST', '/items', agent, { list_id: listId, title: 'Book the venue' });
  const doc = await api<Doc>('POST', '/docs', agent, {
    title: 'Offsite plan',
    list_id: listId,
    body_md: `## Agenda\n\nMorning: planning.\n\n[[task:${task.id}]]\n`,
  });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto('/#/home');
  await page.getByRole('button', { name: 'More', exact: true }).click();
  await page.getByRole('link', { name: 'Docs & Files', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Docs & Files', level: 1 })).toBeVisible();
  await page.getByRole('tree', { name: 'Pages and files' }).getByRole('link', { name: /Offsite plan/ }).click();

  await expect(page.getByRole('heading', { name: 'Agenda' })).toBeVisible();
  const card = page.locator(`.task-embed[data-item="${task.id}"]`);
  await expect(card).toContainText('Book the venue');

  // The embedded task is live: a rename elsewhere shows up on the card.
  await api('PATCH', `/items/${task.id}`, agent, { title: 'Book the venue for June' });
  await expect(card).toContainText('Book the venue for June');

  // An agent appends to the page: it shows without a reload.
  await api('PATCH', `/docs/${doc.id}`, agent, { append_md: '\nAfternoon: the roadmap.' });
  await expect(page.locator('.doc-rendered')).toContainText('Afternoon: the roadmap.');

  // Alex types: it autosaves.
  await page.locator('.doc-rendered').click();
  const editor = page.getByRole('textbox', { name: 'Page text', exact: true });
  await expect(editor).toBeFocused();
  await editor.press('ControlOrMeta+End');
  await editor.pressSequentially('\nEvening: dinner.');
  await expect(page.locator('.doc-save')).toContainText('Saved');
  await expect
    .poll(async () => (await api<Doc>('GET', `/docs/${doc.id}`, alex)).body_md)
    .toContain('Evening: dinner.');

  // The slash menu turns a line into a checklist.
  await editor.pressSequentially('\n/check');
  await expect(page.getByRole('listbox', { name: 'Insert' })).toBeVisible();
  await editor.press('Enter');
  await editor.pressSequentially('Send the invites');
  await editor.press('Escape');
  await page.locator('.doc-title').click();
  await expect(page.locator('.doc-rendered input.md-task')).toHaveCount(1);
  await expect
    .poll(async () => (await api<Doc>('GET', `/docs/${doc.id}`, alex)).body_md)
    .toContain('- [ ] Send the invites');

  // Page history lists who wrote what.
  await page.getByRole('button', { name: 'Page history' }).click();
  const history = page.getByRole('complementary', { name: 'Page history' });
  await expect(history.locator('.dh-list li')).not.toHaveCount(0);
  await expect(history.getByRole('tab', { name: 'Changes since' })).toBeVisible();
});

test('a phone gets the same page and tools', async ({ browser }) => {
  const { alex, listId } = seed();
  const doc = await api<Doc>('POST', '/docs', alex, { title: 'Phone page', list_id: listId, body_md: '## One\n\nHello.' });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/docs/${doc.id}`);
  await expect(page.getByRole('heading', { name: 'One' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Page history' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Page actions' })).toBeVisible();
  await page.locator('.doc-rendered').click();
  await expect(page.getByRole('toolbar', { name: 'Formatting' })).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await context.close();
});

test('a page filed in a section is reachable from that section in the sidebar', async ({ page }) => {
  const { alex } = seed();
  const name = `Pages here ${Date.now()}`;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name, audience: 'work' });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/section/${section.id}`);
  await expect(page.getByRole('group', { name: `Section ${name}` })).toBeVisible();

  // Filed from elsewhere (an agent, another device): the sidebar follows live.
  const doc = await api<{ id: string }>('POST', '/docs', alex, { title: 'Project spec', section_id: section.id, body_md: 'Hello' });
  const row = page.locator('.nav-doc', { hasText: 'Project spec' });
  await expect(row).toBeVisible();
  // A subpage stays inside the page tree, not in the sidebar.
  await api('POST', '/docs', alex, { title: 'Spec detail', parent_id: doc.id, body_md: 'Sub' });
  await expect(page.locator('.nav-doc', { hasText: 'Spec detail' })).toHaveCount(0);

  // Going somewhere else and back: the sidebar link opens the page and marks it active.
  await page.goto('/#/everything');
  await row.getByRole('link', { name: /Project spec/ }).click();
  await expect(page).toHaveURL(new RegExp(`#/docs/${doc.id}`));
  await expect(page.getByText('Hello', { exact: true })).toBeVisible();
  await expect(row).toHaveClass(/active/);
});

test('a page that starts with a facts block shows them as a table, live, and keeps the block editable', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const doc = await api<Doc>('POST', '/docs', agent, {
    title: 'Home insurance',
    list_id: listId,
    body_md: '```facts\nPolicy number: HO-4471-220\nRenews: 2027-03-01\n```\n\n## Notes\n\nClaim closed.\n',
  });
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/docs/${doc.id}`);
  const facts = page.getByRole('table', { name: 'Facts' });
  await expect(facts.getByRole('row')).toHaveCount(2);
  await expect(facts.getByRole('rowheader', { name: 'Policy number' })).toBeVisible();
  await expect(facts.getByRole('cell', { name: 'HO-4471-220' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Notes' })).toBeVisible();
  // The editor still holds the raw block (no facts editor): it is a code block in the page.
  await expect(page.locator('.doc-rendered pre code')).toContainText('Policy number: HO-4471-220');
  // An agent changes a value: the table follows without a reload.
  await api('PATCH', `/docs/${doc.id}`, agent, {
    body_md: '```facts\nPolicy number: HO-4471-220\nRenews: 2028-03-01\nCarrier: Example Mutual\n```\n\n## Notes\n\nClaim closed.\n',
  });
  await expect(facts.getByRole('row')).toHaveCount(3);
  await expect(facts.getByRole('cell', { name: '2028-03-01' })).toBeVisible();
  // A page without the block has no table.
  const plain = await api<Doc>('POST', '/docs', agent, { title: 'Plain page', list_id: listId, body_md: 'Just notes.' });
  await page.goto(`/#/docs/${plain.id}`);
  await expect(page.getByText('Just notes.', { exact: true })).toBeVisible();
  await expect(page.getByRole('table', { name: 'Facts' })).toHaveCount(0);
});
