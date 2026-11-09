// Run now: a card with an agent assigned offers it; pressing shows "Requested…" (or "Agent has no runner connected") until the agent
// reports working, then the badge carries an "Open ↗" link to where the agent works.
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

test('four due cards: work on one never makes the others wait their turn', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Pickup queue', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Four due cards' });
  const items = [];
  for (let n = 0; n < 4; n++) {
    items.push(await api<{ id: string }>('POST', '/items', alex, {
      list_id: list.id, title: `Queue card ${n}`, assignees: ['agent'],
      due_at: new Date(Date.now() - 180_000).toISOString(),
    }));
  }
  // A test-only live agent stream, not a runner or an adapter executing a task.
  const controller = new AbortController();
  const stream = await fetch(`${BASE_URL}/api/v1/checkin/stream`, {
    headers: { Authorization: `Bearer ${agent}` }, signal: controller.signal,
  });
  expect(stream.ok).toBe(true);
  try {
    await api('POST', `/items/${items[0].id}/report`, agent, { state: 'working', comment: false });
    await page.clock.install();
    let rosterReads = 0;
    page.on('request', (request) => {
      if (request.url().includes('/agents?kind=all')) rosterReads++;
    });
    await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
    await page.goto(`/#/list/${list.id}`);
    // Agents run several cards at once: work on one card is never a reason to show the others
    // as waiting their turn. Unclaimed due cards say so plainly.
    const waiting = page.locator('.card [data-run-state]');
    await expect(waiting).toHaveCount(3);
    await expect(page.getByText('working on another card')).toHaveCount(0);
    await expect(page.locator('.card .agent-badge[data-agent-state="working"]')).toHaveCount(1);

    const readsBeforeClock = rosterReads;
    await page.clock.fastForward(150_000);
    await expect(waiting).toHaveCount(3);
    expect(rosterReads).toBe(readsBeforeClock); // the clock only ages evidence; no network polling
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.nav')).not.toBeInViewport();
    const fits = await waiting.first().evaluate((el) => {
      const pill = el.getBoundingClientRect();
      const card = el.closest('.card')!.getBoundingClientRect();
      return pill.left >= card.left && pill.right <= card.right && pill.bottom <= card.bottom;
    });
    expect(fits).toBe(true);
    await page.screenshot({ path: test.info().outputPath('waiting-phone.png') });
    await page.setViewportSize({ width: 1280, height: 720 });

    // Unclaimed long enough: a plain warning, whatever the agent does elsewhere.
    await expect(page.locator('.card .run-pill.late')).toHaveCount(3);

    // A card the agent takes loses its pill live, without polling or reloading.
    await api('POST', `/items/${items[1].id}/report`, agent, { state: 'working', comment: false });
    await expect(page.locator(`[data-item="${items[1].id}"] [data-run-state]`)).toHaveCount(0);
    await expect(page.locator(`[data-item="${items[2].id}"] [data-run-state]`)).toBeVisible();
    await page.reload();
    await expect(page.locator(`[data-item="${items[2].id}"] [data-run-state]`)).toBeVisible();
  } finally {
    controller.abort();
    await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
  }
});

test('Run now: requested pill, then the working badge with an Open link', async ({ page }) => {
  const { alex, agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', alex, { name: 'Autorun', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', alex, { section_id: section.id, name: 'Runs' });
  const item = await api<{ id: string }>('POST', '/items', alex, {
    list_id: list.id,
    title: 'Draft the weekly summary',
    assignees: ['agent'],
  });
  await api('POST', '/items', alex, { list_id: list.id, title: 'Water the plants', assignees: ['alex'] });

  await page.addInitScript((t) => localStorage.setItem('slate.token', t), alex);
  await page.goto(`/#/list/${list.id}`);
  const card = page.locator('.card', { hasText: 'Draft the weekly summary' });
  const personal = page.locator('.card', { hasText: 'Water the plants' });
  await expect(card).toBeVisible();

  // Only the agent's card offers it.
  await personal.hover();
  await expect(personal.getByRole('button', { name: 'Run now' })).toHaveCount(0);

  await card.hover();
  await card.getByRole('button', { name: 'Run now' }).click();
  // Nobody holds the agent's stream here, so it says so at once instead of "Requested…".
  await expect(card.locator('[data-run-state="no-runner"]')).toHaveText('No live connection seen for Agent. Check agent status.');

  // The agent's runner takes it (the hint's item_id) and reports where it works.
  const got = await api<{ item: { id: string } | null; lease: { run_id: string } }>('POST', '/work/next', agent, { item_id: item.id });
  expect(got.item?.id).toBe(item.id);
  await api('POST', `/work/${item.id}/heartbeat`, agent, { run_id: got.lease.run_id, url: 'https://example.com/chat/42' });

  const badge = card.locator('.agent-badge');
  await expect(badge).toHaveAttribute('data-agent-state', 'working');
  await expect(card.locator('[data-run-state="no-runner"]')).toHaveCount(0);
  const open = badge.getByRole('link', { name: 'Open ↗' });
  await expect(open).toHaveAttribute('href', 'https://example.com/chat/42');
  await expect(open).toHaveAttribute('target', '_blank');

  // The agent's own session for the card outlives the badge: after a done run the task window
  // still offers "Open Agent's session" from the stored thread.
  await api('POST', `/work/${item.id}/finish`, agent, {
    run_id: got.lease.run_id, outcome: 'done', thread: { ref: 'sess-42', url: 'https://example.com/chat/42' },
  });
  await expect(card.locator('.agent-badge')).toHaveCount(0);
  await card.getByText('Draft the weekly summary').click();
  const tw = page.locator('.tw');
  const sessionLink = tw.locator('[data-thread-links]').getByRole('link', { name: /Open Agent's session/ });
  await expect(sessionLink).toHaveAttribute('href', 'https://example.com/chat/42');
  await page.keyboard.press('Escape');

  await api('PATCH', `/sections/${section.id}`, alex, { archived: true });
});
