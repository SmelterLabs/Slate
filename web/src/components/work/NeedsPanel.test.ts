// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import type { Attention } from '../../lib/attention';
import type { Item } from '../../lib/types';

const { answer, askItem, boardState } = vi.hoisted(() => {
  const askItem: Item = {
    id: 'I1',
    list_id: 'L1',
    parent_id: null,
    kind: 'task' as const,
    title: 'Fix gutter',
    body_md: '',
    status: null,
    priority: null,
    start_at: null,
    due_at: null,
    completed_at: null,
    waiting_on_account_id: 'me',
    acceptance_md: null,
    recurrence_id: null,
    occurrence_at: null,
    external_ref: null,
    position: 1,
    created_by: 'sc',
    created_at: '2026-10-20T00:00:00Z',
    updated_at: '2026-10-20T00:00:00Z',
    archived_at: null,
    assignees: [],
    tags: [],
    subtask_count: 0,
    comment_count: 0,
    agent: { state: 'blocked' as const, by: 'sc', at: '2026-10-29T10:00:00Z', note: 'Long explanation' },
    ask: { comment_id: 'C1', question: 'Start the import now?', options: ['Yes', 'No'],
      questions: [{ question: 'Start the import now?', options: ['Yes', 'No'] }], by_id: 'sc', at: '2026-10-29T10:00:00Z' },
  };
  return {
    answer: vi.fn(async () => true),
    askItem,
    boardState: {
      needsMe: [{ item: askItem, need: 'waiting' }] as Attention[],
      me: { account: { id: 'me', display_name: 'Alex' } },
      now: new Date('2026-10-29T12:00:00Z'),
      lists: { L1: { name: 'Home' } },
      accountName: (id: string) => (id === 'sc' ? 'My Agent' : undefined),
      open: vi.fn(),
      canRun: () => true,
      runLabel: () => 'Run now',
      runAction: vi.fn(),
    },
  };
});

vi.mock('../../lib/store.svelte', () => ({ board: boardState }));
vi.mock('../../lib/task.svelte', () => ({ task: { clearAgentFlag: vi.fn(), answer } }));

import NeedsPanel from './NeedsPanel.svelte';

describe('NeedsPanel ask row', () => {
  it('shows the question as headline, agent and title second, and tap answers call the answer endpoint', async () => {
    boardState.needsMe = [{ item: askItem, need: 'waiting' }];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    const row = target.querySelector('[data-item="I1"]');
    expect(row?.querySelector('[data-ask-question]')?.textContent).toContain('Start the import now?');
    expect(row?.querySelector('.nd-meta')?.textContent).toContain('My Agent');
    expect(row?.querySelector('.nd-meta')?.textContent).toContain('Fix gutter');
    const buttons = [...target.querySelectorAll<HTMLButtonElement>('[data-answer-option]')];
    expect(buttons.map((b) => b.textContent.trim())).toEqual(['Yes', 'No']);
    expect(buttons.every((b) => b.tagName === 'BUTTON')).toBe(true);
    buttons[0].click();
    expect(answer).toHaveBeenCalledWith('C1', 'Yes');
    await unmount(app);
  });

  it('renders duplicate option labels without throwing and puts options on their own wrapping row', async () => {
    const longOpt = 'Option with forty characters in the text';
    const options = [longOpt, longOpt, 'Third long option with forty characters!', 'Fourth long option with forty characters'];
    const ask = askItem.ask!;
    boardState.needsMe = [
      {
        item: {
          ...askItem,
          ask: { ...ask, options, questions: [{ question: ask.question, options }] },
        },
        need: 'waiting',
      },
    ];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    const row = target.querySelector('[data-item="I1"]');
    expect(row?.classList.contains('has-opts')).toBe(true);
    const buttons = [...target.querySelectorAll<HTMLButtonElement>('[data-answer-option]')];
    expect(buttons).toHaveLength(4);
    expect(buttons[0].textContent.trim()).toBe(longOpt);
    expect(buttons[1].textContent.trim()).toBe(longOpt);
    await unmount(app);
  });

  it('keeps someone else’s structured ask out of the viewer’s separate Ask row', async () => {
    boardState.needsMe = [{
      item: {
        ...askItem,
        waiting_on_account_id: 'other',
        open_asks: ['other', 'me'],
        agent: { ...askItem.agent!, note: askItem.ask!.question },
      },
      need: 'asked',
    }];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    try {
      const row = target.querySelector('[data-item="I1"]')!;
      expect(row.querySelector('[data-ask-question]')).toBeNull();
      expect(row.querySelector('[data-answer-option]')).toBeNull();
      expect(row.querySelector('.nd-title')?.textContent).toBe(askItem.title);
      expect(row.textContent).not.toContain(askItem.ask!.question);
      expect(row.querySelector('.nd-act')?.textContent?.trim()).toBe('Reply →');
    } finally {
      await unmount(app);
    }
  });

  it('shows a stalled row with a Run now action', async () => {
    boardState.needsMe = [
      {
        item: { ...askItem, id: 'I2', ask: null, agent: null, waiting_on_account_id: null, stalled: true },
        need: 'stalled',
      },
    ];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    const row = target.querySelector('[data-item="I2"]');
    expect(row?.getAttribute('data-need')).toBe('stalled');
    const runBtn = row?.querySelector<HTMLButtonElement>('[data-stalled-run]');
    expect(runBtn?.textContent?.trim()).toBe('Run now');
    runBtn?.click();
    expect(boardState.runAction).toHaveBeenCalled();
    await unmount(app);
  });
});

describe('NeedsPanel questions and reviews', () => {
  it('shows an ask with several questions by its headline, with Answer → instead of options', async () => {
    const several = {
      ...askItem.ask!,
      question: '2 questions: Start the import now?',
      options: [],
      questions: [
        { question: 'Start the import now?', options: ['Yes', 'No'] },
        { question: 'Which file first?', options: [] },
      ],
    };
    boardState.needsMe = [{ item: { ...askItem, ask: several }, need: 'waiting' }];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    const row = target.querySelector('[data-item="I1"]')!;
    expect(row.querySelector('[data-ask-question]')?.textContent).toBe('2 questions: Start the import now?');
    expect(row.querySelectorAll('[data-answer-option]')).toHaveLength(0);
    expect(row.classList.contains('has-opts')).toBe(false);
    expect(row.querySelector('.nd-act')?.textContent?.trim()).toBe('Answer →');
    row.querySelector<HTMLButtonElement>('.nd-main')!.click();
    expect(boardState.open).toHaveBeenCalledWith('I1');
    await unmount(app);
  });

  it('gives a review row what to check, not the first line of the reply', async () => {
    const review: Item = {
      ...askItem,
      id: 'I3',
      ask: null,
      waiting_on_account_id: null,
      agent: { state: 'review', by: 'sc', at: '2026-10-29T10:00:00Z', note: 'Here is the weekly report.' },
      standing: { line: 'Check the totals in the March column', by_id: 'sc', at: '2026-10-29T10:00:00Z', move_id: 'me' },
    };
    boardState.needsMe = [{ item: review, need: 'review' }];
    const target = document.createElement('div');
    const app = mount(NeedsPanel, { target });
    const meta = target.querySelector('[data-item="I3"] .nd-meta')!;
    expect(meta.textContent).toContain('Check the totals in the March column');
    expect(meta.textContent).not.toContain('weekly report');
    await unmount(app);
  });
});
