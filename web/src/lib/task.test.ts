// @vitest-environment happy-dom
// The task window's own writes (lib/task.svelte.ts): comments and Asks, reactions, checklists,
// attachments, the file viewer and watchers. Each write is one REST call, then the open task's
// detail is read again; a refused one shows Slate's reason. Only `fetch` and `EventSource` are
// faked (board/testkit.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { item } from './fixtures';
import type { Attachment, Comment } from './types';
import { calls, deferred, freshBoard, requests, routes, settle, startLive, tearDown, writes, type Board } from './board/testkit';

let board: Board;
let task: (typeof import('./task.svelte'))['task'];
let isMedia: (typeof import('./task.svelte'))['isMedia'];

const DETAIL = { ...item(), subtasks: [], comments: [], events: [], checklists: [], attachments: [], watchers: [] };
const detailReads = () => calls.filter((p) => p === '/items/I1').length;
const file = (id: string, content_type: string, filename = `${id}.bin`): Attachment =>
  ({ id, filename, content_type, size: 10 }) as Attachment;

beforeEach(async () => {
  board = await freshBoard();
  ({ task, isMedia } = await import('./task.svelte'));
  routes.set('/items/I1', () => DETAIL);
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }));
  await startLive(board);
  await board.open('I1');
});
afterEach(() => {
  task.reset();
  tearDown(board);
});

describe('writes', () => {
  it('a comment is one POST, then the open task is read again', async () => {
    routes.set('POST /items/I1/comments', () => ({ id: 'C1' }));
    const before = detailReads();
    expect(await task.comment('I1', 'Looks good')).toBe(true);
    expect(writes().at(-1)).toEqual({ method: 'POST', path: '/items/I1/comments', body: { body_md: 'Looks good' } });
    await settle();
    expect(detailReads()).toBe(before + 1);
  });

  it('an Ask names its account by handle', async () => {
    routes.set('POST /items/I1/comments', () => ({ id: 'C1' }));
    await task.comment('I1', 'Can you check?', 'my-agent');
    expect(writes().at(-1)?.body).toEqual({ body_md: 'Can you check?', assignee: 'my-agent' });
  });

  it("a refused write shows Slate's reason, returns false and reads nothing again", async () => {
    routes.set('POST /comments/C1/resolve', () => ({ status: 403, body: { detail: 'Only its assignee can close this' } }));
    const before = detailReads();
    expect(await task.resolveComment('C1')).toBe(false);
    expect(board.toast).toBe('Only its assignee can close this');
    await settle();
    expect(detailReads()).toBe(before);
  });

  it('clearing an agent flag is an idle report with no comment', async () => {
    routes.set('POST /items/I1/report', () => ({}));
    await task.clearAgentFlag('I1');
    expect(writes().at(-1)).toEqual({ method: 'POST', path: '/items/I1/report', body: { state: 'idle', comment: false } });
  });

  it('each checklist and watcher write goes to its own route', async () => {
    const from = writes().length; // opening the task marked it read
    await task.addChecklist('I1', 'Before leaving', ['Lock door']);
    await task.renameChecklist('K1', 'Leaving');
    await task.addEntry('K1', 'Bins out');
    await task.updateEntry('E1', { done: true });
    await task.removeEntry('E1');
    await task.deleteChecklist('K1');
    await task.watch('I1', true);
    await task.watch('I1', false);
    await task.setWatchers('I1', ['alex']);
    await task.removeAttachment('F1');
    const sent = writes().slice(from);
    expect(sent.map((w) => `${w.method} ${w.path}`)).toEqual([
      'POST /items/I1/checklists',
      'PATCH /checklists/K1',
      'POST /checklists/K1/items',
      'PATCH /checklist-items/E1',
      'DELETE /checklist-items/E1',
      'DELETE /checklists/K1',
      'POST /items/I1/watch',
      'POST /items/I1/unwatch',
      'PUT /items/I1/watchers',
      'DELETE /attachments/F1',
    ]);
    expect(sent[0].body).toEqual({ name: 'Before leaving', items: ['Lock door'] });
    expect(sent[8].body).toEqual({ watchers: ['alex'] });
  });

  it('a reaction shows at once; reacting again with the same emoji removes it', async () => {
    const c = { id: 'C1', reactions: [] } as unknown as Comment;
    const answer = deferred<unknown>();
    routes.set('POST /comments/C1/reactions', () => answer.promise);
    const done = task.react(c, '👍');
    expect(c.reactions).toEqual([{ emoji: '👍', account_ids: ['A_alex'] }]);
    answer.resolve({});
    await done;
    await task.react(c, '👍');
    expect(writes().at(-1)).toMatchObject({ method: 'DELETE', path: `/comments/C1/reactions/${encodeURIComponent('👍')}` });
    expect(c.reactions).toEqual([]);
  });
});

