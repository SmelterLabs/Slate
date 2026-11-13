// Ask: a comment addressed to someone, open until explicitly resolved. Ask the agent from the composer (it
// reaches its check-in as `asked`); the agent asks Alex back (a "?" badge on the card), and
// Alex's reply leaves it open until Resolve.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('Ask Agent, Agent asks back, only explicit Resolve closes the human Ask', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Asks', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Questions' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Pick a caterer' });
  const since = (await api<{ next_since: string }>('GET', '/checkin?limit=1', undefined, agent)).next_since;

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await tw.getByLabel('Add comment').fill('Which caterer has the best price?');
  await tw.getByLabel('Ask', { exact: true }).selectOption('agent');
  await tw.getByRole('button', { name: /Ask Agent/ }).click();
  // A question to an agent stays visibly open as well as showing dispatch progress.
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(1);
  await expect(tw.locator('[data-agent-pending]')).toContainText('Agent is typing');

  const news = await api<{ news: { why: string }[] }>('GET', `/checkin?since=${since}`, undefined, agent);
  expect(news.news.map((n) => n.why)).toContain('asked');
  await page.keyboard.press('Escape');

  // Asked-only finish explicitly answers its taken Ask, then it asks Alex back.
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, { run_id: taken.lease.run_id, outcome: 'done', reply: 'Lakeside, $20 a head.' }, agent);
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Book it for Friday?', assignee: 'alex' }, agent);
  const card = page.locator('.card', { hasText: 'Pick a caterer' });
  await expect(card.locator('[data-asks]')).toHaveText(/1/);

  // Alex's reply is not resolution; the explicit Resolve button closes it.
  await card.getByText('Pick a caterer').click();
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(1);
  await tw.getByLabel('Add comment').fill('Yes, book it.');
  // The picker starts on the agent (asked last on this card); a plain comment is one pick away.
  await expect(tw.getByLabel('Ask', { exact: true })).toHaveValue('agent');
  await tw.getByLabel('Ask', { exact: true }).selectOption('');
  await tw.getByRole('button', { name: /^Comment$/ }).click();
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(1);
  await tw.locator('article', { hasText: 'Book it for Friday?' }).getByRole('button', { name: 'Resolve', exact: true }).click();
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(0);
  // The first exchange (Alex's Ask and the agent's answer) is older than the newest: collapsed.
  await expect(tw.locator('[data-ask="resolved"]')).toHaveCount(1);
  await tw.locator('[data-chat-collapsed]').first().click();
  await expect(tw.locator('[data-ask="resolved"]')).toHaveCount(2);
  await page.keyboard.press('Escape');
  await expect(card.locator('[data-asks]')).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

// A comment that @mentions only a person is a plain comment, even on a
// card whose Ask picker starts on the agent; it must not go out as an Ask that wakes the agent.
test('@mentioning a person turns the default agent Ask into a plain comment', async ({ page }) => {
  const { alex } = seed();
  const handle = `member-${Date.now().toString(36)}`;
  await api('POST', '/accounts', { handle, display_name: 'Member', kind: 'person' });
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Mentions', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Talk' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Audit', assignees: ['agent'] });

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  const picker = tw.getByLabel('Ask', { exact: true });
  await expect(picker).toHaveValue('agent');
  await tw.getByLabel('Add comment').fill(`@${handle} should I consider it incomplete?`);
  await expect(picker).toHaveValue('');
  await tw.getByLabel('Add comment').press('Enter');
  await expect(tw.locator('article', { hasText: 'should I consider it incomplete?' })).toHaveCount(1);
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(0);
  // Back to no mention: the picker follows the card again.
  await expect(picker).toHaveValue('agent');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
