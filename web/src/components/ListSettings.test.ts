// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { settle } from '../lib/board/testkit';
import { LIST } from '../lib/fixtures';
import type { List } from '../lib/types';

const { patchMock, putMock } = vi.hoisted(() => ({
  patchMock: vi.fn<(path: string, body: unknown) => Promise<List>>(),
  putMock: vi.fn(async () => LIST),
}));

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return { ...actual, get: vi.fn(async () => []), patch: patchMock, put: putMock };
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
  patchMock.mockImplementation(async (_path, body) => ({ ...board.lists.L1, ...(body as object) }));
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

let target: HTMLDivElement;
let unmountApp: (() => Promise<void>) | undefined;

function open() {
  target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(ListSettings, { target, props: { get list() { return board.lists.L1; } } });
  unmountApp = () => unmount(app);
}

async function close() {
  await unmountApp?.();
  unmountApp = undefined;
  target.remove();
}

afterEach(close);

function button(text: string) {
  return [...target.querySelectorAll('button')].find((b) => b.textContent.trim() === text)!;
}

function type(selector: string, value: string) {
  const input = target.querySelector<HTMLInputElement>(selector)!;
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
  return input;
}

describe('ListSettings', () => {
  it('shows the four fields and saves them through the list patch in one round trip', async () => {
    open();

    const note = target.querySelector<HTMLTextAreaElement>('[aria-label="What belongs here"]');
    expect(note?.value).toBe('Reading links worth keeping');
    expect(target.textContent).toContain('Default owner');
    expect(target.textContent).toContain('Alex');
    expect(target.textContent).toContain('reading');
    const refBox = target.querySelector<HTMLInputElement>('[aria-label="Reference list: not counted as work"]');
    expect(refBox?.checked).toBe(false);

    type('[aria-label="What belongs here"]', 'Links and notes, filed whole');
    refBox!.click();
    flushSync();

    button('Save').click();
    await vi.waitFor(() => expect(board.listSettingsId).toBeNull());
    expect(patchMock).toHaveBeenCalledWith('/lists/L1', {
      note_md: 'Links and notes, filed whole',
      reference: true,
    });
  });

  it('keeps a live owner change when saving a local note', async () => {
    open();
    board.lists.L1 = { ...board.lists.L1, default_assignee_id: 'sc' };
    flushSync();
    expect(button('Save').disabled).toBe(true);
    type('[aria-label="What belongs here"]', 'File the whole article');
    button('Save').click();

    await vi.waitFor(() => expect(board.listSettingsId).toBeNull());
    expect(patchMock).toHaveBeenCalledWith('/lists/L1', { note_md: 'File the whole article' });
    expect(board.lists.L1.default_assignee_id).toBe('sc');
  });

  it('edits and clears the default owner and tags using their pickers', async () => {
    open();
    button('+ assign').click();
    flushSync();
    const agent = [...target.querySelectorAll('label')].find((label) => label.textContent.includes('@my-agent'));
    agent!.querySelector('input')!.click();
    target.querySelector<HTMLButtonElement>('[aria-label="Remove tag reading"]')!.click();
    const tag = type('[aria-label="Add tag"]', 'draft');
    tag.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    flushSync();
    button('Save').click();
    await vi.waitFor(() => expect(board.listSettingsId).toBeNull());
    expect(patchMock).toHaveBeenLastCalledWith('/lists/L1', {
      default_assignee_id: 'sc',
      default_tags: ['draft'],
    });

    await close();
    board.listSettingsId = 'L1';
    open();
    target.querySelector<HTMLButtonElement>('[aria-label="Unassign my-agent"]')!.click();
    target.querySelector<HTMLButtonElement>('[aria-label="Remove tag draft"]')!.click();
    flushSync();
    button('Save').click();
    await vi.waitFor(() => expect(board.listSettingsId).toBeNull());
    expect(patchMock).toHaveBeenLastCalledWith('/lists/L1', {
      default_assignee_id: null,
      default_tags: [],
    });
  });

  it('keeps the draft open when a settings patch fails alongside a status edit', async () => {
    patchMock.mockRejectedValueOnce(new Error('Could not save list settings'));
    open();
    const note = type('[aria-label="What belongs here"]', 'File the whole article');
    type('[aria-label="Status name"]', 'Inbox');
    button('Save').click();

    await settle();
    flushSync();
    expect(button('Save').disabled).toBe(false);
    expect(patchMock).toHaveBeenCalledOnce();
    expect(putMock).not.toHaveBeenCalled();
    expect(board.listSettingsId).toBe('L1');
    expect(note.value).toBe('File the whole article');
  });

  it('leaves the reference control out of drawer settings', () => {
    board.lists.L1 = { ...board.lists.L1, kind: 'drawer', reference: true };
    open();
    expect(target.querySelector('[aria-label="Reference list: not counted as work"]')).toBeNull();
  });
});
