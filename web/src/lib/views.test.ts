import { describe, expect, it } from 'vitest';
import { LIST, item } from './fixtures';
import type { Account, Item, List, Section } from './types';
import {
  workspaceInstant,
  comparator,
  daysLate,
  defaultConfig,
  dueBucket,
  matchFilter,
  normalizeConfig,
  onDay,
  quickDays,
  refusal,
  reopenStatus,
  ruleCount,
  runView,
  statusOfKind,
  type Ctx,
  type FilterGroup,
  type Scope,
  type ViewConfig,
} from './views';

const OTHER: List = {
  ...LIST,
  id: 'L2',
  section_id: 'S2',
  name: 'Work',
  slug: 'work',
  position: 0,
  statuses: [
    { id: 'w_todo', name: 'To do', kind: 'open', color: null, position: 0 },
    { id: 'w_done', name: 'Shipped', kind: 'done', color: null, position: 1 },
  ],
};
const SECTIONS: Section[] = [
  { id: 'S1', name: 'Personal', slug: 'p', position: 0, audience: 'personal', created_at: '', archived_at: null },
  { id: 'S2', name: 'Work', slug: 'w', position: 1, audience: 'work', created_at: '', archived_at: null },
];
const ALEX: Account = { id: 'A_alex', handle: 'alex', display_name: 'Alex', kind: 'person' };
const AGENT: Account = { id: 'A_agent', handle: 'agent', display_name: 'Agent', kind: 'agent' };

// Friday Oct 23 2026, 10:00 in Chicago (CDT, UTC-5).
const FRI = new Date('2026-10-23T15:00:00Z');

function ctx(now = FRI, over: Partial<Ctx> = {}): Ctx {
  return {
    now,
    lists: { L1: LIST, L2: OTHER },
    sections: SECTIONS,
    accounts: { A_alex: ALEX, A_agent: AGENT },
    me: ALEX,
    ...over,
  };
}

const EVERYTHING: Scope = { kind: 'everything' };
const done = LIST.statuses[2];
const active = LIST.statuses[1];

function cfg(over: Partial<ViewConfig> = {}, scope: Scope = EVERYTHING): ViewConfig {
  return { ...normalizeConfig(null, scope), ...over };
}

function titles(groups: ReturnType<typeof runView>): Record<string, string[]> {
  return Object.fromEntries(groups.map((g) => [g.key, g.items.map((i) => i.title)]));
}

const f = (rules: FilterGroup['rules'], op: 'and' | 'or' = 'and'): FilterGroup => ({ op, rules });

describe('Chicago dates', () => {
  it('turns a wall-clock time into the right instant on both sides of the DST change', () => {
    expect(workspaceInstant('2026-10-31', 17).toISOString()).toBe('2026-10-31T22:00:00.000Z');
    expect(workspaceInstant('2026-11-02', 17).toISOString()).toBe('2026-11-02T23:00:00.000Z');
    // Nov 1 itself: 01:30 happens twice; 09:00 is unambiguous CST.
    expect(workspaceInstant('2026-11-01', 9).toISOString()).toBe('2026-11-01T15:00:00.000Z');
  });

  it('moves a due date to another day keeping its time of day across DST', () => {
    expect(onDay('2026-11-03', '2026-10-30T14:30:00Z')).toBe('2026-11-03T15:30:00.000Z'); // 9:30
    expect(onDay('2026-11-03', null)).toBe('2026-11-03T23:00:00.000Z'); // 17:00 default
  });

  it('quick picks: next week is the coming Monday', () => {
    expect(quickDays(FRI)).toEqual({ today: '2026-10-23', tomorrow: '2026-10-24', nextWeek: '2026-10-26' });
    const monday = new Date('2026-10-26T15:00:00Z');
    expect(quickDays(monday).nextWeek).toBe('2026-11-02');
  });
});

