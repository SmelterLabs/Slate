// An open board picks up a new deploy by itself. The page is fetched fresh on every load (no-cache
// plus a network-first service worker), but a tab left open keeps running the code it loaded. So
// when the live stream comes back (a deploy restarts the server) or the tab is shown again, the
// board asks /healthz which bundle the server's index.html loads (`board`) and, if it differs from
// its own, reloads, waiting until nothing is being typed. /healthz, not index.html: the service
// worker answers `/` from its cache but never touches /healthz. Unsaved view changes are on the
// account already (prefs `view_drafts`).

import { build } from './build.svelte';

const ENTRY = /\/assets\/index-[^"'\s]+\.js/;

/** The entry bundle an index.html loads, e.g. `/assets/index-AbC12.js`; null when it names none. */
export function buildOf(html: string): string | null {
  return html.match(ENTRY)?.[0] ?? null;
}

/** True only when both builds are known and differ: anything unreadable never reloads. */
export function isNewBuild(running: string | null, served: unknown): boolean {
  return !!running && typeof served === 'string' && !!buildOf(served) && served !== running;
}

/** The bundle this page is running (null under the dev server, which serves no bundle). */
export function runningBuild(doc: Document = document): string | null {
  for (const s of doc.querySelectorAll<HTMLScriptElement>('script[type="module"][src]')) {
    const m = new URL(s.src, location.href).pathname.match(ENTRY);
    if (m) return m[0];
  }
  return null;
}

/** Someone is typing: a reload now would lose it. */
function busy(): boolean {
  const el = document.activeElement as HTMLElement | null;
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}

let pending = false;

function reloadWhenIdle() {
  if (pending) return;
  pending = true;
  const go = () => {
    if (!document.hidden && busy()) return;
    document.removeEventListener('focusout', later);
    document.removeEventListener('visibilitychange', go);
    location.reload();
  };
  // focusout fires before focus moves on; look once it has settled.
  const later = () => setTimeout(go, 0);
  document.addEventListener('focusout', later);
  document.addEventListener('visibilitychange', go);
  go();
}

let checking = false;

/** The server's /healthz body, which also carries the build's rev; null when unreachable. */
async function health(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch('/healthz', { cache: 'no-store' });
    if (!res.ok) return null;
    const body = (await res.json()) as Record<string, unknown>;
    build.take(body);
    return body;
  } catch {
    return null; /* offline or restarting: the next reconnect checks again */
  }
}

/** Reload into the deployed build if it is not the one running. Quiet on any failure. */
export async function checkForNewBuild(): Promise<void> {
  if (checking || pending) return;
  checking = true;
  try {
    const running = runningBuild();
    const body = await health();
    // Under the dev server there is no bundle to compare; the rev is still read above.
    if (running && body && isNewBuild(running, body.board)) reloadWhenIdle();
  } finally {
    checking = false;
  }
}
