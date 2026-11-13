// The views engine (docs/views.md): which items a view shows, in what order, in which
// groups, and what dropping a card on a group means. Pure: everything it needs (the clock, the
// lists, who "me" is) comes in through `Ctx`, so Vitest drives it with fixed instants.
//
// Speed: `runView` makes one pass to filter, one sort, one pass to group. With ~3,000 items that
// is a few milliseconds; components call it once per scope inside a $derived, never per card.

import { workspaceDay } from './quickadd';
import { priorityLong } from './priority';
import { workspaceFormatter, workspaceTimezone } from './timezone';
import type {
  Account,
  Item,
  List,
  Priority,
  Section,
  Status,
  StatusKind,
} from './types';

export const LAYOUTS = ['board', 'list'] as const;
export type Layout = (typeof LAYOUTS)[number];

/** Settings only removed layouts used; a stored view or draft may still carry them. */
const RETIRED_KEYS = ['calendar_span', 'timeline_zoom'];

/** A config with any removed layout read as `list`, as the server reads it, and without the
 *  settings only those layouts used. */
export function withKnownLayout(c: ViewConfig): ViewConfig {
  const out = { ...c } as ViewConfig & Record<string, unknown>;
  for (const k of RETIRED_KEYS) delete out[k];
  if (!(LAYOUTS as readonly string[]).includes(out.layout)) out.layout = 'list';
  return out;
}
export type GroupBy =
  | 'none'
  | 'status'
  | 'status_kind'
  | 'due'
  | 'priority'
  | 'assignee'
  | 'tag'
  | 'list'
  | 'section';
export type SortField =
  | 'manual'
  | 'due'
  | 'priority'
  | 'title'
  | 'created'
  | 'updated'
  | 'start'
  | 'estimate';
export type CardField =
  | 'description'
  | 'subtasks'
  | 'comments'
  | 'checklist'
  | 'attachments'
  | 'due'
  | 'start'
  | 'priority'
  | 'tags'
  | 'status'
  | 'assignees'
  | 'list'
  | 'waiting'
  | 'recurrence'
  | 'estimate'
  | 'blocked';

export interface SortKey {
  field: SortField;
  dir: 'asc' | 'desc';
}

export interface FilterRule {
  field: string;
  cmp: string;
  value?: unknown;
}

export interface FilterGroup {
  op: 'and' | 'or';
  rules: (FilterRule | FilterGroup)[];
}

export interface ViewConfig {
  layout: Layout;
  group_by: GroupBy;
  due_window_days: number;
  sort: SortKey[];
  filter: FilterGroup;
  show_closed: boolean;
  show_subtasks: boolean;
  hide_empty_groups: boolean;
  density: 'comfortable' | 'compact';
  card_fields: CardField[];
  /** Lines of title a card shows before "…"; 0 shows it all. */
  title_lines: number;
  /** Empty columns shrink to a thin strip (ClickUp), still a drop target. */
  collapse_empty: boolean;
}

export type Scope =
  | { kind: 'everything' }
  | { kind: 'section'; id: string }
  | { kind: 'list'; id: string };

export interface Ctx {
  now: Date;
  lists: Record<string, List>;
  sections: Section[];
  accounts: Record<string, Account>;
  me: Account | null;
  /** Free-text search from the toolbar: not part of the saved view. */
  q?: string;
}

/** What dropping a card on a group does. `null` means the group is not a drop target. */
export type Drop =
  | { kind: 'status'; status: Status }
  | { kind: 'status_kind'; value: StatusKind }
  | { kind: 'due'; day: string | null }
  | { kind: 'priority'; value: Priority | null }
  | { kind: 'assignee'; handle: string | null }
  | { kind: 'list'; listId: string }
  /** Swap the tag of the column the card came from for this one (null: just drop that tag). */
  | { kind: 'tag'; tag: string | null }
  /** Move into the section's list (refused, with the reason, when it has several). */
  | { kind: 'section'; sectionId: string }
  | { kind: 'done' }
  | null;

export interface Group {
  key: string;
  title: string;
  /** A second line under the title, e.g. the date under "Today". */
  hint?: string;
  color?: string;
  hot?: boolean;
  items: Item[];
  drop: Drop;
  /** Why a card cannot be dropped here (shown when someone tries), for groups with no drop. */
  refuse?: string;
}

