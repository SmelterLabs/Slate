// Chat's live state: the channel list with this account's unread and mention counts, the loaded
// pages of each channel and each open thread, and the writes. Live: every message.* event (from
// anyone, this account's other devices too) refetches that one message into whatever has it
// loaded, and the counts refresh; this account's channel.read refreshes them at once, so phone
// and PC always show the same unread badges.

import { ApiError, api, authHeaders, get, patch, post } from './api';
import { toggleReaction } from './activity';
import {
  TURN_EVENTS,
  applyTurnEvent,
  EXPOSE_BATCH_MS,
  exposureBatches,
  mentionsIn,
  upsert,
  type AgentTurn,
  type Channel,
  type ChatFile,
  type ChatMessage,
  pastedName,
} from './chat';
import { board } from './store.svelte';
import { task } from './task.svelte';
import type { Item, SlateEvent } from './types';

const PAGE = 50;

class Chat {
  channels = $state<Channel[]>([]);
  loaded = $state(false);
  /** Top-level messages of each loaded channel, oldest first. */
  pages = $state<Record<string, ChatMessage[]>>({});
  /** Whether a channel has older messages to load. */
  older = $state<Record<string, boolean>>({});
  /** Each loaded thread: its first message, then the replies. */
  threads = $state<Record<string, ChatMessage[]>>({});
  /** Each channel's open agent turns ("My Agent is working…"): seeded from `GET /channels`, then
   * kept by the `message.mentioned_agent` / `message.agent_turn` events. */
  turns = $state<Record<string, AgentTurn[]>>({});
  /** Bumped by every turn event, so a channel list fetched before one cannot undo it. */
  #turnSeq = 0;
  #off: (() => void) | null = null;
  #countTimer: ReturnType<typeof setTimeout> | undefined;
  /** Exact exposures this session, also used for matching Inbox entries. */
  exposed = new Set<string>();
  /** Exposures not yet sent, by channel: sent together, once a second (`EXPOSE_BATCH_MS`). */
  #pending = new Map<string, Set<string>>();
  #exposeTimer: ReturnType<typeof setTimeout> | undefined;

  get me(): string {
    return board.me?.account.id ?? '';
  }

  start() {
    this.#off?.();
    this.#off = board.onEvent((ev) => this.#heard(ev));
    void this.refreshChannels();
  }

  stop() {
    this.#off?.();
    this.#off = null;
    clearTimeout(this.#countTimer);
    clearTimeout(this.#exposeTimer);
    this.#exposeTimer = undefined;
    this.#pending.clear();
    this.forgetThumbs();
    this.exposed.clear();
  }

  channel(key: string): Channel | undefined {
    return this.channels.find((c) => c.key === key);
  }

  get unread(): number {
    return this.channels.reduce((n, c) => n + c.unread, 0);
  }

  get mentions(): number {
    return this.channels.reduce((n, c) => n + c.mentions, 0);
  }

  async refreshChannels() {
    try {
      const seq = this.#turnSeq;
      const found = await get<Channel[]>('/channels');
      this.channels = found;
      if (seq === this.#turnSeq) this.turns = Object.fromEntries(found.map((c) => [c.key, c.agent_turns ?? []]));
      this.loaded = true;
    } catch {
      // badges stay as they were; the next event retries
    }
  }

  #counts(delay = 300) {
    clearTimeout(this.#countTimer);
    this.#countTimer = setTimeout(() => void this.refreshChannels(), delay);
  }

  #heard(ev: SlateEvent) {
    if (ev.type === 'channel.read') {
      if (ev.subject_id === this.me) this.#counts(30);
      return;
    }
    // A list, a section or an agent (its own chat) came, went or was renamed.
    if (
      /^(list|section)\.(created|updated|archived|unarchived)$/.test(ev.type) ||
      /^account\.(created|updated|disabled|enabled)$/.test(ev.type)
    ) {
      this.#counts(400);
      return;
    }
    if ((TURN_EVENTS as readonly string[]).includes(ev.type)) {
      this.applyTurn(ev);
      return;
    }
    if (!ev.type.startsWith('message.') || !ev.subject_id) return;
    const after = (ev.after ?? ev.before ?? {}) as { channel?: string; parent_id?: string | null };
    void this.#refetch(ev.subject_id, after.channel, after.parent_id);
    if (ev.type === 'message.posted' && after.parent_id) void this.#refetch(after.parent_id, after.channel);
    if (ev.type === 'message.posted' || ev.type === 'message.deleted') {
      this.#counts();
    }
    if (ev.type === 'message.edited') this.#counts();
  }

  /** One chat-turn event into its channel's turns (idempotent: see `applyTurnEvent`). */
  applyTurn(ev: SlateEvent) {
    const key = (ev.after as { channel?: string } | null)?.channel;
    if (!key) return;
    this.#turnSeq++;
    const next = applyTurnEvent(this.turns[key] ?? [], ev);
    if (next !== this.turns[key]) this.turns[key] = next;
  }

  async #refetch(id: string, channel?: string, parent?: string | null) {
    const wanted =
      (channel && this.pages[channel]) ||
      this.threads[id] ||
      (parent && this.threads[parent]) ||
      Object.values(this.threads).some((t) => t.some((m) => m.id === id));
    if (!wanted) return;
    try {
      this.#place(await get<ChatMessage>(`/messages/${id}`));
    } catch {
      // gone or unreachable: the next load shows the truth
    }
  }

  /** Put a message wherever it shows: its channel's page (top level) and its thread. */
  #place(m: ChatMessage) {
    if (!m.parent_id && this.pages[m.channel]) this.pages[m.channel] = upsert(this.pages[m.channel], m);
    const root = m.parent_id ?? m.id;
    if (this.threads[root]) this.threads[root] = upsert(this.threads[root], m);
  }

