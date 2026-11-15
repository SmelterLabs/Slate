// After a run: the agent's answer carries a small "N steps" button that opens what the run did,
// and the All view keeps the run's history to what a person needs (History shows every line).
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('an agent answer opens its steps, and the run reads quietly in All', async ({ page }) => {
  const { agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Run log', audience: 'ai' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Runs' });
  const item = await api<{ id: string }>('POST', '/items', {
    list_id: list.id,
    title: 'Check the backups',
    assignees: ['agent'],
  });
  const taken = await api<{ lease: { run_id: string } }>('POST', '/work/next', { item_id: item.id }, agent);
  const run = { run_id: taken.lease.run_id };
  await api('POST', `/work/${item.id}/heartbeat`, { ...run, note: 'Looking at the backup folder' }, agent);
  await api('POST', `/work/${item.id}/activity`, { ...run, lines: ['terminal: ls /backups', 'Thinking: the newest is from today'] }, agent);
  await api('POST', `/work/${item.id}/activity`, { ...run, lines: ['read_file: backup.log'] }, agent);
  await api('POST', `/work/${item.id}/finish`, { ...run, outcome: 'done', reply: 'The backups are current.' }, agent);

  await signIn(page);
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  const tw = page.locator('.tw');
  const answer = tw.locator('[data-chat="agent"]', { hasText: 'The backups are current.' });
  const steps = answer.locator('[data-run-steps]');
  await expect(steps).toHaveText('3 steps');
  await expect(answer.locator('[data-run-log]')).toHaveCount(0);
  await steps.click();
  const log = answer.locator('[data-run-log]');
  await expect(log).toContainText('terminal: ls /backups');
  await expect(log).toContainText('Thinking: the newest is from today');
  await expect(log).toContainText('read_file: backup.log');
  await steps.click();
  await expect(log).toHaveCount(0);

  // All: started and finished, no progress notes, no bookkeeping, no "via API".
  const lines = tw.locator('.activity .ev');
  await expect(lines.filter({ hasText: 'Agent started working on this' })).toHaveCount(1);
  await expect(lines.filter({ hasText: 'Agent finished this' })).toHaveCount(1);
  await expect(lines.filter({ hasText: 'is working on this' })).toHaveCount(0);
  await expect(lines.filter({ hasText: 'ended the run' })).toHaveCount(0);
  await expect(tw.locator('.activity .ev .src')).toHaveCount(0);

  // History keeps every line, with how the agent reached Slate.
  await tw.getByRole('tab', { name: 'History' }).click();
  await expect(lines.filter({ hasText: 'is working on this' })).not.toHaveCount(0);
  await expect(lines.filter({ hasText: 'Agent ended the run (3 steps)' })).toContainText('via API');
  await api('POST', `/items/${item.id}/trash`);
});
