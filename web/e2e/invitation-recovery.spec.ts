import { expect, test } from '@playwright/test';

// Synthetic failures only: no invitation is issued or consumed by these tests.
test('an unavailable invitation offers recovery without asking for another person’s token', async ({ page }) => {
  await page.route('**/api/v1/invitations/accept', route => route.fulfill({
    status: 410, contentType: 'application/json', body: JSON.stringify({ detail: 'invitation expired' }),
  }));
  await page.goto('/#/invite?secret=synthetic-expired-link');
  await expect(page).not.toHaveURL(/secret=/);
  await page.getByRole('button', { name: 'Accept invitation & sign in' }).click();
  await expect(page.getByRole('alert')).toContainText('invitation expired');
  await expect(page.getByText(/Ask the person who invited you for a fresh invitation link/)).toBeVisible();
  await expect(page.getByLabel('Token', { exact: true })).toHaveCount(0);
});

test('temporary invitation service failure suggests retry, not replacing a valid invitation', async ({ page }) => {
  await page.route('**/api/v1/invitations/accept', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'temporarily unavailable' }),
  }));
  await page.goto('/#/invite?secret=synthetic-retry-link');
  await page.getByRole('button', { name: 'Accept invitation & sign in' }).click();
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable');
  await expect(page.getByText(/Try again once your connection or Slate has recovered/)).toBeVisible();
  await expect(page.getByText(/Ask the person who invited you for a fresh invitation link/)).toHaveCount(0);
});

test('an incomplete invitation explains the next step without submitting', async ({ page }) => {
  let requests = 0;
  await page.route('**/api/v1/invitations/accept', route => { requests++; return route.abort(); });
  await page.goto('/#/invite?secret=');
  await expect(page.getByRole('button', { name: 'Accept invitation & sign in' })).toBeDisabled();
  await expect(page.getByText(/This invitation link is incomplete/)).toBeVisible();
  expect(requests).toBe(0);
});
