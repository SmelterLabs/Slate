// Chat on the board: the shapes from /channels and /messages, and the pure helpers the chat
// components share (where a channel lives in the URL, how messages group on screen, what a body
// renders to). The live state is chat.svelte.ts.

import { workspaceFormatter } from './timezone';
import { MENTION_RE, highlightMentions } from './activity';
import { renderMarkdown, type RefResolvers } from './markdown';

export interface Channel {
  key: string;
  kind: 'general' | 'list' | 'section' | 'agent';
  name: string;
  list_id: string | null;
  section_id: string | null;
  /** The agent, for an agent's own chat (`agent:<id>`). */
  account_id?: string | null;
  mention_required?: boolean;
  position: number;
  unread: number;
  mentions: number;
  first_unread_id?: string | null;
  first_unread_thread_id?: string | null;
  unread_threads?: Record<string, number>;
  last_message_id: string | null;
  last_message_at: string | null;
  /** Agents mentioned here whose answer is still to come (`GET /channels`). */
  agent_turns?: AgentTurn[];
}

/** An agent's open chat turn in a channel or one of its threads: `waiting` between the mention
 * and the runner's take, `working` from the take until it ends. */
export interface AgentTurn {
  agent_id: string;
  thread_id: string | null;
  state: 'waiting' | 'working';
  since?: string;
}

/** The two chat-turn events the chat store reduces (service/chatwake.py). */
export const TURN_EVENTS = ['message.mentioned_agent', 'message.agent_turn'] as const;

/** A channel's open agent turns after one `message.mentioned_agent` / `message.agent_turn`
 * event. Pure and idempotent (set-to-value per agent and thread): applying an event twice gives
 * the same list. `mentioned_agent`: each agent in `for` is waiting, one in `queued` already has
 * its turn open (working). `agent_turn`: `started` is working; any end (`done`, `released`,
 * `expired`) removes it, unless a mention arrived meanwhile (`pending`), which waits again. */
export function applyTurnEvent(turns: AgentTurn[], ev: { type: string; after: unknown; at?: string }): AgentTurn[] {
  const a = (ev.after ?? {}) as {
    thread_id?: string | null;
    for?: string[];
    queued?: string[];
    agent?: string;
    state?: string;
    pending?: boolean;
  };
  const thread = a.thread_id ?? null;
  let out = turns;
  const set = (agent: string, state: AgentTurn['state'] | null) => {
    const rest = out.filter((t) => !(t.agent_id === agent && t.thread_id === thread));
    const was = out.find((t) => t.agent_id === agent && t.thread_id === thread);
    if (!state) {
      out = rest;
      return;
    }
    if (was?.state === state) return;
    out = [...rest, { agent_id: agent, thread_id: thread, state, since: ev.at }];
  };
  if (ev.type === 'message.mentioned_agent') {
    for (const id of a.for ?? []) set(id, 'waiting');
    for (const id of a.queued ?? []) set(id, 'working');
  } else if (ev.type === 'message.agent_turn' && a.agent) {
    set(a.agent, a.state === 'started' ? 'working' : a.pending ? 'waiting' : null);
  }
  return out;
}

/** The turns that show under a channel's messages (`thread` null) or in one thread. */
export function turnsIn(turns: AgentTurn[] | undefined, thread: string | null): AgentTurn[] {
  return (turns ?? []).filter((t) => t.thread_id === thread);
}

export interface LinkedItem {
  id: string;
  title: string;
  list_id: string;
  due_at: string | null;
  status: string | null;
  done: boolean;
}

/** A file sent with a message (a pasted screenshot, a PDF): bytes at `/message-files/{id}`. */
export interface ChatFile {
  id: string;
  filename: string;
  content_type: string;
  size: number;
}

