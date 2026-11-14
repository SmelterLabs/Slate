// While an agent works a card, the task window shows what it is doing right now in a small box
// under "is typing…": the run's lines arrive live, the box keeps its height and scrolls only
// itself (the comments above never move), and it goes away with the run.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('the box under "is typing" shows what the agent is doing and scrolls only itself', async ({ page }) => {
  const { agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Live', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Runs' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Rebuild the search index',
    assignees: ['agent'],
  });
  // Enough comments that the thread itself scrolls.
  for (let n = 1; n <= 14; n++) await api('POST', `/items/${item.id}/comments`, { body_md: `Earlier note ${n}` });

  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  const say = (...lines: string[]) =>
    api('POST', `/work/${item.id}/activity`, { run_id: taken.lease.run_id, lines }, agent);
  await say('terminal: uv run pytest -q');

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  await page.locator('.card', { hasText: 'Rebuild the search index' }).getByText('Rebuild the search index').click();
  const tw = page.locator('.tw');
  const typing = tw.locator('[data-agent-pending="working"]');
  await expect(typing).toContainText('Agent is typing');

  // Opened mid-run: the line posted before the window opened is there.
  const box = typing.locator('[data-live-activity]');
  await expect(box).toContainText('terminal: uv run pytest -q');
  const height = (await box.boundingBox())!.height;
  const head = (await typing.locator('.typing-head').boundingBox())!.height;
  expect(height).toBeGreaterThan(head * 3);

  // The reader scrolls up in the comments; new lines then arrive live.
  const feed = tw.locator('.feed');
  await feed.evaluate((el) => (el.scrollTop = 0));
  const feedHeight = await feed.evaluate((el) => el.scrollHeight);
  for (let n = 1; n <= 12; n++) await say(`read_file: docs/page-${n}.md`);
  await expect(box).toContainText('read_file: docs/page-12.md');

  // The box kept its height and followed its own newest line; the comments did not move.
  expect((await box.boundingBox())!.height).toBe(height);
  expect(await feed.evaluate((el) => el.scrollTop)).toBe(0);
  expect(await feed.evaluate((el) => el.scrollHeight)).toBe(feedHeight);
  expect(await box.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
  expect(await box.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(2);

  // Scrolled back inside the box, it stays where the reader put it.
  await box.evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
  });
  await say('patch: src/search.py');
  await expect(box).toContainText('patch: src/search.py');
  expect(await box.evaluate((el) => el.scrollTop)).toBe(0);

  // None of it is history, and it ends with the run.
  await tw.getByRole('tab', { name: 'History' }).click();
  await expect(tw.locator('.feed')).not.toContainText('uv run pytest');
  await tw.getByRole('tab', { name: 'All' }).click();
  await api('POST', `/work/${item.id}/finish`, { run_id: taken.lease.run_id, outcome: 'done', reply: 'Rebuilt.' }, agent);
  await expect(tw.locator('[data-chat="agent"]')).toContainText('Rebuilt.');
  await expect(tw.locator('[data-live-activity]')).toHaveCount(0);
});
