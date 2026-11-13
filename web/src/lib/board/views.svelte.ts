// Saved views and their drafts: the view in use for the current scope, unsaved changes
// kept on the account until Save, and the saved views' own writes. Also the URL for a
// scope and view.

import { api, get, patch, post, authHeaders } from '../api';
import { MAX_LAST_VIEWS, MAX_VIEW_DRAFTS, applyDiff, draftDiff, withEntry } from '../lastview';
import type { Prefs, SavedView } from '../types';
import { defaultConfig, normalizeConfig, sameConfig, type Ctx, type Scope, type ViewConfig } from '../views';
import { scopeKey, type ActiveView } from './state.svelte';
import { BoardPrefs } from './prefs.svelte';

export class BoardViews extends BoardPrefs {
  protected async loadViews() {
    const views = await get<SavedView[]>('/views');
    this.views = Object.fromEntries(views.map((v) => [v.id, v]));
  }

  /** What the views engine needs from the store, as plain copies (it reads them per item). */
  get ctx(): Ctx {
    return $state.snapshot({
      now: this.now,
      lists: this.lists,
      sections: this.sections,
      accounts: this.accounts,
      me: this.me?.account ?? null,
      q: this.q,
    }) as Ctx;
  }

  /** Saved views of a scope, in tab order. */
  viewsFor(scope: Scope): SavedView[] {
    const id = scope.kind === 'everything' ? null : scope.id;
    return Object.values(this.views)
      .filter((v) => !v.archived_at && v.scope_kind === scope.kind && v.scope_id === id)
      .sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
  }

  get active(): ActiveView {
    const scope = this.selection;
    const saved = this.viewsFor(scope);
    // No view in the URL: the tab you last had open here, else the first.
    const wanted = this.viewId ?? this.prefs.last_view?.[scopeKey(scope)];
    const view = saved.find((v) => v.id === wanted) ?? saved[0] ?? null;
    const key = view ? view.id : `default:${scopeKey(scope)}`;
    const base = view ? normalizeConfig(view.config, scope) : defaultConfig(scope);
    const diff = this.prefs.view_drafts?.[key];
    const config = diff ? applyDiff(base, diff) : base;
    return {
      key,
      saved: view,
      name: view?.name ?? 'Default',
      config,
      dirty: !!diff && !sameConfig(config, base),
    };
  }

  /** Change the view in use. The change is a draft until Save, kept on the account meanwhile
   *  (prefs `view_drafts`), so a reload, coming back to the list or another device shows it. */
  setConfig(changes: Partial<ViewConfig>) {
    const a = this.active;
    const next = { ...$state.snapshot(a.config), ...changes } as ViewConfig;
    const base = a.saved ? normalizeConfig(a.saved.config, this.selection) : defaultConfig(this.selection);
    this.#setDraft(a.key, sameConfig(next, base) ? null : draftDiff(next, base));
  }

  revert() {
    this.#setDraft(this.active.key, null);
  }

  /** The view tab open in a scope, remembered for the next visit (prefs `last_view`). */
  rememberView(scope: Scope, viewId: string) {
    const key = scopeKey(scope);
    if (!this.me || this.prefs.last_view?.[key] === viewId) return;
    void this.setPrefs({ last_view: withEntry(this.prefs.last_view, key, viewId, MAX_LAST_VIEWS) });
  }

  // Unsaved view changes show at once and are saved shortly after the last one (a dragged
  // slider or typed filter is one write). While one is unsent, prefs arriving from the server
  // keep this device's `view_drafts`, and for a few seconds after a save only that save's own
  // echo is taken: a late echo of an older save must not undo a newer change.
  #draftsTimer: ReturnType<typeof setTimeout> | undefined;
  #draftsRev = 0;
  #draftsSentRev = 0;
  #draftsInFlight = 0;
  #draftsSent: { json: string; at: number } | null = null;

