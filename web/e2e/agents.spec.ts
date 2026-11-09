// Agents at work: a report puts a live badge on the card and in the task window, and the
// Agents page shows the roster and a feed that follows the stream.
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

test('agent reports: card badge, task window, Agents page roster and live feed', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Agent work', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Queue' });
  const item = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Summarise the router logs',
    assignees: ['agent'],
  });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Summarise the router logs' });
  await expect(card).toBeVisible();

  // A "just started" 5% says nothing: no percent, no bar.
  await api('POST', `/items/${item.id}/report`, agent, { state: 'working', note: 'Starting', progress: 5 });
  const badge = card.locator('.agent-badge');
  await expect(badge).toHaveAttribute('data-agent-state', 'working');
  await expect(badge).not.toContainText('%');
  await expect(badge.locator('.bar')).toHaveCount(0);

  // The agent reports real progress; the card's badge follows live.
  await api('POST', `/items/${item.id}/report`, agent, { state: 'working', note: 'Reading the logs', progress: 30 });
  await expect(badge).toContainText('Agent');
  await expect(badge).toContainText('30%');
  await expect(badge.locator('.bar')).toHaveCount(1);
  // One line, not the List layout's row grid (a global .row class once leaked into the badge).
  expect((await badge.boundingBox())!.height).toBeLessThan(40);

  // The task window shows the full badge with the note.
  await card.getByText('Summarise the router logs').click();
  const tw = page.locator('.tw');
  await expect(tw.locator('.agent-now .agent-badge')).toContainText('Reading the logs');
  await page.keyboard.press('Escape');

  // The Agents page: the agent is on the roster with what it is on, and the feed has the report.
  await page.goto('/#/agents');
  const agentCard = page.locator('.agents-page article.agent', { hasText: /@agent(?![\w-])/ });
  await expect(agentCard).toContainText('Summarise the router logs');
  const feed = page.getByLabel('Agent activity');
  await expect(feed.locator('.ev').first()).toContainText('is working on this');

  // A new report lands in the feed and the roster without a reload.
  await api('POST', `/items/${item.id}/report`, agent, { state: 'blocked', note: 'Need the VPN key' });
  await expect(feed.locator('.ev').first()).toContainText('is blocked on this');
  await expect(agentCard.locator('li.state-blocked')).toBeVisible();

  // Done clears the badge.
  await api('POST', `/items/${item.id}/report`, agent, { state: 'done' });
  await expect(agentCard).not.toContainText('Summarise the router logs');
  await page.goto(`/#/list/${list.id}`);
  await expect(page.locator('.card', { hasText: 'Summarise the router logs' }).locator('.agent-badge')).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});

test('hand a task to an agent: it lands in the agent check-in as assigned and mentioned', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Hand-offs', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Inbox test' });
  const item = await api<{ id: string }>('POST', '/items', alex, { list_id: list.id, title: 'Book the plumber' });
  const start = (await api<{ next_since: string }>('GET', '/checkin?limit=1', agent)).next_since;

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await tw.getByRole('button', { name: /Hand to an agent/ }).click();
  const form = tw.getByRole('form', { name: 'Hand to an agent' });
  await form.getByLabel('Agent').selectOption('agent');
  await form.getByLabel('What should it do?').fill('Find a slot next week.');
  await form.getByRole('button', { name: 'Hand off' }).click();
  await expect(form).toHaveCount(0);
  await expect(tw.locator('.activity .comment', { hasText: 'Find a slot next week.' })).toBeVisible();

  const inbox = await api<{ queue: { id: string }[]; news: { why: string; item_id: string }[] }>(
    'GET',
    `/checkin?since=${start}`,
    agent,
  );
  expect(inbox.queue.map((i) => i.id)).toContain(item.id);
  expect(inbox.news.filter((n) => n.item_id === item.id).map((n) => n.why)).toEqual(['assigned', 'mentioned']);

  // The agent picks it up; the hand-off button gives way to its badge.
  await api('POST', `/items/${item.id}/report`, agent, { state: 'working', note: 'Calling around' });
  await expect(tw.locator('.agent-now .agent-badge')).toContainText('Calling around');
  await expect(tw.getByRole('button', { name: /Hand to an agent/ })).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});

test('Next up on the Agents page: what each agent runs next, live after Run now', async ({ page }) => {
  const { alex, agent } = seed();
  void agent;
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Planned work', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Plan' });
  await api('POST', '/items', alex, { list_id: list.id, title: 'Water the ferns', assignees: ['agent'] });
  const later = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'File the quarterly report',
    assignees: ['agent'],
    due_at: '2031-03-01',
  });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto('/#/agents');
  const next = page.locator('[data-next-up]');
  const agentPlan = next.locator('[data-plan-agent="agent"]');
  await expect(agentPlan).toContainText('Water the ferns');
  await expect(agentPlan.locator('li', { hasText: 'Water the ferns' }).locator('.when')).toHaveText('now');
  await expect(agentPlan).not.toContainText('File the quarterly report'); // years away

  // Run now opens the gate: the task joins the plan without a reload.
  await api('POST', `/items/${later.id}/run`, alex);
  const pressed = agentPlan.locator('li', { hasText: 'File the quarterly report' });
  await expect(pressed).toHaveAttribute('data-plan-reason', 'run_requested');

  // A task opens from the block, and the item view carries the request so the card says so.
  await pressed.getByRole('button', { name: 'File the quarterly report' }).click();
  await expect(page.getByRole('dialog', { name: /File the quarterly report/ })).toBeVisible();
  const view = await api<{ run_requested_at: string | null }>('GET', `/items/${later.id}`, alex);
  expect(view.run_requested_at).not.toBeNull();

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});
