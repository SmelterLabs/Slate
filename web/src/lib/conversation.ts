// The card is the conversation: what the task window shows of
// an agent's turns. Comments by an agent, and the comments addressed to an agent (Asks and the
// replies that answer its questions), read as a chat. Each exchange ends with the agent's
// answer; every exchange older than the newest collapses to its first line. The live bubble and
// the badge words come from the item's agent flag. Pure; the components feed it plain data.

import type { Account, AgentFlag, Comment, Item, SlateEvent } from './types';
import type { Row } from './activity';
import { waitingOn } from './words';

/** Is this comment part of a conversation with an agent? */
export function isChat(c: Comment, isAgent: (id: string) => boolean): boolean {
  return isAgent(c.author_id) || (!!c.assignee_id && isAgent(c.assignee_id));
}

/** The ids of chat comments to show collapsed: every exchange but the newest. An exchange is a
 * run of chat comments closed by an agent's comment; comments after the last agent comment
 * (an answer waiting for the agent) stay with the newest exchange. An open Ask stays open. */
export function collapsedChat(rows: Row[], isAgent: (id: string) => boolean): Set<string> {
  const chat = rows.flatMap((r) => (r.kind === 'comment' && isChat(r.comment, isAgent) ? [r.comment] : []));
  const exchanges: Comment[][] = [];
  let current: Comment[] = [];
  for (const c of chat) {
    current.push(c);
    if (isAgent(c.author_id)) {
      exchanges.push(current);
      current = [];
    }
  }
  // The newest exchange is the last one closed by the agent, plus anything after it.
  const keep = exchanges.length - 1;
  const out = new Set<string>();
  exchanges.forEach((ex, i) => {
    // An open question is never hidden, however old.
    if (i < keep) for (const c of ex) if (!(c.assignee_id && !c.resolved_at)) out.add(c.id);
  });
  return out;
}

