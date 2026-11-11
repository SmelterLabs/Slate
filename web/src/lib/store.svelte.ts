// The board's single in-memory store. One instance (`board`), exported as an object whose
// fields are $state, so components read `board.items[id]` reactively (Svelte: exported state
// must not be reassigned, only its properties).
//
// Lists of many items (the views engine, counts) read `plainItems` instead: plain copies kept in
// step by #sync, which every write to `items` must call (proxy reads cost ~10x at 3,000 items).
//
// Data flow: open the SSE stream first, buffer what it sends, load everything over REST, then
// apply the buffer and go live. `lastEventId` is the cursor for every reconnect, so a dropped
// connection replays exactly what it missed (see docs/live-stream.md).

import { flushSync } from 'svelte';
import { setWorkspaceTimezone, workspaceTimezone } from './timezone';
import type { RunMode } from './types';
import { SvelteSet } from 'svelte/reactivity';
import { ApiError, api, configure, get, patch, post, put } from './api';
import { describeRrule, hiddenAsFinished, isOpen } from './format';
import { attention, byList, type Attention, type Need } from './attention';
import { type QuickAdd, workspaceDay } from './quickadd';
import { moveBefore } from './favorites';
import { MAX_LAST_VIEWS, MAX_VIEW_DRAFTS, applyDiff, draftDiff, withEntry } from './lastview';
import { agents } from './agents.svelte';
import { pickupSince, pickupStatus, workingItems } from './pickup';
import { affectsMyAccess, listLevel } from './permissions';
import { applyEvent } from './reducer';
import { type CommentDot, dotsChangedBy } from './commentdots';
import { changeLabel, groupReverts, revertPatch } from './undo';
import { LEAVES } from './words';
import { applyTheme, asTheme, type Theme } from './theme';
import { place, reorder, type StatusDraft } from './structure';
import type {
  Level,
  Account,
  Favorite,
  FavoriteKind,
  Item,
  ItemDetail,
  LinkKind,
  List,
  Me,
  Prefs,
  Recurrence,
  RecurTrigger,
  UpcomingRepeat,
  RulePreview,
  SavedView,
  Section,
  SlateEvent,
  Capacity,
  Status,
  Tag,
} from './types';
import {
  dayOf,
  defaultConfig,
  isClosed,
  normalizeConfig,
  onDay,
  quickDays,
  reopenStatus,
  sameConfig,
  statusOfKind,
  type Ctx,
  type Drop,
  type Scope,
  type ViewConfig,
} from './views';

import { COOKIE_SESSION, inviteSecretFrom } from './collaboration';
import { placeSection } from './structure';
import { authHeaders } from './api';

const TOKEN_KEY = 'slate.token';
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

function store(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: per-viewer conveniences only
  }
}

class Board {
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
  /** Capacity per account id (GET /capacities, defaults included): the Box view and the Agents page. */
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
  #dotsTimer: ReturnType<typeof setTimeout> | undefined;
  workingByAgent = $derived(workingItems(this.plainItems, this.now));
  toast = $state<string | null>(null);
  toastAction = $state<{ label: string; run: () => void } | null>(null);

  // A plain (non-proxy) copy of every item, kept in step with `items` by #sync. The views engine
  // and the cards read these: reading 3,000 items through Svelte proxies costs ~10x more.
  #plain = new Map<string, Item>();
  #source: EventSource | null = null;
  #buffer: SlateEvent[] | null = null;
  #backoff = 1000;
  #retryTimer: ReturnType<typeof setTimeout> | undefined;
  #refetchQueue = new Set<string>();
  #refetchTimer: ReturnType<typeof setTimeout> | undefined;
  #clock: ReturnType<typeof setInterval> | undefined;
  #toastTimer: ReturnType<typeof setTimeout> | undefined;
  #viewsTimer: ReturnType<typeof setTimeout> | undefined;
  #favoritesTimer: ReturnType<typeof setTimeout> | undefined;
  #capacityTimer: ReturnType<typeof setTimeout> | undefined;
  #ruleQueue = new Set<string>();
  #ruleTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    configure({ token: () => this.token, onUnauthorized: () => this.signOut() });
    // A phone dropping Wi-Fi often leaves the socket looking open; trust the browser instead.
    window.addEventListener('offline', () => {
      this.online = false;
      if (this.#source) this.#drop(this.#source);
    });
    window.addEventListener('online', () => {
      this.online = true;
      if (this.conn !== 'reconnecting' || !this.token) return;
      clearTimeout(this.#retryTimer);
      this.#backoff = 1000;
      void this.#reconnect();
    });
  }

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
  #sync(id: string | null | undefined) {
    if (!id) return;
    const it = this.items[id];
    if (it) this.#plain.set(id, $state.snapshot(it) as Item);
    else this.#plain.delete(id);
    this.rev++;
  }

