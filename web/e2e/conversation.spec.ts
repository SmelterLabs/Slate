// The card is the conversation: while the agent works, a live
// bubble shows its latest note; Run now says who has it; its question lands as its comment and
// the badge reads "Waiting on Alex"; Alex's answer in the thread is addressed to the agent, and once
// the agent verifies, the first exchange collapses to one line.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('a run reads as a conversation on the card', async ({ page }) => {
  const { alex, agent } = seed();
  const me = await api<{ account: { handle: string } }>('GET', '/me');
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Talk', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Runs' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Re-authenticate the build server login',
    assignees: ['agent'],
  });

  await signIn(page);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Re-authenticate the build server login' });
  await expect(card).toBeVisible();

  // The agent's runner takes it and reports what it is doing.
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/heartbeat`, {
    run_id: taken.lease.run_id,
    note: 'Running the login on the build server',
    thread: { ref: 'sess_1', url: 'https://example.com/chat/1' },
  }, agent);
  await expect(card.locator('.agent-badge')).toHaveAttribute('data-agent-state', 'working');
  await expect(card.locator('[data-agent-age]')).toBeVisible();

  // Run now while the agent works: the board says who has it.
  await card.hover();
  await card.getByRole('button', { name: 'Run now' }).click();
  await expect(page.locator('.toast')).toContainText('Agent already has it');

  // The task window: the live bubble with the latest note.
  await card.getByText('Re-authenticate the build server login').click();
  const tw = page.locator('.tw');
  const bubble = tw.locator('[data-live-bubble]');
  await expect(bubble).toContainText('Agent is working');
  await expect(bubble).toContainText('Running the login on the build server');
  await expect(bubble.locator('[data-live-age]')).toContainText(/updated \d+ s ago/);

  // The agent asks: its reply is its comment, the badge says Waiting on Alex, the bubble goes.
  await api('POST', `/work/${item.id}/finish`, {
    run_id: taken.lease.run_id,
    outcome: 'blocked',
    waiting_on: me.account.handle,
    reply: 'I started the login. Sign in here: https://login.example/abc and tell me when done.',
  }, agent);
  await expect(bubble).toHaveCount(0);
  await expect(tw.locator('.agent-badge')).toHaveAttribute('data-agent-label', 'Waiting on Alex');
  await expect(tw.locator('[data-chat="agent"]')).toContainText('Sign in here');

  // Alex answers in the thread: addressed to the agent, the badge says Answered.
  // The Ask picker starts on the agent (the card's agent) and never lists Alex himself.
  const askPicker = tw.getByLabel('Ask', { exact: true });
  await expect(askPicker).toHaveValue('agent');
  await expect(askPicker.locator(`option[value="${me.account.handle}"]`)).toHaveCount(0);
  await tw.getByLabel('Add comment').fill('done');
  await tw.getByRole('button', { name: /Ask Agent/ }).click();
  await expect(tw.locator('[data-chat="person"] [data-ask="open"]')).toHaveCount(1);
  await expect(tw.locator('.agent-badge')).toHaveAttribute('data-agent-label', 'Answered');
  // Dots under the thread until the agent answers: sent, then working once it takes it.
  await expect(tw.locator('[data-agent-pending="sent"]')).toContainText('Agent is typing');

  // The agent takes the answer as its next turn, verifies and closes; the first exchange collapses.
  const next = await api<{ lease: { run_id: string }; reply: { body_md: string } | null; thread: { ref: string } | null }>(
    'POST',
    '/work/next',
    { item_id: item.id },
    agent,
  );
  expect(next.reply?.body_md).toBe('done');
  expect(next.thread?.ref).toBe('sess_1');
  await expect(tw.locator('[data-agent-pending="working"]')).toContainText('Agent is typing');
  await api('POST', `/work/${item.id}/finish`, { run_id: next.lease.run_id, outcome: 'done', reply: 'Verified: signed in.' }, agent);
  await expect(tw.locator('[data-chat="agent"]')).toContainText('Verified: signed in.');
  await expect(tw.locator('[data-agent-pending]')).toHaveCount(0);
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(1);
  expect((await api<{ item: unknown }>('POST', '/work/next', { item_id: item.id }, agent)).item).toBeNull();
  // Reload preserves the separate open-Ask and settled-dispatch states.
  await page.reload();
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(1);
  await expect(tw.locator('[data-agent-pending]')).toHaveCount(0);
  const folded = tw.locator('[data-chat-collapsed]');
  await expect(folded).toHaveCount(1);
  await expect(folded).toContainText('I started the login.');
  await folded.click();
  await expect(tw.locator('[data-chat="agent"]')).toHaveCount(2);
  await page.keyboard.press('Escape');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('a newer Ask during a run remains open and dispatches exactly one later turn', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'New Ask', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Turns' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Answer in order', assignees: ['agent'] });
  await api('POST', `/items/${item.id}/comments`, { body_md: 'First question', assignee: 'agent' });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  const newer = await api<{ id: string }>('POST', `/items/${item.id}/comments`, { body_md: 'Newer question', assignee: 'agent' });
  // More work arrived during this turn: keep the task open for review, not completed.
  // A take on a completed task is intentionally an asked-only answer (and resolves that Ask).
  await api('POST', `/work/${item.id}/finish`, { run_id: taken.lease.run_id, outcome: 'review', reply: 'First answer' }, agent);
  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(2);
  await expect(tw.locator('[data-agent-pending="sent"]')).toBeVisible();
  const turn = await api<{ lease: { run_id: string }; asks: { id: string }[] }>('POST', '/work/next', { item_id: item.id }, agent);
  expect(turn.asks.map((a) => a.id)).toEqual([newer.id]);
  await api('POST', `/work/${item.id}/finish`, { run_id: turn.lease.run_id, outcome: 'done', reply: 'Newer answer' }, agent);
  await expect(tw.locator('[data-agent-pending]')).toHaveCount(0);
  await expect(tw.locator('[data-ask="open"]')).toHaveCount(2);
  expect((await api<{ item: unknown }>('POST', '/work/next', { item_id: item.id }, agent)).item).toBeNull();
  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('Mark reviewed clears an answered card', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Review', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Answers' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'When does the library close?',
    assignees: ['agent'],
  });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, { run_id: taken.lease.run_id, outcome: 'review', reply: 'At six on Friday.' }, agent);

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await expect(tw.locator('.agent-badge')).toHaveAttribute('data-agent-label', 'Ready for review');
  await tw.locator('[data-mark-reviewed]').click();
  await expect(tw.locator('.agent-badge')).toHaveCount(0);
  await expect(tw.locator('[data-chat="agent"]')).toContainText('At six on Friday.');
  await page.keyboard.press('Escape');
  await expect(page.locator('.card', { hasText: 'When does the library close?' }).locator('.agent-badge')).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('Clear takes a blocked badge off the card', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Blocked', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Stuck' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Renew the domain', assignees: ['agent'] });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, { run_id: taken.lease.run_id, outcome: 'blocked', reply: 'I need the registrar login.' }, agent);

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  await expect(tw.locator('.agent-badge')).toHaveAttribute('data-agent-state', 'blocked');
  // Blocked and waiting on nobody: the badge itself offers the next run.
  await expect(tw.locator('.agent-badge [data-blocked-run] .run-now')).toBeVisible();
  await tw.locator('[data-clear-blocked]').click();
  await expect(tw.locator('.agent-badge')).toHaveCount(0);
  await expect(tw.locator('[data-chat="agent"]')).toContainText('I need the registrar login.');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
