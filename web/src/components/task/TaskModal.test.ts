// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, ev, item } from '../../lib/fixtures';
import type { Comment, ItemDetail } from '../../lib/types';

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(async () => []),
  postMock: vi.fn(async () => ({})),
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: vi.fn(async () => undefined),
    get: getMock,
    post: postMock,
    patch: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
  };
});

vi.mock('../../lib/docs.svelte', () => ({
  docs: { embedding: vi.fn(async () => []) },
}));

vi.mock('../../lib/liveactivity.svelte', () => ({
  liveActivity: { load: vi.fn(), clear: vi.fn(), lines: {} },
}));

import { board } from '../../lib/store.svelte';
import { task } from '../../lib/task.svelte';
import TaskModal from './TaskModal.svelte';

function makeDetail(over: Partial<ItemDetail> = {}): ItemDetail {
  return {
    ...item({
      id: 'I1',
      list_id: 'L1',
      title: 'Import data',
      waiting_on_account_id: 'me',
      agent: { state: 'blocked', by: 'sc', at: '2026-10-29T10:00:00Z', note: 'Start the import now?' },
    }),
    comments: [],
    events: [],
    subtasks: [],
    checklists: [],
    attachments: [],
    watchers: [],
    ...over,
  };
}

function makeComment(over: Partial<Comment> = {}): Comment {
  return {
    id: 'C1',
    item_id: 'I1',
    author_id: 'sc',
    author_handle: 'my-agent',
    body_md: 'The file is ready.',
    created_at: '2026-10-29T10:00:00Z',
    edited_at: null,
    mentions: [],
    assignee_id: 'me',
    resolved_at: null,
    resolved_by_id: null,
    reactions: [],
    ask: null,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  getMock.mockResolvedValue([]);
  postMock.mockResolvedValue({});
  board.me = {
    account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    token_id: 'T1',
    scopes: ['admin'],
    prefs: {},
  };
  board.accounts = {
    me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' },
    sc: { id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' },
  };
  board.lists = { L1: LIST };
  board.sections = [{ id: 'S1', name: 'Work', slug: 'work', audience: 'work', position: 0, archived_at: null, created_at: '2026-10-20T00:00:00Z' }];
  board.now = new Date('2026-10-29T12:00:00Z');
  board.openId = 'I1';
  task.reset();
  task.filter = 'all';
});

it('requires both a note and a future date to park, and clears them together', async () => {
  const detail = makeDetail({ waiting_on_account_id: null, agent: null });
  board.items = { I1: detail };
  board.detail = detail;
  const update = vi.spyOn(board, 'update').mockResolvedValue(true);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(TaskModal, { target });
  flushSync();

  const button = target.querySelector<HTMLButtonElement>('[data-park-save]')!;
  const note = target.querySelector<HTMLInputElement>('[aria-label="Waiting on note"]')!;
  const date = target.querySelector<HTMLInputElement>('[aria-label="Waiting until date"]')!;
  const fill = (el: HTMLInputElement, value: string) => {
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
  };
  try {
    expect(button.disabled).toBe(true);
    fill(note, '  Bank reply  ');
    fill(date, '2026-10-29');
    expect(button.disabled).toBe(true);
    fill(date, '2026-10-30');
    expect(button.disabled).toBe(false);
    button.click();
    const parked = { parked_note: 'Bank reply', parked_until: '2026-10-30' };
    expect(update).toHaveBeenLastCalledWith('I1', parked);

    board.items.I1 = { ...detail, ...parked };
    board.detail = { ...detail, ...parked };
    flushSync();
    target.querySelector<HTMLButtonElement>('[data-park-clear]')!.click();
    expect(update).toHaveBeenLastCalledWith('I1', { parked_note: null, parked_until: null });
  } finally {
    await unmount(app);
    target.remove();
    update.mockRestore();
  }
});

describe('TaskModal ask and waiting banner', () => {
  it('shows the question as heading, "<Agent> asks" below it, renders duplicate option labels, and removes buttons when answered while open', async () => {
    const ask = {
      comment_id: 'C1',
      question: 'Start the import now?',
      options: ['Yes', 'Yes'],
      questions: [{ question: 'Start the import now?', options: ['Yes', 'Yes'] }],
      by_id: 'sc',
      at: '2026-10-29T10:00:00Z',
    };
    const askComment = makeComment({
      id: 'C1',
      ask: { question: ask.question, options: ask.options, questions: ask.questions },
    });
    const detail = makeDetail({ ask, comments: [askComment] });
    board.items = { I1: { ...detail } };
    board.detail = detail;
    task.activity = [{ kind: 'comment', at: askComment.created_at, comment: askComment, event: null }];

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TaskModal, { target });

    const banner = target.querySelector('[data-banner="waiting"]');
    expect(banner).not.toBeNull();
    expect(banner?.querySelector('[data-waiting-question]')?.textContent?.trim()).toBe('Start the import now?');
    expect(banner?.textContent).toContain('My Agent asks');
    expect(banner?.textContent).not.toContain('My Agent is waiting on you');

    const bannerButtons = [...target.querySelectorAll<HTMLButtonElement>('[data-banner="waiting"] [data-answer-option]')];
    expect(bannerButtons.map((b) => b.textContent?.trim())).toEqual(['Yes', 'Yes']);

    const commentButtons = [...target.querySelectorAll<HTMLButtonElement>('[data-comment="C1"] [data-answer-option]')];
    expect(commentButtons.map((b) => b.textContent?.trim())).toEqual(['Yes', 'Yes']);

    // Answer while the task window stays open.
    bannerButtons[0].click();
    expect(postMock).toHaveBeenCalledWith('/comments/C1/answer', { option: 'Yes' });

    const resolved = { ...askComment, resolved_at: '2026-10-29T12:00:00Z', resolved_by_id: 'me' };
    const reply = makeComment({
      id: 'C2',
      author_id: 'me',
      author_handle: 'alex',
      body_md: 'Yes',
      created_at: '2026-10-29T12:00:01Z',
      assignee_id: null,
    });
    board.items.I1 = { ...board.items.I1, waiting_on_account_id: null, ask: null, agent: null };
    board.detail = makeDetail({
      waiting_on_account_id: null,
      ask: null,
      agent: null,
      comments: [resolved, reply],
    });
    task.activity = [
      { kind: 'comment', at: resolved.created_at, comment: resolved, event: null },
      { kind: 'comment', at: reply.created_at, comment: reply, event: null },
    ];
    flushSync();

    expect(target.querySelector('[data-banner="waiting"]')).toBeNull();
    expect(target.querySelectorAll('[data-answer-option]')).toHaveLength(0);
    const plainOptions = [...target.querySelectorAll<HTMLElement>('[data-comment="C1"] [data-ask-option]')];
    expect(plainOptions.map((el) => el.textContent?.trim())).toEqual(['Yes', 'Yes']);
    expect(plainOptions.every((el) => el.tagName === 'SPAN')).toBe(true);

    await unmount(app);
    target.remove();
  });

  it('lets the live row ask: null win over a stale detail snapshot ask', async () => {
    const staleAsk = {
      comment_id: 'C1',
      question: 'Stale question?',
      options: ['A', 'B'],
      questions: [{ question: 'Stale question?', options: ['A', 'B'] }],
      by_id: 'sc',
      at: '2026-10-29T10:00:00Z',
    };
    const detail = makeDetail({
      ask: staleAsk,
      comments: [makeComment({ id: 'C1', body_md: 'Which branch should I use?', assignee_id: null })],
      events: [ev({ field: 'waiting_on_account_id', after: 'me', actor_id: 'sc', at: '2026-10-29T10:00:00Z' })],
    });
    // Live board row already cleared ask to null.
    board.items = { I1: { ...detail, ask: null } };
    board.detail = detail;
    task.activity = [];

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TaskModal, { target });

    const banner = target.querySelector('[data-banner="waiting"]');
    expect(banner?.querySelectorAll('[data-answer-option]')).toHaveLength(0);
    expect(banner?.textContent).toContain('My Agent is waiting on you');
    expect(banner?.querySelector('[data-waiting-question]')).toBeNull();

    await unmount(app);
    target.remove();
  });

  it('keeps a typed reply working without treating prose as an Ask', async () => {
    const comment = makeComment({ id: 'C1', body_md: 'Which branch should I use?', assignee_id: null });
    const detail = makeDetail({
      ask: null,
      comments: [comment],
      events: [ev({ field: 'waiting_on_account_id', after: 'me', actor_id: 'sc', at: '2026-10-29T10:00:00Z' })],
    });
    board.items = { I1: { ...detail } };
    board.detail = detail;
    task.activity = [{ kind: 'comment', at: comment.created_at, comment, event: null }];

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TaskModal, { target });

    const banner = target.querySelector('[data-banner="waiting"]');
    expect(banner?.textContent).toContain('My Agent is waiting on you');
    expect(banner?.textContent).not.toContain('Which branch should I use?');
    expect(banner?.querySelector('[data-waiting-question]')).toBeNull();
    expect(banner?.querySelectorAll('[data-answer-option]')).toHaveLength(0);

    const textarea = target.querySelector<HTMLTextAreaElement>('.compose textarea');
    expect(textarea).not.toBeNull();
    textarea!.value = 'Use main';
    textarea!.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();

    const sendBtn = target.querySelector<HTMLButtonElement>('.compose button.send');
    expect(sendBtn).not.toBeNull();
    sendBtn!.click();
    await Promise.resolve();

    expect(postMock).toHaveBeenCalledWith(
      '/items/I1/comments',
      expect.objectContaining({ body_md: 'Use main' }),
    );

    await unmount(app);
    target.remove();
  });

  it('shows standing and stalled at the top of the task window, but hides standing when an open ask is shown', async () => {
    const standing = {
      line: 'Check the draft linked above.',
      by_id: 'sc',
      at: '2026-10-29T11:00:00Z',
      move_id: 'me',
    };
    const detail = makeDetail({
      waiting_on_account_id: null,
      agent: null,
      ask: null,
      assignees: [{ id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' }],
      standing,
      stalled: true,
    });
    board.items = { I1: { ...detail } };
    board.detail = detail;
    task.activity = [];

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TaskModal, { target });

    expect(target.querySelector('[data-standing-line]')?.textContent?.trim()).toBe(standing.line);
    expect(target.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('Your move');
    expect(target.querySelector('[data-stalled]')?.textContent).toContain('Stalled');
    expect(target.querySelector('[data-stalled-run]')?.textContent).toContain('Run now');

    // When an open ask is shown, the question box already says it: do not show both.
    const ask = {
      comment_id: 'C1',
      question: 'Start the import now?',
      options: ['Yes', 'No'],
      questions: [{ question: 'Start the import now?', options: ['Yes', 'No'] }],
      by_id: 'sc',
      at: '2026-10-29T11:30:00Z',
    };
    board.items.I1 = { ...board.items.I1, waiting_on_account_id: 'me', ask, stalled: false };
    board.detail = makeDetail({
      waiting_on_account_id: 'me',
      ask,
      standing: { ...standing, line: ask.question },
      stalled: false,
    });
    flushSync();

    expect(target.querySelector('[data-banner="waiting"]')).not.toBeNull();
    expect(target.querySelector('[data-standing]')).toBeNull();

    await unmount(app);
    target.remove();
  });
});

