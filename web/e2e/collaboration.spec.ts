import { expect, test, type Page } from '@playwright/test';
import { api, openMore, signedIn, streamLive } from './support';

async function project(name: string) {
  const section = await api('POST', '/sections', { name, audience: 'work' });
  await api('PATCH', `/sections/${section.id}`, { private: true });
  return section;
}

test('four primary destinations, More preference, deep-link discovery, keyboard', async ({ page }) => {
  await signedIn(page, '/#/home', { live: true });
  const nav = page.getByRole('navigation', { name: 'Lists' });
  await expect(nav.locator(':scope > a.nav-item')).toHaveCount(4);
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeHidden();
  const more = nav.getByRole('button', { name: 'More', exact: true });
  await more.focus(); await page.keyboard.press('Enter');
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeVisible();
  await page.reload();
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeVisible();
  await more.click();
  await page.reload();
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeHidden();
  await page.goto('/#/agents');
  await expect(nav.getByRole('link', { name: 'Agents', exact: true })).toBeVisible();
  await expect(nav.getByRole('button', { name: /More · agents/ })).toBeVisible();
});

test('section drag persists across reload and second board; keyboard menu fallback', async ({ page, browser }) => {
  const a = await project('Drag project A');
  const b = await project('Drag project B');
  await api('POST', '/lists', { section_id: a.id, name: 'Stays inside A' });
  await signedIn(page, '/#/home', { live: true });
  const other = await browser.newPage(); await signedIn(other, '/#/home', { live: true });
  const target = page.getByRole('group', { name: 'Section Drag project A', exact: true });
  const source = page.getByRole('group', { name: 'Section Drag project B', exact: true });
  await source.dragTo(target, { targetPosition: { x: 50, y: 4 } });
  const names = async (p: Page) => p.locator('.nav-section').evaluateAll(rows => rows.map(r => r.getAttribute('data-section')));
  await expect.poll(async () => { const n = await names(page); return n.indexOf(b.name) < n.indexOf(a.name); }).toBe(true);
  await expect.poll(async () => { const n = await names(other); return n.indexOf(b.name) < n.indexOf(a.name); }).toBe(true);
  await page.reload();
  await expect.poll(async () => { const n = await names(page); return n.indexOf(b.name) < n.indexOf(a.name); }).toBe(true);
  await source.getByRole('button', { name: 'Drag project B options' }).click();
  await page.getByRole('menuitem', { name: 'Move down', exact: true }).click();
  await expect.poll(async () => { const n = await names(page); return n.indexOf(a.name) < n.indexOf(b.name); }).toBe(true);
  await page.reload();
  await expect(page.locator('[data-section="Drag project A"] [data-list="Stays inside A"]')).toBeVisible();
  await other.close();
});