/** A name for a pasted file: a clipboard image has none, or the same `image.png` every time. */
export function pastedName(name: string, type: string, at: Date): string {
  if (name && !/^image\.[a-z0-9]+$/i.test(name)) return name;
  const ext = /^image\/([a-z0-9]+)/i.exec(type)?.[1]?.replace('jpeg', 'jpg') ?? 'png';
  const p = (n: number) => String(n).padStart(2, '0');
  const day = `${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())}`;
  return `Screenshot ${day} ${p(at.getHours())}.${p(at.getMinutes())}.${p(at.getSeconds())}.${ext}`;
}

export interface ChatMessage {
  id: string;
  channel: string;
  parent_id: string | null;
  author_id: string;
  body_md: string;
  mentions: string[];
  item_id: string | null;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
  /** Hidden from guests (the workspace agents' talk on a shared project); guests never receive one. */
  members_only?: boolean;
  reactions: { emoji: string; account_ids: string[] }[];
  reply_count: number;
  last_reply_at: string | null;
  reply_author_ids: string[];
  linked_items: LinkedItem[];
  files: ChatFile[];
}

export const GENERAL = 'general';

/** How long the board gathers exposed messages before one read call, and the most ids one
 * call names (the server's limit). */
export const EXPOSE_BATCH_MS = 1000;
export const EXPOSE_MAX_IDS = 200;

/** Gathered exposures as read calls: one per channel, `max` ids at most each, oldest first. */
export function exposureBatches(
  pending: Map<string, Set<string>>,
  max = EXPOSE_MAX_IDS,
): [string, string[]][] {
  const out: [string, string[]][] = [];
  for (const [channel, ids] of pending) {
    const sorted = [...ids].sort();
    for (let i = 0; i < sorted.length; i += max) out.push([channel, sorted.slice(i, i + max)]);
  }
  return out;
}

/** The channel of a list or section (`list:<id>`), or General. */
export function channelKey(kind: 'general' | 'list' | 'section', id?: string | null): string {
  return kind === 'general' || !id ? GENERAL : `${kind}:${id}`;
}

export function isAgentChat(key: string): boolean {
  return key.startsWith('agent:');
}

/** The icon a channel shows: a robot for an agent's chat, a folder for a section, else #. */
export function channelIcon(c: { key: string; kind?: string } | undefined | null): 'robot' | 'folder' | 'hash' {
  if (!c) return 'hash';
  if (c.kind === 'agent' || isAgentChat(c.key)) return 'robot';
  return c.kind === 'section' ? 'folder' : 'hash';
}

/** The agents' chats, in the order the server lists them, with their total unread. */
export function agentChats<C extends { kind: string; unread: number; mentions: number }>(channels: C[]): {
  chats: C[];
  unread: number;
} {
  const chats = channels.filter((c) => c.kind === 'agent');
  return { chats, unread: chats.reduce((n, c) => n + c.unread, 0) };
}

/** `#/chat/<key>[?thread=<id>]`: the Chat page on one channel, optionally with a thread open. */
export function chatHref(key: string, thread?: string | null): string {
  return `#/chat/${key}${thread ? `?thread=${thread}` : ''}`;
}