describe('due bucketing', () => {
  it('buckets in Chicago days, not UTC days', () => {
    // 03:00Z on Oct 24 is still Oct 23 at 22:00 in Chicago.
    expect(dueBucket(item({ due_at: '2026-10-24T03:00:00Z' }), '2026-10-23', 6)).toBe('today');
    expect(dueBucket(item({ due_at: '2026-10-24T06:00:00Z' }), '2026-10-23', 6)).toBe('2026-10-24');
  });

  it('overdue only for open items; closed ones go to Done whatever their date', () => {
    expect(dueBucket(item({ due_at: '2026-10-20T22:00:00Z' }), '2026-10-23', 6)).toBe('overdue');
    expect(dueBucket(item({ due_at: '2026-10-20T22:00:00Z', status: done }), '2026-10-23', 6)).toBe('done');
    expect(dueBucket(item({ status: done }), '2026-10-23', 6)).toBe('done');
  });

  it('beyond the window is future; no date is none', () => {
    expect(dueBucket(item({ due_at: '2026-10-29T22:00:00Z' }), '2026-10-23', 6)).toBe('2026-10-29');
    expect(dueBucket(item({ due_at: '2026-10-30T22:00:00Z' }), '2026-10-23', 6)).toBe('future');
    expect(dueBucket(item(), '2026-10-23', 6)).toBe('none');
  });

  it('builds Overdue / Today / named days / Future / No due date / Done, in order', () => {
    const groups = runView([], EVERYTHING, cfg({ group_by: 'due', due_window_days: 6 }), ctx());
    expect(groups.map((g) => g.title)).toEqual([
      'Overdue',
      'Today',
      'Tomorrow',
      'Sun',
      'Mon',
      'Tue',
      'Wed',
      'Thu',
      'Future',
      'No due date',
      'Done',
    ]);
    expect(groups[1].hint).toBe('Fri Oct 23');
    expect(groups[2].hint).toBe('Sat Oct 24');
    expect(groups[3].hint).toBe('Oct 25');
    expect(groups[0].drop).toBeNull();
    expect(groups[1].drop).toEqual({ kind: 'due', day: '2026-10-23' });
    expect(groups.at(-3)?.drop).toEqual({ kind: 'due', day: '2026-10-30' });
    expect(groups.at(-2)?.drop).toEqual({ kind: 'due', day: null });
    expect(groups.at(-1)?.drop).toEqual({ kind: 'done' });
  });

  it('places items around the DST change (Sun Nov 1 2026)', () => {
    const fri = new Date('2026-10-30T15:00:00Z');
    const items = [
      item({ id: 'a', title: 'just after midnight CDT', due_at: '2026-11-01T05:30:00Z' }),
      item({ id: 'b', title: 'late Sunday CST', due_at: '2026-11-02T05:30:00Z' }),
      item({ id: 'c', title: 'Monday early', due_at: '2026-11-02T06:30:00Z' }),
    ];
    const g = titles(runView(items, EVERYTHING, cfg({ group_by: 'due' }), ctx(fri)));
    expect(g['2026-11-01']).toEqual(['just after midnight CDT', 'late Sunday CST']);
    expect(g['2026-11-02']).toEqual(['Monday early']);
  });

  it('reflows at midnight: today becomes overdue, done-today drops off the Done column', () => {
    const items = [
      item({ id: 'a', title: 'due tonight', due_at: '2026-10-23T22:00:00Z' }),
      item({
        id: 'b',
        title: 'finished today',
        due_at: '2026-10-23T22:00:00Z',
        status: done,
        completed_at: '2026-10-23T16:00:00Z',
      }),
    ];
    const before = new Date('2026-10-24T04:59:00Z'); // Oct 23 23:59 CDT
    const after = new Date('2026-10-24T05:01:00Z'); // Oct 24 00:01 CDT
    const c = cfg({ group_by: 'due' });
    const now = titles(runView(items, EVERYTHING, c, ctx(before)));
    expect(now.today).toEqual(['due tonight']);
    expect(now.done).toEqual(['finished today']);
    const next = titles(runView(items, EVERYTHING, c, ctx(after)));
    expect(next.overdue).toEqual(['due tonight']);
    expect(next.today).toEqual([]);
    expect(next.done).toEqual([]);
  });

  it('show_closed brings back items finished on earlier days', () => {
    const old = item({ id: 'o', title: 'old', status: done, completed_at: '2026-10-01T00:00:00Z', due_at: '2026-10-01T22:00:00Z' });
    expect(titles(runView([old], EVERYTHING, cfg({ group_by: 'due' }), ctx())).done).toEqual([]);
    expect(titles(runView([old], EVERYTHING, cfg({ group_by: 'due', show_closed: true }), ctx())).done).toEqual(['old']);
  });

  it('a Done column ends every grouping except status ones', () => {
    const fin = item({ id: 'f', title: 'fin', status: done, completed_at: '2026-10-23T16:00:00Z', priority: 'high' });
    for (const group_by of ['none', 'priority', 'assignee', 'tag', 'list'] as const) {
      const groups = runView([fin], EVERYTHING, cfg({ group_by }), ctx());
      expect(groups.at(-1)?.key).toBe('done');
      expect(titles(groups).done).toEqual(['fin']);
    }
    const byKind = runView([fin], EVERYTHING, cfg({ group_by: 'status_kind' }), ctx());
    expect(byKind.some((g) => g.key === 'done' && g.title === 'Done' && g.drop?.kind === 'status_kind')).toBe(true);
  });

  it('every column says what a drop does, or why it cannot take one', () => {
    const late = item({ id: 'l', title: 'late', due_at: '2026-10-20T22:00:00Z', tags: ['home'] });
    const due = runView([late], EVERYTHING, cfg({ group_by: 'due' }), ctx());
    const overdue = due.find((g) => g.key === 'overdue')!;
    expect(overdue.drop).toBeNull();
    expect(refusal(overdue)).toMatch(/Today or a day/);
    const byTag = runView([late], EVERYTHING, cfg({ group_by: 'tag' }), ctx());
    expect(byTag.find((g) => g.key === 'home')?.drop).toEqual({ kind: 'tag', tag: 'home' });
    expect(byTag.find((g) => g.key === '')?.drop).toEqual({ kind: 'tag', tag: null });
    const bySection = runView([late], EVERYTHING, cfg({ group_by: 'section' }), ctx());
    expect(bySection.find((g) => g.key === 'S2')?.drop).toEqual({ kind: 'section', sectionId: 'S2' });
  });

  it('daysLate counts Chicago calendar days', () => {
    expect(daysLate(item({ due_at: '2026-10-20T22:00:00Z' }), FRI)).toBe(3);
    expect(daysLate(item({ due_at: '2026-10-23T14:00:00Z' }), FRI)).toBe(0); // earlier today
    expect(daysLate(item({ due_at: '2026-10-20T22:00:00Z', status: done }), FRI)).toBe(0);
  });
});

