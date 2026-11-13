// Item writes. Each one is a REST call; most show at once and put the old values back if
// Slate refuses (optimistic), and each successful one can be undone from this board.

import { api, patch, post } from '../api';
import { describeRrule } from '../format';
import { type QuickAdd, workspaceDay } from '../quickadd';
import { changeLabel, groupReverts, revertPatch } from '../undo';
import { LEAVES } from '../words';
import type { Item, LinkKind, List, Status } from '../types';
import { dayOf, onDay, quickDays, reopenStatus, statusOfKind, type Drop } from '../views';
import { BoardStream } from './stream.svelte';

export class BoardItems extends BoardStream {
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

  // --- optimistic edits, drops and bulk ---------------------------------------------------

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
}
