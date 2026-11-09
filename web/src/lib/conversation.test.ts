import { describe, expect, it } from 'vitest';
import type { Row } from './activity';
import { badgeLabel, collapsedChat, askFor, defaultAsk, firstLine, isChat, mentionedAgent, pendingAgent, safeAgentUrl, shortAge, shownProgress, updatedAgo, waitingQuestion } from './conversation';
import type { Account, AgentFlag, Comment } from './types';

const AGENTS = new Set(['agent', 'agent2']);
const isAgent = (id: string) => AGENTS.has(id);

function c(id: string, author: string, extra: Partial<Comment> = {}): Comment {
  return { id, item_id: 'i', author_id: author, body_md: id, created_at: '2026-10-26T20:00:00Z', edited_at: null, ...extra };
}

function rows(...comments: Comment[]): Row[] {
  return comments.map((x) => ({ kind: 'comment', key: x.id, at: x.created_at, comment: x }));
}

describe('isChat', () => {
  it('is an agent comment or one addressed to an agent', () => {
    expect(isChat(c('a', 'agent'), isAgent)).toBe(true);
    expect(isChat(c('b', 'alex', { assignee_id: 'agent' }), isAgent)).toBe(true);
    expect(isChat(c('c', 'alex'), isAgent)).toBe(false);
    expect(isChat(c('d', 'alex', { assignee_id: 'member' }), isAgent)).toBe(false);
  });
});

describe('collapsedChat', () => {
  it('keeps only the newest exchange open', () => {
    const r = rows(
      c('q1', 'agent'), // exchange 1: the agent asks
      c('a1', 'alex', { assignee_id: 'agent' }),
      c('v1', 'agent'), // exchange 2: Alex answered, the agent verified
      c('note', 'alex'), // not chat
      c('a2', 'alex', { assignee_id: 'agent' }), // waiting for the agent: newest
    );
    expect([...collapsedChat(r, isAgent)].sort()).toEqual(['q1']);
  });

  it('never collapses an open Ask', () => {
    const r = rows(c('q1', 'agent', { assignee_id: 'alex' }), c('q2', 'agent'));
    expect(collapsedChat(r, isAgent).size).toBe(0);
  });

  it('collapses nothing with one exchange', () => {
    expect(collapsedChat(rows(c('q1', 'agent')), isAgent).size).toBe(0);
    expect(collapsedChat(rows(c('x', 'alex')), isAgent).size).toBe(0);
  });

  it('collapses every closed exchange but the last', () => {
    const done = { assignee_id: 'agent', resolved_at: '2026-10-26T20:05:00Z' };
    const r = rows(c('q1', 'agent'), c('a1', 'alex', done), c('q2', 'agent'), c('a2', 'alex', done), c('q3', 'agent'));
    expect([...collapsedChat(r, isAgent)].sort()).toEqual(['a1', 'q1', 'q2']);
  });
});

describe('firstLine', () => {
  it('takes the first non-empty line without markdown marks', () => {
    expect(firstLine('\n## **Done** it\nmore')).toBe('Done it');
    expect(firstLine('x'.repeat(200), 10)).toBe('xxxxxxxxx…');
  });
});

describe('safeAgentUrl', () => {
  it('allows http(s) when the server said nothing', () => {
    expect(safeAgentUrl('https://chat.example/c/1', undefined)).toBe('https://chat.example/c/1');
    expect(safeAgentUrl('HTTP://x.example/', [])).toBe('HTTP://x.example/');
    expect(safeAgentUrl('hermes://chat/abc?profile=agent2', undefined)).toBeNull();
    expect(safeAgentUrl('javascript:alert(1)', undefined)).toBeNull();
    expect(safeAgentUrl(null, undefined)).toBeNull();
  });
  it('follows a desktop scheme only when the server allows it', () => {
    const schemes = ['http', 'https', 'hermes'];
    expect(safeAgentUrl('hermes://chat/abc?profile=agent2', schemes)).toBe('hermes://chat/abc?profile=agent2');
    expect(safeAgentUrl('hermes:chat', schemes)).toBeNull(); // no host part
    expect(safeAgentUrl('file:///etc/passwd', schemes)).toBeNull();
  });
});

describe('badgeLabel', () => {
  const labels = { working: 'Working', blocked: 'Agent blocked', review: 'Ready for review' };
  const flag: AgentFlag = { state: 'blocked', by: 'agent', at: '2026-10-26T20:00:00Z' };
  const names: Record<string, string> = { alex: 'Alex' };
  const nameOf = (id: string) => names[id];
  it('names the person the card waits on, for everyone', () => {
    expect(badgeLabel(flag, { waiting_on_account_id: 'alex', open_asks: [] }, nameOf, labels)).toBe('Waiting on Alex');
  });
  it('says Agent blocked when the agent named nobody', () => {
    expect(badgeLabel(flag, { waiting_on_account_id: null, open_asks: [] }, nameOf, labels)).toBe('Agent blocked');
  });
  it('says Answered once the person replied', () => {
    expect(badgeLabel(flag, { waiting_on_account_id: null, open_asks: ['agent'] }, nameOf, labels)).toBe('Answered');
  });
  it('keeps the state label otherwise', () => {
    expect(badgeLabel({ ...flag, state: 'working' }, null, nameOf, labels)).toBe('Working');
  });
});

