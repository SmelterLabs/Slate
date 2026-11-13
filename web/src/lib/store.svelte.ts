// The board's single in-memory store. One instance (`board`), exported as an object whose
// fields are $state, so components read `board.items[id]` reactively (Svelte: exported state
// must not be reassigned, only its properties).
//
// The store is one class built in layers, one module each under lib/board/, each layer
// extending the one before it (docs/board.md, "The store"):
//
//   state      the $state fields, the plain mirror of the items, derived reads, the toast
//   prefs      board preferences on the account, and this device's quick-add list and AI add
//   views      saved views and their drafts, the URL for a scope and view
//   load       the REST load and the caches (tags, rules, favorites...), the open task's detail
//   stream     sign in and out, start and retry, the live stream and how each event applies
//   items      item writes: optimistic updates and rollbacks, undo, drops, bulk
//   run        a task's run control (Run now, Nudge, Copy brief) and its pickup state
//   editors    sections, lists, statuses, rules, tags, capacity, the favorites bar
//   selection  the multi-select set and the dialogs the board opens

import type {
  RunMode,
  Favorite,
  FavoriteKind,
  Item,
  List,
  Recurrence,
  RecurTrigger,
  RulePreview,
  Section,
  Capacity,
  Tag,
} from './types';
import { ApiError, api, get, patch, post, put } from './api';
import { moveBefore } from './favorites';
import { agents } from './agents.svelte';
import { pickupSince, pickupStatus } from './pickup';
import { place, reorder, type StatusDraft, placeSection } from './structure';
import { isClosed } from './views';
import { BoardItems } from './board/items.svelte';

export { scopeKey, type ActiveView, type Conn, type Selection } from './board/state.svelte';

class Board extends BoardItems {

















  // --- writes -------------------------------------------------------------------------------






  // --- views, drops and bulk ----------------------------------------------------------------


  // --- board prefs (theme, folded columns): on the account, the same on every device -------




  /** Can Run now be offered: an agent is assigned and the task is open. */
  canRun(item: Item): boolean {
    return !isClosed(item) && !item.archived_at && item.assignees.some((a) => a.kind === 'agent');
  }

  /** What the task's run control does, from its agents' `wake`: `run` when any agent has a
   * runner (push), `nudge` when they only poll from their own product (pull: its owner is told),
   * `brief` when a person carries the task to the agent's chat (manual: Copy brief). */
  runMode(item: Item): RunMode | null {
    if (!this.canRun(item)) return null;
    const wakes = item.assignees
      .filter((a) => a.kind === 'agent')
      .map((a) => this.accounts[a.id]?.wake ?? 'push');
    if (wakes.includes('push')) return 'run';
    return wakes.includes('pull') ? 'nudge' : 'brief';
  }

  /** The run control's words: Run now, Nudge or Copy brief. */
  runLabel(item: Item): string {
    const mode = this.runMode(item);
    return mode === 'nudge' ? 'Nudge' : mode === 'brief' ? 'Copy brief' : 'Run now';
  }

  /** Shared by the card and task window; derives from live board reports, not runner capacity. */
  pickup(item: Item) {
    if (this.runMode(item) !== 'run') return null;
    // A press this page saw, else the item's own stamp (so a wait survives a reload).
    const stamp = item.run_requested_at ? Date.parse(item.run_requested_at) : undefined;
    const since = pickupSince(item, this.runRequested[item.id] ?? stamp, this.now);
    if (since == null) return null;
    const ids = item.assignees
      .filter((a) => a.kind === 'agent' && (this.accounts[a.id]?.wake ?? 'push') === 'push')
      .map((a) => a.id);
    return pickupStatus(item, since, ids, this.workingByAgent, agents.roster, agents.rosterAt, this.now);
  }

  /** The names of the task's agents that no runner wakes (for the Nudge and brief text). */
  pulledAgents(item: Item): string {
    const names = item.assignees
      .filter((a) => a.kind === 'agent' && (this.accounts[a.id]?.wake ?? 'push') !== 'push')
      .map((a) => a.display_name);
    return names.join(' and ') || 'the agent';
  }