describe('filters', () => {
  const c = ctx();
  const today = '2026-10-23';
  const m = (it: Item, node: FilterGroup) => matchFilter(it, node, c, today);

  it('a rule with no value picked yet is ignored', () => {
    expect(m(item(), f([{ field: 'tag', cmp: 'any', value: [] }]))).toBe(true);
    expect(m(item(), f([{ field: 'status_kind', cmp: 'is', value: [] }]))).toBe(true);
    expect(m(item({ due_at: '2026-10-22T22:00:00Z' }), f([{ field: 'due', cmp: 'before', value: '' }]))).toBe(true);
    expect(m(item(), f([{ field: 'recurring', cmp: 'is', value: true }]))).toBe(false);
  });

  it('empty groups match everything', () => {
    expect(m(item(), f([]))).toBe(true);
    expect(m(item(), f([], 'or'))).toBe(true);
  });

  it('status and status_kind', () => {
    expect(m(item(), f([{ field: 'status', cmp: 'is', value: ['st_open'] }]))).toBe(true);
    expect(m(item(), f([{ field: 'status', cmp: 'is_not', value: ['st_open'] }]))).toBe(false);
    expect(m(item({ status: active }), f([{ field: 'status_kind', cmp: 'is', value: ['open', 'active'] }]))).toBe(true);
    expect(m(item({ status: done }), f([{ field: 'status_kind', cmp: 'is', value: ['open', 'active'] }]))).toBe(false);
    expect(m(item({ status: done }), f([{ field: 'status_kind', cmp: 'is_not', value: ['done'] }]))).toBe(false);
    expect(m(item({ status: null }), f([{ field: 'status_kind', cmp: 'is', value: 'open' }]))).toBe(true);
  });

  it('assignee with me, is_not, empty, not_empty', () => {
    const mine = item({ assignees: [ALEX] });
    const agents = item({ assignees: [AGENT] });
    expect(m(mine, f([{ field: 'assignee', cmp: 'is', value: ['me'] }]))).toBe(true);
    expect(m(agents, f([{ field: 'assignee', cmp: 'is', value: ['me'] }]))).toBe(false);
    expect(m(agents, f([{ field: 'assignee', cmp: 'is_not', value: ['me'] }]))).toBe(true);
    expect(m(item(), f([{ field: 'assignee', cmp: 'empty' }]))).toBe(true);
    expect(m(mine, f([{ field: 'assignee', cmp: 'not_empty' }]))).toBe(true);
  });

  it('Me follows the viewer while a selected account stays fixed', () => {
    const mine = item({ assignees: [ALEX] });
    const otherViewer = ctx(FRI, { me: AGENT });
    expect(matchFilter(mine, f([{ field: 'assignee', cmp: 'is', value: ['me'] }]), otherViewer)).toBe(false);
    expect(matchFilter(mine, f([{ field: 'assignee', cmp: 'is', value: [ALEX.handle] }]), otherViewer)).toBe(true);
  });

  it('tag any / all / none / empty', () => {
    const it = item({ tags: ['home', 'chores'] });
    expect(m(it, f([{ field: 'tag', cmp: 'any', value: ['home', 'x'] }]))).toBe(true);
    expect(m(it, f([{ field: 'tag', cmp: 'all', value: ['home', 'x'] }]))).toBe(false);
    expect(m(it, f([{ field: 'tag', cmp: 'all', value: ['home', 'chores'] }]))).toBe(true);
    expect(m(it, f([{ field: 'tag', cmp: 'none', value: ['home'] }]))).toBe(false);
    expect(m(item(), f([{ field: 'tag', cmp: 'empty' }]))).toBe(true);
    expect(m(it, f([{ field: 'tag', cmp: 'any', value: ['HOME'] }]))).toBe(true);
  });

  it('priority is / is_not / empty', () => {
    expect(m(item({ priority: 'high' }), f([{ field: 'priority', cmp: 'is', value: ['urgent', 'high'] }]))).toBe(true);
    expect(m(item({ priority: 'low' }), f([{ field: 'priority', cmp: 'is_not', value: ['low'] }]))).toBe(false);
    expect(m(item(), f([{ field: 'priority', cmp: 'empty' }]))).toBe(true);
  });

  it('due comparators', () => {
    const at = (iso: string | null) => item({ due_at: iso });
    const d = (cmp: string, value?: unknown) => f([{ field: 'due', cmp, value }]);
    expect(m(at('2026-10-22T22:00:00Z'), d('overdue'))).toBe(true);
    expect(m(at('2026-10-23T14:00:00Z'), d('overdue'))).toBe(true); // earlier today
    expect(m(at('2026-10-23T22:00:00Z'), d('overdue'))).toBe(false);
    expect(m(at('2026-10-23T22:00:00Z'), d('today'))).toBe(true);
    expect(m(at('2026-10-20T22:00:00Z'), d('today_or_earlier'))).toBe(true);
    expect(m(at('2026-10-24T22:00:00Z'), d('today_or_earlier'))).toBe(false);
    expect(m(at('2026-10-24T22:00:00Z'), d('tomorrow'))).toBe(true);
    // This week: Mon Oct 19 – Sun Oct 25.
    expect(m(at('2026-10-19T22:00:00Z'), d('this_week'))).toBe(true);
    expect(m(at('2026-10-25T22:00:00Z'), d('this_week'))).toBe(true);
    expect(m(at('2026-10-26T22:00:00Z'), d('this_week'))).toBe(false);
    expect(m(at('2026-10-30T22:00:00Z'), d('next_n_days', 7))).toBe(true);
    expect(m(at('2026-10-31T22:00:00Z'), d('next_n_days', 7))).toBe(false);
    expect(m(at('2026-10-22T22:00:00Z'), d('next_n_days', 7))).toBe(false);
    expect(m(at('2026-10-22T22:00:00Z'), d('before', '2026-10-23'))).toBe(true);
    expect(m(at('2026-10-24T22:00:00Z'), d('after', 'today'))).toBe(true);
    expect(m(at('2026-10-25T22:00:00Z'), d('between', ['2026-10-24', '+2']))).toBe(true);
    expect(m(at('2026-10-26T22:00:00Z'), d('between', ['2026-10-24', '+2']))).toBe(false);
    expect(m(at(null), d('empty'))).toBe(true);
    expect(m(at(null), d('not_empty'))).toBe(false);
    expect(m(at(null), d('today'))).toBe(false);
  });

  it('start, list, section, text, recurring, has_subtasks, waiting_on, created, updated', () => {
    expect(m(item({ start_at: '2026-10-20T22:00:00Z' }), f([{ field: 'start', cmp: 'before', value: 'today' }]))).toBe(true);
    expect(m(item(), f([{ field: 'list', cmp: 'is', value: ['L1'] }]))).toBe(true);
    expect(m(item(), f([{ field: 'list', cmp: 'is_not', value: ['L1'] }]))).toBe(false);
    expect(m(item({ list_id: 'L2' }), f([{ field: 'section', cmp: 'is', value: ['S2'] }]))).toBe(true);
    expect(m(item({ body_md: 'call the Plumber' }), f([{ field: 'text', cmp: 'contains', value: 'plumber' }]))).toBe(true);
    expect(m(item({ recurrence_id: 'R1' }), f([{ field: 'recurring', cmp: 'is', value: true }]))).toBe(true);
    expect(m(item(), f([{ field: 'recurring', cmp: 'is', value: false }]))).toBe(true);
    expect(m(item({ subtask_count: 2 }), f([{ field: 'has_subtasks', cmp: 'is', value: true }]))).toBe(true);
    expect(m(item({ waiting_on_account_id: 'A_alex' }), f([{ field: 'waiting_on', cmp: 'is', value: 'me' }]))).toBe(true);
    expect(m(item({ waiting_on_account_id: 'A_agent' }), f([{ field: 'waiting_on', cmp: 'is', value: 'me' }]))).toBe(false);
    expect(m(item({ waiting_on_account_id: 'A_agent' }), f([{ field: 'waiting_on', cmp: 'is', value: 'agent' }]))).toBe(true);
    expect(m(item({ created_at: '2026-10-20T00:00:00Z' }), f([{ field: 'created', cmp: 'within_days', value: 7 }]))).toBe(true);
    expect(m(item({ updated_at: '2026-10-01T00:00:00Z' }), f([{ field: 'updated', cmp: 'within_days', value: 7 }]))).toBe(false);
  });

  it('ignores unfinished rules and empty nested groups instead of widening OR filters', () => {
    const incomplete = f([
      { field: 'assignee', cmp: 'is', value: [] },
      { field: 'text', cmp: 'contains', value: '  ' },
      { field: 'due', cmp: 'between', value: ['2026-10-23', ''] },
      { field: 'waiting_on', cmp: 'is', value: '' },
      { field: 'estimate', cmp: 'gt', value: null },
    ], 'or');
    expect(ruleCount(incomplete)).toBe(0);
    expect(m(item(), incomplete)).toBe(true);
    for (const op of ['and', 'or'] as const) {
      const node = f([incomplete, f([], 'or'), { field: 'priority', cmp: 'is', value: ['high'] }], op);
      expect(ruleCount(node)).toBe(1);
      expect(m(item({ priority: 'low' }), node)).toBe(false);
      expect(m(item({ priority: 'high' }), node)).toBe(true);
    }
    expect(ruleCount(f([
      { field: 'assignee', cmp: 'empty' },
      { field: 'recurring', cmp: 'is', value: false },
      { field: 'estimate', cmp: 'gt', value: 0 },
    ]))).toBe(3);
  });

  it('nested AND / OR', () => {
    // high priority AND (tagged home OR assigned to me)
    const node = f([
      { field: 'priority', cmp: 'is', value: ['high'] },
      f(
        [
          { field: 'tag', cmp: 'any', value: ['home'] },
          { field: 'assignee', cmp: 'is', value: ['me'] },
        ],
        'or',
      ),
    ]);
    expect(m(item({ priority: 'high', tags: ['home'] }), node)).toBe(true);
    expect(m(item({ priority: 'high', assignees: [ALEX] }), node)).toBe(true);
    expect(m(item({ priority: 'high' }), node)).toBe(false);
    expect(m(item({ tags: ['home'] }), node)).toBe(false);
    expect(ruleCount(node)).toBe(3);
  });

  it('the seeded Agenda filter: due today or earlier, or waiting on me', () => {
    const agenda = f(
      [
        { field: 'due', cmp: 'today_or_earlier', value: null },
        { field: 'waiting_on', cmp: 'is', value: 'me' },
      ],
      'or',
    );
    expect(m(item({ due_at: '2026-10-21T22:00:00Z' }), agenda)).toBe(true);
    expect(m(item({ waiting_on_account_id: 'A_alex' }), agenda)).toBe(true);
    expect(m(item({ due_at: '2026-10-28T22:00:00Z' }), agenda)).toBe(false);
  });
});

