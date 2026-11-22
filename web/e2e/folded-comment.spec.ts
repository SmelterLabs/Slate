import { expect, test } from '@playwright/test';
import { api, screenshots, seed, signIn } from './support';

test('an older agent report stays a comment card in All activity', async ({ page }) => {
  const { agent } = seed();
  const { account } = await api<{ account: { display_name: string; handle: string } }>('GET', '/me', undefined, agent);
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Stockroom', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Stock checks' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Count the fasteners', assignees: [account.handle] });
  const first = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, {
    run_id: first.lease.run_id,
    outcome: 'answered',
    reply: '**Stock report**\n\nTwelve boxes on the top shelf.\n\nFour boxes near the loading door.\n\nThe next delivery is Friday.\n\nReorder after the next count.',
  }, agent);
  await api('POST', `/items/${item.id}/comments`, { body_md: 'Are those boxes sealed?', assignee: account.handle });
  const next = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, { run_id: next.lease.run_id, outcome: 'answered', reply: 'All sixteen are sealed.' }, agent);

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const activity = page.locator('.tw .activity');
  await activity.getByRole('tab', { name: 'All', exact: true }).click();
  const report = activity.locator('article.comment', { hasText: 'Stock report' });
  await expect(report).toHaveAttribute('data-chat-collapsed', '');
  await expect(report.locator('.avatar.agent')).toBeVisible();
  await expect(report.locator('.who strong')).toHaveText(account.display_name);
  await expect(report.locator('time')).toHaveAttribute('datetime', /.+/);
  await expect(report.locator('.md strong')).toHaveText('Stock report');

  // In the mixed feed there is history before and after the report.
  await expect(report.locator('xpath=preceding-sibling::div[contains(@class, "ev")]').first()).toBeAttached();
  await expect(report.locator('xpath=following-sibling::div[contains(@class, "ev")]').first()).toBeAttached();
  const answer = activity.locator('article.comment', { hasText: 'All sixteen are sealed.' });
  for (const property of ['background-color', 'border-left-color', 'border-left-width']) {
    await expect(report).toHaveCSS(property, await answer.evaluate((el, key) => getComputedStyle(el).getPropertyValue(key), property));
  }

  const showAll = report.getByRole('button', { name: 'Show all' });
  await expect(showAll).toBeVisible();
  await expect(showAll).toHaveAttribute('aria-expanded', 'false');
  const preview = report.locator('.comment-content');
  await expect(preview).toHaveCSS('overflow-y', 'hidden');
  expect(await preview.evaluate((el) => el.clientHeight)).toBeLessThan(await preview.evaluate((el) => el.scrollHeight));

  const header = report.locator('.who');
  await header.scrollIntoViewIfNeeded();
  const box = (await header.boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  const showLess = report.getByRole('button', { name: 'Show less' });
  await expect(showLess).toHaveAttribute('aria-expanded', 'true');
  await expect(report.getByText('Reorder after the next count.')).toBeVisible();
  await showLess.focus();
  await page.keyboard.press('Enter');
  await expect(showAll).toBeFocused();
  await expect(showAll).toHaveAttribute('aria-expanded', 'false');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('an answered Ask keeps its questions visible when its report folds', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const { agent } = seed();
  const { account } = await api<{ account: { handle: string } }>('GET', '/me', undefined, agent);
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Stock review', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Supply orders' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id, title: 'Review the stock order', assignees: [account.handle],
  });
  const questions = ['Keep the extra boxes?', 'Who should approve the order?', 'Ship this Friday?'];
  const end = 'The full count is in the attached inventory.';
  const run = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api('POST', `/work/${item.id}/finish`, {
    run_id: run.lease.run_id, outcome: 'ask', waiting_on: 'alex',
    questions: questions.map((question) => ({ question })),
    reply: ['**Stock count complete**', 'Twelve boxes on the top shelf.', 'Four by the loading door.',
      'Space is reserved for the next shipment.', end].join('\n\n'),
  }, agent);
  const comments = `/items/${item.id}/comments`;
  const [ask] = await api<{ id: string }[]>('GET', comments);
  const exchanges = [
    ['Keep them, I will approve, and ship on Friday.', 'I recorded all three decisions.'],
    ['Did you book the pickup?', 'The pickup is booked.'],
    ['Where is the receipt?', 'The receipt is with the order.'],
  ];
  for (const [index, [message, reply]] of exchanges.entries()) {
    await api('POST', comments, { body_md: message, assignee: index === 0 ? undefined : account.handle });
    const next = await api<{ lease: { run_id: string }; reply: { body_md: string } }>(
      'POST', '/work/next', { item_id: item.id }, agent,
    );
    expect(next.reply.body_md).toBe(message);
    await api('POST', `/work/${item.id}/finish`, {
      run_id: next.lease.run_id, outcome: 'answered', reply,
    }, agent);
  }

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const article = page.locator(`.tw article[data-comment="${ask.id}"]`);
  await expect(article).toHaveAttribute('data-chat-collapsed', '');
  const block = article.locator(':scope > [data-ask-box]:first-child');
  await expect(block).toHaveAttribute('data-ask-state', 'answered');
  await expect(block.locator('[data-ask-question]')).toHaveText(questions);
  await expect(article.locator('.comment-content[inert]')).toBeAttached();
  expect(await block.evaluate((el) => el.closest('[inert]') === null)).toBe(true);
  await article.scrollIntoViewIfNeeded();
  await expect(block).toBeInViewport();
  await screenshots('SLATE_QUESTIONS_SHOTS')(page, 'desktop-folded-ask');

  await article.getByRole('button', { name: 'Show all' }).click();
  await expect(article.getByRole('button', { name: 'Show less' })).toBeVisible();
  await expect(article.locator('.comment-content[inert]')).toHaveCount(0);
  await expect(article.getByText(end)).toBeVisible();
  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
