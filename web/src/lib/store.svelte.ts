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
  LinkKind,
  List,
  Recurrence,
  RecurTrigger,
  RulePreview,
  Section,
  Capacity,
  Status,
  Tag,
} from './types';
import { ApiError, api, get, patch, post, put } from './api';
import { describeRrule } from './format';
import { type QuickAdd, workspaceDay } from './quickadd';
import { moveBefore } from './favorites';
import { agents } from './agents.svelte';
import { pickupSince, pickupStatus } from './pickup';
import { changeLabel, groupReverts, revertPatch } from './undo';
import { LEAVES } from './words';
import { place, reorder, type StatusDraft, placeSection } from './structure';
import { dayOf, isClosed, onDay, quickDays, reopenStatus, statusOfKind, type Drop } from './views';
import { BoardStream } from './board/stream.svelte';

export { scopeKey, type ActiveView, type Conn, type Selection } from './board/state.svelte';

class Board extends BoardStream {

















  // --- writes -------------------------------------------------------------------------------


  async update(id: string, changes: Record<string, unknown>): Promise<boolean> {
    const pre = this.#pre.get(id) ?? (this.items[id] ? ($state.snapshot(this.items[id]) as Item) : null);
    this.#pre.delete(id);
    try {
      this.storeItem(await patch<Item>(`/items/${id}`, changes));
      const back = pre && revertPatch(pre, changes);
      if (back) this.#remember(changeLabel(changes), () => this.#revert([[id, back]]));
      if (this.openId === id) void this.refreshDetail();
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  /** Drag to a column: show it moved at once, put it back if Slate refuses. */
  async moveToStatus(id: string, status: Status) {
    const item = this.items[id];
    if (!item || item.status?.id === status.id) return;
    const before = item.status;
    this.#stash(id);
    item.status = { ...status };
    this.sync(id);
    const ok = await this.update(id, { status: status.id });
    if (!ok && this.items[id]) {
      this.items[id].status = before;
      this.sync(id);
    }
  }

  /**
   * Quick add: exactly one POST /items; the card itself arrives via the stream. `extra` carries
   * what the column it was typed into implies (its due day, status, priority…); what the text
   * says wins over it.
   */
  async quickAdd(listId: string, q: QuickAdd, extra: Record<string, unknown> = {}): Promise<boolean> {
    const body: Record<string, unknown> = { ...extra, list_id: listId, title: q.title };
    if (q.tags.length) body.tags = q.tags;
    if (q.assignees.length) body.assignees = q.assignees;
    if (q.priority) body.priority = q.priority;
    if (q.due) body.due_at = q.due;
    if (q.start) body.start_at = q.start;
    try {
      if (q.rrule) {
        // "every monday": a recurring rule, whose first instance arrives via the stream.
        const rule: Record<string, unknown> = {
          list_id: listId,
          title_template: q.title,
          rrule: q.rrule,
          dtstart: q.due ?? workspaceDay(new Date()),
        };
        for (const k of ['tags', 'assignees', 'priority']) if (body[k]) rule[k] = body[k];
        await post('/recurrences', rule);
        this.flash(`Added: repeats ${describeRrule(q.rrule)}`);
        return true;
      }
      const made = await post<Item>('/items', body);
      this.#remember(`Added “${made.title}”`, async () => {
        await api('DELETE', `/items/${made.id}`);
        delete this.items[made.id];
        this.sync(made.id);
      });
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }


  /**
   * AI add (src/slate/service/aiadd.py): one POST /items/ai with the words as typed and what the column
   * implies. The card arrives via the stream at once; the model fills in its fields a
   * moment later, also via the stream.
   */
  async aiAdd(listId: string, text: string, extra: Record<string, unknown> = {}): Promise<boolean> {
    if (!this.aiReady || !text.trim()) return false;
    try {
      const made = await post<Item>('/items/ai', { list_id: listId, text: text.trim(), defaults: extra });
      this.#remember(`Added “${made.title}”`, async () => {
        await api('DELETE', `/items/${made.id}`);
        delete this.items[made.id];
        this.sync(made.id);
      });
      this.flash('Added. Filling in the details…');
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  async addSubtask(parentId: string, title: string) {
    try {
      await post<Item>('/items', { parent_id: parentId, title });
      await this.refreshDetail();
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async comment(itemId: string, body: string): Promise<boolean> {
    try {
      await post(`/items/${itemId}/comments`, { body_md: body });
      await this.refreshDetail();
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  async archive(itemId: string) {
    try {
      const item = await post<Item>(`/items/${itemId}/archive`);
      this.storeItem(item);
      this.close();
      this.#remember(
        `Put away “${item.title}”`,
        async () => {
          this.storeItem(await post<Item>(`/items/${itemId}/unarchive`));
        },
        true,
        LEAVES.putAway,
      );
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** Move to the trash (with its subtasks); the toast's Undo restores it. */
  async trash(itemId: string) {
    try {
      const item = await post<Item>(`/items/${itemId}/trash`);
      this.storeItem(item);
      if (this.openId === itemId) this.close();
      this.#remember(
        `Moved “${item.title}” to the trash`,
        async () => {
          this.storeItem(await post<Item>(`/items/${itemId}/restore`));
        },
        true,
        LEAVES.trash,
      );
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** Move many to the trash in one call (bulk `trash`); the toast's Undo restores them all. */
  async trashMany(ids: string[]): Promise<boolean> {
    const send = async (trash: boolean) => {
      for (let i = 0; i < ids.length; i += 500) {
        const out = await post<Item[]>('/items/bulk', { ids: ids.slice(i, i + 500), changes: { trash } });
        for (const item of out) this.storeItem(item);
      }
    };
    try {
      await send(true);
      if (this.openId && ids.includes(this.openId)) this.close();
      const n = ids.length;
      this.#remember(`Moved ${n} task${n === 1 ? '' : 's'} to the trash`, () => send(false), true, LEAVES.trash);
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  /** Take an item out of the trash. Returns it (null when refused, with a toast). */
  async restore(itemId: string): Promise<Item | null> {
    const item = await this.attempt(() => post<Item>(`/items/${itemId}/restore`));
    if (item) {
      this.storeItem(item);
      if (this.openId === itemId) await this.refreshDetail();
    }
    return item ?? null;
  }

  // --- undo -----------------------------------------------------------------------------------
  // Each successful change the board makes pushes how to put it back. Ctrl/Cmd+Z pops the last;
  // a toast's Undo button runs (and removes) its own entry. A revert is not itself remembered.

  #undo: { label: string; run: () => Promise<unknown> }[] = [];
  #undoing = false;
  /** The item as it was before an optimistic local edit, so the revert sees the old values. */
  #pre = new Map<string, Item>();
  /** How many changes can be undone (shown in the shortcuts sheet and the palette). */
  undoDepth = $state(0);

  #stash(id: string) {
    const item = this.items[id];
    if (item && !this.#pre.has(id)) this.#pre.set(id, $state.snapshot(item) as Item);
  }

  /** `say`: what the change does, one sentence after the label in the toast (lib/words.ts
   * LEAVES); the undo history keeps the bare label. */
  #remember(label: string, run: () => Promise<unknown>, toast = false, say = '') {
    if (this.#undoing) return;
    const entry = { label, run };
    this.#undo.push(entry);
    if (this.#undo.length > 50) this.#undo.shift();
    this.undoDepth = this.#undo.length;
    if (toast) {
      this.flash(say ? `${label}. ${say}` : label, {
        label: 'Undo',
        run: () => {
          const i = this.#undo.indexOf(entry);
          if (i >= 0) {
            this.#undo.splice(i, 1);
            this.undoDepth = this.#undo.length;
            void this.#run(entry);
          }
        },
      });
    }
  }

  async #run(entry: { label: string; run: () => Promise<unknown> }) {
    this.#undoing = true;
    try {
      await entry.run();
      this.flash(`Undone: ${entry.label}`);
    } catch (e) {
      this.flash(`Could not undo: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      this.#undoing = false;
    }
  }

  async #revert(backs: [string, Record<string, unknown>][]) {
    for (const g of groupReverts(backs)) {
      const out = await post<Item[]>('/items/bulk', { ids: g.ids, changes: g.changes });
      for (const item of out) this.storeItem(item);
    }
    if (this.openId && backs.some(([id]) => id === this.openId)) await this.refreshDetail();
  }

  /** Ctrl/Cmd+Z: put back the last change made from this board. */
  async undoLast() {
    const entry = this.#undo.pop();
    this.undoDepth = this.#undo.length;
    if (!entry) return this.flash('Nothing to undo');
    await this.#run(entry);
  }

  // --- views, drops and bulk ----------------------------------------------------------------


  // --- board prefs (theme, folded columns): on the account, the same on every device -------


  /** Show a change at once; put the old values back if Slate refuses it. */
  async #optimistic(id: string, local: Partial<Item>, send: () => Promise<boolean>) {
    const item = this.items[id];
    if (!item) return false;
    const before = Object.fromEntries(
      Object.keys(local).map((k) => [k, $state.snapshot((item as unknown as Record<string, unknown>)[k])]),
    ) as Partial<Item>;
    this.#stash(id);
    Object.assign(item, local);
    this.sync(id);
    const ok = await send();
    this.#pre.delete(id);
    if (!ok && this.items[id]) {
      Object.assign(this.items[id], before);
      this.sync(id);
    }
    return ok;
  }

  /** Manual order after a drag (lib/slots.ts): one PATCH for one write, else one atomic batch.
   * Shown at once; put back if Slate refuses. */
  async reposition(moves: { id: string; position: number }[]) {
    if (!moves.length) return true;
    if (moves.length === 1) {
      const [m] = moves;
      return this.#optimistic(m.id, { position: m.position }, () => this.update(m.id, { position: m.position }));
    }
    const before = moves.map((m) => [m.id, this.items[m.id]?.position] as const);
    for (const m of moves) {
      if (!this.items[m.id]) continue;
      this.items[m.id].position = m.position;
      this.sync(m.id);
    }
    try {
      for (let i = 0; i < moves.length; i += 100) {
        const ops = moves.slice(i, i + 100).map((m) => ({
          tool: 'item',
          action: 'update',
          params: { id: m.id, position: m.position },
        }));
        await post('/batch', { ops });
      }
      return true;
    } catch (e) {
      for (const [id, pos] of before) {
        if (!this.items[id] || pos === undefined) continue;
        this.items[id].position = pos;
        this.sync(id);
      }
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  /** Drop a card on a group: what that means depends on what the view groups by. */
  /** `from` is the key of the group the card was dragged out of (a tag column swaps that tag). */
  async applyDrop(id: string, drop: Drop, from?: string): Promise<unknown> {
    const item = this.items[id];
    if (!item || !drop) return;
    switch (drop.kind) {
      case 'status':
        return this.moveToStatus(id, drop.status);
      case 'status_kind': {
        if (item.status?.kind === drop.value) return;
        const status = statusOfKind(this.lists[item.list_id], drop.value);
        if (!status) {
          return this.flash(`${this.lists[item.list_id]?.name ?? 'That list'} has no ${drop.value} status`);
        }
        return this.moveToStatus(id, status);
      }
      case 'due':
        return this.reschedule(id, drop.day);
      case 'priority':
        if (item.priority === drop.value) return;
        await this.#optimistic(id, { priority: drop.value }, () => this.update(id, { priority: drop.value }));
        return;
      case 'assignee': {
        const handles = drop.handle ? [drop.handle] : [];
        if (item.assignees.map((a) => a.handle).join() === handles.join()) return;
        const who = Object.values(this.accounts).filter((a) => handles.includes(a.handle));
        await this.#optimistic(id, { assignees: who }, () => this.update(id, { assignees: handles }));
        return;
      }
      case 'list': {
        if (item.list_id === drop.listId) return;
        await this.#optimistic(id, { list_id: drop.listId }, () => this.bulk([id], { list_id: drop.listId }));
        return;
      }
      case 'tag': {
        const tags = item.tags.filter((t) => t !== from && t !== drop.tag);
        if (drop.tag) tags.push(drop.tag);
        if (tags.join() === item.tags.join()) return;
        await this.#optimistic(id, { tags }, () => this.update(id, { tags }));
        return;
      }
      case 'section': {
        if (this.lists[item.list_id]?.section_id === drop.sectionId) return;
        const lists = this.listsOf(drop.sectionId);
        const name = this.sections.find((s) => s.id === drop.sectionId)?.name ?? 'That section';
        if (lists.length !== 1) {
          return this.flash(
            lists.length
              ? `${name} has ${lists.length} lists. Group by List to pick which one.`
              : `${name} has no list to move it into.`,
          );
        }
        return this.applyDrop(id, { kind: 'list', listId: lists[0].id });
      }
      case 'done':
        if (item.status?.kind !== 'done' && item.status?.kind !== 'cancelled') await this.toggleComplete(id);
        return;
    }
  }

  /** What a new card typed into a column gets from that column (ClickUp's "+ Add Task"). */
  columnDefaults(drop: Drop, listId: string): Record<string, unknown> {
    if (!drop) return {};
    switch (drop.kind) {
      case 'status':
        return this.lists[listId]?.statuses.some((s) => s.id === drop.status.id) ? { status: drop.status.id } : {};
      case 'status_kind': {
        const status = statusOfKind(this.lists[listId], drop.value);
        return status ? { status: status.id } : {};
      }
      case 'due':
        return drop.day ? { due_at: drop.day } : {}; // a bare date: date-only
      case 'priority':
        return drop.value ? { priority: drop.value } : {};
      case 'assignee':
        return drop.handle ? { assignees: [drop.handle] } : {};
      case 'tag':
        return drop.tag ? { tags: [drop.tag] } : {};
      default:
        return {};
    }
  }


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

  /** Move to a workspace day keeping the time of day, or keeping it date-only; null clears. */
  async reschedule(id: string, day: string | null) {
    const item = this.items[id];
    if (!item) return;
    const dateOnly = !item.due_at || !!item.due_date_only;
    const due = day ? onDay(day, dateOnly ? null : item.due_at) : null;
    if (due === item.due_at) return;
    const wire = day && dateOnly ? day : due; // a bare date stays date-only on the server
    await this.#optimistic(id, { due_at: due, due_date_only: !!day && dateOnly }, () =>
      this.update(id, { due_at: wire }),
    );
  }

  /** Swipe left on a row: due tomorrow (keeping its time of day), with Undo. */
  async snooze(id: string) {
    const item = this.items[id];
    if (!item) return;
    const day = quickDays(this.now).tomorrow;
    if (item.due_at && dayOf(item.due_at) === day) return this.flash(`“${item.title}” is already due tomorrow`);
    await this.reschedule(id, day);
    const now = this.items[id]?.due_at;
    if (now && dayOf(now) === day) {
      this.flash(`Snoozed “${item.title}” to tomorrow`, { label: 'Undo', run: () => void this.undoLast() });
    }
  }

  async setPriority(id: string, priority: Item['priority']) {
    await this.#optimistic(id, { priority }, () => this.update(id, { priority }));
  }

  /** The circle on a card: to the list's first done status, or back again. */
  async toggleComplete(id: string) {
    const item = this.items[id];
    const list = item && this.lists[item.list_id];
    if (!item || !list) return;
    const closed = item.status?.kind === 'done' || item.status?.kind === 'cancelled';
    const target = closed ? reopenStatus(list) : statusOfKind(list, 'done');
    if (!target) return this.flash(`${list.name} has no ${closed ? 'open' : 'done'} status`);
    const before = item.status;
    await this.moveToStatus(id, target);
    if (!closed && before && this.items[id]?.status?.id === target.id) {
      this.flash(`Completed “${item.title}”. ${LEAVES.complete}`, {
        label: 'Undo',
        run: () => void this.moveToStatus(id, before),
      });
    }
  }

  /** POST /items/bulk in chunks of 500 (the server's cap). */
  async bulk(ids: string[], changes: Record<string, unknown>): Promise<boolean> {
    const backs: [string, Record<string, unknown>][] = [];
    for (const id of ids) {
      const pre = this.#pre.get(id) ?? (this.items[id] ? ($state.snapshot(this.items[id]) as Item) : null);
      const back = pre && revertPatch(pre, changes);
      if (back) backs.push([id, back]);
    }
    try {
      for (let i = 0; i < ids.length; i += 500) {
        const out = await post<Item[]>('/items/bulk', { ids: ids.slice(i, i + 500), changes });
        for (const item of out) this.storeItem(item);
      }
      if (backs.length === ids.length) {
        const label = changeLabel(changes, ids.length);
        const say = changes.archive === true ? LEAVES.putAway : '';
        this.#remember(label, () => this.#revert(backs), ids.length > 1 || 'list_id' in changes || !!say, say);
      }
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  /** Status kind across lists: one bulk call; the server gives each item its own list's status
   *  of that kind, all or nothing. Lists without that kind are left out up front. */
  async bulkKind(ids: string[], kind: Status['kind']) {
    const send: string[] = [];
    const missing = new Set<string>();
    for (const id of ids) {
      const it = this.items[id];
      if (!it) continue;
      if (statusOfKind(this.lists[it.list_id], kind)) send.push(id);
      else missing.add(this.lists[it.list_id]?.name ?? it.list_id);
    }
    if (send.length) await this.bulk(send, { status_kind: kind });
    if (missing.size) this.flash(`No ${kind} status in ${[...missing].join(', ')}; those were left alone`);
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


  /** Move to another list (keeps a status of the same kind there). */
  async moveItem(id: string, listId: string) {
    const pre = this.items[id] ? ($state.snapshot(this.items[id]) as Item) : null;
    const moved = await this.attempt(() =>
      post<Item[]>('/items/bulk', { ids: [id], changes: { list_id: listId } }),
    );
    if (moved) {
      for (const i of moved) this.storeItem(i);
      if (this.openId === id) await this.refreshDetail();
      if (pre) {
        const back: Record<string, unknown> = { list_id: pre.list_id };
        if (pre.status) back.status = pre.status.id;
        this.#remember(`Moved “${pre.title}” to ${this.lists[listId]?.name ?? 'another list'}`, () => this.#revert([[id, back]]), true);
      }
    }
  }

  async unarchive(id: string) {
    const item = await this.attempt(() => post<Item>(`/items/${id}/unarchive`));
    if (item) {
      this.storeItem(item);
      if (this.openId === id) await this.refreshDetail();
    }
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


  async setEstimate(itemId: string, minutes: number | null) {
    await this.#optimistic(itemId, { estimate_minutes: minutes }, () =>
      this.update(itemId, { estimate_minutes: minutes }),
    );
  }

  /** Add or remove a dependency, from `itemId`'s side. Both items are refreshed. */
  async link(itemId: string, kind: LinkKind, otherId: string, add = true) {
    const path = `/items/${itemId}/links`;
    const ok = await this.attempt(() =>
      add
        ? post(path, { kind, item_id: otherId })
        : api('DELETE', `${path}?${new URLSearchParams({ kind, other: otherId })}`),
    );
    if (ok === null) return;
    await this.refetchItems([itemId, otherId]);
    if (this.openId === itemId || this.openId === otherId) await this.refreshDetail();
  }

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
