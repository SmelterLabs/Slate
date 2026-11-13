// Board preferences. Theme, folded columns, the last view per scope and unsaved view
// changes live on the account (`PATCH /me/prefs`), so every device shows the same; the
// quick-add list per view and the AI add switch are kept on this device.

import { patch } from '../api';
import { applyTheme, asTheme, type Theme } from '../theme';
import type { Prefs } from '../types';
import type { Scope } from '../views';
import { BoardState } from './state.svelte';

const QA_KEY = 'slate.quickadd.';
const AI_ADD_KEY = 'slate.aiAdd';

/** AI add is on unless this device turned it off. */
function readAiAdd(): boolean {
  try {
    return localStorage.getItem(AI_ADD_KEY) !== '0';
  } catch {
    return true;
  }
}
/** Where folded columns lived per browser before they moved onto the account. */
const LEGACY_COLLAPSE_KEY = 'slate.collapsed.';

function store(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: per-viewer conveniences only
  }
}

export abstract class BoardPrefs extends BoardState {
  /** Prefs from the server, keeping this device's view changes that are not saved yet
   * (the views layer, which owns the unsent drafts). */
  protected abstract takePrefs(incoming: Prefs): Prefs;

  get prefs(): Prefs {
    return this.me?.prefs ?? {};
  }

  get theme(): Theme {
    return asTheme(this.prefs.theme);
  }

  /** Merge into the account's prefs; a key set to null is removed. Shown at once, then saved. */
  async setPrefs(changes: Partial<Record<keyof Prefs, unknown>>) {
    if (!this.me) return;
    const next: Record<string, unknown> = { ...this.me.prefs };
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) delete next[k];
      else next[k] = v;
    }
    this.me.prefs = next as Prefs;
    try {
      const saved = await patch<Prefs>('/me/prefs', changes);
      if (this.me) this.me.prefs = this.takePrefs(saved);
    } catch (e) {
      this.flash(String(e));
    }
  }

  setTheme(theme: Theme) {
    applyTheme(theme);
    void this.setPrefs({ theme: theme === 'auto' ? null : theme });
  }

  collapsedFor(viewKey: string): Record<string, boolean> {
    return this.prefs.collapsed?.[viewKey] ?? {};
  }

  setCollapsed(viewKey: string, state: Record<string, boolean>) {
    const all = { ...this.prefs.collapsed };
    if (Object.keys(state).length) all[viewKey] = state;
    else delete all[viewKey];
    void this.setPrefs({ collapsed: Object.keys(all).length ? all : null });
  }

  /** Before prefs lived on the account, each browser kept its own theme and folds. Hand any it
   *  still has to the account once, where the account has none, then forget them here. */
  protected async adoptLocalPrefs() {
    if (!this.me) return;
    const changes: Partial<Record<keyof Prefs, unknown>> = {};
    const stale: string[] = [];
    try {
      const theme = localStorage.getItem('slate.theme');
      if (!this.me.prefs.theme && (theme === 'light' || theme === 'dark')) changes.theme = theme;
      const folds: Record<string, Record<string, boolean>> = {};
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (!key?.startsWith(LEGACY_COLLAPSE_KEY)) continue;
        stale.push(key);
        const state = JSON.parse(localStorage.getItem(key) ?? '{}');
        if (state && typeof state === 'object' && Object.keys(state).length) {
          folds[key.slice(LEGACY_COLLAPSE_KEY.length)] = state;
        }
      }
      if (!this.me.prefs.collapsed && Object.keys(folds).length) changes.collapsed = folds;
    } catch {
      return; // no storage: nothing to hand over
    }
    if (Object.keys(changes).length) await this.setPrefs(changes);
    for (const key of stale) store(key, null);
  }

  /** The list quick add files into in a wide scope: remembered per view. */
  quickAddList(viewKey: string, scope: Scope): string | null {
    if (scope.kind === 'list') return scope.id;
    let remembered: string | null = null;
    try {
      remembered = localStorage.getItem(QA_KEY + viewKey);
    } catch {
      remembered = null;
    }
    const candidates = this.sortedLists.filter(
      (l) => scope.kind === 'everything' || l.section_id === scope.id,
    );
    if (remembered && candidates.some((l) => l.id === remembered)) return remembered;
    const ordered = this.sections.flatMap((s) => candidates.filter((l) => l.section_id === s.id));
    return ordered[0]?.id ?? candidates[0]?.id ?? null;
  }

  rememberQuickAddList(viewKey: string, listId: string) {
    store(QA_KEY + viewKey, listId);
  }

  // --- AI add switch (this device) --------------------------------------------------------

  /** AI add is on for this device (default on); the switch shows only when `aiReady`. */
  aiAddOn = $state(readAiAdd());

  /** The server has AI add set up (`GET /me` `ai_add`): the configured model fills tasks in. */
  get aiReady(): boolean {
    return !!this.me?.ai_add;
  }

  setAiAdd(on: boolean) {
    this.aiAddOn = on;
    try {
      localStorage.setItem(AI_ADD_KEY, on ? '1' : '0');
    } catch {
      /* storage unavailable: the choice lasts this page */
    }
  }
}