describe('sorting', () => {
  it('multi-key, nulls last both ways, stable on position', () => {
    const items = [
      item({ id: 'a', title: 'a', priority: null, due_at: '2026-10-24T22:00:00Z', position: 1 }),
      item({ id: 'b', title: 'b', priority: 'high', due_at: '2026-10-26T22:00:00Z', position: 2 }),
      item({ id: 'c', title: 'c', priority: 'high', due_at: '2026-10-25T22:00:00Z', position: 3 }),
      item({ id: 'd', title: 'd', priority: 'urgent', due_at: null, position: 4 }),
      item({ id: 'e', title: 'e', priority: 'high', due_at: null, position: 0 }),
    ];
    const by = (s: Parameters<typeof comparator>[0]) => [...items].sort(comparator(s)).map((i) => i.id);
    expect(by([{ field: 'priority', dir: 'desc' }, { field: 'due', dir: 'asc' }])).toEqual(['d', 'c', 'b', 'e', 'a']);
    expect(by([{ field: 'due', dir: 'desc' }])).toEqual(['b', 'c', 'a', 'e', 'd']);
    expect(by([{ field: 'due', dir: 'asc' }])).toEqual(['a', 'c', 'b', 'e', 'd']);
    expect(by([])).toEqual(['e', 'a', 'b', 'c', 'd']);
    expect(by([{ field: 'title', dir: 'desc' }])).toEqual(['e', 'd', 'c', 'b', 'a']);
  });
});

