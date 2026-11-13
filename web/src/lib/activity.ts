// The task window's right-hand column: comments and history interleaved, oldest first. History
// lines are small and gray; runs of the same edit by the same actor within a few minutes fold
// into one line (an autosaving description would otherwise fill the column).

import { safeColor } from './mdcore';
import { workspaceFormatter } from './timezone';
import type { ActivityEntry, Comment, Me, SlateEvent, Status } from './types';

export const FOLD_WINDOW_MS = 10 * 60_000;

export type Row =
  | { kind: 'comment'; key: string; at: string; comment: Comment }
  | { kind: 'event'; key: string; at: string; event: SlateEvent; times: number };

function foldKey(ev: SlateEvent): string {
  return `${ev.actor_id}|${ev.type}|${ev.field ?? ''}`;
}

// Edits that say the same thing when repeated: only the latest of a run matters.
function foldable(ev: SlateEvent): boolean {
  if (ev.type === 'item.updated') {
    return ['body_md', 'title', 'acceptance_md', 'position', 'due_at', 'start_at', 'priority'].includes(
      ev.field ?? '',
    );
  }
  // An agent's progress notes while working (heartbeats): the live bubble shows the latest, the
  // history keeps one line per run of them.
  if (ev.type === 'item.reported') return (ev.after as { state?: string } | null)?.state === 'working';
  return ev.type === 'item.status_changed' || ev.type === 'checklist.updated';
}

/** Activity entries to display rows, oldest first, with folded runs of repeated edits. */
export function timeline(entries: ActivityEntry[]): Row[] {
  const rows: Row[] = [];
  for (const e of entries) {
    if (e.kind === 'comment' && e.comment) {
      rows.push({ kind: 'comment', key: `c:${e.comment.id}`, at: e.at, comment: e.comment });
      continue;
    }
    const ev = e.event;
    if (!ev) continue;
    const last = rows.at(-1);
    // "made this depend on X" already says it: the item.blocked of the same change is noise.
    if (ev.type === 'item.blocked' && last?.kind === 'event' && last.event.type === 'item.linked') {
      const linked = (last.event.after ?? {}) as { kind?: string };
      if (linked.kind === 'blocked_by' && last.event.actor_id === ev.actor_id) continue;
    }
    if (
      last?.kind === 'event' &&
      foldable(ev) &&
      foldKey(last.event) === foldKey(ev) &&
      new Date(ev.at).getTime() - new Date(last.at).getTime() < FOLD_WINDOW_MS
    ) {
      // Keep the first `before` and the latest `after`, so the line reads as the net change.
      last.event = { ...ev, before: last.event.before };
      last.at = ev.at;
      last.times += 1;
      continue;
    }
    rows.push({ kind: 'event', key: `e:${ev.id}`, at: e.at, event: ev, times: 1 });
  }
  return rows;
}

/** Merge a detail's comments and events into entries (used before /activity answers). */
export function entriesFrom(comments: Comment[], events: SlateEvent[]): ActivityEntry[] {
  const hidden = new Set(['item.commented', 'comment.reacted', 'comment.unreacted', 'comment.edited']);
  const out: ActivityEntry[] = [
    ...comments.map((c) => ({ kind: 'comment' as const, at: c.created_at, comment: c, event: null })),
    ...events
      .filter((e) => !hidden.has(e.type))
      .map((e) => ({ kind: 'event' as const, at: e.at, comment: null, event: e })),
  ];
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

const stampFmt = workspaceFormatter('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const stampYearFmt = workspaceFormatter('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const timeOnly = workspaceFormatter('en-US', { hour: 'numeric', minute: '2-digit' });
const dayKey = workspaceFormatter('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' });

/** ClickUp-style stamp: "2:14 PM" today, "Oct 21 at 2:14 PM" this year, with the year otherwise. */
export function stamp(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (dayKey.format(d) === dayKey.format(now)) return timeOnly.format(d);
  const fmt = dayKey.format(d).slice(0, 4) === dayKey.format(now).slice(0, 4) ? stampFmt : stampYearFmt;
  return fmt.format(d).replace(/, (\d{1,2}:\d{2})/, ' at $1');
}

// --- @mentions ----------------------------------------------------------------------------

export const MENTION_RE = /(^|[^\w@])@([a-z][a-z0-9_-]{1,31})/gi;

/** The `@query` being typed at `caret`, if any: where it starts and what is typed so far. */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const m = /(^|[^\w@])@([a-z0-9_-]{0,31})$/i.exec(text.slice(0, caret));
  if (!m) return null;
  return { start: caret - m[2].length - 1, query: m[2].toLowerCase() };
}