/** The channel in `#/chat/<key>`, or null for `#/chat` (the channel list). */
export function chatChannelOf(hash: string): string | null {
  const m = /^\/chat\/([^/?]+)/.exec(hash.replace(/^#/, ''));
  return m ? decodeURIComponent(m[1]) : null;
}

/** `?thread=<id>` in the hash. */
export function threadOf(hash: string): string | null {
  return new URLSearchParams(hash.split('?')[1] ?? '').get('thread');
}

/** Insert or replace by id, keeping id (posting) order. */
export function upsert(list: ChatMessage[], msg: ChatMessage): ChatMessage[] {
  const at = list.findIndex((m) => m.id === msg.id);
  if (at >= 0) return [...list.slice(0, at), msg, ...list.slice(at + 1)];
  const out = [...list, msg];
  out.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return out;
}

const dayFmt = workspaceFormatter('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
});
const dayKeyFmt = workspaceFormatter('en-CA', {});

/** "Today", "Yesterday", or "Wednesday, October 21" (workspace days). */
export function dayLabel(iso: string, now: Date = new Date()): string {
  const day = dayKeyFmt.format(new Date(iso));
  if (day === dayKeyFmt.format(now)) return 'Today';
  if (day === dayKeyFmt.format(new Date(now.getTime() - 86_400_000))) return 'Yesterday';
  return dayFmt.format(new Date(iso));
}

/** Consecutive messages by one author within this long fold under one name and avatar. */
export const FOLD_MS = 5 * 60_000;

export type ChatRow =
  | { kind: 'day'; key: string; label: string }
  | { kind: 'message'; key: string; message: ChatMessage; head: boolean };

/** Messages as screen rows: a divider at each new day, and a name/avatar only where the
 * author changes, a day starts, or five minutes pass. */
export function rows(messages: ChatMessage[], now: Date = new Date()): ChatRow[] {
  const out: ChatRow[] = [];
  let lastDay = '';
  let prev: ChatMessage | null = null;
  for (const m of messages) {
    const day = dayKeyFmt.format(new Date(m.created_at));
    if (day !== lastDay) {
      out.push({ kind: 'day', key: `day-${day}`, label: dayLabel(m.created_at, now) });
      lastDay = day;
      prev = null;
    }
    const head =
      !prev ||
      prev.author_id !== m.author_id ||
      !!prev.deleted_at ||
      new Date(m.created_at).getTime() - new Date(prev.created_at).getTime() > FOLD_MS;
    out.push({ kind: 'message', key: m.id, message: m, head });
    prev = m;
  }
  return out;
}

/** A message body as safe HTML: markdown, known @handles highlighted (yours stronger),
 * `#<item id>` references shown as `#` plus the task's title when the board knows it, and
 * `[[doc:<id>]]` / `[[list:<id>]]` as the page's icon and title or the list's name (`refs`;
 * markdown.ts renders an unknown one as "a page" / "a list"). */
export function renderBody(
  body: string,
  known: (handle: string) => boolean,
  me: string | undefined,
  titleOf: (id: string) => string | undefined,
  refs?: RefResolvers,
): string {
  const html = highlightMentions(renderMarkdown(body, { refs }), known, me);
  return html
    .split(/(<[^>]*>)/)
    .map((part) =>
      part.startsWith('<')
        ? part
        : part.replace(/(^|[^\w/#&])#([0-9A-Za-z]{12})(?![0-9A-Za-z])/g, (all, pre: string, id: string) => {
            const title = titleOf(id);
            if (!title) return all;
            const safe = title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return `${pre}<a class="task-ref" href="#/everything?task=${id}" data-task="${id}">#${safe}</a>`;
          }),
    )
    .join('');
}

/** The `#query` being typed at `caret` (a task reference), if any. */
export function taskRefAt(text: string, caret: number): { start: number; query: string } | null {
  const m = /(^|\s)#([^\s#]{0,40})$/.exec(text.slice(0, caret));
  if (!m) return null;
  return { start: caret - m[2].length - 1, query: m[2].toLowerCase() };
}

/** Replace the `#query` at `start..caret` with `#<id> `. */
export function insertTaskRef(text: string, start: number, caret: number, id: string) {
  const insert = `#${id} `;
  return { text: text.slice(0, start) + insert + text.slice(caret), caret: start + insert.length };
}

// --- the `#` picker: tasks, pages and lists, grouped ----------------------------------------

export type HashKind = 'task' | 'doc' | 'list';

export interface HashPick {
  kind: HashKind;
  id: string;
  title: string;
  /** Dimmed after the title: the task's or page's list, the list's section. */
  sub: string;
  /** A page's emoji icon, when it has one. */
  icon?: string | null;
  /** Position in the flat list of every group's picks (keyboard navigation). */
  index: number;
}

export interface HashGroup {
  kind: HashKind;
  label: 'Tasks' | 'Pages' | 'Lists';
  picks: HashPick[];
}

export interface HashSources {
  tasks: { id: string; title: string; list_id: string; archived_at: string | null; updated_at: string }[];
  docs: { id: string; title: string; icon: string | null; list_id: string | null; section_id: string | null; archived_at: string | null }[];
  lists: { id: string; name: string; section_id: string; position: number; archived_at: string | null }[];
  /** Where the message is typed: the channel's list and section (General has neither). */
  here: { listId: string | null; sectionId: string | null };
  listName: (id: string) => string | undefined;
  sectionName: (id: string) => string | undefined;
}

const PICKS = { task: 5, doc: 4, list: 4 };

/** What `#query` offers: tasks (the channel's list first, then newest), pages the reader can
 * see (this list's or section's first, then by title) and lists (this section's first, then
 * board order), each matched on its title, archived ones left out. Groups with nothing to show
 * are absent; `index` runs across the groups. */
export function hashPicks(query: string, src: HashSources): HashGroup[] {
  const q = query.toLowerCase();
  const hit = (title: string) => !q || title.toLowerCase().includes(q);
  const { listId, sectionId } = src.here;
  const tasks = src.tasks
    .filter((i) => !i.archived_at && (hit(i.title) || i.id.toLowerCase() === q))
    .sort((a, b) => Number(b.list_id === listId) - Number(a.list_id === listId) || b.updated_at.localeCompare(a.updated_at))
    .slice(0, PICKS.task);
  const near = (d: HashSources['docs'][number]) =>
    Number((!!listId && d.list_id === listId) || (!!sectionId && d.section_id === sectionId));
  const docs = src.docs
    .filter((d) => !d.archived_at && hit(d.title))
    .sort((a, b) => near(b) - near(a) || a.title.localeCompare(b.title))
    .slice(0, PICKS.doc);
  const lists = src.lists
    .filter((l) => !l.archived_at && hit(l.name))
    .sort((a, b) => Number(b.section_id === sectionId) - Number(a.section_id === sectionId) || a.position - b.position)
    .slice(0, PICKS.list);
  const out: HashGroup[] = [];
  let index = 0;
  const group = (kind: HashKind, label: HashGroup['label'], picks: Omit<HashPick, 'index'>[]) => {
    if (picks.length) out.push({ kind, label, picks: picks.map((p) => ({ ...p, index: index++ })) });
  };
  group('task', 'Tasks', tasks.map((t) => ({ kind: 'task', id: t.id, title: t.title, sub: src.listName(t.list_id) ?? '' })));
  group(
    'doc',
    'Pages',
    docs.map((d) => ({
      kind: 'doc',
      id: d.id,
      title: d.title,
      icon: d.icon,
      sub: (d.list_id ? src.listName(d.list_id) : d.section_id ? src.sectionName(d.section_id) : 'Workspace') ?? '',
    })),
  );
  group('list', 'Lists', lists.map((l) => ({ kind: 'list', id: l.id, title: l.name, sub: src.sectionName(l.section_id) ?? '' })));
  return out;
}

/** Replace the `#query` at `start..caret` with what the pick renders from: `#<id> ` for a
 * task, `[[doc:<id>]] ` for a page, `[[list:<id>]] ` for a list. */
export function insertHashPick(text: string, start: number, caret: number, pick: { kind: HashKind; id: string }) {
  if (pick.kind === 'task') return insertTaskRef(text, start, caret, pick.id);
  const insert = `[[${pick.kind}:${pick.id}]] `;
  return { text: text.slice(0, start) + insert + text.slice(caret), caret: start + insert.length };
}

/** Handles a body mentions, for an optimistic message (the server's list replaces it). */
export function mentionsIn(body: string): string[] {
  return [...new Set([...body.matchAll(MENTION_RE)].map((m) => m[2].toLowerCase()))];
}

/** Badge text: "", "3", "99+". */
export function badge(n: number): string {
  return n <= 0 ? '' : n > 99 ? '99+' : String(n);
}
