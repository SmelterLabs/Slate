// Agents nobody can wake from Slate: a `pull` agent's card says Nudge (and the pill says where to
// go), a `manual` agent's card says Copy brief (the task as a prompt on the clipboard), and a
// comment pasted back ending `SLATE: done` completes the task.
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

test('Nudge for a pull agent, Copy brief for a manual one, SLATE: done from a paste', async ({
  page,
  context,
}) => {
  const { alex } = seed();
  const n = Date.now() % 1_000_000;
  const grok = `grok${n}`;
  const helper = `helper${n}`;
  await api('POST', '/accounts', alex, { handle: grok, display_name: 'Grok', kind: 'agent', wake: 'pull' });
  await api('POST', '/accounts', alex, { handle: helper, display_name: 'Helper', kind: 'agent', wake: 'manual' });
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Wake', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Elsewhere' });
  await api('POST', '/items', alex, { list_id: list.id, title: 'Summarise the news', assignees: [grok] });
  const brief = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Write a birthday poem',
    assignees: [helper],
  });

  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);

  const pulled = page.locator('.card', { hasText: 'Summarise the news' });
  await pulled.hover();
  await pulled.getByRole('button', { name: 'Nudge' }).click();
  await expect(pulled.locator('[data-run-state="nudged"]')).toContainText('Open Grok and say “work Slate”');

  const manual = page.locator('.card', { hasText: 'Write a birthday poem' });
  await manual.hover();
  await expect(manual.getByRole('button', { name: 'Run now' })).toHaveCount(0);
  await manual.getByRole('button', { name: 'Copy brief' }).click();
  await expect(page.getByText(/Brief copied\. Paste it into Helper/)).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain(`Slate task ${brief.id}: Write a birthday poem`);
  expect(copied).toContain('SLATE: done');

  // The person pastes Helper's answer back; its closing line completes the task.
  await api('POST', `/items/${brief.id}/comments`, alex, { body_md: 'Roses are red…\n\nSLATE: done' });
  const done = await api<{ completed_at: string | null }>('GET', `/items/${brief.id}`, alex);
  expect(done.completed_at).not.toBeNull();

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});
