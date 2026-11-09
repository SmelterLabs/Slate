// What the filter builder offers for each field, and the plain-English chip for a rule.
// Mirrors the server's FILTER_FIELDS (src/slate/service/views.py).

import { KIND_NAMES, isActiveFilter, isGroup, type Ctx, type FilterGroup, type FilterRule, type Scope } from './views';
import type { Account, StatusKind } from './types';

export type ValueKind =
  | 'none'
  | 'statuses'
  | 'kinds'
  | 'people'
  | 'person'
  | 'tags'
  | 'priorities'
  | 'lists'
  | 'sections'
  | 'text'
  | 'bool'
  | 'days'
  | 'date'
  | 'dates'
  | 'number';

export interface FieldDef {
  field: string;
  label: string;
  cmps: { cmp: string; label: string }[];
  /** The value editor, which can depend on the comparator. */
  value: (cmp: string) => ValueKind;
  /** Only offered in some scopes (statuses belong to one list). */
  scopes?: Scope['kind'][];
}

const DATE_CMPS = [
  { cmp: 'overdue', label: 'is overdue' },
  { cmp: 'today', label: 'is today' },
  { cmp: 'today_or_earlier', label: 'is today or earlier' },
  { cmp: 'tomorrow', label: 'is tomorrow' },
  { cmp: 'this_week', label: 'is this week' },
  { cmp: 'next_n_days', label: 'is in the next…' },
  { cmp: 'before', label: 'is before' },
  { cmp: 'after', label: 'is after' },
  { cmp: 'between', label: 'is between' },
  { cmp: 'empty', label: 'is not set' },
  { cmp: 'not_empty', label: 'is set' },
];

function dateValue(cmp: string): ValueKind {
  if (cmp === 'next_n_days') return 'days';
  if (cmp === 'before' || cmp === 'after') return 'date';
  if (cmp === 'between') return 'dates';
  return 'none';
}

const IS = [
  { cmp: 'is', label: 'is' },
  { cmp: 'is_not', label: 'is not' },
];

export const FIELDS: FieldDef[] = [
  { field: 'status', label: 'Status', cmps: IS, value: () => 'statuses', scopes: ['list'] },
  { field: 'status_kind', label: 'Status kind', cmps: IS, value: () => 'kinds' },
  { field: 'due', label: 'Due date', cmps: DATE_CMPS, value: dateValue },
  {
    field: 'assignee',
    label: 'Assignee',
    cmps: [...IS, { cmp: 'empty', label: 'is nobody' }, { cmp: 'not_empty', label: 'is anybody' }],
    value: (c) => (c === 'is' || c === 'is_not' ? 'people' : 'none'),
  },
  {
    field: 'tag',
    label: 'Tag',
    cmps: [
      { cmp: 'any', label: 'has any of' },
      { cmp: 'all', label: 'has all of' },
      { cmp: 'none', label: 'has none of' },
      { cmp: 'empty', label: 'has no tags' },
    ],
    value: (c) => (c === 'empty' ? 'none' : 'tags'),
  },
  {
    field: 'priority',
    label: 'Priority',
    cmps: [...IS, { cmp: 'empty', label: 'is not set' }],
    value: (c) => (c === 'empty' ? 'none' : 'priorities'),
  },
  { field: 'list', label: 'List', cmps: IS, value: () => 'lists', scopes: ['everything', 'section'] },
  { field: 'section', label: 'Section', cmps: IS, value: () => 'sections', scopes: ['everything'] },
  { field: 'text', label: 'Text', cmps: [{ cmp: 'contains', label: 'contains' }], value: () => 'text' },
  {
    field: 'start',
    label: 'Start date',
    cmps: DATE_CMPS.filter((c) => ['before', 'after', 'between', 'empty', 'not_empty'].includes(c.cmp)),
    value: dateValue,
  },
  {
    field: 'waiting_on',
    label: 'Waiting on',
    cmps: [{ cmp: 'is', label: 'is' }, { cmp: 'empty', label: 'is nobody' }, { cmp: 'not_empty', label: 'is anybody' }],
    value: (c) => (c === 'is' ? 'person' : 'none'),
  },
  { field: 'recurring', label: 'Recurring', cmps: [{ cmp: 'is', label: 'is' }], value: () => 'bool' },
  { field: 'has_subtasks', label: 'Has subtasks', cmps: [{ cmp: 'is', label: 'is' }], value: () => 'bool' },
  { field: 'created', label: 'Created', cmps: [{ cmp: 'within_days', label: 'in the last…' }], value: () => 'days' },
  { field: 'updated', label: 'Updated', cmps: [{ cmp: 'within_days', label: 'in the last…' }], value: () => 'days' },
  { field: 'blocked', label: 'Depends on an open task', cmps: [{ cmp: 'is', label: 'is' }], value: () => 'bool' },
  {
    field: 'estimate',
    label: 'Time estimate',
    cmps: [
      { cmp: 'gt', label: 'is more than (min)' },
      { cmp: 'lt', label: 'is less than (min)' },
      { cmp: 'empty', label: 'is not set' },
      { cmp: 'not_empty', label: 'is set' },
    ],
    value: (c) => (c === 'gt' || c === 'lt' ? 'number' : 'none'),
  },
];

