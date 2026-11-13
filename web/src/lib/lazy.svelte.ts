// Pages the board loads on demand. Each is its own chunk (a dynamic import), so the first load
// carries the board and the task window but not Agents, Docs, Members, Audit, Health, Archive,
// Chat or the settings dialogs. Once the board is up, `prefetchPages` fetches them in the
// background, so opening one later is instant and the installed app has them offline.

import type { Component } from 'svelte';

type AnyComponent = Component<any, any, any>;

export class LazyPage<C extends AnyComponent = AnyComponent> {
  /** The page's component, once its chunk has loaded. */
  component = $state<C | null>(null);
  /** The last load the page asked for failed (offline, or a deploy replaced the chunk). */
  failed = $state(false);
  #loader: () => Promise<{ default: C }>;
  #pending: Promise<void> | null = null;

  constructor(loader: () => Promise<{ default: C }>) {
    this.#loader = loader;
  }

  /** Load for showing the page: a failure is shown, with a way to try again. */
  load(): Promise<void> {
    return this.#fetch(true);
  }

  /** Load in the background: a failure stays quiet, and opening the page tries again. */
  prefetch(): Promise<void> {
    return this.#fetch(false);
  }

  #fetch(shown: boolean): Promise<void> {
    if (this.component) return Promise.resolve();
    if (shown) this.failed = false;
    this.#pending ??= this.#loader()
      .then((m) => {
        this.component = m.default;
      })
      .finally(() => {
        this.#pending = null;
      });
    return this.#pending.catch(() => {
      if (shown) this.failed = true;
    });
  }
}

export const pages = {
  agents: new LazyPage(() => import('../components/AgentsPage.svelte')),
  docs: new LazyPage(() => import('../components/docs/DocsPage.svelte')),
  archive: new LazyPage(() => import('../components/ArchivePage.svelte')),
  members: new LazyPage(() => import('../components/members/MembersPage.svelte')),
  audit: new LazyPage(() => import('../components/audit/AuditPage.svelte')),
  health: new LazyPage(() => import('../components/health/HealthPage.svelte')),
  chat: new LazyPage(() => import('../components/chat/ChatPage.svelte')),
  tags: new LazyPage(() => import('../components/TagManager.svelte')),
  notifications: new LazyPage(() => import('../components/NotificationSettings.svelte')),
  shortcuts: new LazyPage(() => import('../components/ShortcutSheet.svelte')),
};

let prefetched = false;

/** Fetch every on-demand page in the background, once, when the browser is idle. */
export function prefetchPages() {
  if (prefetched || typeof window === 'undefined') return;
  prefetched = true;
  const run = () => {
    for (const p of Object.values(pages)) void p.prefetch();
  };
  if ('requestIdleCallback' in window) window.requestIdleCallback(run, { timeout: 3000 });
  else setTimeout(run, 1500);
}