describe('grouping', () => {
  const items = [
    item({ id: 'a', title: 'home urgent', priority: 'urgent', tags: ['home', 'money'], assignees: [AGENT] }),
    item({ id: 'b', title: 'work ready', list_id: 'L2', status: OTHER.statuses[0], assignees: [ALEX, AGENT] }),
    item({ id: 'c', title: 'home active', status: active }),
  ];

  it('none', () => {
    expect(titles(runView(items, EVERYTHING, cfg({ group_by: 'none' }), ctx())).all).toHaveLength(3);
  });

  it('status in list scope keeps the list order and every column', () => {
    const scope: Scope = { kind: 'list', id: 'L1' };
    const g = runView(items, scope, cfg({ group_by: 'status' }, scope), ctx());
    expect(g.map((x) => x.title)).toEqual(['Open', 'Ready', 'Done']);
    expect(g[1].drop).toEqual({ kind: 'status', status: active });
    expect(titles(g)).toEqual({ st_open: ['home urgent'], st_ready: ['home active'], st_done: [] });
  });

  it('status_kind across lists', () => {
    const g = titles(runView(items, EVERYTHING, cfg({ group_by: 'status_kind' }), ctx()));
    expect(g.open).toEqual(['home urgent', 'work ready']);
    expect(g.active).toEqual(['home active']);
  });

  it('priority, with No priority last', () => {
    const g = runView(items, EVERYTHING, cfg({ group_by: 'priority' }), ctx());
    expect(g.map((x) => x.key)).toEqual(['urgent', 'high', 'normal', 'low', 'none', 'done']);
    expect(titles(g).none).toEqual(['work ready', 'home active']);
  });

  it('assignee: an item shows under each assignee; me first; Unassigned last', () => {
    const g = runView(items, EVERYTHING, cfg({ group_by: 'assignee' }), ctx());
    expect(g.map((x) => x.key)).toEqual(['alex', 'agent', '', 'done']);
    expect(g[1].title).toBe('🤖 Agent');
    expect(titles(g)).toEqual({ alex: ['work ready'], agent: ['home urgent', 'work ready'], '': ['home active'], done: [] });
    expect(g[0].drop).toEqual({ kind: 'assignee', handle: 'alex' });
  });

  it('tag: alphabetical, No tag last', () => {
    const g = runView(items, EVERYTHING, cfg({ group_by: 'tag' }), ctx());
    expect(g.map((x) => x.title)).toEqual(['#home', '#money', 'No tag', 'Done']);
  });

  it('list and section follow nav order', () => {
    expect(runView(items, EVERYTHING, cfg({ group_by: 'list' }), ctx()).map((g) => g.title)).toEqual(['Home', 'Work', 'Done']);
    expect(runView(items, EVERYTHING, cfg({ group_by: 'section' }), ctx()).map((g) => g.title)).toEqual(['Personal', 'Work', 'Done']);
  });

  it('hide_empty_groups', () => {
    const g = runView(items, EVERYTHING, cfg({ group_by: 'priority', hide_empty_groups: true }), ctx());
    expect(g.map((x) => x.key)).toEqual(['urgent', 'none']);
  });

  it('scopes, subtasks, archived and search', () => {
    const more = [
      ...items,
      item({ id: 's', title: 'a subtask', parent_id: 'a' }),
      item({ id: 'x', title: 'archived', archived_at: '2026-10-22T00:00:00Z' }),
    ];
    const section: Scope = { kind: 'section', id: 'S2' };
    expect(titles(runView(more, section, cfg({ group_by: 'none' }), ctx())).all).toEqual(['work ready']);
    expect(titles(runView(more, EVERYTHING, cfg({ group_by: 'none' }), ctx())).all).toHaveLength(3);
    expect(titles(runView(more, EVERYTHING, cfg({ group_by: 'none', show_subtasks: true }), ctx())).all).toHaveLength(4);
    expect(titles(runView(more, EVERYTHING, cfg({ group_by: 'none' }), ctx(FRI, { q: 'URGENT' }))).all).toEqual(['home urgent']);
  });
});

