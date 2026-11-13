// A one-file TypeScript client for Slate. Copy it next to your agent; it needs only `fetch`
// (Node 18+, Deno, Bun, browsers).
//
//   import { Slate, SlateError } from './slate';
//
//   const slate = new Slate('https://slate.example.com', token, { sessionRef: 'nightly-42' });
//   let task = await slate.next({ wait: 30 }); // your best free task (item null: none came)
//   while (task?.item) {
//     // ... do the work; slate.heartbeat(task.item.id) if it takes long
//     task = (await slate.finish(task.item.id, 'done', 'Shipped: https://...', { next: { wait: 30 } })).next;
//   }
//
// Or let it run the loop: `await slate.workForever(handle)`. React instead of polling with
// `slate.agentStream()`: news that concerns you and hints that work is free.
//
// Like the Python client: ETag cache (a `304` returns the remembered body), the check-in
// cursor kept for you (`since`), `429` retried after `Retry-After`, errors thrown as
// `SlateError` with `status` and `detail`. Docs: docs/agents/quickstart.md in the Slate repo.

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Item = { id: string; title: string; [key: string]: unknown };
export type SlateEvent = {
  id: string;
  type: string;
  item_id: string | null;
  actor_id: string;
  source: 'ui' | 'api' | 'mcp' | 'system';
  field: string | null;
  before: unknown;
  after: unknown;
  session_ref: string | null;
  at: string;
};
export type News = SlateEvent & { why: string; item_title: string | null };
export type Checkin = { queue: Item[]; news: News[]; next_since: string | null };
export type ReportState = 'working' | 'blocked' | 'review' | 'done' | 'idle';
export type ReportOptions = {
  run_id?: string;
  progress?: number;
  comment?: boolean;
  complete?: boolean;
  waiting_on?: string;
  assign_me?: boolean;
  claim?: boolean;
};
export type Lease = { run_id: string | null; item_id: string; account_id: string; claimed_at: string; until: string };
export type NextOptions = {
  kinds?: ('task' | 'chat')[];
  run_id?: string;
  item_id?: string;
  channel?: string;
  thread_id?: string;
  wait?: number;
  lease_seconds?: number;
  pool?: boolean;
  list_id?: string;
  section_id?: string;
  tag?: string;
  resume?: boolean;
  note?: string;
  context?: boolean;
};
export type Task = {
  chat?: {channel: string; thread_id: string | null; message_id: string; lease: Omit<Lease, 'item_id'>} | null;
  item: Item | null;
  lease: Lease | null;
  resumed: boolean;
  why: 'assigned' | 'waiting_on_you' | 'pool' | null;
  context: unknown[];
  remaining: number;
};
export type Outcome = 'done' | 'review' | 'later' | 'blocked' | 'release';
export type FinishOptions = {
  run_id?: string;
  status?: string;
  hand_to?: string;
  waiting_on?: string;
  progress?: number;
  comment?: boolean;
  next?: NextOptions;
};
export type StreamMessage = { event: 'news' | 'work'; id: string; data: Record<string, unknown> };
export type BatchOp = { tool: string; action?: string; params?: Record<string, unknown>; ref?: string };
type Params = Record<string, string | number | boolean | string[] | null | undefined>;

