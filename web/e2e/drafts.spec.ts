// Drafts: text typed and left (card closed, page reloaded, another view) comes back where it was
// typed, the card says it holds a draft, and sending clears it. Cancel throws a draft away.
import { expect, test } from '@playwright/test';
import { api, signedIn } from './support';

test('a comment typed in a card survives closing it and a reload, then sends and clears', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Draft space', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Drafts' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Call the plumber' });
  await signedIn(page, `/#/list/${list.id}`, { ready: true });

  const card = page.locator('.card', { hasText: 'Call the plumber' });
  await card.getByText('Call the plumber').click();
  const tw = page.locator('.tw');
  await tw.getByLabel('Add comment').fill('Ask about the Tuesday slot and');
  await page.keyboard.press('Escape'); // leaves the box
  await page.keyboard.press('Escape'); // closes the card
  await expect(tw).toHaveCount(0);
  await expect(card.locator('.card-draft')).toHaveText('Draft');

  await page.reload();
  await expect(card.locator('.card-draft')).toBeVisible();
  await card.getByText('Call the plumber').click();
  await expect(tw.getByLabel('Add comment')).toHaveValue('Ask about the Tuesday slot and');

  await tw.getByLabel('Ask', { exact: true }).selectOption('');
  await tw.getByLabel('Add comment').fill('Ask about the Tuesday slot.');
  await tw.getByLabel('Add comment').press('Enter');
  await expect(tw.locator('.comment', { hasText: 'Ask about the Tuesday slot.' })).toBeVisible();
  await expect(tw.getByLabel('Add comment')).toHaveValue('');
  await page.keyboard.press('Escape');
  await expect(card.locator('.card-draft')).toHaveCount(0);

  // Another card's draft is its own.
  const other = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Pay the plumber' });
  await page.goto(`/#/list/${list.id}?task=${other.id}`);
  await expect(tw.getByLabel('Add comment')).toHaveValue('');
  await page.goto(`/#/list/${list.id}?task=${item.id}`);
  await expect(tw.getByLabel('Add comment')).toHaveValue('');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('a new task typed in a column, the quick add bar and a channel message all come back', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Draft space 2', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Tasks' });
  await signedIn(page, `/#/list/${list.id}`, { ready: true });

  await page.getByLabel('Quick add').fill('Buy furnace filter');
  await page.locator('.col-add-btn').first().click();
  await page.locator('.col-add textarea').fill('Return library books');
  await page.reload();
  await expect(page.getByLabel('Quick add')).toHaveValue('Buy furnace filter');
  await expect(page.locator('.col-add-btn .draft-tag').first()).toBeVisible();
  await page.locator('.col-add-btn').first().click();
  await expect(page.locator('.col-add textarea')).toHaveValue('Return library books');
  // Cancel throws it away.
  await page.locator('.col-add').getByRole('button', { name: 'Cancel' }).click();
  await expect(page.locator('.col-add-btn .draft-tag')).toHaveCount(0);

  // The Chat tab page, left and come back (the tab itself opens the chat panel on a wide screen).
  await page.goto(`/#/list/${list.id}/chat`);
  const box = page.getByRole('textbox', { name: 'Message #Tasks' });
  await box.fill('Who has the car on Sat');
  await page.goto(`/#/list/${list.id}`);
  await expect(box).toHaveCount(0);
  await page.goto(`/#/list/${list.id}/chat`);
  await expect(box).toHaveValue('Who has the car on Sat');
  await box.press('Enter');
  await expect(page.locator('.chat-msg', { hasText: 'Who has the car on Sat' }).first()).toBeVisible();
  await page.reload();
  await expect(box).toHaveValue('');

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('a subtask, a hand-off and a title typed in a card are kept when it closes', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Draft space 3', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Garden' });
  const item = await api<{ id: string }>('POST', '/items', { list_id: list.id, title: 'Plant bulbs' });
  await signedIn(page, `/#/list/${list.id}?task=${item.id}`, { ready: true });
  const tw = page.locator('.tw');

  await tw.getByRole('button', { name: 'Subtask', exact: true }).click();
  await tw.getByLabel('Add subtask').fill('Buy tulips');
  await tw.getByRole('button', { name: /Hand to an agent/ }).click();
  await tw.getByLabel('What should it do?').fill('Find a nursery near me');
  await tw.getByLabel('Title').fill('Plant bulbs before frost');
  await page.getByRole('button', { name: 'Close task' }).click({ position: { x: 5, y: 5 } });
  await expect(tw).toHaveCount(0);
  await expect(page.locator('.card', { hasText: 'Plant bulbs before frost' })).toBeVisible();

  await page.reload();
  await page.locator('.card', { hasText: 'Plant bulbs before frost' }).getByText('Plant bulbs before frost').click();
  await expect(tw.getByLabel('Add subtask')).toHaveValue('Buy tulips');
  await expect(tw.getByLabel('What should it do?')).toHaveValue('Find a nursery near me');
  // Cancel on the hand-off throws its draft away.
  await tw.locator('.handoff').getByRole('button', { name: 'Cancel' }).click();
  await page.keyboard.press('Escape');
  await page.reload();
  await page.locator('.card', { hasText: 'Plant bulbs before frost' }).getByText('Plant bulbs before frost').click();
  await expect(tw.getByLabel('Add subtask')).toHaveValue('Buy tulips');
  await expect(tw.getByLabel('What should it do?')).toHaveCount(0);

  await api('PATCH', `/sections/${section.id}`, { archived: true });
});

test('a message still sending when the page reloads does not come back as a draft', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Draft space 4', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Chores' });
  await signedIn(page, `/#/list/${list.id}/chat`, { ready: true });
  // Hold the send long enough that the reload lands while it is still in flight.
  await page.route('**/api/v1/channels/*/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue().catch(() => {});
  });
  const box = page.getByRole('textbox', { name: 'Message #Chores' });
  await box.fill('Take the bins out');
  await box.press('Enter');
  await expect(box).toHaveValue('');
  await page.reload();
  await expect(box).toHaveValue('');
  expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('slate.draft.')).map((k) => localStorage.getItem(k)))).not.toContain(
    'Take the bins out',
  );

  await page.unrouteAll({ behavior: 'ignoreErrors' });
  await api('PATCH', `/sections/${section.id}`, { archived: true });
});
