import { describe, expect, it } from 'vitest';
import {
  NO_FILTERS,
  activeFilters,
  affectsMyAccess,
  atLeast,
  auditQuery,
  auditTone,
  byDay,
  canCreateSections,
  changeLine,
  changesAgentRules,
  listLevel,
  reachLabel,
  toggleDenied,
} from './permissions';
import type { AgentRules } from './permissions';
import type { AuditEntry, Me, MyAccess, SlateEvent } from './types';

const guestAccess: MyAccess = {
  all: false,
  level: 'edit',
  manage: false,
  general: false,
  sections: { s1: null },
  lists: { l1: 'comment' },
};

function me(role: 'owner' | 'member' | 'guest', access?: MyAccess): Me {
  return {
    account: { id: 'me0000000001', handle: 'me', display_name: 'Me', kind: 'person', role },
    scopes: ['write'],
    token_id: 't',
    prefs: {},
    access,
  };
}

function ev(partial: Partial<SlateEvent>): SlateEvent {
  return {
    id: '1',
    type: 'item.updated',
    item_id: null,
    subject_id: null,
    actor_id: 'x',
    source: 'api',
    field: null,
    before: null,
    after: null,
    session_ref: null,
    at: '2026-10-24T00:00:00Z',
    ...partial,
  } as SlateEvent;
}

function entry(at: string, extra: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: at,
    at,
    type: 'item.updated',
    field: null,
    before: null,
    after: null,
    source: 'ui',
    session_ref: null,
    actor: { id: 'a', handle: 'a', display_name: 'A', kind: 'person' },
    item_id: null,
    subject_id: null,
    item: null,
    summary: '',
    ...extra,
  };
}

describe('levels', () => {
  it('ranks view < comment < edit', () => {
    expect(atLeast('edit', 'comment')).toBe(true);
    expect(atLeast('view', 'comment')).toBe(false);
    expect(atLeast(null, 'view')).toBe(false);
  });

  it('reads a list level from /me access; no access info means everything', () => {
    expect(listLevel(undefined, 'l1')).toBe('edit');
    expect(listLevel(guestAccess, 'l1')).toBe('comment');
    expect(listLevel(guestAccess, 'l2')).toBeNull();
    expect(listLevel({ ...guestAccess, all: true, level: 'view' }, 'l2')).toBe('view');
  });

  it('only non-guests with unlimited editing tokens create sections', () => {
    expect(canCreateSections(me('guest', guestAccess))).toBe(false);
    expect(canCreateSections(me('member', { ...guestAccess, all: true }))).toBe(true);
    expect(canCreateSections(me('member', { ...guestAccess, limited: true }))).toBe(false);
    expect(canCreateSections(me('owner', { ...guestAccess, all: true, level: 'view' }))).toBe(false);
  });
});

describe('affectsMyAccess', () => {
  it('reloads on shares and role changes about me only', () => {
    const m = me('guest', guestAccess);
    expect(affectsMyAccess(ev({ type: 'share.granted', after: { account_id: 'me0000000001' } }), m)).toBe(true);
    expect(affectsMyAccess(ev({ type: 'share.revoked', before: { account_id: 'me0000000001' } }), m)).toBe(true);
    expect(affectsMyAccess(ev({ type: 'share.granted', after: { account_id: 'other' } }), m)).toBe(false);
    expect(affectsMyAccess(ev({ type: 'account.role_changed', subject_id: 'me0000000001' }), m)).toBe(true);
  });

  it('privacy changes matter only to restricted boards', () => {
    const e = ev({ type: 'list.updated', field: 'private' });
    expect(affectsMyAccess(e, me('guest', guestAccess))).toBe(true);
    expect(affectsMyAccess(e, me('owner', { ...guestAccess, all: true }))).toBe(false);
  });
});

describe('audit helpers', () => {
  it('builds the query, with an inclusive end date and list over section', () => {
    const q = auditQuery({ ...NO_FILTERS, kind: 'agent', section: 's1', list: 'l1', type: 'share.', q: ' milk ' });
    const p = new URLSearchParams(q.slice(1));
    expect(p.get('actor_kind')).toBe('agent');
    expect(p.get('list_id')).toBe('l1');
    expect(p.get('section_id')).toBeNull();
    expect(p.get('type')).toBe('share.');
    expect(p.get('q')).toBe('milk');
    const d = new URLSearchParams(auditQuery({ ...NO_FILTERS, since: '2026-10-01', until: '2026-10-01' }).slice(1));
    expect(new Date(d.get('until')!).getTime() - new Date(d.get('since')!).getTime()).toBe(86_400_000);
    expect(auditQuery(NO_FILTERS)).toBe('');
    expect(activeFilters({ ...NO_FILTERS, actor: 'x', kind: 'person' })).toBe(2);
  });

  it('groups by workspace day with Today and Yesterday, even when the device is a day away', () => {
    const now = new Date('2026-10-24T06:00:00Z'); // 1 am in Chicago; the previous calendar day in UTC
    const groups = byDay(
      [
        entry('2026-10-24T03:00:00Z'), // 10 pm the day before, Chicago time
        entry('2026-10-23T18:00:00Z'),
        entry('2026-10-23T04:00:00Z'),
        entry('2026-10-20T15:00:00Z'),
      ],
      now,
    );
    expect(groups.map((g) => g.label.split(',')[0])).toEqual(['Yesterday', 'Thursday', 'Tuesday']);
    expect(groups[0].entries).toHaveLength(2);
    expect(groups).toHaveLength(3);
  });

  it('tones and change lines', () => {
    expect(auditTone('share.granted')).toBe('access');
    expect(auditTone('item.created')).toBe('create');
    expect(auditTone('item.trashed')).toBe('remove');
    expect(auditTone('item.updated')).toBe('change');
    expect(changeLine(entry('x', { field: 'title', before: 'Milk', after: 'Oat milk' }))).toBe('Milk → Oat milk');
    expect(changeLine(entry('x', { field: 'private', before: false, after: true }))).toBe('no → yes');
    expect(changeLine(entry('x'))).toBeNull();
  });

  it('labels reach', () => {
    expect(reachLabel({ all: true, sections: {}, lists: {} })).toBe('Everything');
    expect(reachLabel({ all: false, sections: {}, lists: {} })).toBe('Nothing shared yet');
    expect(reachLabel({ all: false, sections: { a: 1 }, lists: { b: 1, c: 1 } })).toBe('1 section, 2 lists');
  });
});

describe('agent rules', () => {
  const rules: AgentRules = {
    denied: ['share', 'delete'],
    default: true,
    actions: [
      { key: 'delete', label: 'delete for good' },
      { key: 'trash', label: 'trash' },
      { key: 'share', label: 'share' },
    ],
  };
  it('toggles a key and keeps the vocabulary order', () => {
    expect(toggleDenied(rules, 'trash', true)).toEqual(['delete', 'trash', 'share']);
    expect(toggleDenied(rules, 'delete', false)).toEqual(['share']);
    expect(toggleDenied(rules, 'share', true)).toEqual(['delete', 'share']);
  });
  it('knows which events change them', () => {
    expect(changesAgentRules({ type: 'settings.changed', field: 'agents.denied' })).toBe(true);
    expect(changesAgentRules({ type: 'settings.changed', field: 'other' })).toBe(false);
    expect(changesAgentRules({ type: 'share.granted', field: null })).toBe(false);
  });
});
