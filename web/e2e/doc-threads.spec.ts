// Discussion and review threads on a doc: a discussion comment with its revision stamp, a
// review thread started from a selection with a suggested replacement that Accept applies, a
// revision upload that appears as a folded activity line, and a thread that reads as outdated
// once the file moves on. The panel follows live changes from an agent.
import { expect, test } from '@playwright/test';
import { api, BASE_URL, seed, signIn } from './support';

async function upload(docId: string, token: string, note: string, body: string) {
  const res = await fetch(`${BASE_URL}/api/v1/docs/${docId}/revisions?filename=${encodeURIComponent('venue.png')}&note=${encodeURIComponent(note)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'image/png', Authorization: `Bearer ${token}` },
    body,
  });
  expect(res.ok, `upload -> ${res.status}`).toBe(true);
}

// A 1x1 PNG, enough for the image viewer.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64').toString('binary');

test('discussion, a review thread from a selection, and Accept on a page', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const doc = await api<{ id: string }>('POST', '/docs', {
    title: 'Seating plan',
    list_id: listId,
    body_md: 'Seat 12 guests at the head table.\n\nConfirm by Friday.',
  });

  await signIn(page);
  await page.goto(`/#/docs/${doc.id}`);
  await expect(page.getByText('Seat 12 guests at the head table.')).toBeVisible();

  // Discussion: a comment, stamped with the page version.
  await page.getByRole('button', { name: 'Discussion', exact: true }).click();
  const panel = page.getByRole('complementary', { name: 'Discussion' });
  await panel.getByLabel('Discussion comment').fill('The venue wants this by the 10th.');
  await panel.getByRole('button', { name: 'Send' }).click();
  const talk = panel.locator('[data-comment]', { hasText: 'The venue wants this by the 10th.' });
  await expect(talk).toBeVisible();
  await expect(talk.locator('.dd-rev')).toHaveText('on v1');

  // An agent's comment arrives live, and Alex asks the agent something.
  await api('POST', `/docs/${doc.id}/comments`, { body_md: 'I can start Monday.' }, agent);
  await expect(panel.locator('[data-comment]', { hasText: 'I can start Monday.' })).toBeVisible();

  // Review: select text on the page, Comment, suggest a replacement, Accept.
  const para = page.locator('.doc-rendered p', { hasText: 'Seat 12 guests' });
  await para.evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  });
  await page.locator('.doc-bubble').getByRole('button', { name: 'Comment' }).click();
  const form = page.getByTestId('review-form');
  await expect(form).toContainText('On “Seat 12 guests at the head table.”');
  await form.getByLabel('Review comment').fill('The guest list shows 14.');
  await form.getByLabel('Suggested replacement').fill('Seat 14 guests at the head table.');
  await form.getByRole('button', { name: 'Start thread' }).click();
  const thread = panel.locator('[data-thread]', { hasText: 'The guest list shows 14.' });
  await expect(thread).toBeVisible();
  await expect(thread.locator('.dd-suggestion ins')).toHaveText('Seat 14 guests at the head table.');
  await expect(page.getByRole('tab', { name: /Review/ })).toContainText('1');

  await thread.getByRole('button', { name: 'Accept' }).click();
  await expect(page.locator('.doc-rendered p', { hasText: 'Seat 14 guests at the head table.' })).toBeVisible();
  // Resolved threads leave the open list; Show resolved brings it back, marked.
  await expect(thread).toHaveCount(0);
  await panel.getByRole('button', { name: /Show 1 resolved/ }).click();
  await expect(thread).toContainText('Resolved');
  await expect(thread.locator('.dd-suggestion')).toContainText('Accepted replacement');
  // The accept and the save show up as folded activity in the discussion.
  await page.getByRole('tab', { name: /Discussion/ }).click();
  const activity = panel.locator('[data-activity]').last();
  await expect(activity).toContainText(/accepted a suggestion|edited the text/);
  await activity.locator('summary').click();
  await expect(activity).toContainText('accepted a suggestion');
});

test('a pin on an image, and a thread reads as outdated after the next revision', async ({ page }) => {
  const { alex, agent, listId } = seed();
  const doc = await api<{ id: string }>('POST', '/docs', { kind: 'file', title: 'Venue photo', list_id: listId });
  await upload(doc.id, agent, 'First shot', PNG);

  await signIn(page);
  await page.goto(`/#/docs/${doc.id}`);
  const img = page.locator('.file-view[data-kind="image"] img');
  await expect(img).toBeVisible();
  await img.click({ position: { x: 1, y: 1 } });
  const form = page.getByTestId('review-form');
  await expect(form).toContainText('On a spot');
  await form.getByLabel('Review comment').fill('Is this the stage?');
  await form.getByRole('button', { name: 'Start thread' }).click();
  const panel = page.getByRole('complementary', { name: 'Discussion' });
  const thread = panel.locator('[data-thread]', { hasText: 'Is this the stage?' });
  await expect(thread).toBeVisible();
  await expect(thread.locator('.dd-rev')).toHaveText('on rev 1');
  await expect(page.locator('.file-pin')).toHaveCount(1);

  // The next revision: the thread folds as outdated, the pin turns stale, and the upload is an
  // activity line in the discussion.
  await upload(doc.id, agent, 'Re-shot in daylight', PNG);
  await expect(page.getByTestId('file-meta')).toContainText('Revision 2');
  await expect(panel.locator('[data-thread-folded]')).toContainText('Outdated · on rev 1');
  await page.getByRole('tab', { name: /Discussion/ }).click();
  const act = panel.locator('[data-activity]').last();
  await expect(act).toContainText('uploaded rev 2');
  await act.locator('summary').click();
  await expect(act).toContainText('Re-shot in daylight');
});
