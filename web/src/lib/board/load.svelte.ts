// Loading and the caches: the full REST load behind the stream, the nav, the archived
// panel, tags, rules, capacities and favorites, refetching single items, and the open
// task's detail (the drawer) with its comment dots.

import { ApiError, get, post } from '../api';
import { agents } from '../agents.svelte';
import type { CommentDot } from '../commentdots';
import type {
  Account,
  Favorite,
  Item,
  ItemDetail,
  List,
  Recurrence,
  SavedView,
  Section,
  Capacity,
  Tag,
} from '../types';
import { BoardViews } from './views.svelte';

export class BoardLoad extends BoardViews {
  // Full reloads overlap (a burst of list.statuses_changed, a share change during a refresh):
  // only the newest one may write, so an older answer never overwrites a newer one.
  #loadGen = 0;

  protected async loadAll() {
    const gen = ++this.#loadGen;
    const [sections, lists, items, views, favorites] = await Promise.all([
      get<Section[]>('/sections'),
      get<List[]>('/lists'),
      get<Item[]>('/items'),
      get<SavedView[]>('/views'),
      get<Favorite[]>('/favorites'),
      agents.refreshRoster(),
    ]);
    if (gen !== this.#loadGen) return; // a newer load is answering instead
    this.views = Object.fromEntries(views.map((v) => [v.id, v]));
    this.favorites = favorites;
    this.sections = sections.sort((a, b) => a.position - b.position);
    this.lists = Object.fromEntries(lists.map((l) => [l.id, l]));
    this.items = Object.fromEntries(structuredClone(items).map((i) => [i.id, i]));
    this.syncAll(items);
    void this.loadTags(); // tag colours on chips
    void this.loadCommentDots(); // the dot in a card's corner
    void this.loadRules(); // repeat labels on cards
    void this.loadCapacities(); // the Agents page
    await this.loadAccounts();
    if (this.selection.kind === 'list' && !this.lists[this.selection.id]) {
      this.selection = { kind: 'everything' };
    }
  }

  protected async loadAccounts() {
    const known: Record<string, Account> = {};
    if (this.me) known[this.me.account.id] = this.me.account;
    if (this.me?.scopes.includes('admin')) {
      for (const a of await get<Account[]>('/accounts')) known[a.id] = a;
    }
    // The scoped roster is already loaded with the board. Guests cannot call /accounts;
    // shared agents still need names/handles for @picking and the chat turn indicator even
    // in a brand-new project with no assigned tasks or previous messages.
    for (const { account } of agents.roster) known[account.id] ??= account;
    for (const i of Object.values(this.items)) for (const a of i.assignees) known[a.id] ??= a;
    // A private section is a shared project: its participants are named on the board for
    // everyone in it (a guest's board would otherwise show an author it has no row for as
    // "Someone"), and its agents are who the Ask picker starts on (`projectAgents`).
    const agentsBySection: Record<string, Account[]> = {};
    await Promise.all(
      this.sections
        .filter((s) => s.private && !s.archived_at)
        .map(async (s) => {
          try {
            const p = await get<{ participants: Account[] }>(`/sections/${s.id}/project`);
            for (const a of p.participants) known[a.id] ??= a;
            agentsBySection[s.id] = p.participants.filter((a) => a.kind === 'agent');
          } catch {
            // not this account's project after all: nothing to name
          }
        }),
    );
    this.accounts = known;
    this.sectionAgents = agentsBySection;
  }

  protected async loadStructure() {
    const [sections, lists] = await Promise.all([
      get<Section[]>('/sections'),
      get<List[]>('/lists'),
    ]);
    this.sections = sections.sort((a, b) => a.position - b.position);
    this.lists = Object.fromEntries(lists.map((l) => [l.id, l]));
  }

  async reloadStructure() {
    await this.loadStructure();
    if (this.archivedSections.length || this.archivedLists.length) await this.loadArchived();
  }

  protected async reloadEverything() {
    await this.loadAll();
    await this.loadArchived();
  }

  /** Archived sections, and archived lists whose section is not archived (Archived panel). */
  async loadArchived() {
    const [sections, lists] = await Promise.all([
      get<Section[]>('/sections?include_archived=true'),
      get<List[]>('/lists?include_archived=true'),
    ]);
    this.archivedSections = sections.filter((s) => s.archived_at);
    const gone = new Set(this.archivedSections.map((s) => s.id));
    this.archivedLists = lists.filter((l) => l.archived_at && !gone.has(l.section_id));
  }

  /** Several items in one `GET /items?ids=` per 200 (the item cache answers it); an id that
   * comes back missing was deleted or trashed, as a fresh load would leave it out. */
  async refetchItems(ids: string[]) {
    for (let i = 0; i < ids.length; i += 200) {
      const chunk = ids.slice(i, i + 200);
      const q = new URLSearchParams({ ids: chunk.join(','), include_archived: 'true' });
      try {
        const got = await get<Item[]>(`/items?${q}`);
        const seen = new Set(got.map((it) => it.id));
        for (const it of got) this.storeItem(it);
        for (const id of chunk) {
          if (!seen.has(id) && this.items[id]) {
            delete this.items[id];
            this.sync(id);
          }
        }
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) this.flash(String(e));
      }
    }
  }