describe('activity', () => {
  it('a load asked for while one is running runs once more after it, not twice at once', async () => {
    const first = deferred<unknown>();
    let n = 0;
    routes.set('/items/I1/activity', () => (++n === 1 ? first.promise : [{ id: 'second' }]));
    const loading = task.loadActivity('I1');
    void task.loadActivity('I1');
    void task.loadActivity('I1');
    expect(n).toBe(1);
    first.resolve([{ id: 'first' }]);
    await loading;
    await settle();
    expect(n).toBe(2);
    expect(task.activity).toEqual([{ id: 'second' }]);
  });

  it('an answer for a task that is no longer open is dropped', async () => {
    const answer = deferred<unknown>();
    routes.set('/items/I1/activity', () => answer.promise);
    const loading = task.loadActivity('I1');
    board.close();
    answer.resolve([{ id: 'late' }]);
    await loading;
    expect(task.activity).toBeNull();
  });
});

describe('attachments and the viewer', () => {
  it('uploads each file with its type, names a pasted screenshot, and reads the task once', async () => {
    routes.set('POST /items/I1/attachments', () => ({ id: 'F1' }));
    const before = detailReads();
    const shot = new File(['x'], 'image.png', { type: 'image/png' });
    const doc = new File(['y'], 'notes.txt', { type: 'text/plain' });
    const done = task.upload('I1', [shot, doc]);
    expect(task.uploading).toBe(1);
    expect(await done).toHaveLength(2);
    expect(task.uploading).toBe(0);
    const ups = requests.filter((r) => r.path === '/items/I1/attachments');
    expect(ups).toHaveLength(2);
    await settle();
    expect(detailReads()).toBe(before + 1);
  });

  it('a refused upload names the file in the message and counts nothing', async () => {
    routes.set('POST /items/I1/attachments', () => ({ status: 413, body: { detail: 'Too large' } }));
    expect(await task.upload('I1', [new File(['x'], 'big.mov', { type: 'video/quicktime' })])).toEqual([]);
    expect(board.toast).toBe('Could not attach big.mov: Too large');
    expect(task.uploading).toBe(0);
  });

  it('images, videos, PDFs and drawable documents open in the viewer; other files do not', () => {
    expect(isMedia(file('a', 'image/png'))).toBe(true);
    expect(isMedia(file('b', 'video/mp4'))).toBe(true);
    expect(isMedia(file('c', 'application/pdf'))).toBe(true);
    expect(isMedia(file('d', 'text/csv', 'sheet.csv'))).toBe(true);
    expect(isMedia(file('e', 'application/zip', 'bundle.zip'))).toBe(false);
  });

  it('the viewer steps through the open task\'s media, wrapping, and closing frees the URL', async () => {
    const files = [file('A1', 'image/png'), file('A2', 'application/zip', 'x.zip'), file('A3', 'video/mp4')];
    board.detail = { ...DETAIL, attachments: files } as typeof board.detail;
    await task.open(files[0]);
    expect(task.viewing?.a.id).toBe('A1');
    task.step(1);
    await settle();
    expect(task.viewing?.a.id).toBe('A3'); // the zip is skipped
    task.step(1);
    await settle();
    expect(task.viewing?.a.id).toBe('A1'); // wraps
    task.closeViewer();
    expect(task.viewing).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it('a file opened from a chat message steps through that message\'s files and their paths', async () => {
    const files = [file('M1', 'image/png'), file('M2', 'image/jpeg')];
    const among = { files, path: (id: string) => `/chat/files/${id}` };
    await task.open(files[0], among);
    task.step(-1);
    await settle();
    expect(task.viewing?.a.id).toBe('M2');
    expect(calls).toContain('/chat/files/M2');
  });
});

it('a task link keeps the page and adds the task to the hash', () => {
  location.hash = '#/list/L1';
  expect(task.link('I1')).toBe(`${location.origin}${location.pathname}#/list/L1?task=I1`);
  location.hash = '';
  expect(task.link('I1')).toBe(`${location.origin}${location.pathname}#/everything?task=I1`);
});