describe('ages', () => {
  const now = new Date('2026-10-26T20:01:12Z');
  it('counts seconds, then minutes, then hours', () => {
    expect(updatedAgo('2026-10-26T20:01:00Z', now)).toBe('updated 12 s ago');
    expect(updatedAgo('2026-10-26T19:57:00Z', now)).toBe('updated 4 min ago');
    expect(shortAge('2026-10-26T20:01:00Z', now)).toBe('now');
    expect(shortAge('2026-10-26T18:01:00Z', now)).toBe('2 h');
  });
});

describe('defaultAsk', () => {
  const acct = (id: string, kind: 'agent' | 'person', extra: Partial<Account> = {}): Account => ({
    id,
    handle: id,
    display_name: id,
    kind,
    ...extra,
  });
  const accounts = [
    acct('alex', 'person'),
    acct('agent3', 'agent', { wake: 'push', created_at: '2026-10-26T12:00:00Z' }),
    acct('agent', 'agent', { wake: 'push', created_at: '2026-10-20T12:00:00Z' }),
    acct('agent2', 'agent', { wake: 'manual', created_at: '2026-10-01T12:00:00Z' }),
  ];
  const at = (n: number) => `2026-10-26T20:0${n}:00Z`;

  it('starts on the agent assigned to the card', () => {
    const item = { assignees: [accounts[0], accounts[1]] };
    expect(defaultAsk(item, [], accounts, 'agent')).toBe('agent3');
  });

  it('then the agent last asked on the card, then the one reporting on it', () => {
    const item = { assignees: [accounts[0]], agent: { state: 'review', by: 'agent2' } as AgentFlag };
    expect(defaultAsk(item, [{ assignee_id: 'agent3', created_at: at(1) }], accounts, null)).toBe('agent3');
    expect(defaultAsk(item, [{ assignee_id: 'alex', created_at: at(1) }], accounts, null)).toBe('agent2');
  });

  it('then the agent you asked last, then the oldest agent with a runner', () => {
    expect(defaultAsk({ assignees: [] }, [], accounts, 'agent2')).toBe('agent2');
    expect(defaultAsk({ assignees: [] }, [], accounts, 'alex')).toBe('agent');
    expect(defaultAsk({ assignees: [] }, [], [accounts[0]], null)).toBe('');
  });

  it("on a project, starts on the project's agent before anyone asked elsewhere", () => {
    const guestAgent = acct('guest-agent', 'agent', { wake: 'push', role: 'guest' });
    const all = [...accounts, guestAgent];
    // The workspace agent was asked on this card and is the one remembered, but the card is the guest agent's project.
    expect(defaultAsk({ assignees: [] }, [{ assignee_id: 'agent', created_at: at(1) }], all, 'agent', ['guest-agent', 'agent'])).toBe('guest-agent');
    // A card's own agent assignee still wins; an unknown project agent is skipped.
    expect(defaultAsk({ assignees: [accounts[1]] }, [], all, null, ['guest-agent'])).toBe('agent3');
    expect(defaultAsk({ assignees: [] }, [], accounts, 'agent2', ['guest-agent'])).toBe('agent2');
  });
});

describe('mentionedAgent', () => {
  const accounts: Account[] = [
    { id: 'c', handle: 'alex', display_name: 'Alex', kind: 'person' },
    { id: 'g', handle: 'guest-agent', display_name: 'Guest Agent', kind: 'agent' },
    { id: 'k', handle: 'agent', display_name: 'Agent', kind: 'agent' },
  ];
  it('is the first agent @mentioned, ignoring people and e-mail addresses', () => {
    expect(mentionedAgent('@guest-agent can you create a task for Alex?', accounts)).toBe('guest-agent');
    expect(mentionedAgent('@alex and @Agent, thoughts?', accounts)).toBe('agent');
    expect(mentionedAgent('mail guest@guest-agent.test', accounts)).toBe('');
    expect(mentionedAgent('no mention here', accounts)).toBe('');
  });
});

describe('askFor', () => {
  const accounts: Account[] = [
    { id: 'j', handle: 'guest', display_name: 'Guest', kind: 'person' },
    { id: 'g', handle: 'guest-agent', display_name: 'Guest Agent', kind: 'agent' },
  ];
  it('a comment to a person is not an Ask to the default agent', () => {
    expect(askFor('@guest Should I consider the 2nd one incomplete?', accounts, 'guest-agent')).toBe('');
  });
  it('an @agent wins, and no mention keeps the default', () => {
    expect(askFor('@guest and @guest-agent, thoughts?', accounts, 'agent')).toBe('guest-agent');
    expect(askFor('can you check this?', accounts, 'guest-agent')).toBe('guest-agent');
    expect(askFor('mail guest@guest-agent.test', accounts, 'guest-agent')).toBe('guest-agent');
    expect(askFor('@nobody here', accounts, 'guest-agent')).toBe('guest-agent');
  });
});

