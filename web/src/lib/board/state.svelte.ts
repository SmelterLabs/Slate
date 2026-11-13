// The board's data: every `$state` field the components read, the plain mirror of the
// items, and the reads derived from them (counts, "needs you", who can be picked, what
// this token may do). The other layers in this folder add behaviour on top; `board` in
// lib/store.svelte.ts is the one instance of the whole chain.
//
// Lists of many items (the views engine, counts) read `plainItems` instead of `items`:
// plain copies kept in step by `sync`, which every write to `items` must call (proxy
// reads cost ~10x at 3,000 items).

import { workspaceTimezone } from '../timezone';
import { SvelteSet } from 'svelte/reactivity';
import { hiddenAsFinished, isOpen } from '../format';
import { attention, byList, type Attention, type Need } from '../attention';
import { workspaceDay } from '../quickadd';
import { workingItems } from '../pickup';
import { listLevel } from '../permissions';
import type { CommentDot } from '../commentdots';
import type {
  Level,
  Account,
  Favorite,
  Item,
  ItemDetail,
  List,
  Me,
  Recurrence,
  SavedView,
  Section,
  Capacity,
  Tag,
} from '../types';
import { dayOf, type Scope, type ViewConfig } from '../views';

export const TOKEN_KEY = 'slate.token';

/** Where the board is looking: everything, one section or one list (a view's scope). */
export type Selection = Scope;
export type Conn = 'connecting' | 'live' | 'reconnecting';

/** The view in use: a saved one, or the scope's built-in default before anything is saved. */
export interface ActiveView {
  /** Saved view id, or `default:<scope>`. Drafts are kept under this key. */
  key: string;
  saved: SavedView | null;
  name: string;
  config: ViewConfig;
  dirty: boolean;
}

export function scopeKey(s: Scope): string {
  return s.kind === 'everything' ? 'everything' : `${s.kind}:${s.id}`;
}

export class BoardState {
  token = $state<string | null>(localStorage.getItem(TOKEN_KEY));
  me = $state<Me | null>(null);
  ready = $state(false);
  sections = $state<Section[]>([]);
  lists = $state<Record<string, List>>({});
  items = $state<Record<string, Item>>({});
  accounts = $state<Record<string, Account>>({});
  recurrences = $state<Record<string, Recurrence>>({});
  detail = $state<ItemDetail | null>(null);
  openId = $state<string | null>(null);
  conn = $state<Conn>('connecting');
  /** The browser's own idea of the network (navigator.onLine), for the offline banner. */
  online = $state(typeof navigator === 'undefined' ? true : navigator.onLine !== false);
  lastEventId = $state('');
  now = $state(new Date());
  selection = $state<Selection>({ kind: 'everything' });
  /** The view picked in the URL; null means the scope's first view. */
  viewId = $state<string | null>(null);
  views = $state<Record<string, SavedView>>({});
  /** Your pinned lists, sections, views and items, in bar order. */
  favorites = $state<Favorite[]>([]);
  /** Capacity per account id (GET /capacities, defaults included): the Agents page. */
  capacities = $state<Record<string, Capacity>>({});
  /** Toolbar search: narrows whatever view is showing, never saved. */
  q = $state('');
  selected = new SvelteSet<string>();
  /** The card under the pointer or keyboard focus: `x` selects it. */
  focusId = $state<string | null>(null);
  /** Bumped whenever an item changes: what readers of `plainItems` depend on. */
  rev = $state(0);
  /** Run requests observed in this tab. Due tasks can also show waiting on a fresh load;
   * neither is proof of runner receipt. Reports supersede requests. */
  runRequested = $state<Record<string, number>>({});
  /** Comment dots for this account: item id -> agent | human | mention (lib/commentdots.ts). */
  commentDots = $state<Record<string, CommentDot>>({});
  workingByAgent = $derived(workingItems(this.plainItems, this.now));
  toast = $state<string | null>(null);
  toastAction = $state<{ label: string; run: () => void } | null>(null);

