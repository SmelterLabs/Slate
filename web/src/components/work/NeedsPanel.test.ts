// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

const { answer, askItem } = vi.hoisted(() => ({
  answer: vi.fn(async () => true),
  askItem: {
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
  },
}));

vi.mock('../../lib/store.svelte', () => ({
  board: {
    needsMe: [{ item: askItem, need: 'waiting' }],
    me: { account: { id: 'me', display_name: 'Alex' } },
    now: new Date('2026-10-29T12:00:00Z'),
    lists: { L1: { name: 'Home' } },
    accountName: (id: string) => (id === 'sc' ? 'My Agent' : undefined),
    open: vi.fn(),
  },
}));
vi.mock('../../lib/task.svelte', () => ({ task: { clearAgentFlag: vi.fn(), answer } }));

import NeedsPanel from './NeedsPanel.svelte';

describe('NeedsPanel ask row', () => {
  it('shows the question as headline, agent and title second, and tap answers call the answer endpoint', async () => {
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
});
