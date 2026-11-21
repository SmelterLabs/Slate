// Standing: finishing a run with `status` shows the standing line and whose move on the row
// live without a reload. Needs the server's `status` / `standing` support from s3-server; until
// that merges this test fails at the finish call or standing assertion.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('standing: finish with status shows on the row without a reload', async ({ page }) => {
  const { agent, listId } = seed();
  const created = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Standing live card',
    assignees: ['agent'],
  });

  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  const row = page.locator(`[data-item="${created.id}"]`);
  await expect(row).toBeVisible();

  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: created.id }, agent);
  await api(
    'POST',
    `/work/${created.id}/finish`,
    {
      run_id: taken.lease.run_id,
      outcome: 'review',
      status: 'Check the draft linked above.',
      waiting_on: 'alex',
    },
    agent,
  );

  // Appears live via SSE without reloading the page.
  await expect(row.locator('[data-standing-line]')).toHaveText('Check the draft linked above.');
  await expect(row.locator('[data-standing-move]')).toHaveText('Your move');
});
