import { describe, expect, it } from 'vitest';
import { LIST } from './fixtures';
import { accountFilterOptions, describeRule, fieldsFor, initialValue, newRule } from './filterfields';
import type { Ctx } from './views';

const ctx: Ctx = { now: new Date('2026-10-23T15:00:00Z'), lists: { L1: LIST }, sections: [], accounts: {}, me: null };

describe('filter fields', () => {
  it('describes rules in plain words', () => {
    expect(describeRule({ field: 'tag', cmp: 'any', value: ['home', 'money'] }, ctx)).toBe('Tag has any of #home or #money');
    expect(describeRule({ field: 'due', cmp: 'overdue', value: null }, ctx)).toBe('Due date is overdue');
    expect(describeRule({ field: 'due', cmp: 'next_n_days', value: 3 }, ctx)).toBe('Due date is in the next 3 days');
    expect(describeRule({ field: 'status', cmp: 'is_not', value: ['st_done'] }, ctx)).toBe('Status is not Done');
    expect(describeRule({ field: 'assignee', cmp: 'is', value: ['me', 'agent'] }, ctx)).toBe('Assignee is me (current viewer) or @agent');
    expect(describeRule({ field: 'status_kind', cmp: 'is', value: ['open', 'active'] }, ctx)).toBe('Status kind is Open or Active');
    expect(
      describeRule(
        { op: 'or', rules: [{ field: 'priority', cmp: 'is', value: ['high'] }, { field: 'recurring', cmp: 'is', value: true }] },
        ctx,
      ),
    ).toBe('(Priority is high or Recurring is yes)');
  });

  it('omits unfinished nested summaries but keeps explicit nobody and false rules', () => {
    expect(describeRule({ op: 'or', rules: [
      { field: 'assignee', cmp: 'is', value: [] },
      { op: 'and', rules: [] },
      { field: 'assignee', cmp: 'empty' },
      { field: 'recurring', cmp: 'is', value: false },
    ] }, ctx)).toBe('(Assignee is nobody or Recurring is no)');
  });

  it('offers status only in a list and sections only in everything', () => {
    expect(fieldsFor({ kind: 'everything' }).map((f) => f.field)).not.toContain('status');
    expect(fieldsFor({ kind: 'list', id: 'L1' }).map((f) => f.field)).toContain('status');
    expect(fieldsFor({ kind: 'list', id: 'L1' }).map((f) => f.field)).not.toContain('section');
  });

  it('offers active accounts, labeling only selected historical or unavailable accounts', () => {
    const ALEX = { id: 'person', handle: 'person', display_name: 'Test Person', kind: 'person' as const };
    const AGENT = { id: 'agent', handle: 'agent', display_name: 'Test Agent', kind: 'agent' as const };
    const retired = { ...AGENT, disabled_at: '2026-10-31T12:00:00Z' };
    const system = { ...AGENT, id: 'system', handle: 'slate', display_name: 'Slate' };
    const accounts = { [ALEX.id]: ALEX, [AGENT.id]: retired, system };
    expect(accountFilterOptions([ALEX], accounts, []).map((o) => o.value)).toEqual(['me', ALEX.handle]);
    const options = accountFilterOptions([ALEX], accounts, [retired.handle, 'slate', 'missing']);
    expect(options.find((o) => o.value === 'me')?.label).toBe('Me (current viewer)');
    expect(options.find((o) => o.value === ALEX.handle)?.label).toContain('(specific person)');
    expect(options.find((o) => o.value === retired.handle)?.label).toContain('disabled');
    expect(options.find((o) => o.value === 'slate')?.label).toContain('system');
    expect(options.find((o) => o.value === 'missing')?.label).toContain('unavailable account');
  });

  it('new rules start with a usable value', () => {
    expect(newRule('tag')).toEqual({ field: 'tag', cmp: 'any', value: [] });
    expect(newRule('due')).toEqual({ field: 'due', cmp: 'overdue', value: null });
    expect(initialValue('days')).toBe(7);
  });
});