describe('configs and statuses', () => {
  it('defaults: list scope groups by status, wide scopes by due sorted by due date', () => {
    expect(defaultConfig({ kind: 'list', id: 'L1' }).group_by).toBe('status');
    expect(defaultConfig({ kind: 'list', id: 'L1' }).collapse_empty).toBe(false);
    expect(normalizeConfig({}, { kind: 'list', id: 'L1' }).collapse_empty).toBe(false);
    const wide = defaultConfig(EVERYTHING);
    expect(wide.group_by).toBe('due');
    expect(wide.sort[0]).toEqual({ field: 'due', dir: 'asc' });
    expect(wide.filter.rules).toHaveLength(0);
    expect(wide.title_lines).toBe(2);
    expect(wide.collapse_empty).toBe(true);
  });

  it('normalize fills gaps and refuses status grouping outside a list', () => {
    const c = normalizeConfig({ group_by: 'status' } as Partial<ViewConfig>, EVERYTHING);
    expect(c.group_by).toBe('status_kind');
    expect(c.card_fields.length).toBeGreaterThan(0);
    expect(c.title_lines).toBe(2);
    expect(c.collapse_empty).toBe(true);
  });

  it('status helpers', () => {
    expect(statusOfKind(LIST, 'done')?.id).toBe('st_done');
    expect(statusOfKind(OTHER, 'active')).toBeUndefined();
    expect(reopenStatus(LIST)?.id).toBe('st_open');
  });
});