describe('TaskModal questions and reviews', () => {
  const THREE = {
    question: '3 questions: Which branch should I ship?',
    options: [],
    questions: [
      { question: 'Which branch should I ship?', options: ['main', 'next'] },
      { question: 'Tell the customer today?', options: ['Yes', 'No'] },
      { question: 'Who signs off the invoice?', options: [] },
    ],
  };

  async function open(detail: ItemDetail) {
    board.items = { I1: { ...detail } };
    board.detail = detail;
    task.activity = [
      ...detail.comments.map((comment) => ({ kind: 'comment' as const, at: comment.created_at, comment, event: null })),
    ];
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TaskModal, { target });
    flushSync();
    return {
      target,
      close: async () => {
        await unmount(app);
        target.remove();
      },
    };
  }

  it('puts the question block first in the comment, above its author and body', async () => {
    const ask = { ...THREE, comment_id: 'C1', by_id: 'sc', at: '2026-10-29T10:00:00Z' };
    const comment = makeComment({ body_md: 'A long weekly report.\n\nWith many paragraphs.', ask: THREE });
    const { target, close } = await open(makeDetail({ ask, comments: [comment] }));
    try {
      const card = target.querySelector('[data-comment="C1"]')!;
      expect(card.firstElementChild?.matches('[data-ask-box]')).toBe(true);
      expect(card.querySelectorAll('[data-ask-box]')).toHaveLength(1);
      expect(card.querySelector('.ask-pill')).toBeNull();
      // Three questions: nothing in the thread answers them one tap at a time.
      expect(card.querySelectorAll('[data-answer-option]')).toHaveLength(0);
      expect(target.querySelector('[data-banner="waiting"] .wq-heading')?.textContent?.trim()).toBe('My Agent needs 3 answers from you');
    } finally {
      await close();
    }
  });

  it('keeps the question block in full on a folded comment', async () => {
    const asked = makeComment({ id: 'C1', ask: THREE, resolved_at: '2026-10-29T10:30:00Z', resolved_by_id: 'me' });
    const reply = makeComment({
      id: 'C2',
      author_id: 'me',
      author_handle: 'alex',
      body_md: 'Answers',
      created_at: '2026-10-29T10:30:00Z',
      assignee_id: null,
    });
    const later = makeComment({ id: 'C3', body_md: 'Done, shipped.', created_at: '2026-10-29T11:00:00Z', assignee_id: null });
    const { target, close } = await open(
      makeDetail({ waiting_on_account_id: null, agent: null, ask: null, comments: [asked, reply, later] }),
    );
    try {
      const article = target.querySelector('article[data-chat-collapsed][data-comment="C1"]')!;
      expect(article).not.toBeNull();
      const block = article.querySelector(':scope > [data-ask-box]:first-child')!;
      expect(block).not.toBeNull();
      expect(block.closest('[inert]')).toBeNull();
      expect(article.querySelector('.comment-content[inert]')).not.toBeNull();
      expect(block.getAttribute('data-ask-state')).toBe('answered');
      expect([...block.querySelectorAll('[data-ask-question]')].map((q) => q.textContent?.trim())).toEqual(
        THREE.questions.map((q) => q.question),
      );
    } finally {
      await close();
    }
  });

  it('does not offer a new live Ask when the detail still says the viewer is waiting', async () => {
    const q = { question: 'Start the import now?', options: ['Yes', 'No'] };
    const ask = { ...q, questions: [q], comment_id: 'C1', by_id: 'sc', at: '2026-10-29T10:00:00Z' };
    const detail = makeDetail({ ask });
    const { target, close } = await open(detail);
    try {
      expect(target.querySelector('[data-banner="waiting"] [data-answer-option]')).not.toBeNull();
      board.items = { I1: {
        ...detail,
        waiting_on_account_id: 'other',
        ask: { ...ask, comment_id: 'C2', question: 'Should the next report wait?',
          questions: [{ ...q, question: 'Should the next report wait?' }] },
      } };
      flushSync();
      expect(target.querySelector('[data-banner="waiting"]')).toBeNull();
    } finally {
      await close();
    }
  });

  it('shows a review for the viewer as a banner saying what to check, in place of the badge', async () => {
    const standing = { line: 'Check the totals in the March column', by_id: 'sc', at: '2026-10-29T11:00:00Z', move_id: 'me' };
    const agent = { state: 'review' as const, by: 'sc', at: '2026-10-29T11:00:00Z', note: 'Here is the weekly report.' };
    const { target, close } = await open(makeDetail({ waiting_on_account_id: null, ask: null, agent, standing }));
    try {
      const banner = target.querySelector('[data-banner="review"]')!;
      expect(banner.querySelector('.wq-heading')?.textContent?.trim()).toBe(standing.line);
      expect(target.querySelector('.tw-head')?.textContent).not.toContain('weekly report');
      expect(target.querySelector('.agent-badge')).toBeNull();
      expect(target.querySelector('[data-standing]')).toBeNull();
      banner.querySelector<HTMLButtonElement>('[data-mark-reviewed]')!.click();
      expect(postMock).toHaveBeenCalledWith('/items/I1/report', { state: 'idle', comment: false });
    } finally {
      await close();
    }
  });

  it('shows another viewer the review badge with what to check in place of the note', async () => {
    const standing = { line: 'Check the totals in the March column', by_id: 'sc', at: '2026-10-29T11:00:00Z', move_id: 'sc' };
    const agent = { state: 'review' as const, by: 'sc', at: '2026-10-29T11:00:00Z', note: 'Here is the weekly report.' };
    const { target, close } = await open(makeDetail({ waiting_on_account_id: null, ask: null, agent, standing }));
    try {
      expect(target.querySelector('[data-banner="review"]')).toBeNull();
      const badge = target.querySelector('.agent-badge')!;
      expect(badge.querySelector('.note')?.textContent?.trim()).toBe(standing.line);
      expect(badge.getAttribute('title')).not.toContain('weekly report');
      expect(target.querySelector('[data-standing-move]')?.textContent?.trim()).toBe('My Agent');
      expect(target.querySelectorAll('[data-standing-line]')).toHaveLength(0);
    } finally {
      await close();
    }
  });
});
