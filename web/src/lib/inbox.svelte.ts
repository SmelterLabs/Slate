// The notifications inbox on the board: entries from GET /inbox, the unread count for the nav
// badge, and read/clear. Live: any event from someone else may be news, so the inbox refetches
// (debounced) when one arrives; this account's own inbox.read / inbox.cleared (from another
// device too) refetch at once, so phone and PC always agree.

import { get, post } from './api';
import { chatHref } from './chat';
import { describe, type HistoryContext } from './history';
import { board } from './store.svelte';
import type { SlateEvent } from './types';

export type Reason =
  | 'chat'
  | 'mention'
  | 'reply'
  | 'assigned'
  | 'waiting'
  | 'failed'
  | 'stalled'
  | 'due'
  | 'unblocked'
  | 'nudge'
  | 'reminder'
  | 'digest'
  | 'health'
  | 'access'
  | 'comment'
  | 'status'
  | 'update'
  | 'archived'
  | 'moved'
  | 'watching';

export interface InboxEntry {
  event: SlateEvent;
  reason: Reason;
  read: boolean;
  item_title: string | null;
  list_id: string | null;
  loudness?: number;
  /** The event an escalation refers to, when still present in the inbox. */
  originalEvent?: SlateEvent;
  /** The chat channel, for a mention or reply in chat (then there is no item). */
  channel?: string | null;
  /** The doc page, for a mention in a doc (then there is no item). */
  doc_id?: string | null;
}

/** One row on the page: an item's entries together, newest first. */
export interface InboxGroup {
  itemId: string | null;
  title: string;
  listId: string | null;
  /** A chat channel's entries group under the channel; a doc's under the page. */
  channel: string | null;
  docId: string | null;
  entries: InboxEntry[];
  unread: number;
  latest: string;
}

export const REASON_LABEL: Record<Reason, string> = {
  chat: 'Chat message',
  mention: 'Mentioned you',
  reply: 'Replied to you',
  assigned: 'Assigned to you',
  waiting: 'Waiting on you', // the page says "Waiting on <your first name>" (lib/words.ts)
  failed: 'Could not run',
  stalled: 'Stalled',
  due: 'Due',
  unblocked: 'Ready to start',
  nudge: 'Start your agent',
  reminder: 'Reminder',
  digest: 'Daily digest',
  health: 'Slate health',
  access: 'Project access',
  comment: 'Comment',
  status: 'Status',
  update: 'Update',
  archived: 'Archived',
  moved: 'Moved',
  watching: 'On a task you watch',
};

export function linkEscalations(entries: InboxEntry[]): InboxEntry[] {
  const byId = new Map(entries.map((e) => [e.event.id, e]));
  return entries.map((entry) => {
    if (entry.event.type !== 'inbox.escalated') return entry;
    const { event_id } = (entry.event.after ?? {}) as { event_id?: string };
    const original = event_id ? byId.get(event_id) : undefined;
    if (!original) return entry;
    return {
      ...entry,
      originalEvent: original.event,
      item_title: entry.item_title ?? original.item_title,
      list_id: entry.list_id ?? original.list_id,
      channel: entry.channel ?? original.channel,
      doc_id: entry.doc_id ?? original.doc_id,
    };
  });
}

export function describeEntry(entry: InboxEntry, ctx: HistoryContext): string {
  if (entry.event.type !== 'inbox.escalated') return describe(entry.event, ctx);
  const wording = entry.originalEvent ? describe(entry.originalEvent, ctx) : REASON_LABEL[entry.reason];
  return `Still waiting: ${wording}`;
}

function cardId(entry: InboxEntry): string | null {
  return entry.event.item_id ?? entry.originalEvent?.item_id ?? null;
}

export function groupEntries(entries: InboxEntry[]): InboxGroup[] {
  const groups = new Map<string, InboxGroup>();
  for (const e of entries) {
    const channel = e.channel ?? null;
    const docId = e.doc_id ?? null;
    const itemId = cardId(e);
    const key =
      itemId ??
      (channel ? `chat:${channel}` : docId ? `doc:${docId}` : e.reason === 'health' ? 'health' : e.event.id);
    let g = groups.get(key);
    if (!g) {
      g = {
        itemId,
        title:
          e.item_title ??
          (e.event.type === 'account.digest'
            ? 'Your day'
            : e.reason === 'health'
              ? 'Slate health'
              : e.reason === 'access'
                ? 'Project access'
                : 'A deleted item'),
        listId: e.list_id,
        channel,
        docId,
        entries: [],
        unread: 0,
        latest: e.event.at,
      };
      groups.set(key, g);
    }
    g.entries.push(e);
    if (!e.read) g.unread++;
  }
  return [...groups.values()];
}

class Inbox {
  settingsOpen = $state(false);
  entries = $state<InboxEntry[]>([]);
  unread = $state(0);
  loaded = $state(false);
  #timer: ReturnType<typeof setTimeout> | undefined;
  #off: (() => void) | null = null;

