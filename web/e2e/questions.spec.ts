// Questions for a person are structured data. An agent that finished its work and has three
// decisions for Alex ends with an ask of three questions under a long report: the comment opens
// with the questions, and the task window's banner takes all three answers and sends them as one
// comment. A review for Alex says what to check, not the reply's first line.
import { expect, test } from '@playwright/test';
import type { CommentAsk, ItemAsk } from '../src/lib/types';
import { api, screenshots, seed, signIn } from './support';

const REPORT = [
  'Weekly run finished: 14 invoices matched, 2 left over.',
  ...Array.from({ length: 12 }, (_, i) => `- Line ${i + 1} of the weekly report: matched and filed.`),
  '',
  'Totals are in the March column.',
].join('\n');

test('a long single-question Ask opens at the top of its comment and in view', async ({ page }) => {
  const { agent, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', { list_id: listId, title: 'Review the invoice draft', assignees: ['agent'] });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  const question = 'Send the draft invoice?';
  await api('POST', `/work/${item.id}/finish`, {
    run_id: taken.lease.run_id, outcome: 'ask', waiting_on: 'alex',
    question, options: ['Yes', 'No'], reply: REPORT.repeat(4),
  }, agent);

  await signIn(page);
  await page.goto(`/#/list/${listId}?task=${item.id}`);
  const comment = page.locator('.tw article[data-chat="agent"]').filter({ hasText: 'Weekly run finished' });
  const block = comment.locator(':scope > [data-ask-box]:first-child');
  await expect(block.locator('[data-ask-question]')).toHaveText(question);
  await expect(block).toBeInViewport();
});

const shot = screenshots('SLATE_QUESTIONS_SHOTS');

for (const view of [
  { name: 'desktop', width: 1440, height: 1000, section: 'Invoice review' },
  { name: 'mobile', width: 390, height: 844, section: 'March invoice review' },
]) {
  test(`a three-question ask arrives live and receives one reply at ${view.width}px`, async ({ page }) => {
    await page.setViewportSize({ width: view.width, height: view.height });
    const { agent } = seed();
    const section = await api<{ id: string }>('POST', '/sections', { name: view.section, audience: 'ai' });
    const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Invoices' });
    const item = await api<{ id: string }>('POST', '/items', {
      list_id: list.id, title: 'Weekly invoice run', assignees: ['agent'],
    });
    const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
    const questions = [
      { question: 'Send the two left-over invoices to the bank?', options: ['Yes', 'No'] },
      { question: 'Which account pays the March fees?', options: [] },
      { question: 'Who should sign off the totals?', options: [] },
    ];
    const title = 'Weekly invoice run';
    const path = `/items/${item.id}`;

    await signIn(page);
    await page.goto(`/#/list/${list.id}?task=${item.id}`);
    const tw = page.locator('.tw');
    const banner = tw.locator('[data-banner="waiting"]');
    await expect(tw.getByLabel('Close', { exact: true })).toBeVisible();
    await expect(page.locator('.conn-live').first()).toBeAttached();
    await expect(banner).toHaveCount(0);
    await api('POST', `/work/${item.id}/finish`, {
      run_id: taken.lease.run_id, outcome: 'ask', waiting_on: 'alex', reply: REPORT, questions,
    }, agent);
    await expect(banner.locator('.wq-heading')).toHaveText('Agent needs 3 answers from you');
    const [posted] = await api<{ id: string; author_id: string; created_at: string; ask: CommentAsk }[]>('GET', `${path}/comments`);
    const headline = `3 questions: ${questions[0].question}`;
    expect(posted.ask).toEqual({ question: headline, options: [], questions });
    expect((await api<{ ask: ItemAsk }>('GET', path)).ask).toEqual({
      ...posted.ask, comment_id: posted.id, by_id: posted.author_id, at: posted.created_at,
    });

    const comment = tw.locator('[data-chat="agent"]').filter({ hasText: 'Weekly run finished' });
    const block = comment.locator(':scope > [data-ask-box]:first-child');
    await expect(block).toContainText('Questions for you');
    await expect(block.locator('[data-ask-question]')).toHaveText(questions.map((q) => q.question));
    await expect(comment.locator('[data-answer-option]')).toHaveCount(0);
    await shot(page, `${view.name}-task-before`);
    await block.scrollIntoViewIfNeeded();
    await shot(page, `${view.name}-activity-questions`);

    await tw.getByLabel('Close', { exact: true }).click();
    const card = page.locator('.card', { hasText: title });
    await expect(card.locator('[data-asks]')).toHaveText('? 3');
    await shot(page, `${view.name}-board`);
    await expect(card.locator('[data-standing-line]')).toHaveText(questions[0].question);
    if (view.name === 'mobile') {
      await page.getByRole('link', { name: /^Needs you:/ }).click();
    } else {
      await page.locator('.nb-pill').click();
    }
    const panel = view.name === 'mobile' ? 'section.needs' : '.nb-pop';
    const row = page.locator(`${panel} [data-item="${item.id}"]`);
    await expect(row).toContainText(headline);
    await expect(row).toContainText('Answer');
    await expect(row.locator('[data-answer-option]')).toHaveCount(0);
    await shot(page, `${view.name}-needs-you`);
    await row.locator('.nd-main').click();

    const send = banner.locator('[data-send-answers]');
    await expect(send).toBeDisabled();
    await banner.locator('[data-pick-option="Yes"]').click();
    await banner.getByLabel('Answer to question 2').fill('Savings');
    await expect(send).toBeDisabled();
    await banner.getByLabel('Answer to question 3').fill('Alex');
    await send.click();

    await expect(banner).toHaveCount(0);
    await expect(block).toHaveAttribute('data-ask-state', 'answered');
    const answer = '1. Send the two left-over invoices to the bank? **Yes**\n'
      + '2. Which account pays the March fees? **Savings**\n'
      + '3. Who should sign off the totals? **Alex**';
    const comments = await api<{ body_md: string; resolved_at: string | null }[]>('GET', `${path}/comments`);
    expect(comments.map((c) => c.body_md)).toEqual([REPORT, answer]);
    expect(comments[0].resolved_at).toBeTruthy();
    const updated = await api<{ waiting_on_account_id: string | null; ask: unknown }>('GET', path);
    expect(updated.waiting_on_account_id).toBeNull();
    expect(updated.ask).toBeNull();
    await tw.getByText('Who should sign off the totals?', { exact: false }).last().scrollIntoViewIfNeeded();
    await shot(page, `${view.name}-task-after`);
    const turn = await api<{ item: { id: string }; reply: { body_md: string } }>(
      'POST', '/work/next', { item_id: item.id }, agent,
    );
    expect(turn.item.id).toBe(item.id);
    expect(turn.reply.body_md).toBe(answer);
    await api('PATCH', `/sections/${section.id}`, { archived: true });
  });
}

test('an agent comment turns its two trailing ask lines into one structured Ask', async ({ page }) => {
  const { agent, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: listId, title: 'Close the stock check', assignees: ['agent'],
  });
  const body = 'The stock check is complete.';
  const questions = [
    { question: 'Keep the extra boxes?', options: ['Keep', 'Return'] },
    { question: 'Who picks them up?', options: [] },
  ];
  const comment = await api<{ id: string; body_md: string; ask: { questions: typeof questions } }>(
    'POST', `/items/${item.id}/comments`, {
      body_md: `${body}\n\nSLATE: ask alex: Keep the extra boxes? [Keep | Return]\nSLATE: ask alex: Who picks them up?`,
    }, agent,
  );
  expect(comment.body_md).toBe(body);
  expect(comment.ask.questions).toEqual(questions);
  await signIn(page);
  await page.goto(`/#/list/${listId}?task=${item.id}`);
  const article = page.locator(`.tw article[data-comment="${comment.id}"]`);
  await expect(article.locator(':scope > [data-ask-box]:first-child [data-ask-question]'))
    .toHaveText(questions.map((q) => q.question));
  await expect(article.locator('.md')).toHaveText(body);
  await expect(page.locator('[data-banner="waiting"] .wq-heading')).toHaveText('Agent needs 2 answers from you');
});

