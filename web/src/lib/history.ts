// An item's event log in plain English: "My Agent moved this to Ready · 2 min ago via MCP".

import { shownProgress } from './conversation';
import { priorityLabel } from './priority';
import { ago, formatEstimate, shortDate } from './format';
import { digestText, leadText, type DigestCounts } from './reminders';
import type { Priority, SlateEvent } from './types';

export interface HistoryContext {
  /** Display name for an account id, if known. */
  name: (accountId: string) => string | undefined;
  /** Status name for a status id, if known. */
  status: (statusId: string) => string | undefined;
  /** An item's title by id, for dependencies. */
  title?: (itemId: string) => string | undefined;
  now?: Date;
}

const SOURCES: Record<string, string> = {
  ui: 'via the board',
  api: 'via API',
  mcp: 'via MCP',
  system: 'automatically',
};

function list(xs: string[]): string {
  return xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs.at(-1)}`;
}

function arr(v: unknown): string[] {
  return Array.isArray(v) ? (v as string[]) : [];
}

function obj(v: unknown): Record<string, string | undefined> {
  return v && typeof v === 'object' ? (v as Record<string, string | undefined>) : {};
}

/** `#<item id>` in a chat message as `#` plus the task's title, when it is known (as chat
 * shows it). */
function taskRefs(text: string, ctx: HistoryContext): string {
  return text.replace(/(^|[^\w/#&])#([0-9A-Za-z]{12})(?![0-9A-Za-z])/g, (all, pre: string, id: string) => {
    const title = ctx.title?.(id);
    return title ? `${pre}#${title}` : all;
  });
}

function diff(before: unknown, after: unknown): [string[], string[]] {
  const b = arr(before);
  const a = arr(after);
  return [a.filter((x) => !b.includes(x)), b.filter((x) => !a.includes(x))];
}

function dateChange(label: string, after: unknown, now?: Date): string {
  return after ? `set the ${label} to ${shortDate(String(after), now)}` : `cleared the ${label}`;
}

/** What happened, without the actor: "moved this to Ready". */
export function verb(ev: SlateEvent, ctx: HistoryContext): string {
  switch (ev.type) {
    case 'item.created':
      return 'created this';
    case 'item.status_changed': {
      const name = ctx.status(String(ev.after));
      return name ? `moved this to ${name}` : 'changed the status';
    }
    case 'item.assigned': {
      const [added, removed] = diff(ev.before, ev.after);
      const bits = [];
      if (added.length) bits.push(`assigned ${list(added)}`);
      if (removed.length) bits.push(`unassigned ${list(removed)}`);
      return bits.join(' and ') || 'changed the assignees';
    }
    case 'item.archived':
      return 'put this away';
    case 'item.unarchived':
      return 'brought this back';
    case 'item.deleted':
      return 'deleted this';
    case 'item.trashed':
      return 'moved this to the trash';
    case 'item.restored':
      return 'restored this from the trash';
    case 'item.purged':
      return 'deleted this for good';
    case 'item.split':
      return `split this into ${arr(ev.after).length} subtasks`;
    case 'item.commented':
      return 'commented';
    case 'message.posted':
    case 'message.edited': {
      const a = (ev.after ?? {}) as { body_md?: string; parent_id?: string | null; channel_name?: string };
      const text = taskRefs((a.body_md ?? '').replace(/\s+/g, ' ').trim(), ctx);
      const snippet = text.length > 80 ? `${text.slice(0, 79)}…` : text;
      const how = ev.type === 'message.edited' ? 'edited a message' : a.parent_id ? 'replied' : 'wrote';
      return snippet ? `${how}: “${snippet}”` : how;
    }
    case 'doc.mentioned': {
      const d = (ev.after ?? {}) as { mentions?: string[]; title?: string; excerpt?: string };
      const where = d.title ? ` in “${d.title}”` : ' in a doc';
      return `mentioned ${list(arr(d.mentions).map((h) => `@${h}`))}${where}${d.excerpt ? `: “${taskRefs(d.excerpt, ctx)}”` : ''}`;
    }
    case 'item.mentioned': {
      const who = arr((ev.after as { mentions?: string[] } | null)?.mentions);
      return `mentioned ${list(who.map((h) => `@${h}`))}`;
    }
    case 'item.watched':
      return ev.after === ev.actor_id
        ? 'started watching this'
        : `added ${ctx.name(String(ev.after)) ?? 'someone'} as a watcher`;
    case 'item.unwatched':
      return ev.before === ev.actor_id
        ? 'stopped watching this'
        : `removed ${ctx.name(String(ev.before)) ?? 'someone'} as a watcher`;
    case 'item.attachment_added':
      return `attached ${obj(ev.after).filename ?? 'a file'}`;
    case 'item.attachment_removed':
      return `removed the attachment ${obj(ev.before).filename ?? ''}`.trim();
    case 'checklist.created':
      return `added the checklist “${obj(ev.after).name ?? 'Checklist'}”`;
    case 'checklist.deleted':
      return `deleted the checklist “${obj(ev.before).name ?? 'Checklist'}”`;
    case 'checklist.updated':
      return ev.field === 'name' ? `renamed a checklist to “${String(ev.after)}”` : 'reordered a checklist';
    case 'checklist.entry_added':
      return `added “${obj(ev.after).title ?? 'an entry'}” to a checklist`;
    case 'checklist.entry_removed':
      return `removed “${obj(ev.before).title ?? 'an entry'}” from a checklist`;
    case 'checklist.entry_checked':
      return `checked “${obj(ev.after).title ?? 'an entry'}”`;
    case 'checklist.entry_unchecked':
      return `unchecked “${obj(ev.after).title ?? 'an entry'}”`;
    case 'checklist.entry_updated':
      if (ev.field === 'title') return `renamed a checklist entry to “${String(ev.after)}”`;
      if (ev.field === 'assignee_id') {
        return ev.after
          ? `assigned a checklist entry to ${ctx.name(String(ev.after)) ?? 'someone'}`
          : 'unassigned a checklist entry';
      }
      return 'reordered a checklist';
    case 'item.linked':
    case 'item.unlinked': {
      const link = obj(ev.type === 'item.linked' ? ev.after : ev.before);
      const other = `“${(link.item_id && ctx.title?.(link.item_id)) ?? 'another task'}”`;
      if (ev.type === 'item.unlinked') return `removed the link with ${other}`;
      if (link.kind === 'blocks') return `marked this as blocking ${other}`;
      if (link.kind === 'blocked_by') return `made this depend on ${other}`;
      if (link.kind === 'duplicate_of') return `marked this as a duplicate of ${other}`;
      if (link.kind === 'duplicated_by') return `marked ${other} as a duplicate of this`;
      return `linked this to ${other}`;
    }
    case 'item.blocked': {
      const ids = arr(obj(ev.after).blocked_by as unknown);
      const names = ids.map((i) => `“${ctx.title?.(i) ?? 'another task'}”`);
      return `made this depend on ${list(names) || 'an open task'}`;
    }
    case 'item.unblocked': {
      const ids = arr(obj(ev.after).cleared_by as unknown);
      const names = ids.map((i) => `“${ctx.title?.(i) ?? 'another task'}”`);
      return names.length
        ? `freed this: it no longer depends on ${list(names)}`
        : 'freed this: it no longer depends on an open task';
    }
    case 'item.reported': {
      // An agent's report; the note itself arrives as the comment beside it.
      const r = obj(ev.after) as { state?: string; progress?: unknown };
      const shown = shownProgress(r.progress);
      const pct = shown != null ? ` (${shown}%)` : '';
      switch (r.state) {
        case 'working':
          return `is working on this${pct}`;
        case 'blocked':
          return `is blocked on this${pct}`;
        case 'review':
          return `marked this ready for review${pct}`;
        case 'done':
          return 'finished this';
        default:
          return 'stopped working on this';
      }
    }
    case 'item.reminder_set':
    case 'item.reminder_removed': {
      const r = obj(ev.type === 'item.reminder_set' ? ev.after : ev.before) as {
        account_id?: string;
        remind_at?: string;
        before_minutes?: number | null;
      };
      const whom = r.account_id && r.account_id !== ev.actor_id ? ` for ${ctx.name(r.account_id) ?? 'someone'}` : '';
      const when = r.remind_at
        ? ` on ${shortDate(r.remind_at, ctx.now)}`
        : typeof r.before_minutes === 'number'
          ? ` ${leadText(r.before_minutes)}`
          : '';
      return `${ev.type === 'item.reminder_set' ? 'set' : 'removed'} a reminder${whom}${when}`;
    }
    case 'item.reminder': {
      const r = obj(ev.after);
      const whom = (r.account_id && ctx.name(r.account_id)) || 'someone';
      return `reminded ${whom}${r.note ? `: ${r.note}` : ''}`;
    }
    case 'health.alert': {
      const h = (ev.after ?? {}) as { label?: string; failures?: number; error?: string | null };
      return `${h.label ?? 'A background job'} failed ${h.failures ?? 'several'} runs in a row${h.error ? `: ${h.error}` : ''}`;
    }
    case 'health.recovered':
      return `${obj(ev.after).label ?? 'A background job'} works again`;
    case 'account.digest': {
      const after = ev.after as { counts?: DigestCounts; agents?: { next?: unknown[] }[] } | null;
      const crews = (after?.agents ?? []).reduce((n, a) => n + (a.next?.length ?? 0), 0);
      const tail = crews ? `; agents today: ${crews} task${crews === 1 ? '' : 's'}` : '';
      return `sent the daily digest: ${digestText(after?.counts)}${tail}`;
    }
    case 'recurrence.spawned':
      return 'created this from a recurring rule';
    case 'recurrence.rolled_forward':
      return 'rolled this forward to the next occurrence';
    case 'recurrence.reopened': {
      const due = (ev.after as { due_at?: string } | null)?.due_at;
      return due ? `reopened this for the next time, due ${shortDate(due, new Date())}` : 'reopened this for the next time';
    }
    case 'recurrence.advanced':
      return 'scheduled the next occurrence';
    case 'item.due':
      return 'marked this due now';
    case 'item.overdue':
      return 'marked this overdue';
    case 'item.due_soon': {
      const h = Number((ev.after as { lead_hours?: number } | null)?.lead_hours ?? 24);
      return `flagged this due within ${h} hour${h === 1 ? '' : 's'}`;
    }
    case 'item.updated':
    case 'item.moved':
      break;
    default:
      return ev.type.replace('.', ' ').replace(/_/g, ' ');
  }
  switch (ev.field) {
    case 'title':
      return `renamed this to “${String(ev.after)}”`;
    case 'body_md':
      return 'edited the description';
    case 'acceptance_md':
      return 'edited what “done” means';
    case 'due_at':
      return dateChange('due date', ev.after, ctx.now);
    case 'start_at':
      return dateChange('start date', ev.after, ctx.now);
    case 'priority':
      return ev.after ? `set priority to ${typeof ev.after === 'string' && ['urgent', 'high', 'normal', 'low'].includes(ev.after) ? priorityLabel(ev.after as Priority) : String(ev.after)}` : 'cleared the priority';
    case 'completed_at':
      return ev.after ? 'marked this complete' : 'reopened this';
    case 'waiting_on_account_id': {
      if (!ev.after) return 'stopped waiting on anyone';
      return `marked this waiting on ${ctx.name(String(ev.after)) ?? 'someone'}`;
    }
    case 'tags': {
      const [added, removed] = diff(ev.before, ev.after);
      const bits = [];
      if (added.length) bits.push(`tagged this ${added.map((t) => `#${t}`).join(' ')}`);
      if (removed.length) bits.push(`removed ${removed.map((t) => `#${t}`).join(' ')}`);
      return bits.join(' and ') || 'changed the tags';
    }
    case 'position':
      return 'reordered this';
    case 'list_id':
      return 'moved this to another list';
    case 'occurrence_at':
      return 'moved the occurrence date';
    case 'estimate_minutes':
      return ev.after ? `set the estimate to ${formatEstimate(Number(ev.after))}` : 'cleared the estimate';
    default:
      return `changed ${String(ev.field ?? 'this').replace(/_/g, ' ')}`;
  }
}

export function describe(ev: SlateEvent, ctx: HistoryContext): string {
  const who = ctx.name(ev.actor_id) ?? 'Someone';
  return `${who} ${verb(ev, ctx)} · ${ago(ev.at, ctx.now)} ${SOURCES[ev.source] ?? ''}`.trim();
}