/** Every card field, in the order a card shows them. */
export const ALL_CARD_FIELDS: CardField[] = [
  'description',
  'subtasks',
  'comments',
  'checklist',
  'attachments',
  'due',
  'start',
  'priority',
  'tags',
  'status',
  'assignees',
  'list',
  'waiting',
  'recurrence',
  'estimate',
  'blocked',
];

/** What a card shows before anyone customises it: the classic task card, plus Depends on (shown only
 * while the task waits on an open one). */
export const DEFAULT_CARD_FIELDS: CardField[] = ALL_CARD_FIELDS.filter(
  (f) => f !== 'start' && f !== 'estimate',
);

export const CARD_FIELD_NAMES: Record<CardField, string> = {
  description: 'Description icon',
  subtasks: 'Subtask count',
  comments: 'Comment count',
  checklist: 'Checklist progress',
  attachments: 'Attachment count',
  due: 'Due date',
  start: 'Start date',
  priority: 'Priority',
  tags: 'Tags',
  status: 'Status',
  assignees: 'Assignees',
  list: 'List',
  waiting: 'Waiting on',
  recurrence: 'Recurring',
  estimate: 'Time estimate',
  blocked: 'Depends on',
};

export const KINDS: StatusKind[] = ['open', 'active', 'done', 'cancelled'];
export const KIND_NAMES: Record<StatusKind, string> = {
  open: 'Open',
  active: 'Active',
  done: 'Done',
  cancelled: 'Cancelled',
};
export const PRIORITIES: Priority[] = ['urgent', 'high', 'normal', 'low'];
const PRIORITY_RANK: Record<Priority, number> = { urgent: 3, high: 2, normal: 1, low: 0 };

export const GROUP_NAMES: Record<GroupBy, string> = {
  none: 'None',
  status: 'Status',
  status_kind: 'Status kind',
  due: 'Due date',
  priority: 'Priority',
  assignee: 'Assignee',
  tag: 'Tag',
  list: 'List',
  section: 'Section',
};

export const SORT_NAMES: Record<SortField, string> = {
  manual: 'Manual',
  due: 'Due date',
  priority: 'Priority',
  title: 'Title',
  created: 'Created',
  updated: 'Updated',
  start: 'Start date',
  estimate: 'Time estimate',
};

/** Display name of a group-by or sort key. */
export function keyName(key: GroupBy | SortField): string {
  return (GROUP_NAMES as Record<string, string>)[key] ?? (SORT_NAMES as Record<string, string>)[key] ?? key;
}

// --- defaults ----------------------------------------------------------------------------------

export function baseConfig(): ViewConfig {
  return {
    layout: 'board',
    group_by: 'none',
    due_window_days: 6,
    sort: [{ field: 'manual', dir: 'asc' }],
    filter: { op: 'and', rules: [] },
    show_closed: false,
    show_subtasks: false,
    hide_empty_groups: false,
    density: 'comfortable',
    card_fields: [...DEFAULT_CARD_FIELDS],
    title_lines: 2,
    collapse_empty: true,
  };
}

/** The view a scope shows before anyone saves one there. */
export function defaultConfig(scope: Scope): ViewConfig {
  const c = baseConfig();
  if (scope.kind === 'list') {
    // A list's own words: its status columns, in its order.
    c.group_by = 'status';
    c.card_fields = DEFAULT_CARD_FIELDS.filter((f) => f !== 'list' && f !== 'status');
    // A list's columns are its workflow: they stay open even when empty (ClickUp does the same).
    c.collapse_empty = false;
    return c;
  }
  // Everything and sections: a due board, sorted by due date, with today's
  // finished work in a Done column at the end (earlier days stay hidden unless show_closed).
  c.group_by = 'due';
  c.sort = [
    { field: 'due', dir: 'asc' },
    { field: 'priority', dir: 'desc' },
  ];
  return c;
}

