// Deleting a comment: its author deletes their own, an owner or admin anyone's. The Delete button
// shows only where the server would allow it; after a confirm the comment leaves the window and
// the history says who deleted whose comment.
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

test('a member deletes only their own comment; the owner deletes anyone’s', async ({ browser }) => {
  const { alex } = seed();
  const handle = `member${Date.now().toString(36)}`;
  const member = await api<{ id: string }>('POST', '/accounts', alex, {
    handle,
    display_name: 'Member',
    kind: 'person',
  });
  const memberToken = (await api<{ token: string }>('POST', `/accounts/${member.id}/tokens`, alex, { scopes: ['write'] }))
    .token;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Deleting', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Notes' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Tidy the notes' });
  await api('POST', `/items/${item.id}/comments`, alex, { body_md: 'Alex wrote this' });
  await api('POST', `/items/${item.id}/comments`, memberToken, { body_md: 'Member first note' });
  await api('POST', `/items/${item.id}/comments`, memberToken, { body_md: 'Member second note' });

  // A member: Delete on their own comments only.
  const asMember = await browser.newPage();
  await asMember.addInitScript((t) => localStorage.setItem('slate.token', t), memberToken);
  await asMember.goto(`/#/list/${list.id}?task=${item.id}`);
  const btw = asMember.locator('.tw');
  const alexs = btw.locator('article.comment', { hasText: 'Alex wrote this' });
  const ownNote = btw.locator('article.comment', { hasText: 'Member second note' });
  await expect(alexs).toBeVisible();
  await expect(alexs.getByRole('button', { name: 'Delete', exact: true })).toHaveCount(0);
  asMember.once('dialog', (d) => {
    expect(d.message()).toMatch(/^Delete your comment\?/);
    void d.accept();
  });
  await ownNote.hover();
  await ownNote.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(ownNote).toHaveCount(0);
  await expect(btw.locator('.ev', { hasText: 'You deleted a comment' })).toBeVisible();

  // Alex (the owner): Delete on the member's remaining comment; a Cancel keeps it.
  const asAlex = await browser.newPage();
  await asAlex.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await asAlex.goto(`/#/list/${list.id}?task=${item.id}`);
  const atw = asAlex.locator('.tw');
  const theirs = atw.locator('article.comment', { hasText: 'Member first note' });
  await expect(theirs).toBeVisible();
  // The names load just after the task: until then the author reads "Someone".
  await expect(theirs.locator('.who strong')).toHaveText('Member');
  asAlex.once('dialog', (d) => void d.dismiss());
  await theirs.hover();
  await theirs.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(theirs).toBeVisible();
  asAlex.once('dialog', (d) => {
    expect(d.message()).toMatch(/^Delete Member’s comment\?/);
    void d.accept();
  });
  await theirs.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(theirs).toHaveCount(0);
  await expect(atw.locator('.ev', { hasText: 'You deleted Member’s comment' })).toBeVisible();
  // The member's open window follows live.
  await expect(btw.locator('article.comment', { hasText: 'Member first note' })).toHaveCount(0);
  await expect(btw.locator('article.comment', { hasText: 'Alex wrote this' })).toBeVisible();
});
