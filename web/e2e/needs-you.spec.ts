// "Needs you": the bottom-right pill on every page, its pop-up list, the grouped panel on My
// Work, and the card on its own list still showing the agent's badge.
import { expect, test } from '@playwright/test';
import { api, seed, signIn } from './support';

test('needs you: pill on any page, grouped on My Work, cleared by Mark reviewed', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const review = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Needs-you review card',
    assignees: ['agent'],
  });
  const stuck = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Needs-you waiting card',
    assignees: ['agent'],
  });

  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  await expect(page.locator('.card', { hasText: 'Needs-you review card' })).toBeVisible();

  // Live: reports arrive over the stream and the pill follows.
  await api('POST', `/items/${review.id}/report`, { state: 'review', note: 'Draft is ready' }, agent);
  await api('POST', `/items/${stuck.id}/report`, { state: 'blocked', waiting_on: 'alex', note: 'Need the VIN' }, agent);
  const pill = page.locator('.nb-pill');
  await expect(pill).toBeVisible();
  // Red: something is blocked on Alex.
  await expect(pill).toHaveClass(/tone-hot/);
  // The card on its list keeps its own agent badge.
  await expect(page.locator('.card', { hasText: 'Needs-you review card' }).locator('.agent-badge')).toHaveAttribute(
    'data-agent-state',
    'review',
  );

  // The list's row in the sidebar carries the same count, red (something waits on Alex).
  const mark = page.locator(`.nav-list:has(a[href="#/list/${listId}"]) .needs-mark`);
  await expect(mark).toHaveClass(/need-waiting/);

  await pill.click();
  const pop = page.locator('.nb-pop');
  await expect(pop.locator(`[data-item="${stuck.id}"]`)).toHaveAttribute('data-need', 'waiting');
  const row = pop.locator(`[data-item="${review.id}"]`);
  await expect(row).toHaveAttribute('data-need', 'review');
  await expect(row).toContainText('Draft is ready');
  await page.keyboard.press('Escape');
  await expect(pop).toHaveCount(0);

  // My Work: grouped by reason, and no pill (the panel is right there).
  await page.goto('/#/home');
  const panel = page.locator('section.needs');
  await expect(panel.locator('.nd-group.need-waiting')).toContainText('Needs-you waiting card');
  await expect(panel.locator('.nd-group.need-review')).toContainText('Needs-you review card');
  await expect(page.locator('.nb-pill')).toHaveCount(0);

  // Mark reviewed clears the flag for good.
  await panel.locator(`[data-item="${review.id}"] [data-mark-reviewed]`).click();
  await expect(panel.locator(`[data-item="${review.id}"]`)).toHaveCount(0);
  await expect
    .poll(async () => (await api<{ agent: unknown }>('GET', `/items/${review.id}`)).agent ?? null)
    .toBeNull();
});

test('waiting on you: the card shows the question at the top, and Answer goes to it', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const card = await api<{ id: string }>('POST', '/items', {
    list_id: listId,
    title: 'Waiting-question card',
    assignees: ['agent'],
  });
  const long = Array.from({ length: 30 }, (_, i) => `- Checked ledger line ${i + 1}.`).join('\n');
  await api('POST', `/items/${card.id}/report`, {
    state: 'blocked',
    waiting_on: 'alex',
    reply: `**Status so far.**\n\n${long}\n\nTo finish: opening the details was declined; should I leave those details unopened? Sign-out is unconfirmed.`,
  }, agent);
  // Alex stops the agent's run: the badge goes, the wait stays (the case that showed nothing).
  await api('POST', `/items/${card.id}/report`, { state: 'idle', comment: false });

  await signIn(page);
  await page.goto(`/#/list/${listId}`);
  // The card on the board says so too, in red, even with the agent's badge gone.
  const onBoard = page.locator('.card', { hasText: 'Waiting-question card' });
  await expect(onBoard).toHaveClass(/needs-me/);
  await expect(onBoard).toHaveAttribute('data-need', 'waiting');
  await expect(onBoard.locator('[data-waits-on-me]')).toHaveText('Waiting on Alex');
  await expect(onBoard.locator('[data-waiting]')).toHaveCount(0); // not said twice
  await page.locator('.nb-pill').click();
  await page.locator(`.nb-pop [data-item="${card.id}"] .nd-main`).click();

  const banner = page.locator('[data-banner="waiting"]');
  await expect(banner).toContainText('Agent is waiting on you');
  await expect(banner.locator('[data-waiting-question]')).toHaveText(
    'To finish: opening the details was declined; should I leave those details unopened?',
  );

  await banner.locator('[data-answer]').click();
  await expect(page.locator('article.comment.lit')).toContainText('should I leave those details unopened?');
  await expect(page.locator('textarea[aria-label="Add comment"]')).toBeFocused();

  // Answering (a comment by Alex) clears the wait, and the banner with it.
  await page.keyboard.type('Leave them unopened.');
  await page.keyboard.press('Enter');
  await expect(banner).toHaveCount(0);
});
