// Board preferences. Theme, folded columns, the last view per scope and unsaved view
// changes live on the account (`PATCH /me/prefs`), so every device shows the same; the
// quick-add list per view and the AI add switch are kept on this device.

import { patch } from '../api';
import { withStorage } from '../storage';
import { applyTheme, asTheme, type Theme } from '../theme';
import type { Prefs } from '../types';
import { alsoShowLists, alsoShowMatches, isClosed, type AlsoShowSetting, type Scope } from '../views';
import { BoardState } from './state.svelte';

const QA_KEY = 'slate.quickadd.';
const AI_ADD_KEY = 'slate.aiAdd';

/** AI add is on unless this device turned it off. */
function readAiAdd(): boolean {
  return withStorage((storage) => storage.getItem(AI_ADD_KEY) !== '0', true);
}
/** Where folded columns lived per browser before they moved onto the account. */
const LEGACY_COLLAPSE_KEY = 'slate.collapsed.';

export abstract class BoardPrefs extends BoardState {
  alsoShowSaving = $state(false);

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
    if (!this.me) return false;
    const next: Record<string, unknown> = { ...this.me.prefs };
    for (const [k, v] of Object.entries(changes)) {
      if (v === null) delete next[k];
      else next[k] = v;
    }
    this.me.prefs = next as Prefs;
    try {
      const saved = await patch<Prefs>('/me/prefs', changes);
      if (this.me) this.me.prefs = this.takePrefs(saved);
      return true;
    } catch (e) {
      this.flash(String(e));
      return false;
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

  alsoShowFor(listId: string): AlsoShowSetting {
    const saved = this.prefs.also_show?.[listId];
    if (!saved?.sections.length) return { sections: [], everyone: false };
    const eligible = new Set(alsoShowLists(listId, this.lists, this.sections).values());
    const sections = saved.sections.filter((id) => eligible.has(id));
    return { sections, everyone: sections.length > 0 && saved.everyone === true };
  }

  alsoShowOptions(listId: string) {
    const lists = alsoShowLists(listId, this.lists, this.sections);
    if (!lists.size) return [];
    const counts = new Map([...lists.values()].map((id) => [id, 0]));
    const me = this.me?.account ?? null;
    const saved = this.prefs.also_show?.[listId];
    const everyone = saved?.everyone === true && saved.sections.some((id) => counts.has(id));
    for (const item of this.plainItems) {
      const section = lists.get(item.list_id);
      // Top-level cards only: the board hides subtasks unless the view asks for them.
      if (section !== undefined && !item.archived_at && !item.parent_id && !isClosed(item) && alsoShowMatches(item, everyone, me)) {
        counts.set(section, counts.get(section)! + 1);
      }
    }
    return this.sections.flatMap((section) => {
      const count = counts.get(section.id);
      return count === undefined ? [] : [{ section, count }];
    });
  }

  async setAlsoShow(listId: string, setting: AlsoShowSetting) {
    if (!this.me || this.alsoShowSaving) return;
    const me = this.me;
    const before = me.prefs.also_show;
    const all = { ...before };
    if (setting.sections.length) all[listId] = setting;
    else delete all[listId];
    this.alsoShowSaving = true;
    const saved = await this.setPrefs({ also_show: Object.keys(all).length ? all : null });
    this.alsoShowSaving = false;
    if (!saved && this.me === me) {
      if (before) me.prefs.also_show = before;
      else delete me.prefs.also_show;
    }
  }

  /** Before prefs lived on the account, each browser kept its own theme and folds. Hand any it
   *  still has to the account once, where the account has none, then forget them here. */
  protected async adoptLocalPrefs() {
    const me = this.me;
    if (!me) return;
    const changes: Partial<Record<keyof Prefs, unknown>> = {};
    const stale: string[] = [];
    const read = withStorage((storage) => {
      const theme = storage.getItem('slate.theme');
      if (!me.prefs.theme && (theme === 'light' || theme === 'dark')) changes.theme = theme;
      const folds: Record<string, Record<string, boolean>> = {};
      for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i);
        if (!key?.startsWith(LEGACY_COLLAPSE_KEY)) continue;
        stale.push(key);
        const state = JSON.parse(storage.getItem(key) ?? '{}');
        if (state && typeof state === 'object' && Object.keys(state).length) {
          folds[key.slice(LEGACY_COLLAPSE_KEY.length)] = state;
        }
      }
      if (!me.prefs.collapsed && Object.keys(folds).length) changes.collapsed = folds;
      return true;
    }, false);
    if (!read) return; // no readable preferences to migrate
    if (Object.keys(changes).length) await this.setPrefs(changes);
    for (const key of stale) withStorage((storage) => storage.removeItem(key), undefined);
  }

  /** The list quick add files into in a wide scope: remembered per view. */
  quickAddList(viewKey: string, scope: Scope): string | null {
    if (scope.kind === 'list') return scope.id;
    const remembered = withStorage((storage) => storage.getItem(QA_KEY + viewKey), null);
    const candidates = this.sortedLists.filter(
      (l) => scope.kind === 'everything' || l.section_id === scope.id,
    );
    if (remembered && candidates.some((l) => l.id === remembered)) return remembered;
    const ordered = this.sections.flatMap((s) => candidates.filter((l) => l.section_id === s.id));
    return ordered[0]?.id ?? candidates[0]?.id ?? null;
  }

  rememberQuickAddList(viewKey: string, listId: string) {
    withStorage((storage) => storage.setItem(QA_KEY + viewKey, listId), undefined);
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
    withStorage((storage) => storage.setItem(AI_ADD_KEY, on ? '1' : '0'), undefined);
  }
}
