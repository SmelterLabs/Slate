// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST } from '../lib/fixtures';
import type { List } from '../lib/types';

const { patchMock } = vi.hoisted(() => ({ patchMock: vi.fn(async (_path: string, _body: unknown) => ({})) }));

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return { ...actual, api: vi.fn(async () => undefined), get: vi.fn(async () => []), post: vi.fn(async () => ({})), patch: patchMock, put: vi.fn(async () => ({})) };
});

import { board } from '../lib/store.svelte';
import ListSettings from './ListSettings.svelte';

const LIST_WITH: List = {
  ...LIST,
  note_md: 'Reading links worth keeping',
  default_assignee_id: 'me',
  default_tags: ['reading'],
  reference: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  patchMock.mockImplementation(async (_path: string, body: unknown) => ({ ...LIST_WITH, ...(body as object) }));
  board.me = { account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' }, token_id: 'T1', scopes: ['admin'], prefs: {} };
  board.accounts = {
    me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' },
    sc: { id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' },
  };
  board.lists = { L1: LIST_WITH };
  board.items = {};
  board.tags = [{ id: 't1', name: 'reading', color: null }];
  board.listSettingsId = 'L1';
});

describe('ListSettings', () => {
  it('shows the four fields and saves them through the list patch in one round trip', async () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(ListSettings, { target, props: { list: LIST_WITH } });

    const note = target.querySelector<HTMLTextAreaElement>('[aria-label="What belongs here"]');
    expect(note?.value).toBe('Reading links worth keeping');
    expect(target.textContent).toContain('Default owner');
    expect(target.textContent).toContain('Alex');
    expect(target.textContent).toContain('reading');
    const refBox = target.querySelector<HTMLInputElement>('[aria-label="Reference list: not counted as work"]');
    expect(refBox?.checked).toBe(false);

    note!.value = 'Links and notes, filed whole';
    note!.dispatchEvent(new Event('input', { bubbles: true }));
    refBox!.click();
    flushSync();

    const save = [...target.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent.trim() === 'Save');
    expect(save?.disabled).toBe(false);
    save!.click();
    await Promise.resolve();
    await Promise.resolve();

    expect(patchMock).toHaveBeenCalledWith('/lists/L1', {
      note_md: 'Links and notes, filed whole',
      default_assignee_id: 'me',
      default_tags: ['reading'],
      reference: true,
    });
    await unmount(app);
    target.remove();
  });
});