  /** Listen to the board's events; called once the board is running. */
  start() {
    this.#off?.();
    this.#off = board.onEvent((ev) => this.#heard(ev));
    void this.refresh();
  }

  stop() {
    this.#off?.();
    this.#off = null;
    clearTimeout(this.#timer);
  }

  #heard(ev: SlateEvent) {
    const me = board.me?.account.id;
    if (!me) return;
    const mine = ev.type.startsWith('inbox.') || ev.type === 'channel.read';
    if (mine && ev.subject_id !== me) return;
    // Reminders and digests are the system's events about this account.
    const forMe =
      ((ev.type === 'account.digest' || ev.type === 'session.expiring') && ev.subject_id === me) ||
      (ev.type === 'item.reminder' && (ev.after as { account_id?: string } | null)?.account_id === me);
    const chat = ev.type === 'message.posted' || ev.type === 'message.edited' || ev.type === 'doc.mentioned';
    if (!mine && !forMe && (ev.actor_id === me || (!ev.item_id && !chat))) return;
    clearTimeout(this.#timer);
    this.#timer = setTimeout(() => void this.refresh(), mine ? 50 : 600);
  }

  async refresh() {
    try {
      this.entries = linkEscalations(await get<InboxEntry[]>('/inbox?limit=200'));
      this.unread = this.entries.filter((e) => !e.read).length;
      this.loaded = true;
      // Chat entries need no marking here: the server reads them with their message
      // (`channel.read` from any device brings a refresh).
    } catch (e) {
      board.loadFailed('refresh inbox', e);
    }
  }

  async #mark(path: 'read' | 'clear', body: Record<string, unknown>) {
    // Show it at once; the inbox.* event brings the server's answer.
    const ids = new Set(body.event_ids as string[] | undefined);
    const hit = (e: InboxEntry) =>
      body.all === true || ids.has(e.event.id) || (!!body.item_id && e.event.item_id === body.item_id);
    if (path === 'clear') this.entries = this.entries.filter((e) => !hit(e));
    else this.entries = this.entries.map((e) => (hit(e) ? { ...e, read: true } : e));
    this.unread = this.entries.filter((e) => !e.read).length;
    try {
      await post(`/inbox/${path}`, body);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      void this.refresh();
    }
  }

  /** Open the row's destination. Chat navigation is not acknowledgement: only exposed
   * messages are read; task/doc/digest policies and explicit bulk actions are unchanged. */
  open(g: InboxGroup) {
    if (g.itemId) {
      void this.readItem(g.itemId);
      void board.open(g.itemId);
      return;
    }
    if (!g.channel) void this.readEvents(g.entries.map((e) => e.event.id));
    if (g.channel) {
      // A chat mention or reply: open the channel at the newest one's thread.
      const event = g.entries[0].originalEvent ?? g.entries[0].event;
      const after = (event.after ?? {}) as { id?: string; parent_id?: string | null };
      const thread = event.type === 'inbox.escalated' ? null : (after.parent_id ?? after.id ?? event.subject_id);
      location.hash = chatHref(g.channel, thread);
    } else if (g.docId) {
      location.hash = `#/docs/${g.docId}`;
    } else if (g.entries.some((e) => e.reason === 'health')) {
      // A background job kept failing (or works again): the Health page says what and why.
      location.hash = '#/health';
    } else if (g.entries.some((e) => e.reason === 'digest')) {
      // The daily digest has no task: My Work lists the same day.
      location.hash = '#/home';
    } else if (g.entries.some((e) => e.reason === 'access')) {
      // A guest session ending: the project it is for (the admin extends it from Share there).
      const after = (g.entries[0].event.after ?? {}) as { section_id?: string | null };
      location.hash = after.section_id ? `#/section/${after.section_id}` : '#/home';
    }
  }

  #markItem(path: 'read' | 'clear', itemId: string) {
    const entries = this.entries.filter((e) => cardId(e) === itemId && (path === 'clear' || !e.read));
    if (!entries.length) return;
    // An escalation can inherit its card from the original. Mark the whole group in one request.
    const body = entries.some((e) => e.event.item_id !== itemId)
      ? { event_ids: entries.map((e) => e.event.id) }
      : { item_id: itemId };
    return this.#mark(path, body);
  }

  readItem(itemId: string) {
    return this.#markItem('read', itemId);
  }

  /** Opening a doc page reads the mentions on it. */
  readDoc(docId: string) {
    const ids = this.entries.filter((e) => e.doc_id === docId && !e.read).map((e) => e.event.id);
    if (ids.length) void this.#mark('read', { event_ids: ids });
  }

  readEvents(eventIds: string[]) {
    return this.#mark('read', { event_ids: eventIds });
  }

  readAll() {
    return this.#mark('read', { all: true });
  }

  clearGroup(g: InboxGroup) {
    return g.itemId ? this.#markItem('clear', g.itemId) : this.#mark('clear', { event_ids: g.entries.map((e) => e.event.id) });
  }

  clearAll() {
    return this.#mark('clear', { all: true });
  }
}

export const inbox = new Inbox();
