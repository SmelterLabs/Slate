import { describe, expect, it } from 'vitest';
import { ago, describeRrule, dueLabel, formatEstimate, hiddenAsFinished, isOverdue, parseEstimate } from './format';
import { describe as describeEvent } from './history';
import { renderMarkdown } from './markdown';
import { LIST, ev, item } from './fixtures';

const NOW = new Date('2026-10-22T20:00:00Z'); // Thu 15:00 Chicago

describe('dates', () => {
  it('labels due dates in Chicago time', () => {
    expect(dueLabel('2026-10-22T22:00:00Z', NOW)).toBe('Today');
    expect(dueLabel('2026-10-23T22:00:00Z', NOW)).toBe('Tomorrow');
    expect(dueLabel('2026-10-22T15:30:00Z', NOW)).toBe('Today 10:30 AM');
    expect(dueLabel('2026-11-11T23:00:00Z', NOW)).toBe('Nov 11');
    expect(dueLabel('2027-01-05T23:00:00Z', NOW)).toBe('Jan 5, 2027');
  });

  it('says how long ago', () => {
    expect(ago('2026-10-22T19:59:50Z', NOW)).toBe('just now');
    expect(ago('2026-10-22T19:58:00Z', NOW)).toBe('2 min ago');
    expect(ago('2026-10-22T17:00:00Z', NOW)).toBe('3 h ago');
    expect(ago('2026-10-21T20:00:00Z', NOW)).toBe('yesterday');
    expect(ago('2026-10-01T20:00:00Z', NOW)).toBe('Oct 1');
  });

  it('overdue only when open and past due', () => {
    expect(isOverdue(item({ due_at: '2026-10-22T19:00:00Z' }), NOW)).toBe(true);
    expect(isOverdue(item({ due_at: '2026-10-22T21:00:00Z' }), NOW)).toBe(false);
    expect(isOverdue(item({ due_at: '2026-10-22T19:00:00Z', status: LIST.statuses[2] }), NOW)).toBe(
      false,
    );
  });

  it('finished items leave the board at Chicago midnight', () => {
    const done = LIST.statuses[2];
    expect(hiddenAsFinished(item({ status: done, completed_at: '2026-10-22T14:00:00Z' }), NOW)).toBe(
      false,
    );
    expect(hiddenAsFinished(item({ status: done, completed_at: '2026-10-22T04:00:00Z' }), NOW)).toBe(
      true,
    );
    expect(hiddenAsFinished(item(), NOW)).toBe(false);
  });
});

describe('estimates', () => {
  it('reads and writes estimates', () => {
    expect(parseEstimate('90')).toBe(90);
    expect(parseEstimate('1h 30m')).toBe(90);
    expect(parseEstimate('1.5h')).toBe(90);
    expect(parseEstimate('2d')).toBe(960);
    expect(parseEstimate('soon')).toBeNull();
    expect(formatEstimate(90)).toBe('1h 30m');
    expect(formatEstimate(45)).toBe('45m');
    expect(formatEstimate(120)).toBe('2h');
  });
});

describe('describeRrule', () => {
  it.each([
    ['FREQ=WEEKLY;INTERVAL=2;BYDAY=WE', 'every 2 weeks on Wednesday'],
    ['FREQ=DAILY', 'every day'],
    ['FREQ=WEEKLY;BYDAY=MO,WE,FR', 'every week on Monday, Wednesday and Friday'],
    ['FREQ=MONTHLY;BYMONTHDAY=1', 'every month on the 1st'],
    ['FREQ=MONTHLY;BYDAY=-1FR', 'every month on last Friday'],
    ['FREQ=MONTHLY;BYDAY=2TU', 'every month on 2nd Tuesday'],
    ['FREQ=YEARLY;INTERVAL=3', 'every 3 years'],
    ['NONSENSE', 'NONSENSE'],
  ])('%s', (rule, want) => expect(describeRrule(rule)).toBe(want));
});

