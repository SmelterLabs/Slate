// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Doc } from './docs';

const mocks = vi.hoisted(() => ({
  board: { token: 'test-only', me: { account: { id: 'Alice' } } as { account: { id: string } } | null,
    flash: vi.fn(), onEvent: vi.fn(() => () => {}), onSignOut: vi.fn<(fn: () => void) => void>() },
  get: vi.fn(), patch: vi.fn(), post: vi.fn(),
}));
vi.mock('./store.svelte', () => ({ board: mocks.board }));
vi.mock('./api', () => ({ ...mocks, api: vi.fn(), authHeaders: vi.fn(),
  ApiError: class extends Error { constructor(public status: number) { super(String(status)); } },
}));
const initial = (id = 'A', body = 'Original') => ({ id, title: 'Title', body_md: body, icon: null, revision: 1, missing_tasks: [] }) as unknown as Doc;
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
function deferred<T>() {
  let resolve!: (v: T) => void; let reject!: (e: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
let docs: typeof import('./docs.svelte').docs;
let ApiError: typeof import('./api').ApiError;
beforeEach(async () => {
  vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers(); localStorage.clear();
  mocks.board.me = { account: { id: 'Alice' } };
  mocks.get.mockImplementation(async (p: string) => p.endsWith('/activity') ? { comments: [], events: [] } : p === '/docs' ? [] : initial(p.split('/').at(-1)));
  mocks.patch.mockImplementation(async (_: string, body: object) => ({ ...initial(), ...body, revision: 2 }));
  ({ docs } = await import('./docs.svelte'));
  ({ ApiError } = await import('./api'));
});
afterEach(() => { docs.stop(); vi.useRealTimers(); vi.unstubAllGlobals(); });

function switchAccount() {
  // Real App's 401 -> signOut -> effect cleanup -> token sign-in lifecycle, no reload.
  mocks.board.me = null; docs.stop();
  mocks.board.me = { account: { id: 'Bob' } }; docs.start();
}

describe('document identity and asynchronous ownership', () => {
  it('returning to the index while a document is loading clears loading and ignores the old response', async () => {
    const late = deferred<Doc>(); mocks.get.mockReturnValue(late.promise);
    const opening = docs.open('A'); await settle();
    await docs.open(null); expect(docs.loading).toBe(false);
    late.resolve(initial()); await opening; expect(docs.current).toBeNull();
  });
  it('an upload finishing after signOut cannot return the departed document to the next identity', async () => {
    await docs.open('A');
    mocks.post.mockResolvedValue(initial('file'));
    const late = deferred<Response>(); vi.stubGlobal('fetch', vi.fn(() => late.promise));
    const creating = docs.createFile({}, new File(['private'], 'file.txt'), 'note'); await settle();
    switchAccount(); late.resolve(new Response(JSON.stringify({id: 'revision', revision_no: 1})));
    expect(await creating).toBeNull(); expect(mocks.post).toHaveBeenCalledTimes(1);
  });
  it('a repeated open of the current route does not invalidate its pending discussion load', async () => {
    const activity = deferred<any>();
    mocks.get.mockImplementation(async (p: string) => p.endsWith('/activity') ? activity.promise : initial());
    await docs.open('A'); await docs.open('A');
    activity.resolve({comments: [{id: 'thread'}], events: []}); await settle();
    expect(docs.comments).toEqual([{id: 'thread'}]);
  });
  it('pending create completion cannot add a previous account document to the index', async () => {
    await docs.open('A');
    const late = deferred<Doc>(); mocks.post.mockReturnValue(late.promise);
    const creating = docs.create({title: 'Private'});
    switchAccount(); late.resolve(initial('private'));
    expect(await creating).toBeNull(); expect(docs.list).toEqual([]);
  });
  it('reauthorizes the same route after 401; does not expose or re-key departing text', async () => {
    await docs.open('A'); docs.edit({ body_md: 'Alice private draft' });
    switchAccount();
    mocks.get.mockRejectedValue(new ApiError(403, 'Denied'));
    await docs.open('A');
    expect(docs.current).toBeNull(); expect(docs.draft).toBeNull();
    docs.edit({ body_md: 'Bob must not edit cached Alice content' });
    expect(localStorage.getItem('slate.doc-draft:Bob:A')).toBeNull();
    expect(JSON.parse(localStorage.getItem('slate.doc-draft:Alice:A')!).body_md).toBe('Alice private draft');
    expect(mocks.patch).not.toHaveBeenCalled();
  });
  it('invalidates synchronously before signOut clears identity (no body-bearing event)', async () => {
    await docs.open('A'); docs.edit({ body_md: 'Alice draft' });
    expect(mocks.board.onSignOut).toHaveBeenCalledOnce();
    mocks.board.onSignOut.mock.calls[0][0]();
    expect(docs.current).toBeNull(); expect(docs.list).toEqual([]);
    expect(localStorage.getItem('slate.doc-draft:Alice:A')).toContain('Alice draft');
  });
  it('ignores an obsolete failed open and its finally while the next open is pending', async () => {
    const a = deferred<Doc>(); const b = deferred<Doc>();
    mocks.get.mockImplementation((p: string) => p === '/docs/A' ? a.promise : b.promise);
    const first = docs.open('A'); await settle();
    const second = docs.open('B'); await settle();
    a.reject(new ApiError(404, 'Missing')); await first;
    expect(docs.loading).toBe(true); expect(docs.missing).toBe(false);
    b.resolve(initial('B')); await second;
    expect(docs.current?.id).toBe('B');
  });
  it('ignores an obsolete 404 after the next open succeeds', async () => {
    const a = deferred<Doc>();
    mocks.get.mockImplementation(async (p: string) => p === '/docs/A' ? a.promise : p.endsWith('/activity') ? {comments: [], events: []} : initial('B'));
    const first = docs.open('A'); await settle(); await docs.open('B');
    a.reject(new ApiError(404, 'Missing')); await first;
    expect(docs.current?.id).toBe('B'); expect(docs.missing).toBe(false);
  });
  it('late index, templates and activity cannot repopulate the next account', async () => {
    await docs.open('A');
    const late = deferred<any>(); mocks.get.mockReturnValue(late.promise);
    const jobs = [docs.load(), docs.loadTemplates(), docs.loadActivity('A')];
    mocks.get.mockImplementation(async (p: string) => p.endsWith('/activity') ? {comments: [], events: []} : p === '/docs' ? [] : initial('A', 'Bob text'));
    switchAccount(); await docs.open('A');
    late.resolve({ comments: [{ body_md: 'Alice secret' }], events: [] }); await Promise.all(jobs);
    expect(docs.comments).toEqual([]); expect(docs.list).toEqual([]); expect(docs.templates).toEqual([]);
  });
  it.each(['success', 'failure'] as const)('late save %s and a waiting flush cannot mutate or write as the next account', async result => {
    await docs.open('A'); docs.edit({body_md: 'Alice sent'});
    const late = deferred<Doc>(); mocks.patch.mockReturnValueOnce(late.promise);
    const saving = docs.flush(); const waiting = docs.flush();
    switchAccount(); await docs.open('A');
    docs.edit({body_md: 'Bob own draft'});
    if (result === 'success') late.resolve({...initial(), body_md: 'Alice sent', revision: 2});
    else late.reject(new ApiError(409, 'Conflict'));
    await Promise.all([saving, waiting]);
    expect(docs.current?.body_md).toBe('Original'); expect(docs.draft?.body_md).toBe('Bob own draft');
    expect(docs.phase).toBe('pending'); expect(mocks.patch).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('slate.doc-draft:Alice:A')).toContain('Alice sent');
  });
});

describe('undo and recovery', () => {
  it('removes abandoned recovery immediately when editing back to clean', async () => {
    await docs.open('A'); docs.edit({body_md: 'Abandoned'}); docs.edit({body_md: 'Original'});
    expect(localStorage.getItem('slate.doc-draft:Alice:A')).toBeNull();
    await docs.flush(); expect(mocks.patch).not.toHaveBeenCalled();
    await docs.open('B'); await docs.open('A');
    expect(docs.draft?.body_md).toBe('Original'); expect(docs.phase).toBe('idle');
  });
  it('keeps undo during an in-flight save and writes the undo against the acknowledged revision', async () => {
    await docs.open('A'); docs.edit({body_md: 'Sent edit'});
    const late = deferred<Doc>(); mocks.patch.mockReturnValueOnce(late.promise);
    const saving = docs.flush(); docs.edit({body_md: 'Original'});
    expect(JSON.parse(localStorage.getItem('slate.doc-draft:Alice:A')!).body_md).toBe('Original');
    late.resolve({...initial(), body_md: 'Sent edit', revision: 2}); await saving; await docs.flush();
    expect(mocks.patch.mock.calls[1][1]).toEqual({body_md: 'Original', base_revision: 2});
    expect(localStorage.getItem('slate.doc-draft:Alice:A')).toBeNull();
  });
});
