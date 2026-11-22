// Questions for a person are structured data. An agent that finished its work and has three
// decisions for Alex ends with an ask of three questions under a long report: the comment opens
// with the questions, and the task window's banner takes all three answers and sends them as one
// comment. A review for Alex says what to check, not the reply's first line.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

const REPORT = [
  'Weekly run finished: 14 invoices matched, 2 left over.',
  ...Array.from({ length: 12 }, (_, i) => `- Line ${i + 1} of the weekly report: matched and filed.`),
  '',
  'Totals are in the March column.',
].join('\n');

test.fixme('an ask with three questions leads its comment and is answered in one go', async ({ page }) => {
  // fixme: needs the server slice (`questions` on a finish); this worktree's API refuses it.
  const { agent, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', { list_id: listId, title: 'Weekly invoice run', assignees: ['agent'] });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api(
    'POST',
    `/work/${item.id}/finish`,
    {
      run_id: taken.lease.run_id,
      outcome: 'ask',
      waiting_on: 'alex',
      reply: REPORT,
      questions: [
        { question: 'Send the two left-over invoices to the bank?', options: ['Yes', 'No'] },
        { question: 'Which account pays the March fees?', options: ['Main', 'Savings'] },
        { question: 'Who should sign off the totals?' },
      ],
    },
    agent,
  );

  await signIn(page);
  await page.goto(`/#/list/${listId}?task=${item.id}`);
  const tw = page.locator('.tw');

  // The comment opens with the questions, numbered, above the report.
  const comment = tw.locator('[data-chat="agent"]').filter({ hasText: 'Weekly run finished' });
  const block = comment.locator('[data-ask-box]');
  await expect(block).toContainText('Questions for you');
  // The thread opens on the questions, not on the end of the long report under them.
  await expect(block).toBeInViewport();
  await expect(block.locator('[data-ask-question]')).toHaveText([
    'Send the two left-over invoices to the bank?',
    'Which account pays the March fees?',
    'Who should sign off the totals?',
  ]);
  expect(await comment.evaluate((el) => el.firstElementChild?.hasAttribute('data-ask-box'))).toBe(true);
  await expect(comment.locator('[data-answer-option]')).toHaveCount(0);

  // The banner takes every answer, then sends them as one comment.
  const banner = tw.locator('[data-banner="waiting"]');
  await expect(banner.locator('.wq-heading')).toHaveText('Agent needs 3 answers from you');
  const send = banner.locator('[data-send-answers]');
  await expect(send).toBeDisabled();
  await banner.locator('[data-pick-option="Yes"]').click();
  await banner.locator('[data-pick-option="Savings"]').click();
  await expect(send).toBeDisabled();
  await banner.getByLabel('Answer to question 3').fill('Alex');
  await send.click();

  await expect(banner).toHaveCount(0);
  await expect(block).toContainText('Answered');
  const comments = await api<{ author_id: string; body_md: string }[]>('GET', `/items/${item.id}/comments`);
  expect(comments.at(-1)?.body_md).toBe(
    '1. Send the two left-over invoices to the bank? **Yes**\n' +
      '2. Which account pays the March fees? **Savings**\n' +
      '3. Who should sign off the totals? **Alex**',
  );
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

  await banner.locator('[data-mark-reviewed]').click();
  await expect(banner).toHaveCount(0);
  await expect.poll(async () => (await api<{ agent: unknown }>('GET', `/items/${item.id}`)).agent ?? null).toBeNull();
});
