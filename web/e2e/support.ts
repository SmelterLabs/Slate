// Shared helpers for the browser tests: call the API, sign in, open More in the left menu,
// take optional screenshots. Every spec imports these instead of carrying its own copy.
import { expect, type Locator, type Page } from '@playwright/test';
import { BASE_URL, seed } from './seed';

export { BASE_URL, seed, SEED_FILE, type Seed } from './seed';

/**
 * Call the Slate API as `token` (Alex by default) and return the JSON body (`undefined` for
 * 204 No Content). A response that is not 2xx fails the test with its status and body.
 */
export async function api<T = any>(method: string, path: string, body?: unknown, token = seed().alex): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  expect(res.ok, `${method} ${path} -> ${res.status} ${res.ok ? '' : await res.text()}`).toBe(true);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

/** POST raw bytes (a file, an image) to `path` as `contentType` and return the JSON reply. */
export async function upload<T = any>(
  path: string,
  body: BodyInit,
  contentType: string,
  token = seed().alex,
): Promise<T> {
  const res = await fetch(`${BASE_URL}/api/v1${path}`, {
    method: 'POST',
    headers: { 'Content-Type': contentType, Authorization: `Bearer ${token}` },
    body,
  });
  expect(res.ok, `POST ${path} -> ${res.status} ${res.ok ? '' : await res.text()}`).toBe(true);
  return (await res.json()) as T;
}

/** Upload a new revision of the file document `docId`. */
export function addRevision(
  docId: string,
  filename: string,
  body: BodyInit,
  { note = '', type = 'text/plain', token = seed().alex }: { note?: string; type?: string; token?: string } = {},
): Promise<{ revision_no: number }> {
  const query = new URLSearchParams(note ? { filename, note } : { filename });
  return upload(`/docs/${docId}/revisions?${query}`, body, type, token);
}

/** Store `token` (Alex by default) before the board loads, so the next `goto` is signed in. */
export async function signIn(page: Page, token = seed().alex): Promise<void> {
  await page.addInitScript((t) => localStorage.setItem('slate.token', t), token);
}

/**
 * Sign in and open `path`. With `ready`, also wait until the left menu shows Everything, the
 * sign that the board has loaded the workspace. With `live`, wait for the live stream too.
 */
export async function signedIn(
  page: Page,
  path = '/',
  { token = seed().alex, ready = false, live = false }: { token?: string; ready?: boolean; live?: boolean } = {},
): Promise<void> {
  await signIn(page, token);
  await page.goto(path);
  if (ready) await expect(page.getByRole('link', { name: /^Everything/ }).first()).toBeVisible();
  if (live) await streamLive(page);
}

/**
 * Wait until the board's live stream is open, so a change made through the API from now on
 * reaches this page (the server has subscribed it before the stream reports open).
 */
export async function streamLive(page: Page): Promise<void> {
  await expect(page.locator('.conn-live').first()).toBeVisible();
}

/**
 * Open the More group of the left menu (in `scope`, the page by default) if it is closed. The
 * board remembers More per account, so a blind click could close it instead.
 */
export async function openMore(scope: Page | Locator, { tap = false } = {}): Promise<void> {
  const more = scope.locator('button[aria-controls="nav-more"]').first();
  await expect(more).toBeVisible();
  if ((await more.getAttribute('aria-expanded')) !== 'true') {
    if (tap) await more.tap();
    else await more.click();
  }
  await expect(more).toHaveAttribute('aria-expanded', 'true');
}

export interface ShotOptions {
  /** Colour scheme for this capture; the page goes back to the default afterwards. */
  scheme?: 'dark' | 'light';
  fullPage?: boolean;
  clip?: { x: number; y: number; width: number; height: number };
  /** Capture only this element instead of the page. */
  target?: Locator;
}

/**
 * A `shot(page, name)` that saves `<dir>/<name>.png`, or does nothing when `dir` is unset (the
 * screenshots are for documentation runs only). `defaults` apply to every capture.
 */
export function screenshotter(dir: string | null | undefined, defaults: ShotOptions = {}) {
  return async (page: Page, name: string, options: ShotOptions = {}): Promise<void> => {
    if (!dir) return;
    const { scheme, fullPage, clip, target } = { ...defaults, ...options };
    if (scheme) await page.emulateMedia({ colorScheme: scheme });
    const path = `${dir}/${name}.png`;
    if (target) await target.screenshot({ path, animations: 'disabled' });
    else await page.screenshot({ path, fullPage, clip, animations: 'disabled' });
    if (scheme) await page.emulateMedia({ colorScheme: null });
  };
}
