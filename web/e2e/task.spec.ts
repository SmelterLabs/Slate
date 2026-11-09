// The task window: opened by link, checklists, description tick boxes, files, reactions,
// watching, and the activity column as an agent works the task.
import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_URL, seed } from './seed';

const shots = process.env.SLATE_SCREENSHOTS
  ? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs', 'img')
  : null;
if (shots) mkdirSync(shots, { recursive: true });
async function shot(page: Page, name: string, scheme: 'dark' | 'light' = 'dark') {
  if (!shots) return;
  await page.emulateMedia({ colorScheme: scheme });
  await page.screenshot({ path: join(shots, `${name}.png`) });
  await page.emulateMedia({ colorScheme: null });
}

async function api<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

// A 2x2 PNG, so the attachment shows a thumbnail.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR4nGNgYGD4z8DAwMDAwMDAAAANBAEB1RbKFwAAAABJRU5ErkJggg==',
  'base64',
);

test('the task window: link, checklists, tick boxes, files, reactions, watching, live activity', async ({
  page,
}) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Projects', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Garage' });
  const item = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Replace the garage door opener',
    priority: 'high',
    due_at: '2030-03-14',
    tags: ['home', 'weekend'],
    assignees: ['alex', 'agent'],
    body_md: 'The old one grinds. Budget **$350**.\n\n- [ ] measure the rail\n- [ ] pick a model\n- [x] find the manual',
  });
  await api('POST', `/items/${item.id}/checklists`, alex, {
    name: 'Install',
    items: ['Unplug the old unit', 'Mount the rail', 'Program the remotes'],
  });
  await api('POST', '/items', alex, { parent_id: item.id, title: 'Buy a ladder', due_at: '2030-03-10' });
  await api('POST', `/items/${item.id}/comments`, agent, {
    body_md: 'Found two models under budget. @alex which do you prefer?',
  });
  const up = await fetch(`${BASE_URL}/api/v1/items/${item.id}/attachments?filename=opener.png`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${agent}`, 'Content-Type': 'image/png' },
    body: PNG,
  });
  expect(up.status).toBe(201);
  await api('PATCH', `/items/${item.id}`, agent, { status: 'In progress' });

  // A link with ?task= opens the window.
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await expect(tw.getByLabel('Title')).toHaveValue('Replace the garage door opener');

  // Checklist: tick one, add one.
  const install = tw.getByRole('region', { name: 'Checklist Install' });
  await expect(install).toContainText('0/3');
  await install.getByRole('checkbox', { name: 'Done: Mount the rail' }).check();
  await expect(install).toContainText('1/3');
  await install.getByLabel('Add to Install').fill('Test the safety sensor');
  await install.getByLabel('Add to Install').press('Enter');
  await expect(install).toContainText('1/4');

  // A tick box in the description saves without opening the editor.
  const desc = tw.getByRole('region', { name: 'Description' });
  await desc.locator('input[data-line="2"]').check();
  await expect
    .poll(async () => (await api<{ body_md: string }>('GET', `/items/${item.id}`, alex)).body_md)
    .toContain('- [x] measure the rail');

  // The file shows as a thumbnail; clicking it opens the full-screen viewer, Escape closes it
  // (and leaves the task window open).
  const thumb = tw.getByRole('region', { name: 'Attachments' }).locator('img');
  await expect(thumb).toBeVisible();
  await thumb.click();
  const viewer = page.getByRole('dialog', { name: 'Viewing opener.png' });
  await expect(viewer.locator('img')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(viewer).toBeHidden();
  await expect(tw.getByLabel('Title')).toHaveValue('Replace the garage door opener');

  // React to the agent's question; the mention of me is highlighted.
  const question = tw.locator('.activity .comment', { hasText: 'Found two models' });
  await expect(question.locator('.mention.me')).toHaveText('@alex');
  await question.hover();
  await question.getByRole('button', { name: 'Add reaction' }).click();
  await question.getByRole('menuitem', { name: 'React 👀' }).click();
  await expect(question.getByRole('button', { name: /👀 1/ })).toBeVisible();

  // Watching: creating the task auto-watched it; the eye shows it.
  await expect(tw.locator('.watch.on')).toBeVisible();

  // The agent works the task; the window follows live, history in gray lines.
  await api('POST', `/items/${item.id}/comments`, agent, { body_md: 'Ordered the quieter model.' });
  await expect(tw.locator('.activity .comment', { hasText: 'Ordered the quieter' })).toBeVisible();
  await expect(tw.locator('.activity .ev', { hasText: 'Agent changed status' })).toContainText('In progress');
  await expect(tw.locator('.activity .ev', { hasText: 'You checked “Mount the rail”' })).toBeVisible();
  await page.mouse.move(0, 0);
  await shot(page, 'task-window-full');
  await shot(page, 'task-window-light', 'light');

  // Esc closes and the link leaves the URL.
  await page.keyboard.press('Escape');
  await expect(tw).toHaveCount(0);
  expect(page.url()).not.toContain('task=');

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});

test('revert from the history: an agent rename goes back as my change; a stale row says changed since', async ({
  page,
}) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Revert', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Edits' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Paint the gate' });
  await api('PATCH', `/items/${item.id}`, agent, { title: 'Paint the gate (Agent)' });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await tw.getByRole('tab', { name: 'History' }).click();
  const row = tw.locator('.activity .ev', { hasText: 'Agent renamed this to “Paint the gate (Agent)”' });
  await expect(row).toBeVisible();
  // My own creation row offers nothing; the agent's rename does.
  await expect(tw.locator('.activity .ev', { hasText: 'You created this' }).locator('.revert')).toHaveCount(0);
  page.once('dialog', (d) => void d.accept());
  await row.locator('.revert').click();
  await expect(page.locator('.toast')).toContainText('Put back');
  await expect(tw.locator('.activity .ev', { hasText: 'You renamed this to “Paint the gate”' })).toBeVisible();
  await expect.poll(async () => (await api<{ title: string }>('GET', `/items/${item.id}`, alex)).title).toBe('Paint the gate');

  // Reverting the same old row again: the field has moved on, so the server refuses (409)
  // and the row stays disabled with the reason.
  page.once('dialog', (d) => void d.accept());
  await row.locator('.revert').click();
  const stale = row.locator('.revert');
  await expect(stale).toBeDisabled();
  await expect(stale).toHaveText('Changed since');
  await expect(stale).toHaveAttribute('title', /changed since/);

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});
