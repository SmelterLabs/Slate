// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import { item } from '../../lib/fixtures';
import type { Comment } from '../../lib/types';
import ActivityPanel from './ActivityPanel.svelte';

vi.mock('../../lib/store.svelte', () => {
  const accounts = {
    'my-agent': { id: 'my-agent', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' },
    alex: { id: 'alex', handle: 'alex', display_name: 'Alex', kind: 'person' },
  };
  return {
    board: {
      accounts,
      accountName: (id: keyof typeof accounts) => accounts[id]?.display_name,
      now: new Date('2026-10-20T12:00:00Z'),
      pickable: Object.values(accounts),
      projectAgents: () => [],
      onSignOut: vi.fn(),
    },
  };
});
vi.mock('../../lib/task.svelte', () => ({ task: { me: 'alex', filter: 'all', loadActivity: vi.fn() } }));

const created = '2026-10-20T11:00:00Z';
const comments: Comment[] = [
  { id: 'resolved', item_id: 'I1', author_id: 'alex', body_md: 'Check the stock.', created_at: '2026-10-20T10:00:00Z', edited_at: null, assignee_id: 'my-agent', resolved_at: created },
  { id: 'open', item_id: 'I1', author_id: 'alex', body_md: 'Check the price too.', created_at: '2026-10-20T10:01:00Z', edited_at: null, assignee_id: 'my-agent' },
  { id: 'report', item_id: 'I1', author_id: 'my-agent', body_md: '**Twelve** in stock.\n\nThe next delivery is Friday.', created_at: created, edited_at: null },
  { id: 'answer', item_id: 'I1', author_id: 'my-agent', body_md: 'There is room for six more.', created_at: '2026-10-20T12:00:00Z', edited_at: null },
];

it('shows earlier comments as cards and toggles their previews in place', async () => {
  const target = document.createElement('div');
  const app = mount(ActivityPanel, {
    target,
    props: { item: { ...item(), subtasks: [], comments, events: [] }, onattach: vi.fn() },
  });

  try {
    const report = target.querySelector<HTMLElement>('article[data-comment="report"][data-chat-collapsed]')!;
    expect(report).not.toBeNull();
    expect(report.querySelector('.avatar.agent')).not.toBeNull();
    expect(report.querySelector('.who strong')?.textContent).toBe('My Agent');
    expect(report.querySelector('time')?.dateTime).toBe(created);
    expect(report.querySelector('time')?.textContent).toBeTruthy();
    expect(report.querySelector('.md strong')?.textContent).toBe('Twelve');
    const toggle = report.querySelector<HTMLButtonElement>('button[aria-expanded="false"]')!;
    expect(toggle.textContent?.trim()).toBe('Show all');
    expect(toggle.tabIndex).toBe(0);

    flushSync(() => toggle.click());
    expect(report.hasAttribute('data-chat-collapsed')).toBe(false);
    expect(report.querySelector('.md')?.textContent).toContain('The next delivery is Friday.');
    expect(toggle.textContent?.trim()).toBe('Show less');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    flushSync(() => toggle.click());
    expect(report.hasAttribute('data-chat-collapsed')).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    const person = target.querySelector('article[data-comment="resolved"][data-chat-collapsed]');
    expect(person?.querySelector('.who strong')?.textContent).toBe('Alex');
    expect(person?.classList.contains('from-agent')).toBe(false);
    expect(target.querySelector('article[data-comment="open"] [data-ask="open"]')).not.toBeNull();
    expect(target.querySelector('article[data-comment="open"][data-chat-collapsed]')).toBeNull();
  } finally {
    await unmount(app);
  }
});
