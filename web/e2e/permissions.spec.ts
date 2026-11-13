// Members, sharing and the audit log: invite a guest from the Members page, share one list with
// them from the Share dialog, see the guest's board hold only that list (with the "can
// comment" note), take it away live, and read the audit log with filters and a CSV export.
// SLATE_SHOTS=<dir> also saves screenshots (desktop and phone).
import { expect, test, type Page } from '@playwright/test';
import { api, seed, signIn } from './support';

const SHOTS = process.env.SLATE_SHOTS;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` });
}

test('invite a guest, share one list, guest sees only it, audit log', async ({ page, browser }) => {
  const { agent } = seed();
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Clients', audience: 'work' });
  const shared = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Example Project' });
  const hidden = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Private' });
  await api('POST', '/items', { list_id: shared.id, title: 'Send the renewal' });
  await api('POST', '/items', { list_id: hidden.id, title: 'Secret plan' });
  await api('POST', '/items', { list_id: shared.id, title: 'Draft the quote' }, agent);

  await signIn(page);
  await page.goto('/#/members');
  await expect(page.getByRole('heading', { name: 'Members' })).toBeVisible();
  await expect(page.locator('[data-member="alex"]')).toContainText('(you)');
  await expect(page.locator('[data-member="agent"]')).toContainText('Agent');

  // Invite a guest; the token is shown once.
  await page.getByRole('button', { name: 'Invite' }).click();
  await page.getByLabel('Name', { exact: true }).fill('Guest');
  await expect(page.getByLabel('Handle')).toHaveValue('guest');
  await page.getByLabel('Role', { exact: true }).selectOption('guest');
  await shot(page, 'members-invite');
  await page.getByRole('button', { name: 'Create and get token' }).click();
  const tokenBox = page.locator('.token-box');
  await expect(tokenBox).toContainText('slate_');
  const guestToken = (await tokenBox.textContent())!.trim();
  await expect(page.locator('[data-member="guest"]')).toContainText('Nothing shared yet');
  await page.getByRole('button', { name: 'Done' }).click();
  await shot(page, 'members-desktop');

  // Share the list with the guest from the list page's Share dialog, at comment.
  await page.goto(`/#/list/${shared.id}`);
  await page.getByRole('button', { name: 'Share' }).click();
  const dialog = page.getByRole('dialog', { name: /Share Example Project/ });
  await dialog.getByLabel('Person or agent to share with').selectOption({ label: 'Guest (@guest) · Guest' });
  await dialog.getByLabel('Level').selectOption('comment');
  await dialog.getByRole('button', { name: 'Share' }).click();
  await expect(dialog.locator('[data-account="guest"]')).toContainText('Shared directly');
  await shot(page, 'share-dialog');
  await dialog.getByRole('button', { name: 'Close' }).click();

  // Make the other list private: the lock shows in the sidebar.
  await api('PATCH', `/lists/${hidden.id}`, { private: true });
  await expect(page.locator('.nav-list[data-list="Private"] .nav-label')).toBeVisible();

  // The guest's board: only the shared list, a comment-only note, no Members link.
  const guest = await browser.newPage();
  await signIn(guest, guestToken);
  await guest.goto(`/#/list/${shared.id}`);
  await expect(guest.getByText('Send the renewal')).toBeVisible();
  await expect(guest.locator('.nav-list')).toHaveCount(1);
  await expect(guest.locator('.level-banner')).toContainText('Can comment');
  await expect(guest.getByRole('link', { name: 'Members' })).toHaveCount(0);
  await expect(guest.getByRole('button', { name: 'New section' })).toHaveCount(0);
  await guest.goto('/#/everything');
  await expect(guest.getByText('Secret plan')).toHaveCount(0);
  await shot(guest, 'guest-board');

  // Live: unsharing takes the list away from the open guest board.
  const shares = await api<{ id: string; account_id: string }[]>('GET', `/shares?scope_kind=list&scope_id=${shared.id}`);
  await api('DELETE', `/shares/${shares[0].id}`);
  await expect(guest.locator('.nav-list')).toHaveCount(0);
  await expect(guest.getByText('Send the renewal')).toHaveCount(0);
  await guest.close();

  // The audit log: agents only, then a CSV of it.
  await page.goto('/#/audit');
  await expect(page.getByRole('heading', { name: 'Audit log' })).toBeVisible();
  await expect(page.locator('.audit-row[data-type="share.revoked"]').first()).toBeVisible();
  await shot(page, 'audit-desktop');
  await page.getByRole('radio', { name: 'Agents' }).click();
  await expect(page.locator('.audit-row').first()).toContainText('Agent');
  await expect(page.locator('.audit-row[data-type="share.granted"]')).toHaveCount(0);
  await page.getByRole('button', { name: /Filters/ }).click();
  await page.getByLabel('Kind of change').selectOption({ label: 'Tasks created' });
  await page.getByLabel('Search the log').fill('Draft the quote');
  await expect(page.locator('.audit-row')).toHaveCount(1);
  await expect(page.locator('.audit-row')).toContainText('Draft the quote');
  await page.locator('.audit-row').getByRole('button', { name: 'Details' }).click();
  await expect(page.locator('.audit-detail')).toContainText('item.created');
  await shot(page, 'audit-filters');
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()]);
  expect(download.suggestedFilename()).toBe('slate-audit.csv');
});

test('members, audit and share on a phone: same pieces, stacked', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
  const page = await ctx.newPage();
  await signIn(page);
  await page.goto('/#/members');
  await expect(page.getByRole('heading', { name: 'Members' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Invite' })).toBeVisible();
  await expect(page.locator('[data-member="alex"] select')).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  await shot(page, 'members-phone');
  await page.goto('/#/audit');
  await expect(page.locator('.audit-row').first()).toBeVisible();
  await page.getByRole('button', { name: /Filters/ }).click();
  await expect(page.getByLabel('Kind of change')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await shot(page, 'audit-phone');
  const lists = await api<{ id: string; name: string }[]>('GET', '/lists');
  const shared = lists.find((l) => l.name === 'Example Project')!;
  await page.goto(`/#/list/${shared.id}`);
  await page.getByRole('button', { name: 'Share' }).click();
  await expect(page.getByRole('dialog', { name: /Share Example Project/ })).toBeVisible();
  await shot(page, 'share-phone');
  await ctx.close();
});
