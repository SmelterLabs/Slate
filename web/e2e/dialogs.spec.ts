// Every modal dialog behaves the same (lib/focustrap.ts): opening moves focus inside, Tab and
// Shift+Tab cycle through its controls and never reach the page behind, Escape closes it, and
// focus goes back to the control that opened it. The docked task window is not modal: Tab moves
// on to the board beside it. A dialog over another closes alone on Escape.
import { expect, test, type Locator, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

test.use({ viewport: { width: 1600, height: 1000 } });

async function api<T>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status}`).toBe(true);
  return (await res.json()) as T;
}

async function signedIn(page: Page, path: string) {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), seed().alex);
  await page.goto(path);
}

/** One blank 1x1 PNG, as an attachment on the item. */
async function attachImage(itemId: string, name: string) {
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
    'base64',
  );
  const res = await fetch(`${BASE_URL}/api/v1/items/${itemId}/attachments?filename=${name}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${seed().alex}`, 'Content-Type': 'image/png' },
    body: png,
  });
  expect(res.status).toBe(201);
}

const holdsFocus = (dialog: Locator) => dialog.evaluate((d) => d.contains(document.activeElement));

/** How many Tab stops the dialog has (the most a Tab cycle needs before it wraps). */
const stops = (dialog: Locator) =>
  dialog.evaluate(
    (d) =>
      Array.from(
        d.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [contenteditable="true"], [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.getClientRects().length).length,
  );

/**
 * The shared contract. `open` opens the dialog (focusing what opens it, when that is a control);
 * `opener` is that control, which must have focus again after Escape.
 */
async function checkDialog(page: Page, dialog: Locator, open: () => Promise<void>, opener?: Locator) {
  await open();
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect.poll(() => holdsFocus(dialog)).toBe(true);

  // Forward past the last control and around again, then backward past the first.
  const n = Math.min(await stops(dialog), 40) + 2;
  for (let i = 0; i < n; i++) {
    await page.keyboard.press('Tab');
    expect(await holdsFocus(dialog), `Tab ${i + 1} of ${n} left the dialog`).toBe(true);
  }
  // A text box keeps Escape for itself (it leaves the box; lib/focustrap.ts), so go on back to
  // a control that does not before pressing it.
  const inTextBox = () =>
    page.evaluate(() => {
      const a = document.activeElement as HTMLElement;
      return /^(INPUT|TEXTAREA)$/.test(a.tagName) || a.isContentEditable;
    });
  for (let i = 0; i < 3 || (i < n + 3 && (await inTextBox())); i++) {
    await page.keyboard.press('Shift+Tab');
    expect(await holdsFocus(dialog), `Shift+Tab ${i + 1} left the dialog`).toBe(true);
  }

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  if (opener) await expect(opener).toBeFocused();
}

test('the task window, the file viewer over it, and the docked window', async ({ page }) => {
  const item = await api<{ id: string }>('POST', '/items', { list_id: seed().listId, title: 'Trap the focus' });
  await attachImage(item.id, 'tiny.png');
  await signedIn(page, `/#/list/${seed().listId}`);
  const card = page.locator('.card', { hasText: 'Trap the focus' }).first();
  const win = page.getByRole('dialog', { name: 'Task: Trap the focus' });

  await checkDialog(page, win, () => card.click(), card);

  // The viewer over the window: Escape closes the viewer only, and focus goes back to the
  // thumbnail that opened it.
  await card.click();
  await expect(win).toBeVisible();
  const thumb = win.getByRole('button', { name: 'Open tiny.png' });
  const viewer = page.getByRole('dialog', { name: 'Viewing tiny.png' });
  await checkDialog(page, viewer, () => thumb.click(), thumb);
  await expect(win).toBeVisible();

  // Docked, the window is not modal: Tab moves on to the board beside it.
  await win.getByTitle('Where the window sits').click();
  await page.getByRole('menuitemradio', { name: 'Right third' }).click();
  await expect(win).toHaveAttribute('aria-modal', 'false');
  await win.getByRole('button', { name: 'Open tiny.png' }).focus();
  let left = false;
  for (let i = 0; i < 80 && !left; i++) {
    await page.keyboard.press('Tab');
    left = !(await holdsFocus(win));
  }
  expect(left).toBe(true);
  await page.evaluate(() => localStorage.removeItem('slate.taskWindow'));
});