test('admin invitation UI, guest project, cookie reload, docs, discussion, logout and replay', async ({ page, browser }) => {
  test.slow(); // one long story: invite, work, sign out, re-invite for another device, extend
  const section = await project('Customer collaboration');
  await api('PATCH', `/sections/${section.id}`, { whiteboard_url: 'https://example.com/whiteboard' });
  await api('POST', '/docs', { title: 'Customer plan', section_id: section.id, body_md: 'Our shared plan' });
  await signedIn(page, `/#/section/${section.id}`, { live: true });
  await page.getByRole('button', { name: 'Share & invite', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share Customer collaboration' });
  await dialog.getByLabel('Guest name').fill('Project Guest');
  await dialog.getByLabel('Guest handle').fill('project-guest');
  await dialog.getByRole('button', { name: 'Create invitation link' }).click();
  const link = await dialog.getByLabel('Invitation link').inputValue();
  expect(link).toContain('#/invite?secret=invite_');
  const guest = await browser.newPage();
  const requests: string[] = []; guest.on('request', r => requests.push(r.url()));
  await guest.goto(link);
  await expect(guest).not.toHaveURL(/secret=/);
  await guest.getByRole('button', { name: 'Accept invitation & sign in' }).click();
  await expect(guest.getByLabel('Project home')).toBeVisible();
  await streamLive(guest);
  await expect(guest.locator('.me-ends')).toContainText('Access ends'); // the session's end date, guests only
  await expect(page.locator('.me-ends')).toHaveCount(0);
  await expect(guest.getByLabel('Project participants')).toContainText('Project Guest');
  await expect(guest.getByLabel('Project docs').getByRole('link', { name: 'Customer plan' })).toBeVisible();
  await expect(guest.getByRole('link', { name: 'Open whiteboard' })).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(guest.locator('.nav-section')).toHaveCount(1);
  await expect(guest.getByRole('button', { name: 'New section' })).toBeHidden();
  const peer = await api('POST', '/accounts', { handle: 'project-peer', display_name: 'Peer Guest', kind: 'person', role: 'guest' });
  await api('PUT', '/shares', { account_id: peer.id, scope_kind: 'section', scope_id: section.id, level: 'edit' });
  await expect(guest.getByLabel('Project participants')).toContainText('Peer Guest', { timeout: 1000 });
  await guest.getByText('External whiteboard settings', { exact: true }).click();
  await guest.getByRole('button', { name: 'Edit whiteboard link' }).click();
  await guest.getByLabel('Whiteboard HTTPS URL').fill('https://example.com/customer-board');
  await guest.getByRole('button', { name: 'Save whiteboard', exact: true }).click();
  await expect(guest.getByRole('link', { name: 'Open whiteboard' })).toHaveAttribute('href', 'https://example.com/customer-board');
  expect((await api('GET', `/sections/${section.id}/project`)).section.whiteboard_url).toBe('https://example.com/customer-board');
  await guest.getByLabel('New project list').fill('Ideas');
  await guest.getByRole('button', { name: 'Create list', exact: true }).click();
  await expect(guest).toHaveURL(/#\/list\//);
  await guest.reload(); await streamLive(guest);
  expect(await guest.evaluate(() => localStorage.getItem('slate.token'))).toBe('@browser-session');
  const cookies = await guest.context().cookies();
  expect(cookies.find(c => c.name === 'slate_session')?.httpOnly).toBe(true);
  expect(requests.filter(url => url.includes('events/stream')).every(url => !url.includes('token='))).toBe(true);
  expect(requests.every(url => !url.split('#')[0].includes('invite_'))).toBe(true);
  await guest.goto(`/#/section/${section.id}`);
  await guest.getByRole('group', { name: 'Section Customer collaboration' }).getByRole('button', { name: 'Customer collaboration options' }).click();
  await expect(guest.getByRole('menuitem', { name: 'Move up', exact: true })).toBeDisabled();
  await page.keyboard.press('Escape');
  await guest.keyboard.press('Escape');
  await guest.locator('.nav-doc').getByRole('link', { name: 'Customer plan' }).click(); // the sidebar's own page link
  await expect(guest).toHaveURL(/#\/docs\//);
  await expect(guest.getByText('Our shared plan', { exact: true })).toBeVisible();
  await guest.goto(`/#/section/${section.id}`);
  await guest.getByRole('link', { name: 'Open chat' }).click(); // the chat panel, over the project home
  await expect(guest.getByRole('complementary', { name: 'Chat panel' })).toHaveAttribute('data-chat-panel', `section:${section.id}`);
  await expect(guest).toHaveURL(new RegExp(`#/section/${section.id}$`));
  // Sign out shows the token form at once and then reloads the page; wait for that reload, or
  // the next goto races it and is interrupted.
  const reloaded = guest.waitForEvent('load');
  await guest.getByRole('button', { name: /Sign out/ }).click();
  await reloaded;
  await expect(guest.getByLabel('Token', { exact: true })).toBeVisible();
  await guest.goto(link);
  await guest.getByRole('button', { name: 'Accept invitation & sign in' }).click();
  await expect(guest.getByRole('alert')).toContainText('already used');
  // The admin extends the (signed-out) session: refused, since it was revoked by the sign-out;
  // "Copy a link for another device" mints a fresh link for the same guest without retyping.
  if (!(await dialog.isVisible())) await page.getByRole('button', { name: 'Share & invite', exact: true }).click();
  await expect(dialog.getByTestId('invite-row')).toContainText('Signed out');
  await expect(dialog.getByRole('button', { name: 'Extend 30 days' })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Copy a link for another device' }).click();
  const second = await dialog.getByLabel('Invitation link').inputValue();
  expect(second).toContain('#/invite?secret=invite_');
  expect(second).not.toBe(link);
  await expect(dialog.getByText(/open it in Safari or Chrome/)).toBeVisible();
  // The installed app has no address bar: the guest pastes the whole link into the token box.
  await guest.goto('/#/home');
  await guest.reload(); // leave the invite card (a hash change alone keeps it)
  await guest.getByLabel('Token', { exact: true }).fill(second);
  await guest.getByRole('button', { name: 'Sign in' }).click();
  await expect(guest.getByLabel('Project home')).toBeVisible();
  await streamLive(guest);
  expect(await guest.evaluate(() => localStorage.getItem('slate.token'))).toBe('@browser-session');
  await expect(dialog.getByTestId('invite-row').filter({ hasText: 'Access ends' })).toHaveCount(1);
  await dialog.getByRole('button', { name: 'Extend 30 days' }).click();
  await expect(page.locator('.toast').filter({ hasText: 'Access now ends' })).toBeVisible();
  await guest.close();
});

test('phone sidebar More and section ordering via touch menu', async ({ browser }) => {
  const section = await project('Phone project');
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  await signedIn(page, `/#/section/${section.id}`, { live: true });
  await expect(page.getByLabel('Project home')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Menu', exact: true }).click();
  await openMore(page, { tap: true });
  await expect(page.getByRole('link', { name: 'Agents', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Phone project options' }).tap();
  await page.getByRole('menuitem', { name: 'Move up', exact: true }).tap();
  await expect.poll(async () => { const sections = await api('GET', '/sections'); return sections.findIndex((s: { id: string }) => s.id === section.id) < sections.length - 1; }).toBe(true);
  await page.getByRole('link', { name: 'My Work', exact: true }).tap();
  await expect(page.locator('.shell')).not.toHaveClass(/nav-open/);
  await context.close();
});

test('revoking a project share removes an open guest doc without manual refresh', async ({ browser }) => {
  const section = await project('Revoked project');
  const doc = await api('POST', '/docs', { section_id: section.id, title: 'Revocation doc', body_md: 'Content disappears after revocation' });
  const invitation = await api('POST', '/invitations', { section_id: section.id, handle: 'revoke-guest', display_name: 'Revoked Guest' });
  const guest = await browser.newPage();
  await guest.goto(`/#/invite?secret=${encodeURIComponent(invitation.secret)}`);
  await guest.getByRole('button', { name: 'Accept invitation & sign in' }).click();
  await streamLive(guest);
  await guest.goto(`/#/docs/${doc.id}`);
  await expect(guest.getByText('Content disappears after revocation', { exact: true })).toBeVisible();
  await api('PUT', '/shares', { account_id: invitation.account_id, scope_kind: 'section', scope_id: section.id, level: null });
  await expect(guest.getByText('Content disappears after revocation', { exact: true })).toBeHidden({ timeout: 10000 });
  await expect(guest.locator('.nav-section')).toHaveCount(0);
  await guest.close();
});
