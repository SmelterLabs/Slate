// While the agent is typing: a message sent now shows "Not read yet" until the running agent
// reads it ("Seen while working"), and Stop on the typing line asks the run to stop.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

type Beat = { messages: { id: string; body_md: string }[]; stop: { by: string } | null };

test('a message sent mid-run is marked seen, and Stop reaches the run', async ({ page }) => {
  const { agent } = seed();
  const me = await api<{ account: { handle: string } }>('GET', '/me');
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Mid-run', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Runs' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Write the release notes',
    assignees: ['agent'],
  });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  const beat = (body: object = {}) =>
    api<Beat>('POST', `/work/${item.id}/heartbeat`, { run_id: taken.lease.run_id, ...body }, agent);
  await beat({ note: 'Reading the changelog' });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await page.locator('.card', { hasText: 'Write the release notes' }).getByText('Write the release notes').click();
  const tw = page.locator('.tw');
  const typing = tw.locator('[data-agent-pending="working"]');
  await expect(typing).toContainText('Agent is typing');
  const stop = typing.getByRole('button', { name: 'Stop' });
  await expect(stop).toBeEnabled();

  // A message while it types: not read yet, then seen once the run takes it.
  await tw.getByLabel('Add comment').fill('Keep it under one page');
  await tw.getByRole('button', { name: /Ask Agent/ }).click();
  const mine = tw.locator('[data-chat="person"]', { hasText: 'Keep it under one page' });
  await expect(mine.locator('[data-seen="no"]')).toContainText('Not read yet');
  const got = await beat();
  expect(got.messages.map((m) => m.body_md)).toEqual(['Keep it under one page']);
  await beat({ seen: got.messages.map((m) => m.id) });
  await expect(mine.locator('[data-seen="yes"]')).toContainText('Seen while working');
  await expect(typing).toContainText('Agent is typing');

  // Stop: the line says so, the run hears it, and its finish ends the typing line.
  await stop.click();
  await expect(typing).toContainText('Agent is stopping');
  await expect(typing.locator('[data-stop-run]')).toBeDisabled();
  expect((await beat()).stop?.by).toBe(me.account.handle);
  await api('POST', `/work/${item.id}/finish`, {
    run_id: taken.lease.run_id,
    outcome: 'blocked',
    waiting_on: me.account.handle,
    reply: 'Stopped as asked. The draft is half done.',
  }, agent);
  await expect(tw.locator('[data-agent-pending]')).toHaveCount(0);
  await expect(tw.locator('[data-chat="agent"]')).toContainText('Stopped as asked');
});