describe('pendingAgent', () => {
  it('consumes only the finishing agent take boundary, not newer or another agent Asks', () => {
    const asks = [{ id: 'ask', assignee_id: 'agent', resolved_at: null, created_at: '2026-10-26T20:01:00Z' }];
    const events = [
      { id: '01', type: 'comment.assigned', subject_id: 'ask' },
      { id: '03', type: 'item.work_finished', actor_id: 'agent', after: { through: '02' } },
    ] as unknown as import('./types').SlateEvent[];
    expect(pendingAgent({}, asks, isAgent, events)).toBeNull();
    expect(asks[0].resolved_at).toBeNull();
    expect(pendingAgent({}, asks, isAgent, [{ ...events[0], id: '04' }, events[1]])).toEqual({ id: 'agent', picked: false });
    expect(pendingAgent({}, asks, isAgent, [events[0], { ...events[1], actor_id: 'agent2' }])).toEqual({ id: 'agent', picked: false });
  });
  it('is the working agent, else the agent with the newest open Ask', () => {
    const working = { state: 'working', by: 'agent' } as AgentFlag;
    expect(pendingAgent({ agent: working }, [], isAgent)).toEqual({ id: 'agent', picked: true });
    const asks = [
      { assignee_id: 'agent', resolved_at: '2026-10-26T20:02:00Z', created_at: '2026-10-26T20:01:00Z' },
      { assignee_id: 'agent2', resolved_at: null, created_at: '2026-10-26T20:03:00Z' },
      { assignee_id: 'member', resolved_at: null, created_at: '2026-10-26T20:04:00Z' },
    ];
    expect(pendingAgent({ agent: null }, asks, isAgent)).toEqual({ id: 'agent2', picked: false });
    expect(pendingAgent({ agent: null }, asks.slice(0, 1), isAgent)).toBeNull();
  });
});

describe('shownProgress', () => {
  it('hides the start marker and missing values, shows real progress', () => {
    expect(shownProgress(null)).toBeNull();
    expect(shownProgress(undefined)).toBeNull();
    expect(shownProgress(0)).toBeNull();
    expect(shownProgress(5)).toBeNull();
    expect(shownProgress(40)).toBe(40);
    expect(shownProgress(100)).toBe(100);
    expect(shownProgress(140)).toBe(100);
  });
});

describe('waitingQuestion', () => {
  const ev = (at: string, actor: string, after: string | null) => ({
    id: `e${at}`, type: 'item.updated', field: 'waiting_on_account_id', after, actor_id: actor, at,
  });
  const body = 'Status table below.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nTo finish: detail-opening was declined; should I leave those details unopened? Final sign-out is unconfirmed.';

  it('finds the asker, their comment and the question sentence', () => {
    const comments = [
      c('old', 'agent', { created_at: '2026-11-01T10:00:00Z' }),
      c('ask', 'agent', { body_md: body, created_at: '2026-11-06T15:08:42Z' }),
    ];
    const q = waitingQuestion({ waiting_on_account_id: 'alex' }, comments, [ev('2026-11-06T15:08:43Z', 'agent', 'alex')], 'alex');
    expect(q).toEqual({ by: 'agent', commentId: 'ask', question: 'To finish: detail-opening was declined; should I leave those details unopened?' });
  });

  it('is null when the card waits on someone else', () => {
    expect(waitingQuestion({ waiting_on_account_id: 'member' }, [], [], 'alex')).toBeNull();
  });

  it('ignores comments the asker wrote after the wait', () => {
    const comments = [
      c('ask', 'agent', { body_md: 'Which one?', created_at: '2026-11-06T15:00:00Z' }),
      c('later', 'agent', { body_md: 'Still here.', created_at: '2026-11-07T15:00:00Z' }),
    ];
    const q = waitingQuestion({ waiting_on_account_id: 'alex' }, comments, [ev('2026-11-06T15:00:01Z', 'agent', 'alex')], 'alex');
    expect(q?.commentId).toBe('ask');
    expect(q?.question).toBe('Which one?');
  });

  it('falls back to the newest comment by someone else, then its first line', () => {
    const comments = [
      c('n', 'other', { body_md: '## Need the invoice\nAttach it here.' }),
      c('mine', 'alex', { created_at: '2026-10-27T20:00:00Z' }),
    ];
    const q = waitingQuestion({ waiting_on_account_id: 'alex' }, comments, [], 'alex');
    expect(q).toEqual({ by: 'other', commentId: 'n', question: 'Need the invoice' });
  });

  it('has no question when nobody has written', () => {
    expect(waitingQuestion({ waiting_on_account_id: 'alex' }, [], [], 'alex')).toEqual({ by: null, commentId: null, question: null });
  });
});