  // A plain (non-proxy) copy of every item, kept in step with `items` by #sync. The views engine
  // and the cards read these: reading 3,000 items through Svelte proxies costs ~10x more.
  #plain = new Map<string, Item>();
  #toastTimer: ReturnType<typeof setTimeout> | undefined;

  // --- derived views ------------------------------------------------------------------------

  get sortedLists(): List[] {
    return Object.values(this.lists).sort((a, b) => a.position - b.position);
  }

  listsOf(sectionId: string): List[] {
    return this.sortedLists.filter((l) => l.section_id === sectionId);
  }

  /** Every item as a plain object; reactive through `rev`. Never mutate these. */
  get plainItems(): Item[] {
    void this.rev;
    return [...this.#plain.values()];
  }

  /** Re-copy one item (or drop it) after any change to `items`. */
  protected sync(id: string | null | undefined) {
    if (!id) return;
    const it = this.items[id];
    if (it) this.#plain.set(id, $state.snapshot(it) as Item);
    else this.#plain.delete(id);
    this.rev++;
  }

  /** After a full load: `fresh` are the loaded items, never handed to `$state` (the store got
   * its own copy), so they are the plain mirror as they are. Snapshotting 20,000 proxies one by
   * one took about two seconds. */
  protected syncAll(fresh: Item[]) {
    this.#plain = new Map(fresh.map((i) => [i.id, i]));
    this.rev++;
  }

  /** Top-level items on a list's board: not archived, not finished before today. */
  boardItems(listId: string): Item[] {
    return this.plainItems
      .filter(
        (i) =>
          i.list_id === listId && !i.parent_id && !i.archived_at && !hiddenAsFinished(i, this.now),
      )
      .sort((a, b) => a.position - b.position || a.created_at.localeCompare(b.created_at));
  }

  // The sidebar's counts, from one pass over the items per change (not one per list per
  // render: 50 lists x 20,000 items on every live event).
  #tallyKey = '';
  #open = new Map<string, number>();
  #overdue = 0;
  #today = 0;

  #tally() {
    void this.rev;
    const now = this.now;
    const key = `${this.rev}|${now.getTime()}|${workspaceTimezone()}|${Object.keys(this.lists).length}`;
    if (key === this.#tallyKey) return;
    this.#tallyKey = key;
    const today = workspaceDay(now);
    const nowIso = now.toISOString(); // stored instants are UTC ISO text: compare as text
    const open = new Map<string, number>();
    let overdue = 0;
    let due = 0;
    for (const i of this.#plain.values()) {
      if (i.archived_at || !isOpen(i)) continue;
      if (!i.parent_id) open.set(i.list_id, (open.get(i.list_id) ?? 0) + 1);
      if (i.due_at && i.due_at < nowIso) overdue++; // isOverdue() without a Date per item
      // Everything's badge: open, in a live list, due by the end of today (workspace time). What waits
      // on me is Needs you's count (My Work's tile, the pill, the title and the app icon), not this.
      const live = this.lists[i.list_id] && !this.lists[i.list_id].archived_at;
      if (live && i.due_at && dayOf(i.due_at) <= today) due++;
    }
    this.#open = open;
    this.#overdue = overdue;
    this.#today = due;
  }

  openCount(listId: string): number {
    this.#tally();
    return this.#open.get(listId) ?? 0;
  }

  get overdueCount(): number {
    this.#tally();
    return this.#overdue;
  }

  /** Overdue and due today, for Everything's badge in the sidebar. */
  get todayCount(): number {
    this.#tally();
    return this.#today;
  }

  #attnKey = '';
  #attn: Attention[] = [];
  #attnByList = new Map<string, { n: number; top: Need }>();

  /** Open cards that wait on the viewer (lib/attention.ts): the "Needs you" badge and panels. */
  get needsMe(): Attention[] {
    void this.rev;
    const me = this.me?.account.id ?? '';
    const key = `${this.rev}|${me}|${Object.keys(this.accounts).length}`;
    if (key !== this.#attnKey) {
      this.#attnKey = key;
      this.#attn = attention([...this.#plain.values()], me, this.accounts);
      this.#attnByList = byList(this.#attn);
    }
    return this.#attn;
  }

  /** The sidebar's per-list "needs you" marker, or undefined. */
  needsIn(listId: string): { n: number; top: Need } | undefined {
    void this.needsMe;
    return this.#attnByList.get(listId);
  }

  /** Accounts a person can pick to assign, mention or hand work to: not disabled, and not
   * `slate`, the account Slate's own scheduler acts as. */
  get pickable(): Account[] {
    // A guest never reaches the workspace's own agents, only a project's guest agents:
    // the server refuses an Ask, an assignment or Run now for them (service/guests.py).
    const guest = this.me?.account.role === 'guest';
    return Object.values(this.accounts).filter(
      (a) => !a.disabled_at && a.handle !== 'slate' && !(guest && a.kind === 'agent' && a.role !== 'guest'),
    );
  }

  accountName(id: string): string | undefined {
    return this.accounts[id]?.display_name;
  }

  /** Agents shared on each private section (a project), from its participants. */
  sectionAgents = $state<Record<string, Account[]>>({});

  /** Handles of a card's project agents, the project's own (guest) agents first. Empty off a
   * private section. The Ask picker starts on the first one (conversation.defaultAsk). */
  projectAgents(listId: string | undefined): string[] {
    const sid = listId ? this.lists[listId]?.section_id : undefined;
    const shared = (sid && this.sectionAgents[sid]) || [];
    return [...shared]
      .filter((a) => !this.accounts[a.id]?.disabled_at)
      .sort((a, b) => Number(a.role !== 'guest') - Number(b.role !== 'guest'))
      .map((a) => a.handle);
  }

  /** This token's level on a list (null: cannot see it). The server enforces it regardless. */
  levelOn(listId: string | null | undefined): Level | null {
    return listLevel(this.me?.access, listId);
  }

  /** Integrations are for the whole workspace: guests and list-limited tokens
   * have none of them (the server's `Access.general`). */
  get canWorkspace(): boolean {
    return this.me?.access?.general ?? true;
  }

  /** Owner or admin with an unlimited token: Members page, sharing, private lists. */
  get canManage(): boolean {
    return !this.me?.access || this.me.access.manage;
  }

  /** Keep an item from the server (and name its assignees). */
  protected storeItem(item: Item) {
    this.items[item.id] = item;
    this.sync(item.id);
    for (const a of item.assignees) this.accounts[a.id] ??= a;
  }

  // --- toast and refused writes ---------------------------------------------------------

  flash(message: string, action: { label: string; run: () => void } | null = null) {
    this.toast = message;
    this.toastAction = action;
    clearTimeout(this.#toastTimer);
    this.#toastTimer = setTimeout(() => {
      this.toast = null;
      this.toastAction = null;
    }, action ? 8000 : 5000);
  }

  /** Run a write; on refusal show Slate's reason and return null. */
  protected async attempt<T>(write: () => Promise<T>): Promise<T | null> {
    try {
      return await write();
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  // --- editor and panel state -----------------------------------------------------------

  tags = $state<Tag[]>([]);
  /** Tags by name, for pill colours. */
  tagMap: Record<string, Tag> = $derived(Object.fromEntries(this.tags.map((t) => [t.name, t])));
  archivedSections = $state<Section[]>([]);
  archivedLists = $state<List[]>([]);
  listSettingsId = $state<string | null>(null);
  /** The sharing dialog's target (owners and admins). */
  shareTarget = $state<{ kind: 'section' | 'list'; id: string } | null>(null);
  tagManagerOpen = $state(false);
}