  /** After a full load: `fresh` are the loaded items, never handed to `$state` (the store got
   * its own copy), so they are the plain mirror as they are. Snapshotting 20,000 proxies one by
   * one took about two seconds. */
  #syncAll(fresh: Item[]) {
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

  // --- auth ---------------------------------------------------------------------------------

  async signIn(token: string): Promise<void> {
    const secret = inviteSecretFrom(token);
    if (secret !== null) {
      // An invitation link (or its secret) pasted into the token box: the installed app has no
      // address bar, so this is how a guest signs it in. Cookie session, like the invite screen.
      const result = await api<{ section_id: string }>('POST', '/invitations/accept', { secret }, null);
      localStorage.setItem(TOKEN_KEY, COOKIE_SESSION); // marker only; the credential is HttpOnly
      location.hash = `#/section/${result.section_id}`;
      location.reload();
      return;
    }
    const me = await api<Me>('GET', '/me', undefined, token);
    setWorkspaceTimezone(me.timezone);
    localStorage.setItem(TOKEN_KEY, token);
    this.token = token;
    this.me = me; // App starts the board once it sees the token
  }

  async logout() {
    if (this.token === COOKIE_SESSION) {
      try { await post('/session/logout'); } catch (e) { this.flash(String(e)); return; }
    }
    this.signOut();
    location.reload(); // discard every scoped store and pending callback on account switch
  }

  #signOutListeners = new Set<() => void>();

