// Structure and editors: sections, lists and their statuses, recurrence rules, tags,
// capacity and the favorites bar.

import { api, get, patch, post, put } from '../api';
import { moveBefore } from '../favorites';
import { place, reorder, type StatusDraft, placeSection } from '../structure';
import type {
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
} from '../types';
import { BoardRun } from './run.svelte';

export class BoardEditors extends BoardRun {
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

  // --- recurrence rules ------------------------------------------------------------------

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

  // --- tags ------------------------------------------------------------------------------

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

  // --- capacity --------------------------------------------------------------------------

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

  // --- favorites bar ---------------------------------------------------------------------

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
}