  #sendingDrafts(): Prefs['view_drafts'] | null {
    const drafts = $state.snapshot(this.prefs.view_drafts) ?? null;
    this.#draftsSentRev = this.#draftsRev;
    this.#draftsSent = { json: JSON.stringify(drafts), at: Date.now() };
    return drafts;
  }

  #setDraft(key: string, diff: Partial<ViewConfig> | null) {
    if (!this.me) return;
    const drafts = withEntry($state.snapshot(this.prefs.view_drafts), key, diff, MAX_VIEW_DRAFTS);
    if (drafts) this.me.prefs.view_drafts = drafts;
    else delete this.me.prefs.view_drafts;
    this.#draftsRev++;
    clearTimeout(this.#draftsTimer);
    this.#draftsTimer = setTimeout(() => void this.#saveDrafts(), 400);
  }

  async #saveDrafts() {
    clearTimeout(this.#draftsTimer);
    this.#draftsTimer = undefined;
    if (!this.me || this.#draftsSentRev === this.#draftsRev) return;
    const drafts = this.#sendingDrafts();
    this.#draftsInFlight++;
    try {
      await this.setPrefs({ view_drafts: drafts });
    } finally {
      this.#draftsInFlight--;
    }
  }

  /** Leaving or reloading the page: send an unsaved view change now, even as the page goes. */
  flushDrafts() {
    if (!this.me || this.#draftsSentRev === this.#draftsRev || !this.token) return;
    clearTimeout(this.#draftsTimer);
    const drafts = this.#sendingDrafts();
    try {
      void fetch('/api/v1/me/prefs', {
        method: 'PATCH',
        keepalive: true,
        headers: { 'Content-Type': 'application/json', ...authHeaders(this.token) },
        body: JSON.stringify({ view_drafts: drafts }),
      });
    } catch {
      /* best effort: the page is going away */
    }
  }

  /** Prefs from the server, keeping this device's view changes that are not saved yet. */
  protected takePrefs(incoming: Prefs): Prefs {
    if (!this.me) return incoming;
    const sent = this.#draftsSent;
    const settled =
      this.#draftsSentRev === this.#draftsRev &&
      !this.#draftsInFlight &&
      (!sent || Date.now() - sent.at > 3000 || JSON.stringify(incoming.view_drafts ?? null) === sent.json);
    if (settled) return incoming;
    const next = { ...incoming };
    const mine = $state.snapshot(this.me.prefs.view_drafts);
    if (mine) next.view_drafts = mine;
    else delete next.view_drafts;
    return next;
  }

  /** Overwrite the saved view with the draft (the built-in default is saved with saveAsNew). */
  async saveView(): Promise<void> {
    const a = this.active;
    if (!a.saved) return;
    try {
      const v = await patch<SavedView>(`/views/${a.saved.id}`, { config: $state.snapshot(a.config) });
      this.views[v.id] = v;
      this.#setDraft(a.key, null);
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async saveAsNew(name: string): Promise<SavedView | null> {
    const a = this.active;
    const s = this.selection;
    try {
      const v = await post<SavedView>('/views', {
        name,
        scope_kind: s.kind,
        scope_id: s.kind === 'everything' ? null : s.id,
        config: $state.snapshot(a.config),
      });
      this.views[v.id] = v;
      this.#setDraft(a.key, null);
      this.goToView(v.id);
      return v;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  async renameView(id: string, name: string) {
    try {
      this.views[id] = await patch<SavedView>(`/views/${id}`, { name });
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async duplicateView(id: string) {
    const v = this.views[id];
    if (!v) return;
    try {
      const copy = await post<SavedView>('/views', {
        name: `${v.name} copy`.slice(0, 100),
        scope_kind: v.scope_kind,
        scope_id: v.scope_id,
        config: $state.snapshot(applyDiff(normalizeConfig(v.config, this.selection), this.prefs.view_drafts?.[id])),
      });
      this.views[copy.id] = copy;
      this.goToView(copy.id);
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async deleteView(id: string) {
    try {
      this.views[id] = await post<SavedView>(`/views/${id}/archive`);
      if (this.prefs.view_drafts?.[id]) this.#setDraft(id, null);
      if (this.viewId === id) this.goToView(null);
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** URL for a scope and view: `#/everything`, `#/section/<id>/v/<view>`, `#/list/<id>`. */
  href(scope: Scope, viewId: string | null = null): string {
    const base = scope.kind === 'everything' ? '#/everything' : `#/${scope.kind}/${scope.id}`;
    return viewId ? `${base}/v/${viewId}` : base;
  }

  goToView(viewId: string | null) {
    location.hash = this.href(this.selection, viewId);
  }
}