  /** The run control's action: Run now and Nudge record a run request; Copy brief copies the
   * task as a paste-ready prompt (GET /items/{id}/brief). */
  async runAction(item: Item) {
    const mode = this.runMode(item);
    if (mode === 'brief') return this.copyBrief(item);
    if (mode) return this.runNow(item.id);
  }

  async copyBrief(item: Item) {
    let text: string;
    try {
      text = (await get<{ text: string }>(`/items/${item.id}/brief`)).text;
    } catch (e) {
      this.flash(e instanceof ApiError ? e.message : 'Could not build the brief');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      this.flash(
        `Brief copied. Paste it into ${this.pulledAgents(item)}, then paste the answer back here as a comment.`,
      );
    } catch {
      this.flash(text);
    }
  }

  /** Ask the task's agents to start on it now (POST /items/{id}/run). */
  async runNow(id: string) {
    this.runRequested[id] = Date.now();
    try {
      await post(`/items/${id}/run`);
    } catch (e) {
      delete this.runRequested[id];
      this.flash(e instanceof ApiError ? e.message : 'Could not ask the agent to run it');
      return;
    }
    void agents.refreshRoster();
  }


  /** Visible item ids in screen order, set by the page: shift-click selects a range of it. */
  order: string[] = [];
  #anchor: string | null = null;

  toggleSelect(id: string, range = false) {
    const from = this.#anchor ? this.order.indexOf(this.#anchor) : -1;
    const to = this.order.indexOf(id);
    if (range && from >= 0 && to >= 0) {
      const [lo, hi] = from < to ? [from, to] : [to, from];
      for (const x of this.order.slice(lo, hi + 1)) this.selected.add(x);
    } else if (this.selected.has(id)) {
      this.selected.delete(id);
    } else {
      this.selected.add(id);
    }
    this.#anchor = id;
  }

  clearSelection() {
    this.selected.clear();
  }

  // --- favorites bar ------------------------------------------------------------------------


  favoriteOf(kind: FavoriteKind, targetId: string): Favorite | undefined {
    return this.favorites.find((f) => f.kind === kind && f.target_id === targetId);
  }

  /** Pin a target (at `position`, or last). Pinning twice is harmless. */
  async addFavorite(kind: FavoriteKind, targetId: string, position?: number) {
    if (this.favoriteOf(kind, targetId)) return;
    const fav = await this.attempt(() =>
      post<Favorite>('/favorites', { kind, target_id: targetId, position }),
    );
    if (fav && !this.favorites.some((f) => f.id === fav.id)) {
      const at = position ?? this.favorites.length;
      this.favorites.splice(Math.min(at, this.favorites.length), 0, fav);
    }
  }