/** A comment's first line as plain text, for a collapsed exchange. */
export function firstLine(md: string, max = 140): string {
  const line =
    md
      .split('\n')
      .map((l) => l.replace(/^[\s#>*-]+/, '').replace(/[*_`]/g, '').trim())
      .find((l) => l) ?? '';
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** The badge's words (lib/words.ts): "Waiting on Alex" while an agent's blocked report names a
 * person, "Answered" once the reply is in (an open Ask for the agent), "Agent blocked" when
 * it named nobody, else the state's label. */
export function badgeLabel(
  flag: AgentFlag,
  item: Pick<Item, 'waiting_on_account_id' | 'open_asks'> | null | undefined,
  nameOf: (id: string) => string | undefined,
  labels: Record<string, string>,
): string {
  if (flag.state === 'blocked' && item) {
    if (item.waiting_on_account_id) return waitingOn(nameOf(item.waiting_on_account_id));
    if (item.open_asks?.includes(flag.by)) return 'Answered';
  }
  return labels[flag.state] ?? flag.state;
}

/** An agent's Open link the board may render, or null. Only an absolute URL whose scheme the
 * server told us it accepts (`Me.link_schemes`; http and https when it said nothing): the server
 * checks too, but a link is never trusted blind, and a desktop app's scheme (`myapp://`) is
 * only followed when the server was configured to allow it. */
export function safeAgentUrl(url: string | null | undefined, schemes: string[] | undefined): string | null {
  if (!url) return null;
  const m = /^([a-z][a-z0-9+.-]*):\/\/./i.exec(url);
  if (!m) return null;
  const allowed = schemes?.length ? schemes : ['http', 'https'];
  return allowed.some((s) => s.toLowerCase() === m[1].toLowerCase()) ? url : null;
}

/** The percent worth showing for an agent's report, or null. Older runners send 5 the moment a
 * task starts and never update it, so 5 or less says nothing: only a value an agent actually
 * moved past that start shows as a percent or bar. */
export function shownProgress(progress: unknown): number | null {
  return typeof progress === 'number' && progress > 5 ? Math.min(100, progress) : null;
}

/** "updated 12 s ago" for the live bubble (seconds matter while an agent works). */
export function updatedAgo(iso: string, now: Date): string {
  const s = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `updated ${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `updated ${m} min ago`;
  return `updated ${Math.round(m / 60)} h ago`;
}

/** Short age for a card's working badge: "now", "4 min", "2 h" (the board's clock ticks every
 * 30 s, so seconds would lie). */
export function shortAge(iso: string, now: Date): string {
  const s = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** Who the Ask picker starts on for this card: the card's agent assignee, else the project's
 * agent (`project`: handles of the agents shared on the card's private section, its own guest
 * agent first; a project's question goes to the project's agent, not to whichever agent the
 * viewer asked last somewhere else), else the agent last asked or reporting on the card, else
 * the agent the viewer asked last anywhere (`remembered`), else the oldest agent with a runner.
 * '' (a plain comment) when there is no agent at all. Returns a handle. */
export function defaultAsk(
  item: Pick<Item, 'assignees'> & { agent?: AgentFlag | null },
  comments: Pick<Comment, 'assignee_id' | 'created_at'>[],
  accounts: Account[],
  remembered: string | null,
  project: string[] = [],
): string {
  const agents = accounts.filter((a) => a.kind === 'agent' && !a.disabled_at && a.handle !== 'slate');
  const byId = (id: string | null | undefined) => agents.find((a) => a.id === id)?.handle;
  const assigned = item.assignees.find((a) => byId(a.id));
  if (assigned) return assigned.handle;
  const own = project.find((h) => agents.some((a) => a.handle === h));
  if (own) return own;
  const asked = [...comments]
    .filter((c) => byId(c.assignee_id))
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .at(-1);
  if (asked) return byId(asked.assignee_id)!;
  if (byId(item.agent?.by)) return byId(item.agent?.by)!;
  if (remembered && agents.some((a) => a.handle === remembered)) return remembered;
  const runners = agents
    .filter((a) => a.wake === 'push')
    .sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
  return runners[0]?.handle ?? '';
}

/** The first agent `@mentioned` in a draft, as a handle, or ''. Typing "@my-agent can you…" means
 * the question is for that agent: the composer addresses the Ask to it unless the viewer picked
 * someone by hand. */
export function mentionedAgent(text: string, accounts: Account[]): string {
  for (const m of (text || '').matchAll(/(?<![\w@])@([a-z][a-z0-9_-]{1,31})/gi)) {
    const handle = m[1].toLowerCase();
    if (accounts.some((a) => a.handle === handle && a.kind === 'agent' && !a.disabled_at && handle !== 'slate')) {
      return handle;
    }
  }
  return '';
}

/** Who a draft's Ask goes to: the first agent it @mentions; '' (a plain comment) when it
 * @mentions only people, because "@alex did you…" is for Alex, not the card's default agent;
 * else `fallback` (the picker's default). */
export function askFor(text: string, accounts: Account[], fallback: string): string {
  const agent = mentionedAgent(text, accounts);
  if (agent) return agent;
  for (const m of (text || '').matchAll(/(?<![\w@])@([a-z][a-z0-9_-]{1,31})/gi)) {
    if (accounts.some((a) => a.handle === m[1].toLowerCase())) return '';
  }
  return fallback;
}

/** The agent the card is waiting on to answer, for the "working" dots at the foot of the
 * thread: the agent working on it now, else the agent with an open Ask (sent, not yet
 * picked up). `picked` says whether it has started. */
export function pendingAgent(
  item: { agent?: AgentFlag | null },
  comments: (Pick<Comment, 'assignee_id' | 'resolved_at' | 'created_at'> & { id?: string })[],
  isAgent: (id: string) => boolean,
  events: SlateEvent[] = [],
): { id: string; picked: boolean } | null {
  if (item.agent?.state === 'working') return { id: item.agent.by, picked: true };
  const assignments = new Map<string, string>();
  const settled = new Map<string, string>();
  for (const event of events) {
    if (event.type === 'comment.assigned' && event.subject_id && event.id > (assignments.get(event.subject_id) ?? '')) {
      assignments.set(event.subject_id, event.id);
    }
    const through = (event.after as { through?: string } | null)?.through;
    if (event.type === 'item.work_finished' && typeof through === 'string' && through > (settled.get(event.actor_id) ?? '')) {
      settled.set(event.actor_id, through);
    }
  }
  const open = comments
    .filter((c) => c.assignee_id && !c.resolved_at && isAgent(c.assignee_id))
    .filter((c) => !c.id || !assignments.has(c.id) || !settled.has(c.assignee_id!) || assignments.get(c.id)! > settled.get(c.assignee_id!)!)
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .at(-1);
  return open ? { id: open.assignee_id!, picked: false } : null;
}

/** What a card that waits on `me` is waiting for: who set the wait, the comment that carries
 * their question, and the question itself in a sentence or two. The wait is the latest
 * `waiting_on_account_id` change naming `me`; the comment is its author's newest comment up to
 * a minute after it (an agent's blocked report writes the comment and the wait together). With
 * no such event in view (an old wait), the newest comment by anyone else stands in. The
 * question is the comment's last sentences that end in "?", else its first line. */
export function waitingQuestion(
  item: Pick<Item, 'waiting_on_account_id'>,
  comments: Pick<Comment, 'id' | 'author_id' | 'body_md' | 'created_at'>[],
  events: Pick<SlateEvent, 'id' | 'type' | 'field' | 'after' | 'actor_id' | 'at'>[],
  me: string,
): { by: string | null; commentId: string | null; question: string | null } | null {
  if (!me || item.waiting_on_account_id !== me) return null;
  const wait = events
    .filter((e) => e.type === 'item.updated' && e.field === 'waiting_on_account_id' && e.after === me)
    .sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))
    .at(-1);
  const by = wait && wait.actor_id !== me ? wait.actor_id : null;
  const limit = wait ? new Date(new Date(wait.at).getTime() + 60_000).toISOString() : null;
  const ordered = [...comments].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const said = ordered
    .filter((c) => (by ? c.author_id === by : c.author_id !== me))
    .filter((c) => !limit || !by || c.created_at <= limit)
    .at(-1);
  if (!said) return { by, commentId: null, question: null };
  return { by: by ?? said.author_id, commentId: said.id, question: questionIn(said.body_md) };
}

/** The asking part of a comment as plain text: its last one or two sentences ending in "?",
 * else its first line. */
export function questionIn(md: string, max = 280): string {
  const plain = md
    .split('\n')
    .filter((l) => !/^\s*\|/.test(l)) // table rows
    .map((l) => l.replace(/^[\s#>*-]+/, '').replace(/[*_`]/g, '').trim())
    .filter(Boolean)
    .join(' ');
  const sentences = plain.match(/[^.!?]*[^.!?\s][^.!?]*[.!?]+/g)?.map((s) => s.trim()) ?? [];
  const asks = sentences.filter((s) => s.endsWith('?'));
  const text = asks.length ? asks.slice(-2).join(' ') : firstLine(md, max);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