describe('history lines', () => {
  const at = '2026-10-22T19:58:00Z';
  const ctx = {
    name: (id: string) => ({ A_agent: 'Agent', A_alex: 'Alex' })[id],
    status: (id: string) => LIST.statuses.find((s) => s.id === id)?.name,
    field: (id: string) =>
      id === 'F_size'
        ? {
            id: 'F_size',
            list_id: null,
            name: 'Size',
            type: 'dropdown' as const,
            options: [{ id: 'o_s', name: 'S', color: null }],
            position: 0,
            created_at: at,
            archived_at: null,
          }
        : undefined,
    title: (id: string) => ({ I_deck: 'Plan the deck' })[id],
    now: NOW,
  };

  it.each([
    [{ type: 'item.status_changed', after: 'st_ready' }, 'Agent moved this to Ready · 2 min ago via MCP'],
    [{ type: 'item.created', source: 'api' as const }, 'Agent created this · 2 min ago via API'],
    [
      { type: 'item.assigned', before: ['alex'], after: ['agent'] },
      'Agent assigned agent and unassigned alex · 2 min ago via MCP',
    ],
    [{ field: 'due_at', after: '2026-11-11T23:00:00Z' }, 'Agent set the due date to Nov 11 · 2 min ago via MCP'],
    [{ field: 'due_at', after: null }, 'Agent cleared the due date · 2 min ago via MCP'],
    [{ field: 'tags', before: ['a'], after: ['b'] }, 'Agent tagged this #b and removed #a · 2 min ago via MCP'],
    [{ field: 'title', after: 'New' }, 'Agent renamed this to “New” · 2 min ago via MCP'],
    [{ field: 'priority', after: 'high' }, 'Agent set priority to High · 2 min ago via MCP'],
    [{ field: 'completed_at', after: at }, 'Agent marked this complete · 2 min ago via MCP'],
    [{ type: 'item.commented' }, 'Agent commented · 2 min ago via MCP'],
    [
      { type: 'recurrence.spawned', actor_id: 'A_alex', source: 'system' as const },
      'Alex created this from a recurring rule · 2 min ago automatically',
    ],
    [{ actor_id: 'nobody', field: 'body_md' }, 'Someone edited the description · 2 min ago via MCP'],
    [{ field: 'estimate_minutes', after: 90 }, 'Agent set the estimate to 1h 30m · 2 min ago via MCP'],
    [
      { type: 'item.linked', field: 'links', after: { kind: 'blocked_by', item_id: 'I_deck' } },
      'Agent made this depend on “Plan the deck” · 2 min ago via MCP',
    ],
    [
      { type: 'item.unlinked', field: 'links', before: { kind: 'blocks', item_id: 'I_x' } },
      'Agent removed the link with “another task” · 2 min ago via MCP',
    ],
  ])('%o', (over, want) => {
    expect(describeEvent(ev({ at, ...over }), ctx)).toBe(want);
  });
});

describe('renderMarkdown', () => {
  it('escapes HTML', () => {
    expect(renderMarkdown('<script>alert(1)</script>')).toBe(
      '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
    );
  });

  it('renders the basics', () => {
    expect(renderMarkdown('# Title\n\nSome **bold** and *it* and `x<y`\n\n- a\n- [x] b')).toBe(
      '<h3>Title</h3><p>Some <strong>bold</strong> and <em>it</em> and <code>x&lt;y</code></p>' +
        '<ul><li>a</li><li>☑ b</li></ul>',
    );
  });

  it('only links safe schemes', () => {
    expect(renderMarkdown('[a](https://x.y/z)')).toContain('href="https://x.y/z"');
    expect(renderMarkdown('[a](javascript:alert(1))')).not.toContain('href');
  });

  it('keeps fenced code verbatim', () => {
    expect(renderMarkdown('```\n**no**\n```')).toBe('<pre><code>**no**</code></pre>');
  });
});