  async removeFavorite(id: string) {
    const was = $state.snapshot(this.favorites) as Favorite[];
    this.favorites = this.favorites.filter((f) => f.id !== id);
    try {
      await api<void>('DELETE', `/favorites/${id}`);
    } catch (e) {
      this.favorites = was;
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async toggleFavorite(kind: FavoriteKind, targetId: string) {
    const fav = this.favoriteOf(kind, targetId);
    if (fav) await this.removeFavorite(fav.id);
    else await this.addFavorite(kind, targetId);
  }

  /** Drag on the bar: `id` lands before `beforeId` (null: last). Optimistic; reverts on error. */
  async moveFavorite(id: string, beforeId: string | null) {
    const was = $state.snapshot(this.favorites) as Favorite[];
    const order = moveBefore(
      was.map((f) => f.id),
      id,
      beforeId,
    );
    if (order.every((x, i) => x === was[i]?.id)) return;
    const byId = new Map(was.map((f) => [f.id, f]));
    this.favorites = order.map((x, i) => ({ ...byId.get(x)!, position: i }));
    try {
      this.favorites = await put<Favorite[]>('/favorites/order', { ids: order });
    } catch (e) {
      this.favorites = was;
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }


  // --- structure & editors ------------------------------------------------------------------
  // Every write here is one REST call; the stream then confirms it to every other board.
  // Structure writes also reload the nav at once, so this board does not wait for the echo.




  async createSection(name: string, audience: Section['audience']): Promise<Section | null> {
    const s = await this.attempt(() => post<Section>('/sections', { name, audience }));
    if (s) await this.reloadStructure();
    return s;
  }

  async updateSection(id: string, changes: Partial<Pick<Section, 'name' | 'audience'>>) {
    if (await this.attempt(() => patch<Section>(`/sections/${id}`, changes))) {
      await this.reloadStructure();
    }
  }

  async placeSection(id: string, target: string, after: boolean) {
    const changes = placeSection(this.sections, id, target, after);
    if (!changes.length) return;
    await this.attempt(() => post('/batch', { ops: changes.map(r => ({ tool: 'section', action: 'update', params: { id: r.id, position: r.position } })) }));
    await this.reloadStructure();
  }

  async moveSection(id: string, dir: -1 | 1) {
    const index = this.sections.findIndex(s => s.id === id);
    const target = this.sections[index + dir];
    if (index >= 0 && target) await this.placeSection(id, target.id, dir === 1);
  }

  async setSectionArchived(id: string, archived: boolean) {
    if (!(await this.attempt(() => patch(`/sections/${id}`, { archived })))) return;
    // Restoring brings its lists' items back into the default queries: reload them.
    if (archived) await this.reloadStructure();
    else await this.reloadEverything();
  }

  async createList(sectionId: string, name: string, kind: 'tasks' | 'drawer' = 'tasks'): Promise<List | null> {
    // Slate's default columns: To do · In progress · Done. A drawer holds documents instead.
    const l = await this.attempt(() => post<List>('/lists', { section_id: sectionId, name, kind }));
    if (l) {
      this.lists[l.id] = l;
      await this.reloadStructure();
    }
    return l;
  }

  async renameList(id: string, name: string) {
    const l = await this.attempt(() => patch<List>(`/lists/${id}`, { name }));
    if (l) this.lists[id] = l;
  }

  /** Convert between a task list and a drawer (to a drawer only while it holds no tasks). */
  async setListKind(id: string, kind: 'tasks' | 'drawer') {
    const l = await this.attempt(() => patch<List>(`/lists/${id}`, { kind }));
    if (l) this.lists[id] = l;
    return l;
  }

  async moveList(id: string, dir: -1 | 1) {
    const list = this.lists[id];
    if (!list) return;
    for (const r of reorder(this.listsOf(list.section_id), id, dir)) {
      if (!(await this.attempt(() => patch(`/lists/${r.id}`, { position: r.position })))) break;
    }
    await this.reloadStructure();
  }

  /** Drag or "Move to": put a list in `sectionId` before `beforeId` (or last). */
  async placeList(id: string, sectionId: string, beforeId: string | null) {
    const list = this.lists[id];
    if (!list) return;
    const target = this.sortedLists.filter((l) => l.section_id === sectionId);
    for (const p of place(target, id, list.section_id, sectionId, beforeId)) {
      const { id: rid, ...changes } = p;
      if (!(await this.attempt(() => patch(`/lists/${rid}`, changes)))) break;
    }
    await this.reloadStructure();
  }

  async setListArchived(id: string, archived: boolean) {
    if (!(await this.attempt(() => patch(`/lists/${id}`, { archived })))) return;
    if (archived) await this.reloadStructure();
    else await this.reloadEverything();
  }


  openShare(kind: 'section' | 'list', id: string) {
    this.shareTarget = { kind, id };
  }

  closeShare() {
    this.shareTarget = null;
  }

  openListSettings(listId: string) {
    this.listSettingsId = listId;
  }

  closeListSettings() {
    this.listSettingsId = null;
  }

  /** Replace a list's statuses; `fallback` takes items on removed ones. Then the default. */
  async saveStatuses(
    listId: string,
    draft: StatusDraft[],
    fallback: string | null,
    defaultName: string | null,
  ): Promise<boolean> {
    const statuses = draft.map((s) => ({
      ...(s.id ? { id: s.id } : {}),
      name: s.name.trim(),
      kind: s.kind,
      color: s.color,
    }));
    const body = fallback ? { statuses, fallback } : { statuses };
    let saved = await this.attempt(() => put<List>(`/lists/${listId}/statuses`, body));
    if (!saved) return false;
    const want = defaultName && saved.statuses.find((s) => s.name === defaultName.trim());
    if (want && want.id !== saved.default_status_id) {
      saved = (await this.attempt(() =>
        patch<List>(`/lists/${listId}`, { default_status: want.id }),
      )) ?? saved;
    }
    this.lists[listId] = saved;
    return true;
  }



  // Recurrence rules ---------------------------------------------------------------------------


  /** Try a rule without saving it. Throws ApiError with Slate's reason for a bad rule. */
  previewRule(
    rrule: string,
    dtstart: string | null,
    timezone: string,
    count = 6,
    opts: { trigger?: RecurTrigger; skip_weekends?: boolean } = {},
  ) {
    const q = new URLSearchParams({ rrule, timezone, count: String(count) });
    if (dtstart) q.set('dtstart', dtstart);
    if (opts.trigger) q.set('trigger', opts.trigger);
    if (opts.skip_weekends) q.set('skip_weekends', 'true');
    return get<RulePreview>(`/recurrences/preview?${q}`);
  }

  /** Make the item recurring: a rule from its fields; the item stays the current occurrence. */
  async createRule(item: Item, fields: Record<string, unknown>): Promise<Recurrence | null> {
    const rule = await this.attempt(() =>
      post<Recurrence>('/recurrences', {
        list_id: item.list_id,
        title_template: item.title,
        body_template_md: item.body_md || null,
        priority: item.priority,
        tags: item.tags,
        assignees: item.assignees.map((a) => a.handle),
        adopt_item_id: item.id,
        ...fields,
      }),
    );
    if (rule) {
      this.recurrences[rule.id] = rule;
      await this.refetchItem(item.id);
      if (this.openId === item.id) await this.refreshDetail();
    }
    return rule;
  }

  async updateRule(id: string, changes: Record<string, unknown>): Promise<boolean> {
    const rule = await this.attempt(() => patch<Recurrence>(`/recurrences/${id}`, changes));
    if (rule) this.recurrences[id] = rule;
    return !!rule;
  }

  async ruleAction(id: string, action: 'pause' | 'resume' | 'end') {
    const rule = await this.attempt(() =>
      action === 'end'
        ? patch<Recurrence>(`/recurrences/${id}`, { ended: true })
        : post<Recurrence>(`/recurrences/${id}/${action}`),
    );
    if (rule) this.recurrences[id] = rule;
  }

  // --- ClickUp parity: dependencies, tags ----------------------------------------------------



  openTagManager() {
    this.tagManagerOpen = true;
    void this.loadTags();
  }

  closeTagManager() {
    this.tagManagerOpen = false;
  }

  async updateTag(name: string, changes: { name?: string; color?: string | null }): Promise<boolean> {
    const t = await this.attempt(() => patch<Tag>(`/tags/${encodeURIComponent(name)}`, changes));
    if (t) await this.loadTags();
    return !!t;
  }

  async createTag(name: string, color: string | null): Promise<boolean> {
    const t = await this.attempt(() => post<Tag>('/tags', { name, color }));
    if (t) await this.loadTags();
    return !!t;
  }

  async mergeTag(name: string, into: string): Promise<boolean> {
    const t = await this.attempt(() => post<Tag>(`/tags/${encodeURIComponent(name)}/merge`, { into }));
    if (t) await this.loadTags();
    return !!t;
  }

  async deleteTag(name: string): Promise<boolean> {
    const ok = await this.attempt(() => api('DELETE', `/tags/${encodeURIComponent(name)}`).then(() => true));
    if (ok) await this.loadTags();
    return !!ok;
  }

  // --- capacity ---------------------------------------------------------------------


  /** Set some of an account's capacity fields (yours, or anyone's as an owner or admin). */
  async setCapacity(accountId: string, changes: Partial<Omit<Capacity, 'account_id' | 'custom'>>): Promise<boolean> {
    const out = await this.attempt(() => patch<Capacity>(`/accounts/${accountId}/capacity`, changes));
    if (out) this.capacities[accountId] = out;
    return out !== null;
  }

  async resetCapacity(accountId: string): Promise<boolean> {
    const out = await this.attempt(() => api<Capacity>('DELETE', `/accounts/${accountId}/capacity`));
    if (out) this.capacities[accountId] = out;
    return out !== null;
  }
}

export const board = new Board();