/** Fill in whatever a stored config leaves out, so old or agent-made views always render. */
export function normalizeConfig(raw: Partial<ViewConfig> | null | undefined, scope: Scope): ViewConfig {
  const c = withKnownLayout({ ...baseConfig(), ...(raw ?? {}) } as ViewConfig);
  if (c.group_by === 'status' && scope.kind !== 'list') c.group_by = 'status_kind';
  if (!Array.isArray(c.sort)) c.sort = [];
  if (!c.filter || !Array.isArray(c.filter.rules)) c.filter = { op: 'and', rules: [] };
  if (!Array.isArray(c.card_fields)) c.card_fields = [...DEFAULT_CARD_FIELDS];
  if (typeof c.title_lines !== 'number') c.title_lines = 2;
  if (typeof raw?.collapse_empty !== 'boolean') c.collapse_empty = scope.kind !== 'list';
  return c;
}

export function sameConfig(a: ViewConfig, b: ViewConfig): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// --- dates in workspace ---------------------------------------------------------------------------

// Item dates repeat across renders: cache the workspace day of each stored instant.
const dayCache = new Map<string, string>();

/** The workspace calendar day (YYYY-MM-DD) of a stored UTC instant. */
export function dayOf(iso: string): string {
  const key = `${workspaceTimezone()}|${iso}`;
  let d = dayCache.get(key);
  if (d === undefined) {
    d = workspaceDay(new Date(iso));
    if (dayCache.size > 50_000) dayCache.clear();
    dayCache.set(key, d);
  }
  return d;
}

