// Chat files: paste a screenshot into the message box (it waits as a thumbnail, then goes with
// the message and opens in the viewer), send a picked file with no text, and take one back.
import { expect, test, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

async function api<T>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

async function signedIn(page: Page, path = '/') {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
  await expect(page.getByRole('link', { name: /^Everything/ }).first()).toBeVisible();
}

async function scratch(name: string) {
  const section = await api<{ id: string }>('POST', '/sections', { name: `${name} space`, audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name });
  return list.id;
}

// A 2x2 red PNG.
const PNG =
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGP4z8DAwMDAxAADAA0GAQHLmD2xAAAAAElFTkSuQmCC';

/** What the browser does on Ctrl+V with a screenshot on the clipboard: a paste event whose
 *  clipboard holds one image file named `image.png` and no text. */
async function pasteScreenshot(page: Page, selector: string) {
  await page.evaluate(
    ([sel, b64]) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const data = new DataTransfer();
      data.items.add(new File([bytes], 'image.png', { type: 'image/png' }));
      const el = document.querySelector(sel)!;
      el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    },
    [selector, PNG],
  );
}

interface Msg {
  body_md: string;
  files: { id: string; filename: string; content_type: string; size: number }[];
}

test('paste a screenshot into chat: it sends with the message and opens in the viewer', async ({ page }) => {
  const listId = await scratch('Screens');
  await signedIn(page, `/#/list/${listId}/chat`);
  const view = page.getByRole('region', { name: '#Screens chat' });
  const box = view.getByRole('textbox', { name: 'Message #Screens' });
  await box.click();
  await box.pressSequentially('Look at this error');

  await pasteScreenshot(page, '.chat-main .chat-composer textarea');
  const waiting = view.getByRole('list', { name: 'Files to send' });
  await expect(waiting.locator('img')).toBeVisible();
  // The text typed before the paste is untouched.
  await expect(box).toHaveValue('Look at this error');

  await box.press('Enter');
  await expect(waiting).toHaveCount(0);
  const sent = view.locator('.chat-main .chat-msg', { hasText: 'Look at this error' });
  await expect(sent.locator('.chat-file-image img')).toBeVisible();

  const [msg] = await api<Msg[]>('GET', `/channels/list:${listId}/messages`);
  expect(msg.files).toHaveLength(1);
  expect(msg.files[0].content_type).toBe('image/png');
  expect(msg.files[0].filename).toMatch(/^Screenshot \d{4}-\d\d-\d\d \d\d\.\d\d\.\d\d\.png$/);
  // The agent on the channel reads the same bytes.
  const res = await fetch(`${BASE_URL}/api/v1/message-files/${msg.files[0].id}`, {
    headers: { Authorization: `Bearer ${seed().agent}` },
  });
  expect(res.status).toBe(200);
  expect(Buffer.from(await res.arrayBuffer()).toString('base64')).toBe(PNG);

  await sent.getByRole('button', { name: /^Open Screenshot/ }).click();
  const viewer = page.getByRole('dialog', { name: /^Viewing Screenshot/ });
  await expect(viewer.locator('.stage img')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(viewer).toHaveCount(0);

  // It is still there after a reload.
  await page.reload();
  await expect(view.locator('.chat-main .chat-msg .chat-file-image img')).toBeVisible();
});

test('a picked file goes with no text; a removed one is not sent', async ({ page }) => {
  const listId = await scratch('Papers');
  await signedIn(page, `/#/list/${listId}/chat`);
  const view = page.getByRole('region', { name: '#Papers chat' });
  const composer = view.locator('.chat-main .chat-composer');
  const send = composer.getByRole('button', { name: 'Send' });
  await expect(send).toBeDisabled();

  const picker = composer.locator('input[type=file]');
  await picker.setInputFiles({ name: 'draft.txt', mimeType: 'text/plain', buffer: Buffer.from('not this one\n') });
  const waiting = composer.getByRole('list', { name: 'Files to send' });
  await expect(waiting.getByText('draft.txt')).toBeVisible();
  await waiting.getByRole('button', { name: 'Remove draft.txt' }).click();
  await expect(waiting).toHaveCount(0);
  await expect(send).toBeDisabled();

  await picker.setInputFiles({ name: 'floor-plan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%fake\n') });
  await expect(waiting.getByText('floor-plan.pdf')).toBeVisible();
  await expect(send).toBeEnabled();
  await send.click();
  await expect(view.locator('.chat-main .chat-msg').getByRole('button', { name: 'Open floor-plan.pdf' })).toBeVisible();

  const msgs = await api<Msg[]>('GET', `/channels/list:${listId}/messages`);
  expect(msgs).toHaveLength(1);
  expect(msgs[0].body_md).toBe('');
  expect(msgs[0].files.map((f) => f.filename)).toEqual(['floor-plan.pdf']);
});
