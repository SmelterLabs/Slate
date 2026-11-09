// The board at scale: regrouping 20,000 items must stay well under a frame budget's worth of
// work per live event. Budgets are generous (CI runners are slow); the measured numbers are in
// docs/PERFORMANCE.md. Prints its timings so a regression shows before it fails.
import { describe, expect, it } from 'vitest';
import { LIST, item } from './fixtures';
import type { Account, Item, List, Section } from './types';
import { normalizeConfig, runView, type Ctx, type Scope, type ViewConfig } from './views';

const ITEMS = 20_000;
const LISTS = 50;
const NOW = new Date('2026-10-23T15:00:00Z');
const SECTIONS: Section[] = [0, 1, 2, 3, 4].map((n) => ({
  id: `S${n}`,
  name: `Area ${n}`,
  slug: `a${n}`,
  position: n,
  audience: 'ai',
  created_at: '',
  archived_at: null,
}));
const LISTS_BY_ID: Record<string, List> = Object.fromEntries(
  Array.from({ length: LISTS }, (_, n) => [
    `L${n}`,
    { ...LIST, id: `L${n}`, section_id: `S${n % 5}`, name: `List ${n}`, slug: `l${n}`, position: n },
  ]),
);
const AGENTS: Account[] = Array.from({ length: 10 }, (_, n) => ({
  id: `A${n}`,
  handle: `agent${n}`,
  display_name: `Agent ${n}`,
  kind: 'agent',
}));
const PRIORITIES = [null, 'low', 'normal', 'high', 'urgent'] as const;

function seeded(): Item[] {
  let r = 7;
  const rand = () => ((r = (r * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  return Array.from({ length: ITEMS }, (_, n) => {
    const due = rand() < 0.7 ? new Date(NOW.getTime() + (rand() * 60 - 20) * 86_400_000) : null;
    return item({
      id: `I${n}`,
      list_id: `L${n % LISTS}`,
      title: `Task ${n}`,
      position: n,
      status: LIST.statuses[rand() < 0.6 ? 0 : Math.floor(rand() * 3)],
      priority: PRIORITIES[n % 5],
      due_at: due ? due.toISOString() : null,
      assignees: n % 2 ? [] : [AGENTS[(n >> 1) % 10]],
      tags: n % 3 ? ['tag1'] : [],
    });
  });
}

const ctx: Ctx = {
  now: NOW,
  lists: LISTS_BY_ID,
  sections: SECTIONS,
  accounts: Object.fromEntries(AGENTS.map((a) => [a.id, a])),
  me: AGENTS[0],
};
const EVERYTHING: Scope = { kind: 'everything' };

function time(fn: () => unknown, runs = 7): number {
  fn(); // warm the day cache and the JIT
  const xs: number[] = [];
  for (let i = 0; i < runs; i++) {
    const t = performance.now();
    fn();
    xs.push(performance.now() - t);
  }
  return xs.sort((a, b) => a - b)[Math.floor(runs / 2)];
}

describe('runView at 20,000 items', () => {
  const items = seeded();
  const cases: [string, Partial<ViewConfig>][] = [
    ['due (This week)', { group_by: 'due' }],
    ['status', { group_by: 'status_kind' }],
    ['list', { group_by: 'list' }],
    ['assignee', { group_by: 'assignee' }],
    ['priority, sorted by due', { group_by: 'priority', sort: [{ field: 'due', dir: 'asc' }] }],
  ];
  for (const [name, over] of cases) {
    it(`groups by ${name} in under 250 ms`, () => {
      const config = { ...normalizeConfig(null, EVERYTHING), ...over } as ViewConfig;
      const groups = runView(items, EVERYTHING, config, ctx);
      expect(groups.reduce((n, g) => n + g.items.length, 0)).toBeGreaterThan(ITEMS / 3);
      const ms = time(() => runView(items, EVERYTHING, config, ctx));
      console.log(`runView ${ITEMS} items, group by ${name}: ${ms.toFixed(1)} ms`);
      expect(ms).toBeLessThan(250);
    });
  }
});