describe('speed', () => {
  it('groups 3,000 items by due in well under a frame budget', () => {
    const kinds = [LIST.statuses[0], LIST.statuses[1], LIST.statuses[2]];
    const many = Array.from({ length: 3000 }, (_, n) =>
      item({
        id: `I${n}`,
        title: `Task ${n}`,
        list_id: n % 2 ? 'L1' : 'L2',
        status: n % 2 ? kinds[n % 3] : OTHER.statuses[0],
        priority: (['urgent', 'high', 'normal', 'low', null] as const)[n % 5],
        due_at: n % 4 ? new Date(FRI.getTime() + ((n % 40) - 10) * 86_400_000).toISOString() : null,
        tags: n % 3 ? ['home'] : [],
        position: n,
      }),
    );
    const c = defaultConfig(EVERYTHING);
    runView(many, EVERYTHING, c, ctx()); // warm up
    const t0 = performance.now();
    for (let i = 0; i < 10; i++) runView(many, EVERYTHING, c, ctx());
    const each = (performance.now() - t0) / 10;
    expect(each).toBeLessThan(40);
  });
});

describe('layouts', () => {
  it('a view saved with a removed layout opens as a list, without its old settings', () => {
    for (const layout of ['table', 'calendar', 'timeline', 'box']) {
      const raw = { layout, calendar_span: 'week', timeline_zoom: 'month', group_by: 'due' };
      const c = normalizeConfig(raw as unknown as Partial<ViewConfig>, EVERYTHING);
      expect(c.layout).toBe('list');
      expect(c.group_by).toBe('due');
      expect('calendar_span' in c || 'timeline_zoom' in c).toBe(false);
    }
    expect(normalizeConfig({ layout: 'board' }, EVERYTHING).layout).toBe('board');
    expect(normalizeConfig({ layout: 'list' }, EVERYTHING).layout).toBe('list');
    expect(normalizeConfig(null, EVERYTHING).layout).toBe('board');
  });
});
