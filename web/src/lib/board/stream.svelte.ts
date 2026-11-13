// Signing in and out, the board's lifecycle and the live stream. Data flow: open the SSE
// stream first, buffer what it sends, load everything over REST, then apply the buffer
// and go live. `lastEventId` is the cursor for every reconnect, so a dropped connection
// replays exactly what it missed (docs/live-stream.md). Each event goes through the pure
// reducer (lib/reducer.ts); what it cannot supply is fetched again here.

import { flushSync } from 'svelte';
import { setWorkspaceTimezone } from '../timezone';
import { ApiError, api, configure, get, post } from '../api';
import { agents } from '../agents.svelte';
import { affectsMyAccess } from '../permissions';
import { applyEvent } from '../reducer';
import { dotsChangedBy } from '../commentdots';
import { applyTheme, asTheme } from '../theme';
import type { Me, Prefs, SlateEvent } from '../types';
import { COOKIE_SESSION, inviteSecretFrom } from '../collaboration';
import { TOKEN_KEY } from './state.svelte';
import { BoardLoad } from './load.svelte';
import { EVENT_TYPES } from './events';

export class BoardStream extends BoardLoad {
  #dotsTimer: ReturnType<typeof setTimeout> | undefined;
  #source: EventSource | null = null;
  #buffer: SlateEvent[] | null = null;
  #backoff = 1000;
  #retryTimer: ReturnType<typeof setTimeout> | undefined;
  #refetchQueue = new Set<string>();
  #refetchTimer: ReturnType<typeof setTimeout> | undefined;
  #clock: ReturnType<typeof setInterval> | undefined;
  #viewsTimer: ReturnType<typeof setTimeout> | undefined;
  #favoritesTimer: ReturnType<typeof setTimeout> | undefined;
  #capacityTimer: ReturnType<typeof setTimeout> | undefined;
  #ruleQueue = new Set<string>();
  #ruleTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    super();
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
      void this.adoptLocalPrefs();
      if (this.viewId) this.rememberView(this.selection, this.viewId);
      await this.loadAll();
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
    // The buffer is gone but the cursor already moved past what it held (and a pull to refresh
    // closed the old stream before it): with no cursor, the retry is always a full start.
    this.lastEventId = '';
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

  #reloadTimer: ReturnType<typeof setTimeout> | undefined;

  /** Reload everything soon: one load for a burst of events that each ask for it. */
  #scheduleReload() {
    clearTimeout(this.#reloadTimer);
    this.#reloadTimer = setTimeout(() => {
      this.#reloadTimer = undefined;
      this.loadAll().catch((e) => this.flash(String(e)));
    }, 100);
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
      this.me.prefs = this.takePrefs((ev.after ?? {}) as Prefs);
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
      this.sync(ev.item_id);
      // Subtask counts live on the parent.
      const snap = (ev.after ?? ev.before) as { parent_id?: string } | null;
      this.sync(parentBefore ?? this.items[ev.item_id]?.parent_id ?? snap?.parent_id);
    }
    for (const id of fx.refetch) this.#refetchQueue.add(id);
    if (fx.refetch.length) {
      clearTimeout(this.#refetchTimer);
      this.#refetchTimer = setTimeout(() => void this.#flushRefetch(), 50);
    }
    if (fx.views) {
      clearTimeout(this.#viewsTimer);
      this.#viewsTimer = setTimeout(() => void this.loadViews(), 50);
    }
    if (fx.tags && this.tagManagerOpen) void this.loadTags();
    // Favorites are per account: only this board's own changes reload the bar.
    if (fx.favorites && ev.actor_id === this.me?.account.id) {
      clearTimeout(this.#favoritesTimer);
      this.#favoritesTimer = setTimeout(() => void this.loadFavorites(), 50);
    }
    if (fx.capacities) {
      clearTimeout(this.#capacityTimer);
      this.#capacityTimer = setTimeout(() => void this.loadCapacities(), 80);
    }
    if (fx.reload) this.#scheduleReload();
    else if (fx.structure) void this.loadStructure();
    // Someone joined or left a project: its names and its agents (the Ask default) changed.
    if (ev.type === 'section.participants_changed') {
      clearTimeout(this.#participantsTimer);
      this.#participantsTimer = setTimeout(() => void this.loadAccounts(), 100);
    }
    if (ev.type.startsWith('recurrence.') && ev.subject_id) {
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
      await this.loadAll();
    } catch {
      /* the next event or reconnect retries */
    }
  }

  async #flushRefetch() {
    const ids = [...this.#refetchQueue];
    this.#refetchQueue.clear();
    await this.refetchItems(ids);
  }
}