test('a review for Alex says what to check, and Mark reviewed clears it', async ({ page }) => {
  const { agent, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', { list_id: listId, title: 'Monthly totals', assignees: ['agent'] });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api(
    'POST',
    `/work/${item.id}/finish`,
    { run_id: taken.lease.run_id, outcome: 'review', status: 'Check the totals in the March column', reply: REPORT },
    agent,
  );

  await signIn(page);
  await page.goto(`/#/list/${listId}?task=${item.id}`);
  const tw = page.locator('.tw');
  const banner = tw.locator('[data-banner="review"]');
  await expect(banner.locator('[data-review-check]')).toHaveText('Check the totals in the March column');
  await expect(banner).toContainText('Agent finished and asks you to check');
  await expect(banner).not.toContainText('Weekly run finished');
  await expect(tw.locator('.agent-badge')).toHaveCount(0);
  await expect(tw.locator('[data-standing]')).toHaveCount(0);
  const heading = await banner.locator('[data-review-check]').boundingBox();
  const metadata = await banner.locator('.wq-sub').boundingBox();
  const action = await banner.locator('[data-mark-reviewed]').boundingBox();
  expect(heading!.y + heading!.height).toBeLessThanOrEqual(metadata!.y);
  expect(metadata!.y + metadata!.height).toBeLessThanOrEqual(action!.y);

  await banner.locator('[data-mark-reviewed]').click();
  await expect(banner).toHaveCount(0);
  await expect.poll(async () => (await api<{ agent: unknown }>('GET', `/items/${item.id}`)).agent ?? null).toBeNull();
});
