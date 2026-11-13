// Deleting a comment: its author deletes their own, an owner or admin anyone's. The Delete button
// shows only where the server would allow it; after a confirm the comment leaves the window and
// the history says who deleted whose comment.
import { expect, test } from '@playwright/test';
import { api, signIn } from './support';

test('a member deletes only their own comment; the owner deletes anyone’s', async ({ browser }) => {
  const handle = `member${Date.now().toString(36)}`;
  const member = await api<{ id: string }>('POST', '/accounts', {
    handle,
    display_name: 'Member',
    kind: 'person',
  });
  const memberToken = (await api<{ token: string }>('POST', `/accounts/${member.id}/tokens`, { scopes: ['write'] }))
    .token;
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Deleting', audience: 'personal' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Notes' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Tidy the notes' });
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Alex wrote this' });
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Member first note' }, memberToken);
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Member second note' }, memberToken);

  // A member: Delete on their own comments only.
  const asMember = await browser.newPage();
  await signIn(asMember, memberToken);
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
  await signIn(asAlex);
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
