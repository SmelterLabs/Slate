// Structured ask: an agent stops a card on Alex with a one-line question and tap answers.
// In Needs you the question is the headline; tapping an option posts it as Alex's answer and
// the row leaves. Needs the server's `ask` finish outcome; until that merges this test fails
// at the finish call.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('ask: tapping an answer in Needs you answers the agent', async ({ page }) => {
  const { agent, listId } = seed();
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Ask-answer card',
    assignees: ['agent'],
  });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  await api(
    'POST',
    `/work/${item.id}/finish`,
    {
      run_id: taken.lease.run_id,
      outcome: 'ask',
      question: 'Start the import now?',
      options: ['Yes', 'No', 'Not yet'],
      waiting_on: 'alex',
      reply: 'The file is ready to import.',
    },
    agent,
  );
  const asked = await api<{ ask: { comment_id: string; question: string; options: string[] } | null }>(
    'GET',
    `/items/${item.id}`,
  );
  expect(asked.ask?.question).toBe('Start the import now?');
  expect(asked.ask?.options).toEqual(['Yes', 'No', 'Not yet']);

  await signIn(page);
  await page.goto('/#/home');
  const panel = page.locator('section.needs');
  const row = panel.locator(`[data-item="${item.id}"]`);
  await expect(row).toBeVisible();
  await expect(row.locator('[data-ask-question]')).toHaveText('Start the import now?');
  await expect(row).toContainText('Ask-answer card');

  await row.locator('[data-answer-option="Yes"]').click();
  await expect(panel.locator(`[data-item="${item.id}"]`)).toHaveCount(0);
  await expect
    .poll(async () => (await api<{ ask: unknown }>('GET', `/items/${item.id}`)).ask ?? null)
    .toBeNull();
});
