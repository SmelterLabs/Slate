// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

const { answer, askItem, boardState } = vi.hoisted(() => {
  const askItem = {
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
    ask: { comment_id: 'C1', question: 'Start the import now?', options: ['Yes', 'No'], by_id: 'sc', at: '2026-10-29T10:00:00Z' },
  };
  return {
    answer: vi.fn(async () => true),
    askItem,
    boardState: {
      needsMe: [{ item: askItem, need: 'waiting' as const }],
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
    boardState.needsMe = [
      {
        item: {
          ...askItem,
          ask: { ...askItem.ask, options: [longOpt, longOpt, 'Third long option with forty characters!', 'Fourth long option with forty characters'] },
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
