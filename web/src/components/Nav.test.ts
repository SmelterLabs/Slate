// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../lib/fixtures';

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return { ...actual, api: vi.fn(async () => undefined), get: vi.fn(async () => []), post: vi.fn(async () => ({})), patch: vi.fn(async () => ({})), put: vi.fn(async () => ({})) };
});

import { board } from '../lib/store.svelte';
import Nav from './Nav.svelte';

beforeEach(() => {
  board.me = { account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' }, token_id: 'T1', scopes: ['admin'], prefs: {} };
  board.accounts = { me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' } };
  board.sections = [{ id: 'S1', name: 'Work', slug: 'work', audience: 'work', position: 0, archived_at: null, created_at: '2026-10-20T00:00:00Z' }];
  board.lists = {
    L1: { ...LIST, id: 'L1', name: 'Reading', reference: true },
    L2: { ...LIST, id: 'L2', name: 'Work list', reference: false },
  };
  board.items = {
    I1: item({ id: 'I1', list_id: 'L1', title: 'A link', due_at: '2026-10-01T00:00:00Z' }),
    I2: item({ id: 'I2', list_id: 'L2', title: 'A task' }),
  };
  (board as unknown as { syncAll(items: unknown[]): void }).syncAll(Object.values(board.items));
  board.now = new Date('2026-10-29T12:00:00Z');
});

describe('Nav reference list', () => {
  it('marks it Reference and gives it no open or overdue badge', async () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(Nav, { target });
    const reading = target.querySelector('[data-list="Reading"]');
    expect(reading?.querySelector('[data-reference]')?.textContent).toContain('Reference');
    expect(reading?.querySelector('.count')).toBeNull();
    const work = target.querySelector('[data-list="Work list"]');
    expect(work?.querySelector('[data-reference]')).toBeNull();
    expect(work?.querySelector('.count')?.textContent.trim()).toBe('1');
    await unmount(app);
    target.remove();
  });
});