  /** Invalidate scoped client caches synchronously, before identity is removed. No payload. */
  onSignOut(fn: () => void) {
    this.#signOutListeners.add(fn);
    return () => { this.#signOutListeners.delete(fn); };
  }

  signOut() {
    setWorkspaceTimezone();
    for (const fn of this.#signOutListeners) fn();
    localStorage.removeItem(TOKEN_KEY);
    this.token = null;
    this.me = null;
    this.ready = false;
    this.#stop();
    this.runRequested = {};
    agents.resetRoster();
  }

  // --- lifecycle ----------------------------------------------------------------------------

  // Each start() (first load, pull to refresh, a retry) takes a generation; #stop() bumps it,
  // so a start overtaken by a newer one, or by signOut, gives up at its next await.
  #startGen = 0;

  /** Stream first (buffering), then load, then apply the buffer: no gap, no reload loop.
   * The REST reads wait for the stream to be open, so an event committed between them and the
   * subscription cannot fall between the two. A failed start (any error but a 401, or the
   * stream dropping before the board is ready) retries with the reconnect backoff. */
  async start() {
    this.#stop();
    const gen = this.#startGen;
    this.#buffer = [];
    try {
      // Open first: once the stream is open the server holds its subscription, so everything
      // committed from here on reaches the buffer. A drop before that scheduled a retry.
      if (!(await this.#connect())) return;
      if (gen !== this.#startGen) return;
      const me = await get<Me>('/me');
      if (gen !== this.#startGen) return;
      this.me = me;
      setWorkspaceTimezone(me.timezone);
      applyTheme(asTheme(me.prefs?.theme));
      void this.#adoptLocalPrefs();
      if (this.viewId) this.rememberView(this.selection, this.viewId);
      await this.#loadAll();
      if (gen !== this.#startGen) return;
      const buffered = this.#buffer ?? [];
      this.#buffer = null;
      for (const ev of buffered) this.#apply(ev);
      this.ready = true;
      this.#backoff = 1000;
      this.#clock = setInterval(() => (this.now = new Date()), 30_000);
    } catch (e) {
      if (gen !== this.#startGen) return;
      if (e instanceof ApiError && e.status === 401) return; // signed out by the api layer
      this.flash(String(e));
      this.#retryStart();
    }
  }

  /** A start that did not get the board ready: drop what it had and try again, backing off
   * like a reconnect (1 s, 2 s, 4 s… capped at 30 s). The retry goes through #reconnect, so it
   * checks `/me` first: a refused token signs out instead of retrying forever (a stream the
   * server refuses never opens, so nothing else would notice the 401). signOut cancels it. */
  #retryStart() {
    this.#startGen++; // a start still in flight gives up at its next await
    this.#buffer = null;
    this.#source?.close();
    this.#source = null;
    this.conn = 'reconnecting';
    clearTimeout(this.#retryTimer);
    this.#retryTimer = setTimeout(() => void this.#reconnect(), this.#backoff);
    this.#backoff = Math.min(this.#backoff * 2, 30_000);
  }

  /** Pull to refresh: load everything again behind the live stream (the board stays up). */
  async refresh() {
    if (!this.token) return;
    await this.start();
    window.dispatchEvent(new CustomEvent('slate:refresh'));
  }

  #stop() {
    this.#startGen++;
    this.#source?.close();
    this.#source = null;
    clearTimeout(this.#retryTimer);
    clearTimeout(this.#reloadTimer);
    clearInterval(this.#clock);
    clearTimeout(this.#drainTimer);
    this.#drainTimer = undefined;
    this.#incoming = [];
    this.lastEventId = '';
  }

  // Full reloads overlap (a burst of list.statuses_changed, a share change during a refresh):
  // only the newest one may write, so an older answer never overwrites a newer one.
  #loadGen = 0;
  #reloadTimer: ReturnType<typeof setTimeout> | undefined;

  /** Reload everything soon: one load for a burst of events that each ask for it. */
  #scheduleReload() {
    clearTimeout(this.#reloadTimer);
    this.#reloadTimer = setTimeout(() => {
      this.#reloadTimer = undefined;
      this.#loadAll().catch((e) => this.flash(String(e)));
    }, 100);
  }

  async #loadAll() {
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
    this.#syncAll(items);
    void this.loadTags(); // tag colours on chips
    void this.loadCommentDots(); // the dot in a card's corner
    void this.loadRules(); // repeat labels on cards
    void this.loadCapacities(); // the Box view and the Agents page
    await this.#loadAccounts();
    if (this.selection.kind === 'list' && !this.lists[this.selection.id]) {
      this.selection = { kind: 'everything' };
    }
  }

  async #loadAccounts() {
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

  async #loadStructure() {
    const [sections, lists] = await Promise.all([
      get<Section[]>('/sections'),
      get<List[]>('/lists'),
    ]);
    this.sections = sections.sort((a, b) => a.position - b.position);
    this.lists = Object.fromEntries(lists.map((l) => [l.id, l]));
  }

  // --- live stream --------------------------------------------------------------------------

  /** Opens the stream. Resolves true once it is open (the server's response has started, so
   * its subscription is in place), false if it fails first: #drop has scheduled the retry. */
  #connect(): Promise<boolean> {
    if (!this.token) return Promise.resolve(false);
    const params = new URLSearchParams(this.token === COOKIE_SESSION ? {} : { token: this.token });
    if (this.lastEventId) params.set('since', this.lastEventId);
    const source = new EventSource(`/api/v1/events/stream?${params}`);
    this.#source = source;
    return new Promise<boolean>((resolve) => {
      source.onopen = () => {
        this.conn = 'live';
        // A reconnected board is back; a starting one has its REST load still to come, so its
        // backoff only resets once it is ready (a server that answers the stream but fails
        // the load must not be hit every second).
        if (this.ready) {
          this.#backoff = 1000;
          void agents.refreshRoster();
        }
        resolve(true);
      };
      source.onmessage = (m) => this.#receive(m);
      // Named SSE events (`event: item.updated`) do not reach onmessage; listen by type prefix
      // through a catch-all: EventSource has no wildcard, so the server's types are listed.
      for (const type of EVENT_TYPES) source.addEventListener(type, (m) => this.#receive(m));
      // Take over from EventSource's own retry: back off, and check the token first.
      source.onerror = () => {
        this.#drop(source);
        resolve(false);
      };
    });
  }

  #drop(source: EventSource) {
    source.close();
    if (this.#source !== source) return;
    this.#source = null;
    this.conn = 'reconnecting';
    // Before the board is ready there is no cursor worth resuming from: the load that was
    // under way no longer knows what it missed, so it starts over instead of reconnecting.
    if (!this.ready) return this.#retryStart();
    clearTimeout(this.#retryTimer);
    this.#retryTimer = setTimeout(() => void this.#reconnect(), this.#backoff);
    this.#backoff = Math.min(this.#backoff * 2, 30_000);
  }

  async #reconnect() {
    if (!this.token) return;
    try {
      const fresh = await get<Me>('/me'); // a 401 signs out instead of retrying forever
      setWorkspaceTimezone(fresh.timezone);
      if (this.token === COOKIE_SESSION && JSON.stringify(fresh.access) !== JSON.stringify(this.me?.access)) {
        location.reload(); // scopes changed while disconnected: discard every content/blob cache
        return;
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      this.#retryTimer = setTimeout(() => void this.#reconnect(), this.#backoff);
      this.#backoff = Math.min(this.#backoff * 2, 30_000);
      return;
    }
    if (!this.lastEventId || !this.ready) {
      // No cursor (nothing tells us what we missed), or the board never got ready (the
      // `online` event found a start waiting to retry): reload from scratch behind the stream.
      await this.start();
      return;
    }
    void this.#connect();
  }

  #receive(m: MessageEvent) {
    let ev: SlateEvent;
    try {
      ev = JSON.parse(m.data);
    } catch {
      return;
    }
    if (ev.id <= this.lastEventId) return;
    this.lastEventId = ev.id;
    if (this.#buffer) this.#buffer.push(ev);
    else {
      this.#incoming.push(ev);
      this.#scheduleDrain();
    }
  }

  // Live events are applied in batches. Every applied batch re-runs the views over every item
  // (about 100 ms at 20,000 items), so ten agents writing 40 changes a second would keep the
  // page busy forever if each event re-ran them. A lone event applies at once; while events
  // keep coming the board spends at most half its time catching up (docs/PERFORMANCE.md).
  #incoming: SlateEvent[] = [];
  #drainTimer: ReturnType<typeof setTimeout> | undefined;
  #drainCost = 0;
  #drainEnd = 0;

  #scheduleDrain() {
    if (this.#drainTimer !== undefined) return;
    const wait = Math.max(0, this.#drainEnd + this.#drainCost - performance.now());
    this.#drainTimer = setTimeout(() => this.#drain(), wait);
  }

  #drain() {
    this.#drainTimer = undefined;
    const batch = this.#incoming;
    this.#incoming = [];
    const start = performance.now();
    try {
      for (const ev of batch) this.#apply(ev);
      flushSync(); // render now, so the cost below includes the views and the DOM
    } finally {
      this.#drainEnd = performance.now();
      this.#drainCost = Math.min(500, this.#drainEnd - start);
      if (this.#incoming.length) this.#scheduleDrain();
    }
  }

  /** Other modules (the inbox) hear every event the board applies. Returns an unsubscribe. */
  #listeners = new Set<(ev: SlateEvent) => void>();
  onEvent(fn: (ev: SlateEvent) => void): () => void {
    this.#listeners.add(fn);
    return () => this.#listeners.delete(fn);
  }

  #apply(ev: SlateEvent) {
    for (const fn of this.#listeners) fn(ev);
    // This account's board prefs changed, here or on another device: show the same thing.
    if (ev.type === 'account.prefs_changed' && this.me && ev.subject_id === this.me.account.id) {
      this.me.prefs = this.#takePrefs((ev.after ?? {}) as Prefs);
      applyTheme(asTheme(this.me.prefs.theme));
      return;
    }
    // A share, role or privacy change about this account: what the board may show changed.
    if (affectsMyAccess(ev, this.me)) {
      if (this.token === COOKIE_SESSION && !ev.type.endsWith('.created')) {
        location.reload(); // discard docs, blobs, chat and details that may have lost access
        return;
      }
      clearTimeout(this.#accessTimer);
      this.#accessTimer = setTimeout(() => void this.#reloadAccess(), 100);
    }
    agents.onEvent(ev); // the Agents page's roster and live feed
    if (dotsChangedBy(ev, this.me?.account.id)) {
      clearTimeout(this.#dotsTimer);
      this.#dotsTimer = setTimeout(() => void this.loadCommentDots(), 150);
    }
    if (ev.item_id && ev.type === 'item.run_requested') this.runRequested[ev.item_id] = Date.parse(ev.at);
    if (ev.item_id && ev.type === 'item.reported' && ev.item_id in this.runRequested) {
      const state = (ev.after as { state?: string } | null)?.state;
      if (state && state !== 'idle') delete this.runRequested[ev.item_id];
    }
    const parentBefore = ev.item_id ? this.items[ev.item_id]?.parent_id : null;
    const fx = applyEvent(this, ev);
    if (ev.item_id) {
      this.#sync(ev.item_id);
      // Subtask counts live on the parent.
      const snap = (ev.after ?? ev.before) as { parent_id?: string } | null;
      this.#sync(parentBefore ?? this.items[ev.item_id]?.parent_id ?? snap?.parent_id);
    }
    for (const id of fx.refetch) this.#refetchQueue.add(id);
    if (fx.refetch.length) {
      clearTimeout(this.#refetchTimer);
      this.#refetchTimer = setTimeout(() => void this.#flushRefetch(), 50);
    }
    if (fx.views) {
      clearTimeout(this.#viewsTimer);
      this.#viewsTimer = setTimeout(() => void this.#loadViews(), 50);
    }
    if (fx.tags && this.tagManagerOpen) void this.loadTags();
    // Favorites are per account: only this board's own changes reload the bar.
    if (fx.favorites && ev.actor_id === this.me?.account.id) {
      clearTimeout(this.#favoritesTimer);
      this.#favoritesTimer = setTimeout(() => void this.#loadFavorites(), 50);
    }
    if (fx.capacities) {
      clearTimeout(this.#capacityTimer);
      this.#capacityTimer = setTimeout(() => void this.loadCapacities(), 80);
    }
    if (fx.reload) this.#scheduleReload();
    else if (fx.structure) void this.#loadStructure();
    // Someone joined or left a project: its names and its agents (the Ask default) changed.
    if (ev.type === 'section.participants_changed') {
      clearTimeout(this.#participantsTimer);
      this.#participantsTimer = setTimeout(() => void this.#loadAccounts(), 100);
    }
    if (ev.type.startsWith('recurrence.') && ev.subject_id) {
      this.recurRev++;
      this.#ruleQueue.add(ev.subject_id);
      clearTimeout(this.#ruleTimer);
      this.#ruleTimer = setTimeout(() => {
        const ids = [...this.#ruleQueue];
        this.#ruleQueue.clear();
        for (const id of ids) void this.loadRule(id);
      }, 100);
    }
    if (this.openId && ev.item_id) {
      const touched = this.items[ev.item_id];
      if (ev.item_id === this.openId || touched?.parent_id === this.openId) {
        void this.refreshDetail();
      }
    }
  }

  #accessTimer: ReturnType<typeof setTimeout> | undefined;
  #participantsTimer: ReturnType<typeof setTimeout> | undefined;

  async #reloadAccess() {
    try {
      const me = await get<Me>('/me');
      if (this.me) me.prefs = this.me.prefs;
      this.me = me;
      setWorkspaceTimezone(me.timezone);
      await this.#loadAll();
    } catch {
      /* the next event or reconnect retries */
    }
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

  async #flushRefetch() {
    const ids = [...this.#refetchQueue];
    this.#refetchQueue.clear();
    await this.refetchItems(ids);
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
        for (const it of got) this.#store(it);
        for (const id of chunk) {
          if (!seen.has(id) && this.items[id]) {
            delete this.items[id];
            this.#sync(id);
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
      this.#store(item);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        delete this.items[id];
        this.#sync(id);
      }
    }
  }

  #store(item: Item) {
    this.items[item.id] = item;
    this.#sync(item.id);
    for (const a of item.assignees) this.accounts[a.id] ??= a;
  }

  // --- writes -------------------------------------------------------------------------------

  flash(message: string, action: { label: string; run: () => void } | null = null) {
    this.toast = message;
    this.toastAction = action;
    clearTimeout(this.#toastTimer);
    this.#toastTimer = setTimeout(() => {
      this.toast = null;
      this.toastAction = null;
    }, action ? 8000 : 5000);
  }

  async update(id: string, changes: Record<string, unknown>): Promise<boolean> {
    const pre = this.#pre.get(id) ?? (this.items[id] ? ($state.snapshot(this.items[id]) as Item) : null);
    this.#pre.delete(id);
    try {
      this.#store(await patch<Item>(`/items/${id}`, changes));
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
    this.#sync(id);
    const ok = await this.update(id, { status: status.id });
    if (!ok && this.items[id]) {
      this.items[id].status = before;
      this.#sync(id);
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
        this.#sync(made.id);
      });
      return true;
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

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
        this.#sync(made.id);
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
      this.#store(item);
      this.close();
      this.#remember(
        `Put away “${item.title}”`,
        async () => {
          this.#store(await post<Item>(`/items/${itemId}/unarchive`));
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
      this.#store(item);
      if (this.openId === itemId) this.close();
      this.#remember(
        `Moved “${item.title}” to the trash`,
        async () => {
          this.#store(await post<Item>(`/items/${itemId}/restore`));
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
        for (const item of out) this.#store(item);
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
    const item = await this.#attempt(() => post<Item>(`/items/${itemId}/restore`));
    if (item) {
      this.#store(item);
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
      for (const item of out) this.#store(item);
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

  async #loadViews() {
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
  #takePrefs(incoming: Prefs): Prefs {
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

  // --- board prefs (theme, folded columns): on the account, the same on every device -------

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
      if (this.me) this.me.prefs = this.#takePrefs(saved);
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
  async #adoptLocalPrefs() {
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

  /** Show a change at once; put the old values back if Slate refuses it. */
  async #optimistic(id: string, local: Partial<Item>, send: () => Promise<boolean>) {
    const item = this.items[id];
    if (!item) return false;
    const before = Object.fromEntries(
      Object.keys(local).map((k) => [k, $state.snapshot((item as unknown as Record<string, unknown>)[k])]),
    ) as Partial<Item>;
    this.#stash(id);
    Object.assign(item, local);
    this.#sync(id);
    const ok = await send();
    this.#pre.delete(id);
    if (!ok && this.items[id]) {
      Object.assign(this.items[id], before);
      this.#sync(id);
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
      this.#sync(m.id);
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
        this.#sync(id);
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

  /** Move or resize a task's dates together (the Timeline's drag and edge handles). Passing a
   * key unchanged leaves it alone; null clears it. Optimistic, like reschedule. */
  async setDates(id: string, dates: { start_at?: string | null; due_at?: string | null }) {
    const item = this.items[id];
    if (!item) return;
    const changes: Record<string, string | null> = {};
    if ('start_at' in dates && dates.start_at !== item.start_at) changes.start_at = dates.start_at ?? null;
    if ('due_at' in dates && dates.due_at !== item.due_at) changes.due_at = dates.due_at ?? null;
    if (!Object.keys(changes).length) return;
    await this.#optimistic(id, changes as Partial<Item>, () => this.update(id, changes));
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
        for (const item of out) this.#store(item);
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

  async #loadFavorites() {
    try {
      this.favorites = await get<Favorite[]>('/favorites');
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) this.flash(String(e));
    }
  }

  favoriteOf(kind: FavoriteKind, targetId: string): Favorite | undefined {
    return this.favorites.find((f) => f.kind === kind && f.target_id === targetId);
  }

  /** Pin a target (at `position`, or last). Pinning twice is harmless. */
  async addFavorite(kind: FavoriteKind, targetId: string, position?: number) {
    if (this.favoriteOf(kind, targetId)) return;
    const fav = await this.#attempt(() =>
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

  // --- structure & editors ------------------------------------------------------------------
  // Every write here is one REST call; the stream then confirms it to every other board.
  // Structure writes also reload the nav at once, so this board does not wait for the echo.

  tags = $state<Tag[]>([]);
  /** Tags by name, for pill colours. */
  tagMap: Record<string, Tag> = $derived(Object.fromEntries(this.tags.map((t) => [t.name, t])));
  archivedSections = $state<Section[]>([]);
  archivedLists = $state<List[]>([]);
  listSettingsId = $state<string | null>(null);
  /** The sharing dialog's target (owners and admins). */
  shareTarget = $state<{ kind: 'section' | 'list'; id: string } | null>(null);

  /** Run a write; on refusal show Slate's reason and return null. */
  async #attempt<T>(write: () => Promise<T>): Promise<T | null> {
    try {
      return await write();
    } catch (e) {
      this.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  async reloadStructure() {
    await this.#loadStructure();
    if (this.archivedSections.length || this.archivedLists.length) await this.loadArchived();
  }

  async createSection(name: string, audience: Section['audience']): Promise<Section | null> {
    const s = await this.#attempt(() => post<Section>('/sections', { name, audience }));
    if (s) await this.reloadStructure();
    return s;
  }

  async updateSection(id: string, changes: Partial<Pick<Section, 'name' | 'audience'>>) {
    if (await this.#attempt(() => patch<Section>(`/sections/${id}`, changes))) {
      await this.reloadStructure();
    }
  }

  async placeSection(id: string, target: string, after: boolean) {
    const changes = placeSection(this.sections, id, target, after);
    if (!changes.length) return;
    await this.#attempt(() => post('/batch', { ops: changes.map(r => ({ tool: 'section', action: 'update', params: { id: r.id, position: r.position } })) }));
    await this.reloadStructure();
  }

  async moveSection(id: string, dir: -1 | 1) {
    const index = this.sections.findIndex(s => s.id === id);
    const target = this.sections[index + dir];
    if (index >= 0 && target) await this.placeSection(id, target.id, dir === 1);
  }

  async setSectionArchived(id: string, archived: boolean) {
    if (!(await this.#attempt(() => patch(`/sections/${id}`, { archived })))) return;
    // Restoring brings its lists' items back into the default queries: reload them.
    if (archived) await this.reloadStructure();
    else await this.#reloadEverything();
  }

  async createList(sectionId: string, name: string, kind: 'tasks' | 'drawer' = 'tasks'): Promise<List | null> {
    // Slate's default columns: To do · In progress · Done. A drawer holds documents instead.
    const l = await this.#attempt(() => post<List>('/lists', { section_id: sectionId, name, kind }));
    if (l) {
      this.lists[l.id] = l;
      await this.reloadStructure();
    }
    return l;
  }

  async renameList(id: string, name: string) {
    const l = await this.#attempt(() => patch<List>(`/lists/${id}`, { name }));
    if (l) this.lists[id] = l;
  }

  /** Convert between a task list and a drawer (to a drawer only while it holds no tasks). */
  async setListKind(id: string, kind: 'tasks' | 'drawer') {
    const l = await this.#attempt(() => patch<List>(`/lists/${id}`, { kind }));
    if (l) this.lists[id] = l;
    return l;
  }

  async moveList(id: string, dir: -1 | 1) {
    const list = this.lists[id];
    if (!list) return;
    for (const r of reorder(this.listsOf(list.section_id), id, dir)) {
      if (!(await this.#attempt(() => patch(`/lists/${r.id}`, { position: r.position })))) break;
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
      if (!(await this.#attempt(() => patch(`/lists/${rid}`, changes)))) break;
    }
    await this.reloadStructure();
  }

  async setListArchived(id: string, archived: boolean) {
    if (!(await this.#attempt(() => patch(`/lists/${id}`, { archived })))) return;
    if (archived) await this.reloadStructure();
    else await this.#reloadEverything();
  }

  async #reloadEverything() {
    await this.#loadAll();
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
    let saved = await this.#attempt(() => put<List>(`/lists/${listId}/statuses`, body));
    if (!saved) return false;
    const want = defaultName && saved.statuses.find((s) => s.name === defaultName.trim());
    if (want && want.id !== saved.default_status_id) {
      saved = (await this.#attempt(() =>
        patch<List>(`/lists/${listId}`, { default_status: want.id }),
      )) ?? saved;
    }
    this.lists[listId] = saved;
    return true;
  }

  async loadTags() {
    try {
      this.tags = await get<Tag[]>('/tags');
    } catch {
      // Autocomplete is a nicety; typing a tag still works without it.
    }
  }

  /** Move to another list (keeps a status of the same kind there). */
  async moveItem(id: string, listId: string) {
    const pre = this.items[id] ? ($state.snapshot(this.items[id]) as Item) : null;
    const moved = await this.#attempt(() =>
      post<Item[]>('/items/bulk', { ids: [id], changes: { list_id: listId } }),
    );
    if (moved) {
      for (const i of moved) this.#store(i);
      if (this.openId === id) await this.refreshDetail();
      if (pre) {
        const back: Record<string, unknown> = { list_id: pre.list_id };
        if (pre.status) back.status = pre.status.id;
        this.#remember(`Moved “${pre.title}” to ${this.lists[listId]?.name ?? 'another list'}`, () => this.#revert([[id, back]]), true);
      }
    }
  }

  async unarchive(id: string) {
    const item = await this.#attempt(() => post<Item>(`/items/${id}/unarchive`));
    if (item) {
      this.#store(item);
      if (this.openId === id) await this.refreshDetail();
    }
  }

  // Recurrence rules ---------------------------------------------------------------------------

  /** Bumped on every rule event: what the calendar's and timeline's future repeats follow. */
  recurRev = $state(0);

  async loadRules() {
    try {
      const rules = await get<Recurrence[]>('/recurrences');
      this.recurrences = Object.fromEntries(rules.map((r) => [r.id, r]));
    } catch {
      // labels fall back to "Recurring"
    }
  }

  /** Dates rules will fill between two workspace days that are not items yet. */
  async upcoming(start: string, end: string): Promise<UpcomingRepeat[]> {
    try {
      return await get<UpcomingRepeat[]>(`/recurrences/upcoming?${new URLSearchParams({ start, end })}`);
    } catch {
      return [];
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
    const rule = await this.#attempt(() =>
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
    const rule = await this.#attempt(() => patch<Recurrence>(`/recurrences/${id}`, changes));
    if (rule) this.recurrences[id] = rule;
    return !!rule;
  }

  async ruleAction(id: string, action: 'pause' | 'resume' | 'end') {
    const rule = await this.#attempt(() =>
      action === 'end'
        ? patch<Recurrence>(`/recurrences/${id}`, { ended: true })
        : post<Recurrence>(`/recurrences/${id}/${action}`),
    );
    if (rule) this.recurrences[id] = rule;
  }

  // --- ClickUp parity: dependencies, tags ----------------------------------------------------

  tagManagerOpen = $state(false);

  async setEstimate(itemId: string, minutes: number | null) {
    await this.#optimistic(itemId, { estimate_minutes: minutes }, () =>
      this.update(itemId, { estimate_minutes: minutes }),
    );
  }

  /** Add or remove a dependency, from `itemId`'s side. Both items are refreshed. */
  async link(itemId: string, kind: LinkKind, otherId: string, add = true) {
    const path = `/items/${itemId}/links`;
    const ok = await this.#attempt(() =>
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
    const t = await this.#attempt(() => patch<Tag>(`/tags/${encodeURIComponent(name)}`, changes));
    if (t) await this.loadTags();
    return !!t;
  }

  async createTag(name: string, color: string | null): Promise<boolean> {
    const t = await this.#attempt(() => post<Tag>('/tags', { name, color }));
    if (t) await this.loadTags();
    return !!t;
  }

  async mergeTag(name: string, into: string): Promise<boolean> {
    const t = await this.#attempt(() => post<Tag>(`/tags/${encodeURIComponent(name)}/merge`, { into }));
    if (t) await this.loadTags();
    return !!t;
  }

  async deleteTag(name: string): Promise<boolean> {
    const ok = await this.#attempt(() => api('DELETE', `/tags/${encodeURIComponent(name)}`).then(() => true));
    if (ok) await this.loadTags();
    return !!ok;
  }

  // --- capacity ---------------------------------------------------------------------

  async loadCapacities() {
    try {
      const list = await get<Capacity[]>('/capacities');
      this.capacities = Object.fromEntries(list.map((c) => [c.account_id, c]));
    } catch {
      /* a token that cannot see capacities gets the defaults on the page */
    }
  }

  /** Set some of an account's capacity fields (yours, or anyone's as an owner or admin). */
  async setCapacity(accountId: string, changes: Partial<Omit<Capacity, 'account_id' | 'custom'>>): Promise<boolean> {
    const out = await this.#attempt(() => patch<Capacity>(`/accounts/${accountId}/capacity`, changes));
    if (out) this.capacities[accountId] = out;
    return out !== null;
  }

  async resetCapacity(accountId: string): Promise<boolean> {
    const out = await this.#attempt(() => api<Capacity>('DELETE', `/accounts/${accountId}/capacity`));
    if (out) this.capacities[accountId] = out;
    return out !== null;
  }
}

// Every event type Slate emits (TECHNICAL-GUIDE.md "Event types"). EventSource dispatches
// named events only to listeners for that exact name.
export const EVENT_TYPES = [
  'agent.connection_created',
  'agent.connection_connected',
  'agent.connection_checked',
  'agent.connection_revoked',
  'item.created',
  'item.updated',
  'item.status_changed',
  'item.assigned',
  'item.archived',
  'item.unarchived',
  'item.split',
  'item.moved',
  'item.deleted',
  'item.trashed',
  'item.restored',
  'item.purged',
  'item.commented',
  'item.mentioned',
  'comment.edited',
  'comment.assigned',
  'comment.resolved',
  'comment.reacted',
  'comment.unreacted',
  'checklist.created',
  'checklist.updated',
  'checklist.deleted',
  'checklist.entry_added',
  'checklist.entry_updated',
  'checklist.entry_checked',
  'checklist.entry_unchecked',
  'checklist.entry_removed',
  'item.attachment_added',
  'item.attachment_removed',
  'item.watched',
  'item.read',
  'item.unwatched',
  'inbox.read',
  'inbox.cleared',
  'message.posted',
  'message.edited',
  'message.deleted',
  'message.file_uploaded',
  'message.file_discarded',
  'message.reacted',
  'message.unreacted',
  'message.task_created',
  'message.mentioned_agent',
  'message.agent_turn',
  'doc.mentioned_agent',
  'doc.agent_turn',
  'channel.read',
  'section.created',
  'section.participants_changed',
  'section.updated',
  'section.archived',
  'section.unarchived',
  'list.created',
  'list.updated',
  'list.archived',
  'list.unarchived',
  'list.statuses_changed',
  'tag.created',
  'tag.updated',
  'account.created',
  'account.updated',
  'account.disabled',
  'account.enabled',
  'account.prefs_changed',
  'account.role_changed',
  'share.granted',
  'share.changed',
  'share.revoked',
  'invitation.created',
  'invitation.accepted',
  'invitation.revoked',
  'session.created',
  'session.expiring',
  'session.extended',
  'token.created',
  'token.revoked',
  'recurrence.created',
  'recurrence.updated',
  'recurrence.paused',
  'recurrence.resumed',
  'recurrence.ended',
  'recurrence.spawned',
  'recurrence.skipped',
  'recurrence.rolled_forward',
  'recurrence.advanced',
  'recurrence.reopened',
  'item.due_soon',
  'item.due',
  'item.overdue',
  'webhook.created',
  'webhook.updated',
  'webhook.disabled',
  'webhook.enabled',
  'webhook.test',
  'view.created',
  'view.updated',
  'view.archived',
  'view.unarchived',
  'item.linked',
  'item.unlinked',
  'item.blocked',
  'item.unblocked',
  'tag.deleted',
  'favorite.added',
  'favorite.moved',
  'favorite.removed',
  'item.reported',
  'item.work_started',
  'item.work_finished',
  'item.run_requested',
  'automation.created',
  'automation.updated',
  'automation.deleted',
  'automation.secret_rotated',
  'item.reminder',
  'item.reminder_set',
  'item.reminder_removed',
  'account.digest',
  'health.alert',
  'health.recovered',
  'push.subscribed',
  'push.unsubscribed',
  'push.expired',
  'settings.changed',
  'doc.created',
  'doc.updated',
  'doc.moved',
  'doc.archived',
  'doc.unarchived',
  'doc.deleted',
  'doc.version_restored',
  'doc.file_added',
  'doc.file_removed',
  'doc.revision_added',
  'doc.commented',
  'doc.comment_edited',
  'doc.comment_resolved',
  'doc.comment_assigned',
  'doc.suggestion_accepted',
  'doc.text_extracted',
  'doc.revision_pinned',
  'doc.revision_unpinned',
  'doc.pinned_to_top',
  'doc.unpinned_from_top',
  'doc.dated',
  'doc.mentioned',
  'capacity.set',
  'capacity.reset',
];

export const board = new Board();