  async refetchItem(id: string) {
    try {
      const d = await get<ItemDetail>(`/items/${id}`);
      const { subtasks: _s, comments: _c, events: _e, checklists: _k, attachments: _a, watchers: _w, ...item } =
        d;
      this.storeItem(item);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        delete this.items[id];
        this.sync(id);
      }
    }
  }

  async loadTags() {
    try {
      this.tags = await get<Tag[]>('/tags');
    } catch {
      // Autocomplete is a nicety; typing a tag still works without it.
    }
  }

  async loadRules() {
    try {
      const rules = await get<Recurrence[]>('/recurrences');
      this.recurrences = Object.fromEntries(rules.map((r) => [r.id, r]));
    } catch {
      // labels fall back to "Recurring"
    }
  }

  async loadRule(id: string): Promise<Recurrence | null> {
    try {
      const r = await get<Recurrence>(`/recurrences/${id}`);
      this.recurrences[id] = r;
      return r;
    } catch {
      return null;
    }
  }

  async loadCapacities() {
    try {
      const list = await get<Capacity[]>('/capacities');
      this.capacities = Object.fromEntries(list.map((c) => [c.account_id, c]));
    } catch {
      /* a token that cannot see capacities gets the defaults on the page */
    }
  }

  protected async loadFavorites() {
    try {
      this.favorites = await get<Favorite[]>('/favorites');
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) this.flash(String(e));
    }
  }

  // --- drawer -------------------------------------------------------------------------------
  async open(id: string) {
    this.openId = id;
    this.detail = null;
    this.#markRead(id);
    await this.refreshDetail();
  }

  /** Which items have comments new to this account since it last opened them. A dot on the
   * card that is open right now is read at once: you are looking at the comment. */
  async loadCommentDots() {
    try {
      const dots = await get<Record<string, CommentDot>>('/me/comment-dots');
      const open = this.openId;
      if (open && dots[open]) {
        delete dots[open];
        this.#markRead(open);
      }
      this.commentDots = dots;
    } catch {
      /* the next comment or reload retries */
    }
  }

  /** Opening a card clears its dot (`POST /items/{id}/read`; a no-op when nothing is new). */
  #markRead(id: string) {
    if (id in this.commentDots) delete this.commentDots[id];
    post(`/items/${id}/read`).catch(() => {
      /* the dot comes back on the next load: harmless */
    });
  }

  close() {
    this.openId = null;
    this.detail = null;
  }

  async refreshDetail() {
    const id = this.openId;
    if (!id) return;
    try {
      const d = await get<ItemDetail>(`/items/${id}`);
      if (this.openId !== id) return;
      // The detail's `events` are only the last 20; the History tab loads the full log.
      this.detail = d;
      if (d.recurrence_id && !this.recurrences[d.recurrence_id]) {
        await this.loadRule(d.recurrence_id);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) this.close();
    }
  }
}