export function addDays(ymd: string, days: number): string {
  const d = new Date(`${ymd}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** 0 = Sunday … 6 = Saturday, for a YYYY-MM-DD calendar date. */
export function weekday(ymd: string): number {
  return new Date(`${ymd}T12:00:00Z`).getUTCDay();
}

const partsFmt = workspaceFormatter('en-US', {
  hourCycle: 'h23',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function wallClock(at: Date): { ymd: string; h: number; m: number; s: number } {
  const p = Object.fromEntries(partsFmt.formatToParts(at).map((x) => [x.type, x.value]));
  return {
    ymd: `${p.year}-${p.month}-${p.day}`,
    h: Number(p.hour) % 24,
    m: Number(p.minute),
    s: Number(p.second),
  };
}

/** The instant of a workspace wall-clock time. DST-safe: the offset is read at the result. */
export function workspaceInstant(ymd: string, h = 17, m = 0): Date {
  const [y, mo, d] = ymd.split('-').map(Number);
  const asUtc = Date.UTC(y, mo - 1, d, h, m);
  let guess = asUtc;
  for (let i = 0; i < 2; i++) {
    const w = wallClock(new Date(guess));
    const [wy, wmo, wd] = w.ymd.split('-').map(Number);
    const shown = Date.UTC(wy, wmo - 1, wd, w.h, w.m);
    guess += asUtc - shown;
  }
  return new Date(guess);
}

/** Move an instant to another workspace day, keeping its workspace time of day. */
export function onDay(ymd: string, keepTimeOf: string | null): string {
  if (!keepTimeOf) return workspaceInstant(ymd).toISOString();
  const w = wallClock(new Date(keepTimeOf));
  return workspaceInstant(ymd, w.h, w.m).toISOString();
}

const dayHead = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
});

/** "Sat Oct 24" for a YYYY-MM-DD. */
export function dayTitle(ymd: string): string {
  return dayHead.format(new Date(`${ymd}T12:00:00Z`)).replace(',', '');
}

// --- visibility, filter, sort -------------------------------------------------------------------

export function isClosed(item: Item): boolean {
  return item.status?.kind === 'done' || item.status?.kind === 'cancelled';
}

function finishedBefore(item: Item, today: string): boolean {
  return dayOf(item.completed_at ?? item.updated_at) < today;
}

export function inScope(item: Item, scope: Scope, lists: Record<string, List>): boolean {
  if (scope.kind === 'everything') return !!lists[item.list_id];
  if (scope.kind === 'list') return item.list_id === scope.id;
  return lists[item.list_id]?.section_id === scope.id;
}

const asArray = (v: unknown): unknown[] => (Array.isArray(v) ? v : v == null || v === '' ? [] : [v]);
const asBool = (v: unknown): boolean => v === true || v === 'true';

function dateRule(iso: string | null, cmp: string, value: unknown, item: Item, ctx: Ctx, today: string): boolean {
  if (cmp === 'empty') return !iso;
  if (cmp === 'not_empty') return !!iso;
  if (!iso) return false;
  const day = dayOf(iso);
  const v = asArray(value).map(String);
  switch (cmp) {
    case 'overdue':
      return !isClosed(item) && Date.parse(iso) < ctx.now.getTime();
    case 'today':
      return day === today;
    case 'today_or_earlier':
      return day <= today;
    case 'tomorrow':
      return day === addDays(today, 1);
    case 'this_week': {
      // Monday to Sunday around today.
      const monday = addDays(today, -((weekday(today) + 6) % 7));
      return day >= monday && day <= addDays(monday, 6);
    }
    case 'next_n_days': {
      const n = Number(v[0] ?? 7) || 0;
      return day >= today && day <= addDays(today, n);
    }
    case 'before':
      return !!v[0] && day < resolveDay(v[0], today);
    case 'after':
      return !!v[0] && day > resolveDay(v[0], today);
    case 'between':
      return !!v[0] && !!v[1] && day >= resolveDay(v[0], today) && day <= resolveDay(v[1], today);
  }
  return true;
}

/** Dates in rules may be literal (2026-11-02) or relative to today ("today", "+7", "-3"). */
function resolveDay(v: string, today: string): string {
  if (v === 'today') return today;
  if (/^[+-]\d+$/.test(v)) return addDays(today, Number(v));
  return v;
}

function meHandle(ctx: Ctx): string | null {
  return ctx.me?.handle ?? null;
}

function accountIdOf(v: string, ctx: Ctx): string | null {
  if (v === 'me') return ctx.me?.id ?? null;
  if (ctx.accounts[v]) return v;
  return Object.values(ctx.accounts).find((a) => a.handle === v)?.id ?? null;
}

// Share completeness between evaluation, badges and summaries. False and zero are values;
// an empty selection/text or half-entered range is a draft, not a predicate.
const NEEDS_VALUE = new Set([
  'is', 'is_not', 'any', 'all', 'none', 'before', 'after', 'between',
  'contains', 'gt', 'lt', 'within_days', 'next_n_days',
]);

export function isActiveRule(rule: FilterRule): boolean {
  if (!NEEDS_VALUE.has(rule.cmp)) return true;
  const values = asArray(rule.value);
  const filled = (v: unknown) => v != null && String(v).trim() !== '';
  return rule.cmp === 'between'
    ? values.length === 2 && values.every(filled)
    : values.some(filled);
}

export function matchRule(item: Item, rule: FilterRule, ctx: Ctx, today: string): boolean {
  if (!isActiveRule(rule)) return true;
  const v = asArray(rule.value);
  const strs = v.map(String);
  switch (rule.field) {
    case 'status': {
      const hit = !!item.status && strs.includes(item.status.id);
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'status_kind': {
      const hit = strs.includes(item.status?.kind ?? 'open');
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'assignee': {
      if (rule.cmp === 'empty') return item.assignees.length === 0;
      if (rule.cmp === 'not_empty') return item.assignees.length > 0;
      const handles = strs.map((h) => (h === 'me' ? meHandle(ctx) : h));
      const hit = item.assignees.some((a) => handles.includes(a.handle));
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'tag': {
      const want = strs.map((t) => t.toLowerCase());
      if (rule.cmp === 'empty') return item.tags.length === 0;
      if (rule.cmp === 'all') return want.every((t) => item.tags.includes(t));
      const hit = want.some((t) => item.tags.includes(t));
      return rule.cmp === 'none' ? !hit : hit;
    }
    case 'priority': {
      if (rule.cmp === 'empty') return !item.priority;
      const hit = strs.includes(item.priority ?? 'none');
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'due':
      return dateRule(item.due_at, rule.cmp, rule.value, item, ctx, today);
    case 'start':
      return dateRule(item.start_at, rule.cmp, rule.value, item, ctx, today);
    case 'list': {
      const hit = strs.includes(item.list_id);
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'section': {
      const hit = strs.includes(ctx.lists[item.list_id]?.section_id ?? '');
      return rule.cmp === 'is_not' ? !hit : hit;
    }
    case 'text': {
      const q = String(rule.value ?? '').trim().toLowerCase();
      return !q || item.title.toLowerCase().includes(q) || item.body_md.toLowerCase().includes(q);
    }
    case 'recurring':
      return !!item.recurrence_id === asBool(rule.value);
    case 'has_subtasks':
      return item.subtask_count > 0 === asBool(rule.value);
    case 'waiting_on': {
      if (rule.cmp === 'empty') return !item.waiting_on_account_id;
      if (rule.cmp === 'not_empty') return !!item.waiting_on_account_id;
      const ids = strs.map((s) => accountIdOf(s, ctx));
      return !!item.waiting_on_account_id && ids.includes(item.waiting_on_account_id);
    }
    case 'created':
    case 'updated': {
      const at = rule.field === 'created' ? item.created_at : item.updated_at;
      const n = Number(strs[0] ?? 7) || 0;
      return ctx.now.getTime() - Date.parse(at) <= n * 86_400_000;
    }
    case 'blocked':
      return !!item.blocked === asBool(rule.value);
    case 'estimate': {
      const e = item.estimate_minutes ?? null;
      if (rule.cmp === 'empty') return e === null;
      if (rule.cmp === 'not_empty') return e !== null;
      if (!strs[0]) return true;
      if (e === null) return false;
      return rule.cmp === 'gt' ? e > Number(strs[0]) : e < Number(strs[0]);
    }
  }
  return true; // unknown fields never hide anything
}

export function isGroup(r: FilterRule | FilterGroup): r is FilterGroup {
  return 'op' in r && Array.isArray((r as FilterGroup).rules);
}

/** Empty/incomplete branches are absent, not true (which would widen an OR group). */
function matchBranch(item: Item, node: FilterGroup, ctx: Ctx, today: string): boolean | null {
  let active = false;
  for (const rule of node.rules) {
    const result = isGroup(rule)
      ? matchBranch(item, rule, ctx, today)
      : isActiveRule(rule) ? matchRule(item, rule, ctx, today) : null;
    if (result === null) continue;
    active = true;
    if (node.op === 'or' && result) return true;
    if (node.op === 'and' && !result) return false;
  }
  return active ? node.op === 'and' : null;
}

/** Only a wholly inactive root matches everything. Draft nodes remain editable in the config. */
export function matchFilter(item: Item, node: FilterGroup, ctx: Ctx, today = workspaceDay(ctx.now)): boolean {
  return matchBranch(item, node, ctx, today) ?? true;
}

export function isActiveFilter(node: FilterRule | FilterGroup): boolean {
  return isGroup(node) ? node.rules.some(isActiveFilter) : isActiveRule(node);
}

/** Active leaf rules: the number on the Filter button, excluding unfinished drafts. */
export function ruleCount(node: FilterGroup): number {
  return node.rules.reduce((n, r) => n + (isGroup(r) ? ruleCount(r) : Number(isActiveRule(r))), 0);
}

function sortValue(item: Item, field: SortField, ctx?: Ctx): string | number | null {
  switch (field) {
    case 'due':
      return item.due_at; // UTC ISO text (…Z): string order is time order, to the second
    case 'start':
      return item.start_at;
    case 'priority':
      return item.priority ? PRIORITY_RANK[item.priority] : null;
    case 'title':
      return item.title.toLowerCase();
    case 'created':
      return item.created_at;
    case 'updated':
      return item.updated_at;
    case 'manual':
      return item.position;
    case 'estimate':
      return item.estimate_minutes ?? null;
  }
}

/** True when a view shows items in their manual order (dragging between two keeps them there). */
export function isManual(sort: SortKey[]): boolean {
  const first = sort[0];
  return !first || (first.field === 'manual' && first.dir === 'asc');
}

/** Multi-key comparator. Empty values sort last whichever way a key points; ties are stable. */
export function comparator(sort: SortKey[], ctx?: Ctx): (a: Item, b: Item) => number {
  const keys = sort.length ? sort : [{ field: 'manual' as const, dir: 'asc' as const }];
  // Each item's keys are worked out once, not once per comparison.
  const memo = new Map<Item, (string | number | null)[]>();
  const vals = (i: Item) => {
    let v = memo.get(i);
    if (!v) memo.set(i, (v = keys.map((k) => sortValue(i, k.field, ctx))));
    return v;
  };
  const cmp = compareKeys(keys);
  return (a, b) => cmp(vals(a), a, vals(b), b);
}

function compareKeys(keys: SortKey[]) {
  return (va: (string | number | null)[], a: Item, vb: (string | number | null)[], b: Item) => {
    for (let n = 0; n < keys.length; n++) {
      const x = va[n];
      const y = vb[n];
      if (x === y) continue;
      if (x === null) return 1;
      if (y === null) return -1;
      const c = x < y ? -1 : 1;
      return keys[n].dir === 'desc' ? -c : c;
    }
    return a.position - b.position || textOrder(a.created_at, b.created_at) || textOrder(a.id, b.id);
  };
}

// Code-unit order: what localeCompare gave for ISO dates and ids, many times faster.
const textOrder = (x: string, y: string) => (x < y ? -1 : x > y ? 1 : 0);

/** `items` sorted as `comparator` would, with each item's keys in an array beside it rather
 * than a map lookup per comparison (20,000 items: about 300,000 comparisons). */
export function sortItems(items: Item[], sort: SortKey[], ctx?: Ctx): Item[] {
  const keys = sort.length ? sort : [{ field: 'manual' as const, dir: 'asc' as const }];
  const cmp = compareKeys(keys);
  const rows = items.map((i) => ({ i, v: keys.map((k) => sortValue(i, k.field, ctx)) }));
  rows.sort((a, b) => cmp(a.v, a.i, b.v, b.i));
  return rows.map((r) => r.i);
}

// --- grouping -------------------------------------------------------------------------------------

/** The due column an item falls in: done | overdue | today | YYYY-MM-DD | future | none. */
let lastWindow = { today: '', window: -1, end: '' };
/** addDays(today, window), worked out once per day rather than once per item. */
function windowEnd(today: string, window: number): string {
  if (lastWindow.today !== today || lastWindow.window !== window) {
    lastWindow = { today, window, end: addDays(today, window) };
  }
  return lastWindow.end;
}

export function dueBucket(item: Item, today: string, window: number): string {
  if (isClosed(item)) return 'done';
  if (!item.due_at) return 'none';
  const day = dayOf(item.due_at);
  if (day < today) return 'overdue';
  if (day === today) return 'today';
  if (day > windowEnd(today, window)) return 'future';
  return day;
}

function dueGroups(today: string, window: number): Group[] {
  const g: Group[] = [
    {
      key: 'overdue',
      title: 'Overdue',
      hot: true,
      items: [],
      drop: null,
      refuse: 'Overdue fills itself with late tasks. Drop on Today or a day to reschedule.',
    },
    { key: 'today', title: 'Today', hint: dayTitle(today), items: [], drop: { kind: 'due', day: today } },
  ];
  for (let i = 1; i <= window; i++) {
    const day = addDays(today, i);
    g.push({
      key: day,
      // "Tomorrow · Sat Oct 24", then "Sun · Oct 25": the weekday leads, as on a classic due board.
      title: i === 1 ? 'Tomorrow' : dayTitle(day).split(' ')[0],
      hint: i === 1 ? dayTitle(day) : dayTitle(day).split(' ').slice(1).join(' '),
      items: [],
      drop: { kind: 'due', day },
    });
  }
  g.push({
    key: 'future',
    title: 'Future',
    hint: `after ${dayTitle(addDays(today, window))}`,
    items: [],
    drop: { kind: 'due', day: addDays(today, window + 1) },
  });
  g.push({ key: 'none', title: 'No due date', items: [], drop: { kind: 'due', day: null } });
  return g;
}

function listOrder(ctx: Ctx): List[] {
  const sectionPos = new Map(ctx.sections.map((s, i) => [s.id, i]));
  return Object.values(ctx.lists).sort(
    (a, b) =>
      (sectionPos.get(a.section_id) ?? 999) - (sectionPos.get(b.section_id) ?? 999) ||
      a.position - b.position,
  );
}

function sortedStatuses(list: List): Status[] {
  return [...list.statuses].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
}

/** Empty groups that exist anyway: every status of the list, every kind, every priority… */
function skeleton(config: ViewConfig, scope: Scope, ctx: Ctx, today: string): Group[] {
  switch (config.group_by) {
    case 'none':
      return [{ key: 'all', title: 'All items', items: [], drop: null }];
    case 'due':
      return dueGroups(today, config.due_window_days);
    case 'status': {
      const list = scope.kind === 'list' ? ctx.lists[scope.id] : undefined;
      const g: Group[] = (list ? sortedStatuses(list) : []).map((s) => ({
        key: s.id,
        title: s.name,
        color: s.color ?? `var(--kind-${s.kind})`,
        items: [],
        drop: { kind: 'status', status: s },
      }));
      g.push({ key: 'nostatus', title: 'No status', items: [], drop: null, refuse: 'Drop on a status column instead.' });
      return g;
    }
    case 'status_kind':
      return KINDS.map((k) => ({
        key: k,
        title: KIND_NAMES[k],
        color: `var(--kind-${k})`,
        items: [],
        drop: { kind: 'status_kind', value: k },
      }));
    case 'priority':
      return [
        ...PRIORITIES.map((p) => ({
          key: p,
          title: priorityLong(p),
          color: `var(--prio-${p})`,
          items: [] as Item[],
          drop: { kind: 'priority', value: p } as Drop,
        })),
        { key: 'none', title: 'No priority', items: [], drop: { kind: 'priority', value: null } },
      ];
    case 'assignee':
      return [{ key: '', title: 'Unassigned', items: [], drop: { kind: 'assignee', handle: null } }];
    case 'tag':
      return [{ key: '', title: 'No tag', items: [], drop: { kind: 'tag', tag: null } }];
    case 'list': {
      const lists = listOrder(ctx).filter(
        (l) => scope.kind === 'everything' || (scope.kind === 'section' ? l.section_id === scope.id : l.id === scope.id),
      );
      return lists.map((l) => ({ key: l.id, title: l.name, items: [], drop: { kind: 'list', listId: l.id } }));
    }
    case 'section':
      return ctx.sections
        .filter((s) => scope.kind !== 'section' || s.id === scope.id)
        .map((s) => ({ key: s.id, title: s.name, items: [], drop: { kind: 'section', sectionId: s.id } as Drop }));
  }
}

/** Groupings where finished work collects in a Done column at the end (as on ClickUp). */
function hasDoneColumn(config: ViewConfig): boolean {
  return config.group_by !== 'status' && config.group_by !== 'status_kind';
}

const DONE_GROUP = (): Group => ({
  key: 'done',
  title: 'Done',
  color: 'var(--kind-done)',
  items: [],
  drop: { kind: 'done' },
});

/** The group keys an item belongs to (assignee and tag can be several). */
function keysOf(item: Item, config: ViewConfig, ctx: Ctx, today: string): string[] {
  if (hasDoneColumn(config) && isClosed(item)) return ['done'];
  switch (config.group_by) {
    case 'none':
      return ['all'];
    case 'due':
      return [dueBucket(item, today, config.due_window_days)];
    case 'status':
      return [item.status?.id ?? 'nostatus'];
    case 'status_kind':
      return [item.status?.kind ?? 'open'];
    case 'priority':
      return [item.priority ?? 'none'];
    case 'assignee':
      return item.assignees.length ? item.assignees.map((a) => a.handle) : [''];
    case 'tag':
      return item.tags.length ? item.tags : [''];
    case 'list':
      return [item.list_id];
    case 'section':
      return [ctx.lists[item.list_id]?.section_id ?? ''];
  }
}

function dynamicGroup(key: string, config: ViewConfig, ctx: Ctx): Group {
  if (config.group_by === 'assignee') {
    const a = Object.values(ctx.accounts).find((x) => x.handle === key);
    return {
      key,
      title: a ? `${a.kind === 'agent' ? '🤖 ' : ''}${a.display_name}` : `@${key}`,
      hint: `@${key}`,
      items: [],
      drop: { kind: 'assignee', handle: key },
    };
  }
  if (config.group_by === 'tag') return { key, title: `#${key}`, items: [], drop: { kind: 'tag', tag: key } };
  return { key, title: key || 'Other', items: [], drop: null };
}


/** What to say when a card is dropped on a group that cannot take it. */
export function refusal(g: Group): string {
  return g.refuse ?? `${g.title} can't take cards in this grouping.`;
}

/**
 * Everything a view shows, grouped and sorted. Items closed before today are hidden unless
 * `show_closed`; items closed today stay (struck through) where the filter lets them.
 */
export function runView(all: Iterable<Item>, scope: Scope, config: ViewConfig, ctx: Ctx): Group[] {
  const today = workspaceDay(ctx.now);
  const q = ctx.q?.trim().toLowerCase() ?? '';
  const shown: Item[] = [];
  for (const item of all) {
    if (item.archived_at) continue;
    if (item.parent_id && !config.show_subtasks) continue;
    if (!inScope(item, scope, ctx.lists)) continue;
    if (!config.show_closed && isClosed(item) && finishedBefore(item, today)) continue;
    if (q && !item.title.toLowerCase().includes(q) && !item.body_md.toLowerCase().includes(q)) continue;
    if (!matchFilter(item, config.filter, ctx, today)) continue;
    shown.push(item);
  }
  const sorted = sortItems(shown, config.sort, ctx);

  const groups = skeleton(config, scope, ctx, today);
  const done = hasDoneColumn(config) ? DONE_GROUP() : null;
  const byKey = new Map(groups.map((g) => [g.key, g]));
  if (done) byKey.set('done', done);
  const extra: Group[] = [];
  for (const item of sorted) {
    for (const key of keysOf(item, config, ctx, today)) {
      let g = byKey.get(key);
      if (!g) {
        g = dynamicGroup(key, config, ctx);
        byKey.set(key, g);
        extra.push(g);
      }
      g.items.push(item);
    }
  }

  let out: Group[];
  if (config.group_by === 'assignee') {
    // Me first, then people and agents by name, Unassigned last.
    const mine = meHandle(ctx);
    extra.sort((a, b) => (a.key === mine ? -1 : b.key === mine ? 1 : a.title.localeCompare(b.title)));
    out = [...extra, ...groups];
  } else if (config.group_by === 'tag') {
    extra.sort((a, b) => a.key.localeCompare(b.key));
    out = [...extra, ...groups];
  } else {
    out = [...groups, ...extra];
  }
  if (done) out.push(done);

  // Housekeeping groups only show when something is in them.
  const optional = new Set(['nostatus']);
  return out.filter((g) => g.items.length || (!config.hide_empty_groups && !optional.has(g.key)));
}

/** Days late for the overdue chip: 0 when not overdue. Counted in workspace calendar days. */
export function daysLate(item: Item, now: Date): number {
  if (!item.due_at || isClosed(item)) return 0;
  const due = new Date(item.due_at);
  if (due >= now) return 0;
  const a = new Date(`${workspaceDay(due)}T12:00:00Z`).getTime();
  const b = new Date(`${workspaceDay(now)}T12:00:00Z`).getTime();
  return Math.round((b - a) / 86_400_000);
}

/** The status a list uses for a kind: its first by position, or undefined. */
export function statusOfKind(list: List | undefined, kind: StatusKind): Status | undefined {
  if (!list) return undefined;
  return sortedStatuses(list).find((s) => s.kind === kind);
}

/** Where "un-complete" goes: the list's default status, else its first open one. */
export function reopenStatus(list: List | undefined): Status | undefined {
  if (!list) return undefined;
  return (
    list.statuses.find((s) => s.id === list.default_status_id && s.kind !== 'done') ??
    statusOfKind(list, 'open') ??
    statusOfKind(list, 'active')
  );
}

/** Quick reschedule picks, as workspace calendar days. */
export function quickDays(now: Date): { today: string; tomorrow: string; nextWeek: string } {
  const today = workspaceDay(now);
  // Next week = the coming Monday (Todoist and Things both do this).
  const nextMonday = addDays(today, ((8 - weekday(today)) % 7) || 7);
  return { today, tomorrow: addDays(today, 1), nextWeek: nextMonday };
}

/** Drag data naming the group a card or row was dragged out of (a tag group swaps that tag). */
export const GROUP_TYPE = 'application/x-slate-group';

/** On dragstart (bubbling from the card or row): record which group it left. */
export function markSourceGroup(e: DragEvent) {
  const key = (e.target as HTMLElement | null)?.closest?.<HTMLElement>('[data-group]')?.dataset.group;
  if (key !== undefined) e.dataTransfer?.setData(GROUP_TYPE, key);
}