/** Replace the `@query` at `start..caret` with `@handle `; returns the text and the new caret. */
export function insertMention(
  text: string,
  start: number,
  caret: number,
  handle: string,
): { text: string; caret: number } {
  const insert = `@${handle} `;
  return { text: text.slice(0, start) + insert + text.slice(caret), caret: start + insert.length };
}

/** Wrap known `@handle`s in rendered (already escaped) HTML, outside of tags. */
export function highlightMentions(html: string, known: (handle: string) => boolean, me?: string): string {
  return html
    .split(/(<[^>]*>)/)
    .map((part) =>
      part.startsWith('<')
        ? part
        : part.replace(MENTION_RE, (all, pre: string, handle: string) => {
            const h = handle.toLowerCase();
            if (!known(h)) return all;
            const cls = h === me ? 'mention me' : 'mention';
            return `${pre}<span class="${cls}">@${handle}</span>`;
          }),
    )
    .join('');
}

// --- reactions ----------------------------------------------------------------------------

export const QUICK_REACTIONS = ['👍', '✅', '🎉', '❤️', '👀', '🙏', '😂', '🚀'];

/** The reactions with the caller's emoji toggled (an optimistic update). */
export function toggleReaction(
  reactions: { emoji: string; account_ids: string[] }[],
  emoji: string,
  me: string,
): { emoji: string; account_ids: string[] }[] {
  const out = reactions.map((r) => ({ ...r, account_ids: [...r.account_ids] }));
  const r = out.find((x) => x.emoji === emoji);
  if (!r) return [...out, { emoji, account_ids: [me] }];
  r.account_ids = r.account_ids.includes(me)
    ? r.account_ids.filter((a) => a !== me)
    : [...r.account_ids, me];
  return out.filter((x) => x.account_ids.length);
}

// --- description task boxes --------------------------------------------------------------

/** Tick or untick the markdown task box (`- [ ]`) on line `n`. */
export function toggleTaskLine(md: string, n: number): string {
  const lines = md.split('\n');
  const line = lines[n];
  if (line === undefined) return md;
  lines[n] = line.replace(/^(\s*[-*+]\s+)\[( |x)\]/i, (_, pre: string, box: string) =>
    `${pre}[${box.trim() ? ' ' : 'x'}]`,
  );
  return lines.join('\n');
}

/** "1.2 MB", "340 KB", "12 B". */
export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A status's color, or its kind's theme color when the list gave it none. */
export function statusColor(s: Pick<Status, 'color' | 'kind'> | null | undefined): string {
  return safeColor(s?.color, `var(--kind-${s?.kind ?? 'open'})`);
}

/** The line a comment carries for each file sent with it (the comment box writes these). */
export function fileLine(filename: string): string {
  return `📎 ${filename}`;
}

/** A comment's text and the task files it names: each `📎 <filename>` line that matches one of
 *  the task's attachments is taken out of the text and returned as that file, so the comment
 *  shows the picture instead of its name. A line whose file is gone stays as text. */
export function commentFiles<T extends { filename: string }>(body: string, attachments: T[]): { text: string; files: T[] } {
  const files: T[] = [];
  const kept: string[] = [];
  for (const line of body.split('\n')) {
    const name = /^\s*📎\s+(.+?)\s*$/u.exec(line)?.[1];
    // The newest attachment of that name: the one just pasted.
    const hit = name ? [...attachments].reverse().find((a) => a.filename === name) : undefined;
    if (hit && !files.includes(hit)) files.push(hit);
    else if (!hit) kept.push(line);
  }
  return { text: kept.join('\n').trim(), files };
}

/** Whether `me` may delete comment `c`: its author, or a workspace owner or admin (the server
 * decides the same way: service/comments.py delete_comment). */
export function canDeleteComment(c: Comment, me: Me | null | undefined): boolean {
  if (!me) return false;
  return c.author_id === me.account.id || me.account.role === 'owner' || me.account.role === 'admin';
}

/** The question asked before a comment is deleted. */
export function deleteCommentQuestion(c: Comment, me: string, authorName: string | undefined): string {
  const whose = c.author_id === me ? 'your comment' : `${authorName ?? 'this person'}’s comment`;
  return `Delete ${whose}? It goes for good; the history keeps a record that it was deleted.`;
}