const SET = [
  { cmp: 'empty', label: 'is not set' },
  { cmp: 'not_empty', label: 'is set' },
];

export function fieldDef(field: string): FieldDef | undefined {
  return FIELDS.find((f) => f.field === field);
}

export function fieldsFor(scope: Scope): FieldDef[] {
  return FIELDS.filter((f) => !f.scopes || f.scopes.includes(scope.kind));
}

/** A fresh value when the field or comparator changes. */
export function initialValue(kind: ValueKind): unknown {
  switch (kind) {
    case 'none':
      return null;
    case 'text':
    case 'date':
    case 'person':
      return kind === 'person' ? 'me' : '';
    case 'bool':
      return true;
    case 'days':
      return 7;
    case 'dates':
      return ['', ''];
    case 'number':
      return 0;
    default:
      return [];
  }
}

export function newRule(field = 'tag'): FilterRule {
  const def = fieldDef(field) ?? FIELDS[0];
  const cmp = def.cmps[0].cmp;
  return { field: def.field, cmp, value: initialValue(def.value(cmp)) };
}

/** Active account choices, plus only the historical selections already in this rule. */
export function accountFilterOptions(pickable: Account[], accounts: Record<string, Account>, value: unknown) {
  const selected = list(value);
  const all = Object.values(accounts);
  const options = [...pickable].sort((a, b) => a.display_name.localeCompare(b.display_name)).map((a) => ({
    value: a.handle,
    label: `${a.kind === 'agent' ? '🤖 ' : ''}${a.display_name} (${a.kind === 'person' ? 'specific person' : `@${a.handle}`})`,
  }));
  for (const handle of selected) {
    if (handle === 'me' || options.some((o) => o.value === handle)) continue;
    const a = all.find((a) => a.handle === handle || a.id === handle);
    options.push({ value: handle, label: a
      ? `${a.display_name} (@${a.handle}; ${a.disabled_at ? 'disabled' : a.handle === 'slate' ? 'system' : 'specific account'})`
      : `@${handle} (unavailable account)`,
    });
  }
  return [{ value: 'me', label: 'Me (current viewer)' }, ...options];
}

const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : v == null || v === '' ? [] : [String(v)]);

function joinOr(words: string[]): string {
  if (words.length <= 2) return words.join(' or ');
  return `${words.slice(0, -1).join(', ')} or ${words.at(-1)}`;
}

function valueWords(rule: FilterRule, ctx: Ctx): string {
  const def = fieldDef(rule.field);
  const kind = def?.value(rule.cmp) ?? 'none';
  const v = list(rule.value);
  switch (kind) {
    case 'none':
      return '';
    case 'statuses':
      return joinOr(
        v.map((id) => {
          for (const l of Object.values(ctx.lists)) {
            const s = l.statuses.find((x) => x.id === id);
            if (s) return s.name;
          }
          return id;
        }),
      );
    case 'kinds':
      return joinOr(v.map((k) => KIND_NAMES[k as StatusKind] ?? k));
    case 'people':
    case 'person':
      return joinOr(v.map((h) => {
        if (h === 'me') return 'me (current viewer)';
        const account = Object.values(ctx.accounts).find((a) => a.handle === h || a.id === h);
        return `@${account?.handle ?? h}${account?.disabled_at ? ' (disabled)' : account?.handle === 'slate' ? ' (system)' : ''}`;
      }));
    case 'tags':
      return joinOr(v.map((t) => `#${t}`));
    case 'priorities':
      return joinOr(v);
    case 'lists':
      return joinOr(v.map((id) => ctx.lists[id]?.name ?? id));
    case 'sections':
      return joinOr(v.map((id) => ctx.sections.find((s) => s.id === id)?.name ?? id));
    case 'text':
      return `“${v[0] ?? ''}”`;
    case 'bool':
      return rule.value === true || rule.value === 'true' ? 'yes' : 'no';
    case 'days':
      return `${v[0] ?? 7} days`;
    case 'date':
      return v[0] ?? '';
    case 'dates':
      return `${v[0] ?? ''} and ${v[1] ?? ''}`;
    case 'number':
      return v[0] ?? '';
  }
}

/** "Tag has any of #home or #money", "Due date is overdue". */
export function describeRule(rule: FilterRule | FilterGroup, ctx: Ctx): string {
  if (isGroup(rule)) {
    const parts = rule.rules.filter(isActiveFilter).map((r) => describeRule(r, ctx));
    return parts.length ? `(${parts.join(rule.op === 'or' ? ' or ' : ' and ')})` : '(empty group)';
  }
  const def = fieldDef(rule.field);
  if (!def) return `${rule.field} ${rule.cmp}`;
  const cmp = def.cmps.find((c) => c.cmp === rule.cmp)?.label ?? rule.cmp;
  return [def.label, cmp.replace('…', ''), valueWords(rule, ctx)].filter(Boolean).join(' ');
}
