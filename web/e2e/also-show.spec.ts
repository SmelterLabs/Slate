import { expect } from '@playwright/test';
import type { Item, List, Prefs } from '../src/lib/types';
import { api, calendarTest as test, seed, signedIn } from './support';

test.use({ viewport: { width: 1920, height: 1080 } });

test('brings in cards by section, saves the switches and drags within the source list', async ({ page }) => {
  const { listId } = seed();
  const home = await api<List>('GET', `/lists/${listId}`);
  const prefs = (await api<{ prefs: Prefs }>('GET', '/me')).prefs;
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Also show lab', audience: 'work' });
  let sourceId: string | undefined;
  const itemIds: string[] = [];
  try {
    const source = await api<List>('POST', '/lists', {
      section_id: section.id, name: 'Source tasks',
      statuses: [{ name: 'Inbox', kind: 'open' }, { name: 'Underway', kind: 'active' }],
    });
    sourceId = source.id;
    const assigned = await api<Item>('POST', '/items', {
      list_id: source.id, title: 'Assigned source card', assignees: ['alex'],
    });
    itemIds.push(assigned.id);
    const other = await api<Item>('POST', '/items', { list_id: source.id, title: 'Unassigned source card' });
    itemIds.push(other.id);
    const open = home.statuses.find((s) => s.kind === 'open')!;
    const active = home.statuses.find((s) => s.kind === 'active')!;
    const ownActive = source.statuses.find((s) => s.kind === 'active')!;

    await signedIn(page, `/#/list/${listId}`);
    const toggle = page.getByRole('group', { name: 'Also show', exact: true }).getByRole('button', { name: /^Also show lab/ });
    const mine = page.locator(`.card[data-item="${assigned.id}"]`);
    const unassigned = page.locator(`.card[data-item="${other.id}"]`);
    const openColumn = page.locator(`.column[data-group="${open.id}"]`);
    const activeColumn = page.locator(`.column[data-group="${active.id}"]`);

    await expect(toggle).toHaveAccessibleName('Also show lab 1');
    await toggle.click();
    await expect(openColumn.locator(mine)).toBeVisible();
    await expect(unassigned).toHaveCount(0);
    await page.getByRole('button', { name: 'Everyone', exact: true }).click();
    await expect(openColumn.locator(unassigned)).toBeVisible();
    await mine.dragTo(activeColumn.locator('.column-head'));
    await expect(activeColumn.locator(mine)).toBeVisible();
    await expect.poll(async () => {
      const current = await api<Item>('GET', `/items/${assigned.id}`);
      return { list: current.list_id, status: current.status?.id, position: current.position };
    }).toEqual({ list: source.id, status: ownActive.id, position: assigned.position });

    await page.reload();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('button', { name: 'Everyone', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(activeColumn.locator(mine)).toBeVisible();

    await page.goto(`/?kiosk=1#/list/${listId}`);
    await expect(activeColumn.locator(mine)).toBeVisible();
    await expect(page.getByRole('group', { name: 'Also show', exact: true })).toHaveCount(0);
    await page.goto(`/#/list/${listId}`);
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');

    const savedOff = page.waitForResponse((r) => r.request().method() === 'PATCH' && new URL(r.url()).pathname.endsWith('/me/prefs'));
    await toggle.click();
    await expect(mine).toHaveCount(0);
    await expect(unassigned).toHaveCount(0);
    expect((await savedOff).ok()).toBe(true);
  } finally {
    await api('PATCH', '/me/prefs', { also_show: prefs.also_show ?? null });
    if (sourceId) await api('PATCH', `/lists/${sourceId}`, { archived: true });
    await api('PATCH', `/sections/${section.id}`, { archived: true });
  }
});
