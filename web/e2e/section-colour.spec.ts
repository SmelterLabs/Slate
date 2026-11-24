// A section's colour: picked from its ⋯ menu, a dot beside its name in the left menu, and on
// Everything (a page that mixes sections) a wash and a section pill on its cards. Cleared with
// "No colour", both go. Needs a server that stores Section.color.
import { expect, test } from '@playwright/test';
import { api, seed, signedIn } from './support';

test.afterEach(async () => {
  await api('PATCH', `/sections/${seed().sectionId}`, { color: null });
});

test('give a section a colour from its menu, see it in the sidebar and on Everything, then clear it', async ({ page }) => {
  await signedIn(page, '/#/everything', { ready: true });
  const section = page.locator(`.nav-section[data-section-id="${seed().sectionId}"]`);
  const card = page.locator(`.card[data-item="${seed().dragId}"]`);
  await expect(card).toBeVisible();
  await expect(card).not.toHaveClass(/section-tint/);

  await section.getByRole('button', { name: 'Personal options' }).click();
  await page.getByRole('menuitem', { name: 'Colour…' }).click();
  const picker = section.getByRole('group', { name: 'Colour for Personal' });
  await expect(picker.getByRole('button', { name: 'No colour' })).toBeFocused();
  await picker.getByRole('button', { name: 'Blue' }).click();
  await expect(picker).toHaveCount(0);

  await expect(section.locator('.nav-heading .space-mark')).toHaveAttribute('data-section-color', '#0090ff');
  await expect(card).toHaveClass(/section-tint/);
  await expect(card.locator('[data-section-pill]')).toContainText('Personal');
  const saved = await api<{ id: string; color?: string | null }[]>('GET', '/sections');
  expect(saved.find((s) => s.id === seed().sectionId)?.color).toBe('#0090ff');

  await section.getByRole('button', { name: 'Personal options' }).click();
  await page.getByRole('menuitem', { name: 'Colour…' }).click();
  await expect(picker.getByRole('button', { name: 'Blue' })).toHaveAttribute('aria-pressed', 'true');
  await picker.getByRole('button', { name: 'No colour' }).click();

  await expect(section.locator('.nav-heading .space-mark')).not.toHaveAttribute('data-section-color');
  await expect(card).not.toHaveClass(/section-tint/);
  await expect(card.locator('[data-section-pill]')).toHaveCount(0);
});
