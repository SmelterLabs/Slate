import { describe, expect, it, vi } from 'vitest';

vi.mock('./store.svelte', () => ({ board: {} }));
vi.mock('./api', () => ({ api: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn() }));

import { automationOf, blankRule, PRESETS, summarize, toDraft, toPayload, type Catalog, type Rule } from './automations.svelte';

const catalog: Catalog = {
  triggers: [
    { type: 'status_changed', label: 'Status changes', event: 'item.status_changed', params: [] },
    { type: 'due_passes', label: 'Due date passes (overdue)', event: 'item.overdue', params: [] },
    { type: 'tag_added', label: 'Tag is added', event: 'item.updated', params: [] },
  ],
  conditions: { fields: [{ field: 'priority', label: 'Priority' }], ops: ['is', 'is_not'] },
  actions: [
    { type: 'add_tag', label: 'Add tag', params: [] },
    { type: 'move_list', label: 'Move to list', params: [] },
    { type: 'set_due', label: 'Set due date', params: [] },
  ],
};

describe('summarize', () => {
  it('reads like a sentence as the builder fills in', () => {
    const r = blankRule();
    r.actions = [{ type: 'add_tag', tag: 'shipped' }];
    expect(summarize(r, catalog, () => '')).toBe('When status changes to a done status, then add tag shipped.');
    r.trigger = { type: 'tag_added', tag: 'agent' };
    r.conditions = [{ field: 'priority', op: 'is_not', value: 'low' }];
    r.actions = [
      { type: 'move_list', list_id: 'L1' },
      { type: 'set_due', in_days: -1 },
    ];
    expect(summarize(r, catalog, (id) => (id === 'L1' ? 'Later' : '?'))).toBe(
      'When tag is added (agent), if priority is not low, then move to list Later, clear the due date.',
    );
  });

  it('names the agent state for an agent report trigger', () => {
    const r = blankRule();
    r.trigger = { type: 'agent_reported', state: 'working' };
    r.actions = [{ type: 'add_tag', tag: 'busy' }];
    expect(summarize(r, catalog, () => '')).toBe('When an agent reports working, then add tag busy.');
    r.trigger = { type: 'agent_reported' };
    expect(summarize(r, catalog, () => '')).toContain('When an agent reports a new state');
  });

  it('shows gaps while a step is empty', () => {
    const r = blankRule();
    r.actions = [{ type: 'add_tag', tag: '' }];
    expect(summarize(r, catalog, () => '')).toContain('add tag …');
  });
});

describe('toPayload', () => {
  it('drops empty optional params, coerces days and clears section when a list is set', () => {
    const r = blankRule('L1');
    r.section_id = 'S1';
    r.name = '  ';
    r.trigger = { type: 'status_changed', to: '', to_kind: 'done', from: '' };
    r.conditions = [{ field: 'priority', op: 'is', value: '' }];
    r.actions = [{ type: 'set_due', in_days: '3' }];
    expect(toPayload(r)).toEqual({
      name: 'Untitled automation',
      list_id: 'L1',
      section_id: null,
      trigger: { type: 'status_changed', to_kind: 'done' },
      conditions: [],
      actions: [{ type: 'set_due', in_days: 3 }],
      enabled: true,
    });
  });

  it('round-trips a saved rule through a draft without sharing objects', () => {
    const rule = { ...blankRule('L1'), id: 'R1', name: 'x' } as Rule;
    const d = toDraft(rule);
    d.actions[0].tag = 'changed';
    expect(rule.actions[0].tag).toBe('');
  });
});

describe('automationOf', () => {
  it('names the rule behind an automation change', () => {
    expect(automationOf({ session_ref: 'automation:abc' })).toBe('abc');
    expect(automationOf({ session_ref: 'recurrence:abc' })).toBeNull();
    expect(automationOf({ session_ref: null })).toBeNull();
  });
});

describe('presets', () => {
  it('each fills a rule with a trigger and at least one action', () => {
    for (const p of PRESETS) {
      const r = p.rule('alex');
      expect(r.name).toBeTruthy();
      expect(r.trigger.type).toBeTruthy();
      expect(r.actions.length).toBeGreaterThan(0);
    }
    expect(PRESETS.find((p) => p.key === 'assign-new')!.rule('alex').actions[0].account).toBe('alex');
  });
});
