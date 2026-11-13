// Blocked state: a task waiting on an open one shows Blocked on its card, table row, task window
// and My Work, and clears live (no reload) when an agent finishes what it waits on; the assignee
// hears "Ready to start". Same on the phone.
import { expect, test } from '@playwright/test';
import { api, screenshots, seed, signedIn } from './support';

const shot = screenshots('SLATE_BLOCKED_SHOTS');

// Sections made here are archived after each test, so their undated tasks never crowd the shared
// Everything board other specs read (its No due date column shows 25 cards before "Show more").
const made: string[] = [];
test.afterEach(async () => {
  for (const id of made.splice(0)) await api('PATCH', `/sections/${id}`, { archived: true });
});

async function setup(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} section`, audience: 'work' });
  made.push(section.id);
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  const design = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: `${name}: design the deck`, assignees: ['agent'] });
  const build = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: `${name}: build the deck`, assignees: ['alex'] });
  await api('POST', `/items/${build.id}/links`, { kind: 'blocked_by', item_id: design.id });
  return { listId: list.id, design: design.id, build: build.id };
}

for (const phone of [false, true]) {
  test.describe(phone ? 'phone' : 'desktop', () => {
    if (phone) test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

    test('blocked shows everywhere and clears live when the blocker is done', async ({ page }) => {
      const tag = phone ? 'Phone blocked' : 'Desk blocked';
      const b = await setup(tag);
      await signedIn(page, `/#/list/${b.listId}`);
      const card = page.locator(`[data-item="${b.build}"]`).first();
      await expect(card.locator('[data-blocked]')).toBeVisible();
      await expect(card.locator('[data-blocked]')).toHaveAttribute('title', `Depends on: ${tag}: design the deck`);
      await expect(page.locator(`[data-item="${b.design}"] [data-blocked]`)).toHaveCount(0);
      await shot(page, `${phone ? 'phone' : 'desktop'}-board`);

      // The task window says what it waits on, with a link to it.
      await card.getByText(`${tag}: build the deck`).click();
      const banner = page.locator('[data-banner="blocked"]');
      await expect(banner).toContainText(`Depends on ${tag}: design the deck`);
      await shot(page, `${phone ? 'phone' : 'desktop'}-task`);

      // The agent finishes the design: the banner goes, no reload.
      await api('PATCH', `/items/${b.design}`, { status: 'Done' }, seed().agent);
      await expect(banner).toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(card.locator('[data-blocked]')).toHaveCount(0);

      // Reopening it blocks again, live.
      await api('PATCH', `/items/${b.design}`, { status: 'To do' }, seed().agent);
      await expect(card.locator('[data-blocked]')).toBeVisible();
    });

    test('My Work lists blocked tasks and the inbox says when one is ready', async ({ page }) => {
      const tag = phone ? 'Phone mywork' : 'Desk mywork';
      const b = await setup(tag);
      await signedIn(page, '/#/home');
      const blockedCard = page.locator('[data-card="blocked"]');
      await expect(blockedCard).toContainText(`${tag}: build the deck`);
      await expect(blockedCard).toContainText(`depends on ${tag}: design the deck`);
      await shot(page, `${phone ? 'phone' : 'desktop'}-mywork`);
      await api('PATCH', `/items/${b.design}`, { status: 'Done' }, seed().agent);
      await expect(blockedCard.getByText(`${tag}: build the deck`)).toHaveCount(0);
      const inbox = await api<{ reason: string; event: { item_id: string } }[]>('GET', '/inbox?reason=unblocked');
      expect(inbox.map((e) => e.event.item_id)).toContain(b.build);
      await signedIn(page, '/#/inbox');
      await expect(page.getByText(`${tag}: build the deck`).first()).toBeVisible();
      await shot(page, `${phone ? 'phone' : 'desktop'}-inbox`);
    });
  });
}