export class SlateError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
    readonly failedOp?: number,
  ) {
    super(`${status}: ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
    this.name = 'SlateError';
  }
}

export type SlateOptions = {
  /** Recorded on every event your calls write (X-Slate-Session-Ref). */
  sessionRef?: string;
  /** Resume the check-in cursor from a previous run. */
  since?: string;
  maxRetries?: number;
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
};

export class Slate {
  readonly api: string;
  since: string | null;
  maxRetries: number;
  lastStatus = 0;
  #headers: Record<string, string>;
  #fetch: typeof fetch;
  #sleep: (ms: number) => Promise<void>;
  #etags = new Map<string, { etag: string; body: unknown }>();

  /** `baseUrl` is the board's address (`https://host:port`), without `/api/v1`. */
  constructor(baseUrl: string, token: string, opts: SlateOptions = {}) {
    this.api = baseUrl.replace(/\/+$/, '') + '/api/v1';
    this.#headers = { Authorization: `Bearer ${token}` };
    if (opts.sessionRef) this.#headers['X-Slate-Session-Ref'] = opts.sessionRef;
    this.since = opts.since ?? null;
    this.maxRetries = opts.maxRetries ?? 3;
    this.#fetch = opts.fetch ?? globalThis.fetch.bind(globalThis);
    this.#sleep = opts.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  }

  // --- transport ------------------------------------------------------------------------

  /** One call. Resolves to the decoded JSON (null for 204). GETs use the ETag cache. */
  async request<T = any>(method: string, path: string, opts: { params?: Params; body?: unknown } = {}): Promise<T> {
    const url = this.api + path + query(opts.params);
    const headers: Record<string, string> = { ...this.#headers };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    const cached = method === 'GET' ? this.#etags.get(url) : undefined;
    if (cached) headers['If-None-Match'] = cached.etag;
    let res!: Response;
    for (let attempt = 0; ; attempt++) {
      res = await this.#fetch(url, {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
      this.lastStatus = res.status;
      if (res.status !== 429) break;
      if (attempt >= this.maxRetries) throw new SlateError(429, await detail(res));
      await this.#sleep(Number(res.headers.get('retry-after') ?? '1') * 1000);
    }
    if (res.status === 304 && cached) return cached.body as T;
    if (res.status >= 400) {
      const body = await res.json().catch(() => null);
      throw new SlateError(res.status, body?.detail ?? res.statusText, body?.failed_op);
    }
    if (res.status === 204) return null as T;
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    const etag = res.headers.get('etag');
    if (method === 'GET' && etag) this.#etags.set(url, { etag, body: data });
    return data as T;
  }

  get<T = any>(path: string, params?: Params) {
    return this.request<T>('GET', path, { params });
  }
  post<T = any>(path: string, body?: unknown) {
    return this.request<T>('POST', path, { body: body ?? {} });
  }
  patch<T = any>(path: string, body: unknown) {
    return this.request<T>('PATCH', path, { body });
  }
  delete(path: string) {
    return this.request<null>('DELETE', path);
  }

  // --- who and what ---------------------------------------------------------------------

  me() {
    return this.get<{ account: { id: string; handle: string; kind: string }; scopes: string[]; token_id: string }>('/me');
  }

  /** Your queue and all news since the last check-in; advances `since`. */
  async checkin(limit = 50): Promise<Checkin> {
    const out: Checkin = { queue: [], news: [], next_since: this.since };
    for (;;) {
      const page = await this.get<Checkin & { has_more: boolean }>('/checkin', { since: this.since, limit });
      out.queue = page.queue;
      out.news.push(...page.news);
      if (page.next_since) this.since = page.next_since;
      out.next_since = this.since;
      if (!page.has_more) return out;
    }
  }

  agents() {
    return this.get<unknown[]>('/agents');
  }

  // --- items ----------------------------------------------------------------------------

  /** `GET /items` with filters: list_id, assignee, tag, status, q, fields: [...] */
  items(filters: Params = {}) {
    return this.get<Item[]>('/items', filters);
  }
  item(id: string) {
    return this.get<Item>(`/items/${encodeURIComponent(id)}`);
  }
  /** File ONE item (never children; pass parent_id for one subtask). */
  create(listId: string, title: string, fields: Record<string, unknown> = {}) {
    return this.post<Item>('/items', { list_id: listId, title, ...fields });
  }
  /** Change only these fields; null clears one. */
  update(id: string, changes: Record<string, unknown>) {
    return this.patch<Item>(`/items/${encodeURIComponent(id)}`, changes);
  }
  comment(id: string, bodyMd: string) {
    return this.post(`/items/${encodeURIComponent(id)}/comments`, { body_md: bodyMd });
  }

  // --- reports: the agent loop ----------------------------------------------------------

  report(id: string, state: ReportState, note?: string, opts: ReportOptions = {}) {
    return this.post<Item>(`/items/${encodeURIComponent(id)}/report`, {
      state,
      ...(note === undefined ? {} : { note }),
      ...opts,
    });
  }
  /** Take the item (and its assignment) unless another account holds it: SlateError 409. */
  claim(id: string, note?: string, opts: ReportOptions = {}) {
    return this.report(id, 'working', note, { ...opts, claim: true });
  }
  progress(id: string, note: string, percent?: number) {
    return this.report(id, 'working', note, percent === undefined ? {} : { progress: percent });
  }
  block(id: string, note: string, waitingOn?: string) {
    return this.report(id, 'blocked', note, waitingOn ? { waiting_on: waitingOn } : {});
  }
  review(id: string, note: string) {
    return this.report(id, 'review', note);
  }
  /** Clear your badge and move the item to its list's done status. */
  done(id: string, note?: string, opts: ReportOptions = {}) {
    return this.report(id, 'done', note, opts);
  }

  // --- the work loop: next, heartbeat, finish -------------------------------------------

  /** Take your best free task (`item` null when none is free, after waiting `wait` s). */
  next(opts: NextOptions = {}) {
    return this.post<Task>('/work/next', opts);
  }
  /** Keep holding the task; SlateError 409 means you lost it: stop. */
  heartbeat(id: string, opts: { run_id?: string; lease_seconds?: number; note?: string; progress?: number } = {}) {
    return this.post<Lease>(`/work/${encodeURIComponent(id)}/heartbeat`, opts);
  }
  /** Report back in one call (outcome, note as a comment, hand_to...); `next` also takes the next task. */
  finish(id: string, outcome: Outcome = 'done', note?: string, opts: FinishOptions = {}) {
    return this.post<{ item: Item; next: Task | null }>(`/work/${encodeURIComponent(id)}/finish`, {
      outcome,
      ...(note === undefined ? {} : { note }),
      ...opts,
    });
  }
  /** The tasks you hold now, with their leases. */
  held() {
    return this.get<Lease[]>('/work');
  }

  // --- batch and events -----------------------------------------------------------------

  /** Up to 100 MCP-shaped ops in one round trip; `$ref.id` reuses an earlier result.
   * Tasks in the results show the end of the batch (`views: 'each'`: as each op left them). */
  batch(ops: BatchOp[], atomic = true, views: 'final' | 'each' = 'final') {
    return this.post<{ atomic: boolean; results: unknown[] }>('/batch', { ops, atomic, views });
  }
  events(since?: string, filters: Params = {}) {
    return this.get<{ events: SlateEvent[]; next_since: string | null; has_more: boolean }>('/events', {
      since,
      ...filters,
    });
  }

  /** Live events from the SSE stream, replaying everything after `since` first. */
  async *stream(since?: string, filters: Params = {}, signal?: AbortSignal): AsyncGenerator<SlateEvent> {
    const res = await this.#fetch(this.api + '/events/stream' + query({ since, ...filters }), {
      headers: { ...this.#headers, Accept: 'text/event-stream' },
      signal,
    });
    if (!res.ok || !res.body) throw new SlateError(res.status, await detail(res));
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buf += value.replace(/\r\n/g, '\n');
      let end;
      while ((end = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, end);
        buf = buf.slice(end + 2);
        const data = block
          .split('\n')
          .filter((l) => l.startsWith('data:'))
          .map((l) => l.slice(5).trim());
        if (data.length) yield JSON.parse(data.join('\n')) as SlateEvent;
      }
    }
  }

  /**
   * Your check-in, live (`GET /checkin/stream`): `news` concerns you (with `why`); `work` means
   * a task you could take with `next()` just became free. Replays what you missed after `since`;
   * reconnect with the last message's `id`.
   */
  async *agentStream(since?: string, opts: { pool?: boolean; signal?: AbortSignal } = {}): AsyncGenerator<StreamMessage> {
    const res = await this.#fetch(this.api + '/checkin/stream' + query({ since, pool: opts.pool }), {
      headers: { ...this.#headers, Accept: 'text/event-stream' },
      signal: opts.signal,
    });
    if (!res.ok || !res.body) throw new SlateError(res.status, await detail(res));
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buf += value.replace(/\r\n/g, '\n');
      let end;
      while ((end = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, end);
        buf = buf.slice(end + 2);
        const fields: Record<string, string> = {};
        for (const line of block.split('\n')) {
          if (!line || line.startsWith(':')) continue;
          const at = line.indexOf(':');
          const key = at < 0 ? line : line.slice(0, at);
          fields[key] = (fields[key] ?? '') + (at < 0 ? '' : line.slice(at + 1).replace(/^ /, ''));
        }
        if (fields.data) {
          yield { event: fields.event as StreamMessage['event'], id: fields.id, data: JSON.parse(fields.data) };
        }
      }
    }
  }

  // --- the whole loop -------------------------------------------------------------------

  /**
   * The work loop: take a task, `await handle(task)`, report back, take the next one. When it
   * resolves (optionally to a result note) the task is finished done and the same call takes
   * the next; if it throws, the task is finished blocked with the error. A heartbeat keeps the
   * lease every `lease_seconds / 3` while `handle` runs. With nothing to do, each `next` waits
   * up to `wait` seconds. `once` stops when no task is free. Stop the loop with `signal`.
   */
  async workForever(
    handle: (task: Task & { item: Item; signal: AbortSignal }) => Promise<string | void> | string | void,
    opts: NextOptions & { once?: boolean; signal?: AbortSignal } = {},
  ): Promise<void> {
    const { once, signal, ...rest } = opts;
    const nextOpts: NextOptions = { wait: 30, lease_seconds: 900, kinds: ['task'], ...rest };
    let task: Task | null = await this.next(nextOpts);
    while (!signal?.aborted) {
      if (!task?.item) {
        if (once) return;
        task = await this.next(nextOpts);
        continue;
      }
      const id: string = task.item.id;
      const run_id = task.lease?.run_id;
      if (!run_id) throw new SlateError(426, 'server lacks execution fencing proof');
      const control = new AbortController();
      let lost: unknown;
      let pending: Promise<unknown> | undefined;
      const abort = () => control.abort(signal?.reason);
      signal?.addEventListener('abort', abort, {once: true});
      const beat = setInterval(() => {
        if (pending || control.signal.aborted) return;
        pending = this.heartbeat(id, { run_id, lease_seconds: nextOpts.lease_seconds })
          .catch(e => { lost = e; control.abort(e); })
          .finally(() => { pending = undefined; });
      }, (nextOpts.lease_seconds! * 1000) / 3);
      let outcome: Outcome = 'done';
      let note: string | undefined;
      try {
        note = (await handle({...task, item: task.item, signal: control.signal})) || undefined;
      } catch (e) {
        outcome = 'blocked';
        note = `Failed: ${e instanceof Error ? e.message : String(e)}`.slice(0, 4000);
      } finally {
        clearInterval(beat);
        await pending;
        signal?.removeEventListener('abort', abort);
      }
      if (lost) throw lost;
      if (control.signal.aborted) throw control.signal.reason;
      await this.heartbeat(id, {run_id, lease_seconds: nextOpts.lease_seconds});
      await this.finish(id, outcome, note, {run_id});
      task = await this.next(nextOpts);
    }
  }

  /**
   * Check in every `intervalMs`; claim each queue item and call `handle(item)`. When it
   * resolves (optionally to a result comment) the item is reported done; if it throws, the
   * item is reported blocked with the error and skipped for the rest of this run. `once`
   * does a single pass. Stop the loop with `signal`.
   */
  async runForever(
    handle: (item: Item) => Promise<string | void> | string | void,
    opts: { intervalMs?: number; once?: boolean; onNews?: (n: News) => void; signal?: AbortSignal } = {},
  ): Promise<void> {
    await this.workForever(async task => {
      if (opts.onNews) (await this.checkin()).news.forEach(opts.onNews);
      return handle({...task.item, signal: task.signal});
    }, {once: opts.once, signal: opts.signal,
        wait: opts.once ? 0 : Math.min(60, Math.max(0, Math.floor((opts.intervalMs ?? 30000) / 1000)))});
  }
}

function query(params?: Params): string {
  if (!params) return '';
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    q.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : '';
}

async function detail(res: Response): Promise<unknown> {
  const text = await res.text().catch(() => '');
  try {
    return JSON.parse(text).detail ?? text;
  } catch {
    return text || res.statusText;
  }
}
