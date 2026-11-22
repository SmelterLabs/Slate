// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST } from '../lib/fixtures';

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return { ...actual, api: vi.fn(async () => undefined), get: vi.fn(async () => []), post: vi.fn(async () => ({})), patch: vi.fn(async () => ({})), put: vi.fn(async () => ({})) };
});

import { board } from '../lib/store.svelte';
import QuickAddBar from './QuickAddBar.svelte';

beforeEach(() => {
  board.me = { account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' }, token_id: 'T1', scopes: ['admin'], prefs: {} };
  board.accounts = {};
  board.sections = [{ id: 'S1', name: 'Work', slug: 'work', audience: 'work', position: 0, archived_at: null, created_at: '2026-10-20T00:00:00Z' }];
  board.lists = { L1: { ...LIST, note_md: '# Reading links worth keeping\nFile the whole article.' } };
});

describe('QuickAddBar list note help', () => {
  it("shows the list note's first line as the placeholder in that list", async () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(QuickAddBar, { target, props: { scope: { kind: 'list', id: 'L1' }, viewKey: 'list:L1' } });
    expect(target.querySelector<HTMLInputElement>('#quick-add')?.placeholder).toBe('Reading links worth keeping');
    await unmount(app);
    target.remove();
  });
});
