// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../../lib/fixtures';
import type { Item } from '../../lib/types';

const { getMock, updateMock, moveToStatusMock, staleItem } = vi.hoisted(() => {
  const staleItem: Item = {
    id: 'I_OLD',
    list_id: 'L1',
    parent_id: null,
    kind: 'task' as const,
    title: 'Old overdue task',
    body_md: '',
    status: { id: 'st_open', name: 'To do', kind: 'open' as const, color: null },
    priority: null,
    start_at: null,
    due_at: '2026-10-01T22:00:00Z',
    completed_at: null,
    waiting_on_account_id: null,
    acceptance_md: null,
    recurrence_id: null,
    occurrence_at: null,
    external_ref: null,
    position: 1,
    created_by: 'me',
    created_at: '2026-09-20T00:00:00Z',
    updated_at: '2026-09-20T00:00:00Z',
    archived_at: null,
    assignees: [{ id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' as const }],
    tags: [],
    subtask_count: 0,
    comment_count: 0,
  };
  return {
    getMock: vi.fn(async () => [staleItem]),
    updateMock: vi.fn(async () => true),
    moveToStatusMock: vi.fn(async () => true),
    staleItem,
  };
});

vi.mock('../../lib/api', () => ({ get: getMock }));
vi.mock('../../lib/store.svelte', () => ({
  board: {
    me: { account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' } },
    prefs: { sweep_weekday: 4 },
    rev: 1,
    // Thursday 2026-10-22, 15:00 Chicago -> coming Friday is 2026-10-23.
    now: new Date('2026-10-22T20:00:00Z'),
    items: { I_OLD: staleItem },
    lists: {
      L1: {
        ...LIST,
        statuses: [
          ...LIST.statuses,
          { id: 'st_cancel', name: 'Cancelled', kind: 'cancelled' as const, color: null, position: 4 },
        ],
      },
    },
    open: vi.fn(),
    update: updateMock,
    moveToStatus: moveToStatusMock,
    flash: vi.fn(),
  },
}));

import DecidePanel from './DecidePanel.svelte';

beforeEach(() => {
  vi.clearAllMocks();
  getMock.mockResolvedValue([item(staleItem)]);
});

describe('DecidePanel', () => {
  it('loads stale cards from /today/sweep and wires This week and Drop to ordinary edits', async () => {
    const target = document.createElement('div');
    const app = mount(DecidePanel, { target });

    await vi.waitFor(() => {
      expect(target.querySelector('[data-decide-item="I_OLD"]')).not.toBeNull();
    });
    expect(getMock).toHaveBeenCalledWith('/today/sweep');

    target.querySelector<HTMLButtonElement>('[data-sweep-week]')!.click();
    expect(updateMock).toHaveBeenCalledWith('I_OLD', { due_at: '2026-10-23' });

    target.querySelector<HTMLButtonElement>('[data-sweep-drop]')!.click();
    expect(moveToStatusMock).toHaveBeenCalledWith(
      'I_OLD',
      expect.objectContaining({ id: 'st_cancel', kind: 'cancelled' }),
    );

    await unmount(app);
  });

  it('New date sets due_at through the same ordinary edit', async () => {
    const target = document.createElement('div');
    const app = mount(DecidePanel, { target });
    await vi.waitFor(() => {
      expect(target.querySelector('[data-decide-item="I_OLD"]')).not.toBeNull();
    });
    target.querySelector<HTMLButtonElement>('[aria-label^="New date:"]')!.click();
    flushSync();
    // The picker panel floats under <body>, not inside the panel.
    const date = document.querySelector<HTMLInputElement>('[aria-label="New date date"]');
    expect(date).not.toBeNull();
    date!.value = '2026-11-05';
    date!.dispatchEvent(new Event('change', { bubbles: true }));
    expect(updateMock).toHaveBeenCalledWith('I_OLD', { due_at: expect.stringContaining('2026-11-05') });
    await unmount(app);
  });

  it('Drop says why when the list has no cancelled or done status', async () => {
    const { board } = await import('../../lib/store.svelte');
    const mocked = board as unknown as { lists: Record<string, unknown>; flash: ReturnType<typeof vi.fn> };
    const was = mocked.lists;
    mocked.lists = {
      L1: { ...LIST, statuses: LIST.statuses.filter((s) => s.kind === 'open') },
    };
    const target = document.createElement('div');
    const app = mount(DecidePanel, { target });
    await vi.waitFor(() => {
      expect(target.querySelector('[data-decide-item="I_OLD"]')).not.toBeNull();
    });
    target.querySelector<HTMLButtonElement>('[data-sweep-drop]')!.click();
    expect(moveToStatusMock).not.toHaveBeenCalled();
    expect(mocked.flash).toHaveBeenCalled();
    mocked.lists = was;
    await unmount(app);
  });
});
