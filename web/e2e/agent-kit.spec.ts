// The agent onboarding kit end to end: Add my agent on the Agents page (account, scoped
// token, setup snippets), the new token driving the TypeScript client (clients/typescript)
// through the whole loop against the real server, then usage and revoke on the agent's card.
import { expect, test } from '@playwright/test';
import { Slate, SlateError, type SlateEvent } from '../../clients/typescript/slate';
import { api, BASE_URL, seed, signIn } from './support';

test('connect an agent: token once, setup snippets, usage and revoke', async ({ page, context }) => {
  const { listId } = seed();
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await signIn(page);
  await page.goto('/#/agents');

  await page.getByRole('button', { name: 'Add my agent' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add my agent' });
  await dialog.getByRole('radio', { name: 'Let my agent use Slate', exact: true }).check();
  const form = dialog.getByRole('form', { name: 'Agent setup' });
  await form.getByLabel('Name').fill('Research Bot');
  await expect(form.getByLabel(/^Handle/)).toHaveValue('research-bot');
  await form.getByRole('radio', { name: /Write/ }).check();
  await form.getByRole('checkbox', { name: 'Personal › Home', exact: true }).check();
  await form.getByLabel(/^Token label/).fill('e2e laptop');
  await form.getByRole('button', { name: 'Create token' }).click();

  const token = (await dialog.getByTestId('new-token').textContent())!.trim();
  await expect(dialog).toContainText('does not set up or test a local executor');
  expect(token).toMatch(/^slate_[0-9A-Za-z]{12}_/);
  await expect(dialog.getByRole('tabpanel')).toContainText(`${BASE_URL}/mcp`);
  await expect(dialog.getByRole('tabpanel')).toContainText(token);
  await dialog.getByRole('tab', { name: 'Python' }).click();
  await expect(dialog.getByRole('tabpanel')).toContainText(`Slate("${BASE_URL}", os.environ["SLATE_TOKEN"]`);
  await dialog.getByRole('tab', { name: '.mcp.json' }).click();
  await expect(dialog.getByRole('tabpanel').locator('pre')).not.toContainText(token);
  await dialog.getByRole('button', { name: 'Copy' }).first().click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(token);
  await dialog.getByRole('button', { name: 'Done' }).click();
  await expect(dialog).toBeHidden();

  // The token works: the agent files one item through the client.
  const bot = new Slate(BASE_URL, token, { sessionRef: 'e2e-kit' });
  const botAccount = (await bot.me()).account;
  expect(botAccount.handle).toBe('research-bot');
  await bot.create(listId, 'Filed by the research bot');

  // Its card shows usage and the token; revoking it stops the agent at once.
  const card = page.locator('.agents-page article.agent', { hasText: 'Research Bot' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Manage' }).click();
  const manage = card.getByLabel('Usage and tokens for Research Bot');
  await expect(manage).toContainText(/\d+ changes? in 14 days/);
  const row = manage.locator('li.token', { hasText: 'e2e laptop' });
  await expect(row).toContainText('Write');
  await expect(row).toContainText('used');
  await row.getByRole('button', { name: 'Revoke' }).click();
  await row.getByRole('button', { name: 'Revoke now' }).click();
  await expect(manage).toContainText('No live tokens');
  await expect(bot.me()).rejects.toMatchObject({ status: 401 });
  // Guest onboarding shares Home. Don't leave a new available chat in subsequent guest tests.
  await api('PATCH', `/accounts/${botAccount.id}`, { disabled: true });
  const accounts = await api<{ id: string; disabled_at: string | null }[]>('GET', '/accounts');
  expect(accounts.find((a) => a.id === botAccount.id)?.disabled_at).toBeTruthy();
});

test('the TypeScript client runs the loop against a real server', async () => {
  const { alex, agent: agentToken, listId } = seed();
  const agent = new Slate(BASE_URL, agentToken, { sessionRef: 'e2e-loop' });
  const me = await agent.me();
  await agent.checkin(); // start the cursor from now
  await agent.checkin();
  await agent.checkin(); // same cursor, nothing changed
  expect(agent.lastStatus).toBe(304);

  // Follow the stream while Alex hands the agent a task.
  const cursor = (await agent.events(undefined, { tail: true, limit: 1 })).next_since ?? undefined;
  const abort = new AbortController();
  const seen: SlateEvent[] = [];
  const following = (async () => {
    for await (const ev of agent.stream(cursor, { type: 'item.assigned' }, abort.signal)) {
      seen.push(ev);
      if (seen.length) return;
    }
  })().catch(() => {});
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Agent loop from TypeScript',
    assignees: ['agent'],
  });
  await expect.poll(() => seen.length, { timeout: 2000 }).toBe(1);
  expect(seen[0].item_id).toBe(item.id);
  abort.abort();
  await following;

  const check = await agent.checkin();
  expect(check.queue.map((i) => i.id)).toContain(item.id);
  expect(check.news.some((n) => n.item_id === item.id && n.why === 'assigned')).toBe(true);

  await agent.claim(item.id, 'Picking this up');
  // Another account cannot take it now.
  const rival = new Slate(BASE_URL, alex);
  const err = await rival.claim(item.id).catch((e) => e);
  expect(err).toBeInstanceOf(SlateError);
  expect(err.status).toBe(409);

  await agent.progress(item.id, 'Halfway', 50);
  await agent.comment(item.id, 'Result: https://example.com/pr/2');
  const done = await agent.done(item.id, 'Shipped');
  expect((done.status as { kind: string }).kind).toBe('done');

  const out = await agent.batch([
    { ref: 't', tool: 'item', action: 'create', params: { list_id: listId, title: 'Batch one' } },
    { tool: 'comment', action: 'add', params: { id: '$t.id', body_md: 'from a batch' } },
  ]);
  expect(out.results).toHaveLength(2);

  const trail = await agent.events(undefined, { actor: me.account.handle, tail: true, limit: 20 });
  expect(trail.events.filter((e) => e.item_id === item.id).every((e) => e.session_ref === 'e2e-loop')).toBe(true);
});

test('the TypeScript client runs the work loop: next, heartbeat, finish, agent stream', async () => {
  const { alex, agent: agentToken, listId } = seed();
  const agent = new Slate(BASE_URL, agentToken, { sessionRef: 'e2e-work' });
  // Only this test's tasks (a tag of their own), so the seed's tasks for the agent stay as they are.
  const scope = { tag: 'work-loop-e2e' };

  const abort = new AbortController();
  const hints: string[] = [];
  const following = (async () => {
    for await (const m of agent.agentStream(undefined, { signal: abort.signal })) {
      if (m.event === 'work') hints.push(String(m.data.item_id));
    }
  })().catch(() => {});
  await new Promise((r) => setTimeout(r, 300)); // let the stream go live
  const first = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Work loop one',
    assignees: ['agent'],
    tags: [scope.tag],
    priority: 'urgent',
  });
  const second = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Work loop two',
    assignees: ['agent'],
    tags: [scope.tag],
    priority: 'urgent',
  });
  await expect.poll(() => hints.includes(first.id), { timeout: 2000 }).toBe(true);
  abort.abort();
  await following;

  const task = await agent.next({ ...scope, lease_seconds: 600 });
  expect(task.item?.id).toBe(first.id);
  expect(task.why).toBe('assigned');
  const run_id = task.lease?.run_id;
  expect(run_id).toBeTruthy();
  const lease = await agent.heartbeat(first.id, { run_id: run_id!, progress: 50, note: 'Halfway' });
  expect(lease.item_id).toBe(first.id);
  expect((await agent.held()).map((l) => l.item_id)).toEqual([first.id]);

  // Fenced settlement is acknowledged before taking the next execution.
  const out = await agent.finish(first.id, 'done', 'Shipped', { run_id: run_id! });
  expect((out.item.status as { kind: string }).kind).toBe('done');
  const next = await agent.next(scope);
  expect(next.item?.id).toBe(second.id);
  const rival = new Slate(BASE_URL, alex);
  const err = await rival.heartbeat(second.id).catch((e) => e);
  expect(err).toBeInstanceOf(SlateError);
  expect(err.status).toBe(409);
  expect(next.lease?.run_id).toBeTruthy();
  await agent.finish(second.id, 'release', 'Not today', { run_id: next.lease!.run_id! });
});
