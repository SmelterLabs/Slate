// The Agents page's data: the roster (GET /agents?kind=all) and a live feed of what agents did
// (seeded by GET /events?tail=true&actor_kind=agent, then fed by the board's one SSE stream
// through `onEvent`). Presence loads with the board; the feed loads when Agents opens.

import { get } from './api';
import { connections } from './connection-data.svelte';
import type { AgentEntry, AgentState, SlateEvent } from './types';

export const FEED_MAX = 200;
export const ONLINE_MS = 2 * 60_000;

export const STATE_LABEL: Record<AgentState, string> = {
  working: 'Working',
  blocked: 'Agent blocked',
  review: 'Ready for review',
};

/** Online: an open live stream, or a request or event in the last two minutes. */
export function isOnline(a: AgentEntry, now: Date): boolean {
  if (a.live_streams > 0) return true;
  const seen = [a.last_seen_at, a.last_event_at].filter(Boolean).map((t) => new Date(t!).getTime());
  return seen.length > 0 && now.getTime() - Math.max(...seen) <= ONLINE_MS;
}

// Events that are bookkeeping noise in a feed of "what agents did".
const QUIET = new Set(['item.due_soon', 'item.due', 'item.overdue', 'recurrence.advanced']);

/** A report with a note also posts the note as a comment, right after the `item.reported`
 * event. The feed already says what the report says, so it drops that echo. */
export function isReportEcho(ev: SlateEvent, prev: SlateEvent | undefined): boolean {
  return (
    ev.type === 'item.commented' &&
    prev?.type === 'item.reported' &&
    prev.actor_id === ev.actor_id &&
    prev.item_id === ev.item_id &&
    Math.abs(new Date(ev.at).getTime() - new Date(prev.at).getTime()) < 2000
  );
}

/** Oldest-first events to a newest-first feed, without noise or report echoes. */
export function toFeed(events: SlateEvent[]): SlateEvent[] {
  const out: SlateEvent[] = [];
  let prev: SlateEvent | undefined;
  for (const ev of events) {
    if (!QUIET.has(ev.type) && !isReportEcho(ev, prev)) out.push(ev);
    prev = ev;
  }
  return out.reverse();
}

class Agents {
  roster = $state<AgentEntry[]>([]);
  /** Newest first. */
  feed = $state<SlateEvent[]>([]);
  loaded = $state(false);
  /** Presence is also used on cards, before the Agents page/feed is opened. */
  rosterAt = $state<number | null>(null);
  #rosterGeneration = 0;
  #scopeGeneration = 0;
  #watchRoster = false;
  error = $state<string | null>(null);
  #reload: ReturnType<typeof setTimeout> | undefined;
  #last: SlateEvent | undefined; // the previous stream event, for isReportEcho

  async load() {
    const generation = ++this.#rosterGeneration;
    const scope = this.#scopeGeneration;
    this.#watchRoster = true;
    try {
      const [roster, tail] = await Promise.all([
        get<AgentEntry[]>('/agents?kind=all'),
        get<{ events: SlateEvent[] }>('/events?tail=true&limit=100&actor_kind=agent'),
      ]);
      if (scope !== this.#scopeGeneration) return;
      if (generation === this.#rosterGeneration) {
        this.roster = roster;
        this.rosterAt = Date.now();
      }
      this.feed = toFeed(tail.events);
      this.loaded = true;
      this.error = null;
    } catch (e) {
      if (scope !== this.#scopeGeneration) return;
      this.error = e instanceof Error ? e.message : String(e);
    }
  }

  get agents(): AgentEntry[] {
    return this.roster.filter((r) => r.account.kind === 'agent' && r.account.handle !== 'slate');
  }

  get people(): AgentEntry[] {
    return this.roster.filter((r) => r.account.kind === 'person');
  }

  entry(accountId: string): AgentEntry | undefined {
    return this.roster.find((r) => r.account.id === accountId);
  }

  /** Every stream event passes through here (from the board store). */
  onEvent(ev: SlateEvent) {
    connections.onEvent(ev);
    if (!this.#watchRoster) return;
    const prev = this.#last;
    this.#last = ev;
    const who = this.entry(ev.actor_id);
    if (who) {
      who.last_event_at = ev.at;
      who.events_24h += 1;
      if (this.loaded && who.account.kind === 'agent' && !QUIET.has(ev.type) && !isReportEcho(ev, prev)) {
        this.feed = [ev, ...this.feed].slice(0, FEED_MAX);
      }
    }
    // Reports move "working on"; new accounts join the roster. Re-read, coalesced.
    if (ev.type === 'item.reported' || ev.type === 'item.run_requested' || ev.type.startsWith('account.') || !who) {
      clearTimeout(this.#reload);
      this.#reload = setTimeout(() => void this.refreshRoster(), 300);
    }
  }

  resetRoster() {
    connections.reset();
    this.#rosterGeneration++;
    this.#scopeGeneration++;
    this.#watchRoster = false;
    clearTimeout(this.#reload);
    this.roster = [];
    this.rosterAt = null;
    this.loaded = false;
    this.feed = [];
    this.#last = undefined;
  }

  async refreshRoster() {
    this.#watchRoster = true;
    const generation = ++this.#rosterGeneration;
    try {
      const roster = await get<AgentEntry[]>('/agents?kind=all');
      if (generation !== this.#rosterGeneration) return;
      this.roster = roster;
      this.rosterAt = Date.now();
    } catch {
      if (generation === this.#rosterGeneration) this.rosterAt = null;
      // the next event or a page visit tries again
    }
  }
}

export const agents = new Agents();