  async load(key: string) {
    try {
      const page = await get<ChatMessage[]>(`/channels/${encodeURIComponent(key)}/messages?limit=${PAGE}`);
      this.pages[key] = page;
      this.older[key] = page.length >= PAGE;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async loadOlder(key: string) {
    const first = this.pages[key]?.[0];
    if (!first || !this.older[key]) return;
    try {
      const page = await get<ChatMessage[]>(
        `/channels/${encodeURIComponent(key)}/messages?limit=${PAGE}&before=${first.id}`,
      );
      this.pages[key] = [...page, ...(this.pages[key] ?? [])];
      this.older[key] = page.length >= PAGE;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async loadThread(id: string): Promise<string | null> {
    try {
      const msgs = await get<ChatMessage[]>(`/messages/${id}/thread`);
      if (!msgs.length) return null;
      this.threads[msgs[0].id] = msgs;
      return msgs[0].id;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  async expose(m: ChatMessage) {
    if (m.author_id === this.me || m.id.startsWith('~') || this.exposed.has(m.id)) return;
    if (document.visibilityState !== 'visible' || !document.hasFocus()) return;
    this.exposed.add(m.id);
    const ids = this.#pending.get(m.channel) ?? new Set<string>();
    ids.add(m.id);
    this.#pending.set(m.channel, ids);
    this.#exposeTimer ??= setTimeout(() => void this.#sendExposed(), EXPOSE_BATCH_MS);
  }

  /** One read call per channel for everything exposed since the last one: a busy channel
   * scrolled through is a handful of `channel.read` events, not one per message. */
  async #sendExposed() {
    this.#exposeTimer = undefined;
    const batches = exposureBatches(this.#pending);
    this.#pending = new Map();
    for (const [channel, ids] of batches) {
      try {
        await post(`/channels/${encodeURIComponent(channel)}/read`, { message_ids: ids });
      } catch {
        for (const id of ids) this.exposed.delete(id);
      }
    }
    if (batches.length) this.#counts(30);
  }

  async markMessageRead(m: ChatMessage): Promise<void> {
    if (m.author_id === this.me || m.id.startsWith('~')) return;
    try {
      // An explicit action can acknowledge a long message without clearing its thread.
      await post(`/channels/${encodeURIComponent(m.channel)}/read`, { message_ids: [m.id] });
      this.exposed.add(m.id);
      this.#counts(30);
      board.flash('Message marked read');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async markRead(key: string) {
    const c = this.channel(key);
    if (c) {
      c.unread = 0;
      c.mentions = 0;
    }
    try {
      await post(`/channels/${encodeURIComponent(key)}/read`, {});
    } catch {
      // the counts come back on the next refresh
    }
  }

  async readAll() {
    for (const c of this.channels) {
      c.unread = 0;
      c.mentions = 0;
    }
    try {
      await post('/channels/read-all');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      void this.refreshChannels();
    }
  }

  // --- files ------------------------------------------------------------------------------

  /** Object URLs of image files, by file id (fetched once, with the token). The newest about 200
   * are kept; older ones are forgotten, and all of them on sign-out (stop). */
  thumbs = $state<Record<string, string>>({});
  #thumbOrder: string[] = [];
  static readonly THUMB_LIMIT = 200;

  rememberThumb(f: ChatFile, blob: Blob) {
    if (this.thumbs[f.id]) URL.revokeObjectURL(this.thumbs[f.id]);
    this.thumbs[f.id] = URL.createObjectURL(blob);
    this.#thumbOrder = [...this.#thumbOrder.filter((id) => id !== f.id), f.id];
    const extra = this.#thumbOrder.slice(0, Math.max(0, this.#thumbOrder.length - Chat.THUMB_LIMIT));
    for (const id of extra) this.#forget(id);
  }

  forgetThumbs() {
    for (const id of this.#thumbOrder) this.#forget(id);
  }

  #forget(id: string) {
    if (this.thumbs[id]) URL.revokeObjectURL(this.thumbs[id]);
    delete this.thumbs[id];
    this.#thumbOrder = this.#thumbOrder.filter((x) => x !== id);
  }

  /** Upload a pasted, dropped or picked file to a channel. It is sent when a message names it. */
  async upload(key: string, file: File): Promise<ChatFile | null> {
    const name = pastedName(file.name, file.type, new Date());
    try {
      const res = await fetch(`/api/v1/channels/${encodeURIComponent(key)}/files?filename=${encodeURIComponent(name)}`, {
        method: 'POST',
        headers: { ...authHeaders(board.token), 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, (data as { detail?: string })?.detail ?? res.statusText);
      const f = data as ChatFile;
      // The picture is already here: show it without fetching it back.
      if (f.content_type.startsWith('image/')) this.rememberThumb(f, file);
      return f;
    } catch (e) {
      board.flash(`Could not attach ${name}: ${e instanceof Error ? e.message : String(e)}`);
      return null;
    }
  }

  /** Take back an upload that was not sent. */
  async discard(f: ChatFile) {
    try {
      await api<void>('DELETE', `/message-files/${f.id}`);
    } catch {
      // the server sweeps what is never sent
    }
  }

  async thumb(f: ChatFile) {
    if (this.thumbs[f.id] || !f.content_type.startsWith('image/')) return;
    try {
      const res = await fetch(`/api/v1/message-files/${f.id}`, { headers: authHeaders(board.token) });
      if (res.ok) this.rememberThumb(f, await res.blob());
    } catch {
      // leave the file chip
    }
  }

  /** Open a message's file in the full-screen viewer (or download what it cannot show). */
  openFile(f: ChatFile, among: ChatFile[]) {
    void task.open(f, { files: among, path: (id) => `/message-files/${id}` });
  }

  /** Post (or reply). Shows at once; the server's copy replaces the placeholder. */
  async send(key: string, body: string, parentId: string | null = null, files: ChatFile[] = []): Promise<boolean> {
    const temp: ChatMessage = {
      id: `~${Date.now()}`,
      channel: key,
      parent_id: parentId,
      author_id: this.me,
      body_md: body,
      mentions: mentionsIn(body),
      item_id: null,
      created_at: new Date().toISOString(),
      edited_at: null,
      deleted_at: null,
      reactions: [],
      reply_count: 0,
      last_reply_at: null,
      reply_author_ids: [],
      linked_items: [],
      files,
    };
    const drop = () => {
      if (this.pages[key]) this.pages[key] = this.pages[key].filter((m) => m.id !== temp.id);
      if (parentId && this.threads[parentId]) {
        this.threads[parentId] = this.threads[parentId].filter((m) => m.id !== temp.id);
      }
    };
    if (parentId) {
      if (this.threads[parentId]) this.threads[parentId] = [...this.threads[parentId], temp];
    } else if (this.pages[key]) {
      this.pages[key] = [...this.pages[key], temp];
    }
    try {
      const msg = await post<ChatMessage>(`/channels/${encodeURIComponent(key)}/messages`, {
        body_md: body,
        ...(parentId ? { parent_id: parentId } : {}),
        ...(files.length ? { file_ids: files.map((f) => f.id) } : {}),
      });
      drop();
      this.#place(msg);
      if (parentId) void this.#refetch(parentId, key);
      return true;
    } catch (e) {
      drop();
      board.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  async edit(m: ChatMessage, body: string): Promise<boolean> {
    try {
      this.#place(await patch<ChatMessage>(`/messages/${m.id}`, { body_md: body }));
      return true;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  async remove(m: ChatMessage) {
    try {
      this.#place(await api<ChatMessage>('DELETE', `/messages/${m.id}`));
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** Optimistic: the chip changes at once; the server's answer replaces it. */
  async react(m: ChatMessage, emoji: string) {
    const mine = m.reactions.find((r) => r.emoji === emoji)?.account_ids.includes(this.me);
    this.#place({ ...m, reactions: toggleReaction(m.reactions, emoji, this.me) });
    const path = `/messages/${m.id}/reactions`;
    try {
      this.#place(
        mine
          ? await api<ChatMessage>('DELETE', `${path}/${encodeURIComponent(emoji)}`)
          : await post<ChatMessage>(path, { emoji }),
      );
    } catch (e) {
      this.#place(m);
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** Turn a message into a task; opens it so it can be finished off. */
  async makeTask(
    m: ChatMessage,
    fields: { list_id?: string; title?: string; due_at?: string; assignees?: string[] },
  ) {
    try {
      const item = await post<Item>(`/messages/${m.id}/task`, fields);
      void this.#refetch(m.id, m.channel);
      board.flash(`Task created: ${item.title}`, { label: 'Open', run: () => void board.open(item.id) });
      return item;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }
}

export const chat = new Chat();