test('the board: list settings, tags, shortcuts and the command palette', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Larder section', audience: 'work' });
  await api('POST', '/lists', { section_id: section.id, name: 'Larder' });
  await signedIn(page, `/#/list/${seed().listId}`);
  await expect(page.locator('.card').first()).toBeVisible();

  const options = page.locator('.nav-section[data-section="Larder section"]').getByRole('button', { name: 'Larder options' });
  await checkDialog(
    page,
    page.getByRole('dialog', { name: 'List settings' }),
    async () => {
      await options.click();
      await page.getByRole('menuitem', { name: 'List settings…' }).click();
    },
    options,
  );

  const tags = page.locator('.nav-foot').getByRole('button', { name: 'Tags' });
  await checkDialog(page, page.getByRole('dialog', { name: 'Tags' }), () => tags.click(), tags);

  await page.locator('.card').first().focus();
  await page.locator('.card').first().press('?');
  await checkDialog(page, page.getByRole('dialog', { name: 'Keyboard shortcuts' }), async () => {});

  await checkDialog(page, page.getByRole('dialog', { name: 'Search and commands' }), () => page.keyboard.press('Control+k'));
});

test('notifications, sharing, member access and adding an agent', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Trap project', audience: 'work' });
  await api('PATCH', `/sections/${section.id}`, { private: true }); // a project that can be shared
  await signedIn(page, '/#/home');
  const bell = page.getByRole('button', { name: 'Notifications' });
  await checkDialog(page, page.getByRole('dialog', { name: 'Notifications' }), () => bell.click(), bell);

  await page.goto(`/#/section/${section.id}`);
  const share = page.getByRole('button', { name: 'Share & invite', exact: true });
  await checkDialog(page, page.getByRole('dialog', { name: 'Share Trap project' }), () => share.click(), share);

  await page.goto('/#/members');
  const access = page.getByRole('button', { name: 'Access', exact: true }).first();
  await checkDialog(page, page.getByRole('dialog', { name: /^Access for / }), () => access.click(), access);

  await page.goto('/#/agents');
  const add = page.getByRole('button', { name: 'Add my agent', exact: true }).first();
  await checkDialog(page, page.getByRole('dialog', { name: 'Add my agent' }), () => add.click(), add);
});

test('chat: turning a message into a task', async ({ page }) => {
  const section = await api<{ id: string }>('POST', '/sections', { name: 'Hinge section', audience: 'work' });
  const list = await api<{ id: string }>('POST', '/lists', { section_id: section.id, name: 'Hinges' });
  await api('POST', `/channels/list:${list.id}/messages`, { body_md: 'Oil the hinges this week' });
  await signedIn(page, `/#/list/${list.id}/chat`);
  const mine = page.locator('.chat-main .chat-msg', { hasText: 'Oil the hinges' });
  await mine.hover();
  const make = mine.getByRole('button', { name: 'Turn into task' });
  await checkDialog(page, page.getByRole('dialog', { name: 'Turn into task' }), () => make.click(), make);
});

test('docs: a new page, an upload and the move picker', async ({ page }) => {
  await api('POST', '/docs', { title: 'Trap outline', body_md: 'Text' });
  await signedIn(page, '/#/docs');
  const create = page.getByRole('button', { name: 'New page', exact: true }).first();
  await checkDialog(page, page.getByRole('dialog', { name: 'New page' }), () => create.click(), create);

  const upload = page.getByRole('button', { name: 'Upload', exact: true });
  await checkDialog(page, page.getByRole('dialog', { name: 'Upload a file' }), () => upload.click(), upload);

  // Opened from a menu item: focus goes back to the menu's button once the menu is gone.
  const actions = page.getByRole('button', { name: 'Actions for Trap outline' });
  await checkDialog(
    page,
    page.getByRole('dialog', { name: 'Move “Trap outline” to…' }),
    async () => {
      await actions.click();
      await page.getByRole('menuitem', { name: 'Move…' }).click();
    },
    actions,
  );
});
