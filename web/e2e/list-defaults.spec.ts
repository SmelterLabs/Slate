// List defaults: a card quick-added to a list with a default owner shows that owner on the
// board without a reload. The hand-off uses the same public batch operations as agents.
import { expect, test } from '@playwright/test';
import { api, signedIn } from './support';

test('default owner: a quick-added card shows its owner without a reload', async ({ page }) => {
  const me = await api<{ account: { id: string } }>('GET', '/me');
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Defaults', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Owned' });
  await signedIn(page, `/#/list/${list.id}`, { live: true });
  const input = page.locator('#quick-add');
  await expect(input).toBeVisible();
  await api('PATCH', `/lists/${list.id}`, {
    note_md: 'Drafts go here.\nInclude the source.', default_assignee_id: me.account.id, default_tags: ['draft'],
  });
  await expect(input).toHaveAttribute('placeholder', 'Drafts go here.');
  await input.fill('Card with a default owner');
  await input.press('Enter');

  const card = page.locator('.card', { hasText: 'Card with a default owner' });
  await expect(card).toBeVisible();
  // The owner arrives with the card (no reload): Alex's avatar is on it.
  await expect(card.locator('[title="Alex (@alex)"]')).toBeVisible();
  await expect
    .poll(async () => {
      const items = await api<{ title: string; tags: string[]; assignees: { handle: string }[] }[]>('GET', `/items?list_id=${list.id}`);
      const item = items.find((i) => i.title === 'Card with a default owner');
      return item && { assignees: item.assignees.map((a) => a.handle), tags: item.tags };
    })
    .toEqual({ assignees: ['alex'], tags: ['draft'] });

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('handing off assigns the agent and posts its mention in one batch', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Hand off', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Drafts' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id, title: 'Proofread the draft', assignees: ['alex'],
  });
  await signedIn(page, `/#/list/${list.id}?task=${item.id}`, { ready: true });
  const task = page.locator('.tw');
  await task.getByRole('button', { name: /Hand to an agent/ }).click();
  await task.getByLabel('What should it do?').fill('Check the heading');
  const handle = await task.getByLabel('Agent', { exact: true }).inputValue();
  const sent = page.waitForResponse((response) => response.url().endsWith('/api/v1/batch') && response.request().method() === 'POST');
  await task.getByRole('button', { name: 'Hand off', exact: true }).click();
  const response = await sent;
  expect(response.ok(), await response.text()).toBe(true);
  await expect(task.getByText(`@${handle} Check the heading`, { exact: true })).toBeVisible();
  const saved = await api<{ assignees: { handle: string }[] }>('GET', `/items/${item.id}`);
  expect(saved.assignees.map((account) => account.handle).sort()).toEqual(['alex', handle].sort());
  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
