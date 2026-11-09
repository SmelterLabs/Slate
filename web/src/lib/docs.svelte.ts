import { authHeaders } from './api';
// Docs on the board (#/docs[/<id>]): the page index (GET /docs, no bodies) for the tree, the
// palette and the favorites bar; one open page at a time (GET /docs/{id}); saves with the
// revision they were made against, so another writer's newer text is never overwritten (409).
// Live: doc.* events reload the index and, when the open page changed somewhere else, either
// take the new text (nothing unsaved here) or flag it so the editor can offer a choice.
// Pure helpers are in docs.ts.

import { api, ApiError, get, patch, post } from './api';
import type { Anchor, Doc, DocComment, DocDiff, DocEvent, DocFile, DocRevision, DocSummary, DocTemplate, DocVersion, Drawer, Place, RevisionCompare, SectionDocuments } from './docs';
import { board } from './store.svelte';
import type { Item, SlateEvent } from './types';

export interface Text {
  title: string;
  body_md: string;
  icon: string | null;
}

const SAVE_DELAY = 900;

function textOf(d: Doc): Text {
  return { title: d.title, body_md: d.body_md, icon: d.icon };
}

function changed(a: Text, b: Text | null): Partial<Text> {
  const out: Partial<Text> = {};
  if (!b || a.title !== b.title) out.title = a.title;
  if (!b || a.body_md !== b.body_md) out.body_md = a.body_md;
  if (!b || a.icon !== b.icon) out.icon = a.icon;
  return out;
}

class Docs {
  list = $state<DocSummary[]>([]);
  loaded = $state(false);
  templates = $state<DocTemplate[]>([]);
  /** The page on screen, with its body. */
  current = $state<Doc | null>(null);
  loading = $state(false);
  missing = $state(false);
  /** What the editor shows for the open page: the server's text plus unsaved edits. */
  draft = $state<Text | null>(null);
  /** Autosave: pending (waiting for a pause), saving, saved, error, or conflict (someone else
   * saved a newer revision while this one had unsaved edits). */
  phase = $state<'idle' | 'pending' | 'saving' | 'saved' | 'error' | 'conflict'>('idle');
  /** The newer page from elsewhere while there are unsaved edits here. */
  remote = $state<Doc | null>(null);
  /** The open doc's comments and the activity events the side panel folds in. */
  comments = $state<DocComment[]>([]);
  activity = $state<DocEvent[]>([]);
  /** A review thread being started from a selection or a pin (the panel shows the form). */
  pendingAnchor = $state<Anchor | null>(null);
  pendingRevision = $state<number | null>(null);
  saveFailed = $state(false);
  /** The thread to show and scroll to (from a pin, a `?comment=` link or a push). */
  focusComment = $state<string | null>(null);
  #activityTimer: ReturnType<typeof setTimeout> | undefined;
  #saved: Text | null = null;
  #saveTimer: ReturnType<typeof setTimeout> | undefined;
  #savedTimer: ReturnType<typeof setTimeout> | undefined;
  #saving: Promise<void> | null = null;
  #off: (() => void) | null = null;
  #listTimer: ReturnType<typeof setTimeout> | undefined;
  #docTimer: ReturnType<typeof setTimeout> | undefined;
  #files = new Map<string, Promise<string>>();
  #openGeneration = 0;
  #openId: string | null = null;
  #drafts = new Map<string, Text>();
  #account: string | null = null;
  #epoch = 0;

  constructor() {
    board.onSignOut(() => this.stop());
  }

  #identity() {
    const account = board.me?.account.id ?? null;
    if (account !== this.#account) {
      this.stop();
      this.#account = account;
    }
  }

  #draftKey(id: string) {
    return `slate.doc-draft:${this.#account}:${id}`;
  }

  #remember() {
    if (!this.#account || !this.current || !this.draft) return;
    // An undo while a PATCH is in flight is still unsaved: that PATCH may change the server.
    if (!this.dirty && !this.#saving) return this.#forget(this.current.id);
    const key = this.#draftKey(this.current.id);
    const text = { ...this.draft };
    this.#drafts.set(key, text);
    try { localStorage.setItem(key, JSON.stringify(text)); }
    catch { board.flash('Draft kept for this session only. Download it before reloading.'); }
  }

  #forget(id: string) {
    const key = this.#draftKey(id);
    this.#drafts.delete(key);
    try { localStorage.removeItem(key); } catch { /* storage unavailable */ }
  }

  #recover(d: Doc) {
    const key = this.#draftKey(d.id);
    let saved = this.#drafts.get(key);
    try { saved ??= JSON.parse(localStorage.getItem(key) ?? 'null'); } catch { /* damaged draft */ }
    if (!saved || typeof saved.title !== 'string' || typeof saved.body_md !== 'string'
      || (saved.icon !== null && typeof saved.icon !== 'string')) return;
    if (!Object.keys(changed(saved, textOf(d))).length) return this.#forget(d.id);
    this.draft = { ...saved };
    this.remote = d;
    this.phase = 'conflict'; // recovery always asks; never silently overwrites a newer server page
  }

  downloadDraft() {
    if (!this.draft) return;
    const url = URL.createObjectURL(new Blob([`# ${this.draft.title}\n\n${this.draft.body_md}`], { type: 'text/markdown' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `slate-draft-${this.current?.id ?? 'page'}.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  byId = $derived(Object.fromEntries(this.list.map((d) => [d.id, d])) as Record<string, DocSummary>);

  /** Load the index and follow the stream; called once the board is live. */
  start() {
    this.#identity();
    void this.load();
    this.#off ??= board.onEvent((ev) => this.#onEvent(ev));
  }

  stop() {
    this.#remember(); // use the departing owner, never board.me after signOut
    ++this.#epoch;
    ++this.#openGeneration;
    this.#openId = null;
    for (const timer of [this.#saveTimer, this.#savedTimer, this.#listTimer, this.#docTimer, this.#activityTimer]) clearTimeout(timer);
    this.#saving = null;
    this.#account = null;
    this.current = null;
    this.draft = null;
    this.#saved = null;
    this.remote = null;
    this.list = [];
    this.templates = [];
    this.comments = [];
    this.activity = [];
    this.pendingAnchor = null;
    this.pendingRevision = null;
    this.focusComment = null;
    this.loaded = false;
    this.loading = false;
    this.missing = false;
    this.saveFailed = false;
    this.phase = 'idle';
    for (const url of this.#files.values()) void url.then(URL.revokeObjectURL, () => {});
    this.#files.clear();
    this.#off?.();
    this.#off = null;
  }

  async load() {
    const epoch = this.#epoch;
    try {
      const list = await get<DocSummary[]>('/docs');
      if (epoch !== this.#epoch) return;
      this.list = list;
    } catch {
      /* the index is a convenience; the page still opens */
    }
    if (epoch === this.#epoch) this.loaded = true;
  }

  async loadTemplates() {
    const epoch = this.#epoch;
    if (!this.templates.length) {
      const templates = await get<DocTemplate[]>('/docs/templates');
      if (epoch === this.#epoch) this.templates = templates;
    }
  }

  async open(id: string | null) {
    this.#identity();
    if (!this.#account) return;
    if (id && this.current?.id === id && this.#openId === id) return;
    this.#openId = id;
    const generation = ++this.#openGeneration;
    if (!id) {
      await this.flush();
      if (generation !== this.#openGeneration) return;
      this.#remember();
      this.current = null;
      this.draft = null;
      this.missing = false;
      this.loading = false;
      this.remote = null;
      this.#saved = null;
      this.comments = [];
      this.activity = [];
      this.pendingAnchor = null;
      this.pendingRevision = null;
      this.phase = 'idle';
      return;
    }
    await this.flush();
    if (generation !== this.#openGeneration) return;
    this.#remember();
    this.loading = true;
    this.missing = false;
    this.remote = null;
    this.phase = 'idle';
    this.comments = [];
    this.activity = [];
    this.pendingAnchor = null;
    this.pendingRevision = null;
    this.current = null;
    this.draft = null;
    this.#saved = null;
    try {
      const d = await get<Doc>(`/docs/${encodeURIComponent(id)}`);
      if (generation !== this.#openGeneration) return;
      this.#show(d, true);
      this.saveFailed = false;
      this.#recover(d);
      void this.loadActivity(id);
    } catch (e) {
      if (generation !== this.#openGeneration) return;
      if (e instanceof ApiError && e.status === 404) {
        this.current = null;
        this.missing = true;
      } else board.flash((e as Error).message);
    } finally {
      if (generation === this.#openGeneration) this.loading = false;
    }
  }

  /** Take the server's page as it is now, dropping unsaved edits (after a conflict). */
  async reload() {
    const generation = this.#openGeneration;
    const id = this.current?.id;
    if (!id) return;
    clearTimeout(this.#saveTimer);
    const d = this.remote ?? (await get<Doc>(`/docs/${encodeURIComponent(id)}`));
    if (generation !== this.#openGeneration) return;
    this.#show(d, true);
    this.saveFailed = false;
    this.remote = null;
    this.#forget(id);
    this.phase = 'idle';
  }

  /** Keep the edits here and save them over the newer text (after a conflict). */
  async keepMine() {
    if (!this.remote || !this.current) return;
    // Keep mine is a guarded overwrite of the revision the user was shown, not a force write.
    this.current = this.remote;
    this.remote = null;
    this.phase = 'pending';
    await this.flush();
  }

  get dirty(): boolean {
    return !!this.draft && Object.keys(changed(this.draft, this.#saved)).length > 0;
  }

  /** Show `d` as the open page; the draft follows unless it holds unsaved edits. */
  #show(d: Doc, replace = false) {
    const keep = !replace && this.current?.id === d.id && this.dirty;
    this.current = d;
    if (!keep) {
      this.#saved = textOf(d);
      this.draft = textOf(d);
    }
  }

  /** The editor typed: remember it and save after a pause. */
  edit(changes: Partial<Text>) {
    this.#identity();
    if (!this.draft) return;
    this.draft = { ...this.draft, ...changes };
    this.#remember();
    if (this.phase === 'conflict') return;
    this.phase = 'pending';
    clearTimeout(this.#saveTimer);
    this.#saveTimer = setTimeout(() => void this.flush(), SAVE_DELAY);
  }

  /** Save now what is unsaved (on blur, Ctrl+S, leaving the page). One PATCH at a time. */
  async flush(): Promise<void> {
    const epoch = this.#epoch;
    clearTimeout(this.#saveTimer);
    while (this.#saving) {
      await this.#saving;
      if (epoch !== this.#epoch) return;
    }
    const doc = this.current;
    const draft = this.draft;
    if (!doc || !draft || this.phase === 'conflict') return;
    const diff = changed(draft, this.#saved);
    if (!Object.keys(diff).length) {
      this.#forget(doc.id);
      if (this.phase === 'pending') this.phase = 'idle';
      return;
    }
    const sent = { ...draft };
    this.phase = 'saving';
    this.#saving = (async () => {
      try {
        const d = await patch<Doc>(`/docs/${encodeURIComponent(doc.id)}`, {
          ...diff,
          base_revision: doc.revision,
        });
        if (epoch !== this.#epoch || this.current?.id !== d.id) return;
        this.current = d;
        this.saveFailed = false;
        this.#saved = sent;
        this.#touch(d);
        if (this.dirty) {
          this.#remember();
          this.phase = 'pending';
          this.#saveTimer = setTimeout(() => void this.flush(), SAVE_DELAY);
        } else {
          this.#forget(d.id);
          this.phase = 'saved';
          clearTimeout(this.#savedTimer);
          this.#savedTimer = setTimeout(() => this.phase === 'saved' && (this.phase = 'idle'), 2500);
        }
      } catch (e) {
        if (epoch !== this.#epoch || this.current?.id !== doc.id) return;
        if (e instanceof ApiError && e.status === 409) {
          this.phase = 'conflict';
          const remote = await get<Doc>(`/docs/${encodeURIComponent(doc.id)}`).catch(() => null);
          if (epoch === this.#epoch && this.current?.id === doc.id) this.remote = remote;
        } else {
          this.phase = 'error';
          this.saveFailed = true;
          board.flash((e as Error).message);
        }
      }
    })();
    try {
      await this.#saving;
    } finally {
      if (epoch === this.#epoch) this.#saving = null;
    }
  }

  #onEvent(ev: SlateEvent) {
    if (ev.type.startsWith('doc.')) {
      clearTimeout(this.#listTimer);
      this.#listTimer = setTimeout(() => void this.load(), 150);
      if (this.current && ev.subject_id === this.current.id) {
        this.#follow(ev);
        clearTimeout(this.#activityTimer);
        this.#activityTimer = setTimeout(() => void this.loadActivity(this.current?.id ?? null), 200);
      }
      // A child of the open page appeared, moved or went: its subpage list changes.
      else if (this.current && /^doc\.(created|moved|archived|unarchived|deleted)$/.test(ev.type)) {
        this.#scheduleRefresh();
      }
    } else if (this.current && ev.item_id && this.current.missing_tasks.includes(ev.item_id)) {
      this.#scheduleRefresh();
    }
  }

  #follow(ev: SlateEvent) {
    if (ev.type === 'doc.deleted') {
      this.current = null;
      this.missing = true;
      return;
    }
    this.#scheduleRefresh();
  }

  #scheduleRefresh() {
    clearTimeout(this.#docTimer);
    this.#docTimer = setTimeout(() => void this.#refresh(), 200);
  }

  async #refresh() {
    const generation = this.#openGeneration;
    const open = this.current;
    if (!open) return;
    let fresh: Doc;
    try {
      fresh = await get<Doc>(`/docs/${encodeURIComponent(open.id)}`);
    } catch {
      return;
    }
    if (generation !== this.#openGeneration || this.current?.id !== fresh.id || this.#saving) return;
    if (fresh.revision > this.current.revision && this.dirty) {
      // Keep the text being typed; the page offers "take theirs" or "keep mine".
      this.remote = fresh;
      this.phase = 'conflict';
      clearTimeout(this.#saveTimer);
      return;
    }
    if (fresh.revision >= this.current.revision) this.#show(fresh);
  }

  // --- writes --------------------------------------------------------------------------------

  async create(opts: { place?: Place; parent_id?: string | null; template?: string; title?: string; body_md?: string; icon?: string; kind?: 'page' | 'file' | 'folder' }): Promise<Doc | null> {
    const body: Record<string, unknown> = opts.kind && opts.kind !== 'page' ? { kind: opts.kind } : { template: opts.template ?? 'blank' };
    if (opts.parent_id) body.parent_id = opts.parent_id;
    else if (opts.place?.kind === 'list') body.list_id = opts.place.id;
    else if (opts.place?.kind === 'section') body.section_id = opts.place.id;
    for (const k of ['title', 'body_md', 'icon'] as const) if (opts[k] !== undefined) body[k] = opts[k];
    const d = await this.#write(() => post<Doc>('/docs', body));
    if (d) {
      this.list = [...this.list.filter((x) => x.id !== d.id), d];
    }
    return d;
  }

  /** Replace a doc's tags (names; unknown ones are created). */
  async setTags(id: string, tags: string[]) {
    const d = await this.#write(() => patch<Doc>(`/docs/${encodeURIComponent(id)}`, { tags }));
    if (d && this.current?.id === id) this.#show(d);
    if (d) this.#touch(d);
    return d;
  }

  /** The project home's documents hub for a section. */
  hub(sectionId: string): Promise<SectionDocuments> {
    return get<SectionDocuments>(`/sections/${encodeURIComponent(sectionId)}/documents`);
  }

  // --- drawers --------------------------------------------------------------------------------

  /** A drawer's entries at its top or inside a folder. */
  drawer(listId: string, folderId?: string | null): Promise<Drawer> {
    return get<Drawer>(`/lists/${encodeURIComponent(listId)}/drawer${folderId ? `?folder=${encodeURIComponent(folderId)}` : ''}`);
  }

  /** Turn a top-level page into a drawer in its section; returns the new list. */
  async toDrawer(docId: string): Promise<import('./types').List | null> {
    const l = await this.#write(() => post<import('./types').List>(`/docs/${encodeURIComponent(docId)}/to-drawer`));
    if (l) {
      board.lists[l.id] = l;
      await board.reloadStructure();
      await this.load();
    }
    return l;
  }

  /** Pin a document to the top of its drawer or folder listing (shared), or take it off. The
   * drawer page reloads on the `doc.pinned_to_top|unpinned_from_top` event. */
  async pinToTop(docId: string, on: boolean) {
    const d = await this.#write(() => on
        ? post<Doc>(`/docs/${encodeURIComponent(docId)}/top`)
        : api<Doc>('DELETE', `/docs/${encodeURIComponent(docId)}/top`));
    if (d) {
      if (this.current?.id === docId) this.current = d;
      board.flash(on ? `${d.title} pinned to the top` : `${d.title} unpinned`);
    }
  }

  /** Pin a revision as the one in use; `null` takes the pin off. */
  async pin(docId: string, revisionNo: number | null, note = '') {
    const d = await this.#write(() =>
      revisionNo === null
        ? api<Doc>('DELETE', `/docs/${encodeURIComponent(docId)}/pin`)
        : post<Doc>(`/docs/${encodeURIComponent(docId)}/revisions/${revisionNo}/pin`, note ? { note } : {}),
    );
    if (d && this.current?.id === docId) this.#show(d, true);
    if (d) this.#touch(d);
    return d;
  }

  /** Close the current page-history version, optionally with a name. */
  async checkpoint(id: string, note: string) {
    const epoch = this.#epoch;
    await this.flush();
    if (epoch !== this.#epoch) return;
    const d = await this.#write(() => patch<Doc>(`/docs/${encodeURIComponent(id)}`, { new_version: true, note: note || undefined }));
    if (!d) return;
    if (this.current?.id === id) this.current = d;
    board.flash(note ? `Saved version “${note}”` : 'Saved a version');
  }

  async move(id: string, to: { parent_id?: string | null; place?: Place; before_id?: string | null }) {
    const body: Record<string, unknown> = {};
    if (to.parent_id) body.parent_id = to.parent_id;
    else if (to.place?.kind === 'list') body.list_id = to.place.id;
    else if (to.place?.kind === 'section') body.section_id = to.place.id;
    if (to.before_id) body.before_id = to.before_id;
    const d = await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/move`, body));
    if (d) {
      if (this.current?.id === id) this.current = d;
      await this.load();
    }
  }

  async archive(id: string) {
    const d = await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/archive`));
    if (d) {
      this.list = this.list.filter((x) => x.id !== id);
      board.flash('Page archived', { label: 'Undo', run: () => void this.unarchive(id) });
    }
  }

  async unarchive(id: string) {
    if (await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/unarchive`))) await this.load();
  }

  async remove(id: string) {
    if (await this.#write(() => api<{ deleted: string[] }>('DELETE', `/docs/${encodeURIComponent(id)}`))) {
      await this.load();
    }
  }

  archived(): Promise<DocSummary[]> {
    return get<DocSummary[]>('/docs?archived=true');
  }

  async duplicate(id: string): Promise<Doc | null> {
    const d = await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/duplicate`, {}));
    if (d) await this.load();
    return d;
  }

  versions(id: string): Promise<DocVersion[]> {
    return get<DocVersion[]>(`/docs/${encodeURIComponent(id)}/versions`);
  }

  version(id: string, n: number): Promise<DocVersion> {
    return get<DocVersion>(`/docs/${encodeURIComponent(id)}/versions/${n}`);
  }

  diff(id: string, from: number, to?: number): Promise<DocDiff> {
    return get<DocDiff>(`/docs/${encodeURIComponent(id)}/diff?from=${from}${to ? `&to=${to}` : ''}`);
  }

  async restore(id: string, n: number) {
    const d = await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/versions/${n}/restore`));
    if (d && this.current?.id === id) {
      this.#show(d, true);
      this.phase = 'idle';
    }
    if (d) board.flash(`Restored version ${n}`);
    return !!d;
  }

  /** File a task from the page; `replace` (with its occurrence) becomes the task's card. */
  async createTask(id: string, body: { title: string; list_id?: string | null; replace?: string; occurrence?: number }): Promise<Item | null> {
    const epoch = this.#epoch;
    await this.flush();
    if (epoch !== this.#epoch) return null;
    const out = await this.#write(() =>
      post<{ item: Item; doc: Doc }>(`/docs/${encodeURIComponent(id)}/tasks`, {
        ...body,
        list_id: body.list_id ?? undefined,
        base_revision: this.current?.id === id ? this.current.revision : undefined,
      }),
    );
    if (!out) return null;
    if (this.current?.id === id) {
      this.#show(out.doc, true);
    }
    board.items[out.item.id] ??= out.item;
    return out.item;
  }

  async upload(id: string, file: File): Promise<DocFile | null> {
    const epoch = this.#epoch;
    const token = board.token;
    try {
      const res = await fetch(`/api/v1/docs/${encodeURIComponent(id)}/files?filename=${encodeURIComponent(file.name || 'pasted.png')}`, {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail ?? `Upload failed (${res.status})`);
      return epoch === this.#epoch ? data as DocFile : null;
    } catch (e) {
      if (epoch === this.#epoch) board.flash((e as Error).message);
      return null;
    }
  }

  // --- file docs ----------------------------------------------------------------------------

  /** A new file doc holding `file` as revision 1: create the doc (titled after the file), then
   * upload. A failed upload leaves an empty file doc, which the page offers to fill. */
  async createFile(where: { place?: Place; parent_id?: string | null }, file: File, note: string): Promise<Doc | null> {
    const epoch = this.#epoch;
    const title = file.name.replace(/\.[a-z0-9]{1,6}$/i, '') || file.name || 'Untitled file';
    const d = await this.create({ ...where, kind: 'file', title });
    if (!d) return null;
    const rev = await this.uploadRevision(d.id, file, note);
    if (epoch !== this.#epoch) return null;
    const out = rev ? await get<Doc>(`/docs/${encodeURIComponent(d.id)}`).catch(() => d) : d;
    return epoch === this.#epoch ? out : null;
  }

  /** The next revision of a file doc; `note` (what changed) is required by the server. */
  async uploadRevision(id: string, file: File, note: string): Promise<DocRevision | null> {
    const epoch = this.#epoch;
    const token = board.token;
    const q = `filename=${encodeURIComponent(file.name || 'file')}&note=${encodeURIComponent(note)}`;
    try {
      const res = await fetch(`/api/v1/docs/${encodeURIComponent(id)}/revisions?${q}`, {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': file.type || 'application/octet-stream' },
        body: file,
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.detail ?? `Upload failed (${res.status})`);
      if (epoch !== this.#epoch) return null;
      if (this.current?.id === id) this.#scheduleRefresh();
      return data as DocRevision;
    } catch (e) {
      if (epoch === this.#epoch) board.flash((e as Error).message);
      return null;
    }
  }

  /** What changed between two revisions of a file doc (`to` default: the current one). */
  compare(id: string, from: number, to?: number): Promise<RevisionCompare> {
    return get<RevisionCompare>(`/docs/${encodeURIComponent(id)}/revisions/compare?from=${from}${to ? `&to=${to}` : ''}`);
  }

  revisions(id: string): Promise<DocRevision[]> {
    return get<DocRevision[]>(`/docs/${encodeURIComponent(id)}/revisions`);
  }

  async restoreRevision(id: string, n: number): Promise<boolean> {
    const d = await this.#write(() => post<Doc>(`/docs/${encodeURIComponent(id)}/revisions/${n}/restore`));
    if (d && this.current?.id === id) this.#show(d);
    if (d) board.flash(`Restored revision ${n}`);
    return !!d;
  }

  /** The text of a small text or CSV revision (fetched with the token). */
  async fileText(fileId: string): Promise<string> {
    const r = await fetch(`/api/v1/doc-files/${encodeURIComponent(fileId)}`, { headers: authHeaders(board.token) });
    if (!r.ok) throw new Error(String(r.status));
    return r.text();
  }

  /** Download a revision: fetched with the token, saved under its filename. */
  async download(rev: DocRevision) {
    try {
      const url = await this.fileUrl(rev.id);
      const a = document.createElement('a');
      a.href = url;
      a.download = rev.filename;
      a.click();
    } catch {
      board.flash('Could not download the file');
    }
  }

  // --- discussion and review threads --------------------------------------------------------

  async loadActivity(id: string | null) {
    const generation = this.#openGeneration;
    if (!id) return;
    try {
      const out = await get<{ comments: DocComment[]; events: DocEvent[] }>(`/docs/${encodeURIComponent(id)}/activity`);
      if (generation !== this.#openGeneration || this.current?.id !== id) return;
      this.comments = out.comments;
      this.activity = out.events;
    } catch {
      /* the panel shows what it has */
    }
  }

  /** Start a review thread from a selection or a pin: the panel opens its form. */
  startReview(anchor: Anchor, viewedRevision?: number) {
    this.pendingRevision = viewedRevision ?? this.current?.revision ?? null;
    this.pendingAnchor = anchor;
  }

  async comment(
    id: string,
    body: string,
    opts: { parent_id?: string; anchor?: Anchor; suggestion_md?: string; assignee?: string | null; viewed_revision?: number } = {},
  ): Promise<DocComment | null> {
    const payload: Record<string, unknown> = { body_md: body };
    if (opts.viewed_revision !== undefined) payload.viewed_revision = opts.viewed_revision;
    if (opts.parent_id) payload.parent_id = opts.parent_id;
    if (opts.anchor && Object.keys(opts.anchor).length) payload.anchor = opts.anchor;
    if (opts.suggestion_md?.trim()) payload.suggestion_md = opts.suggestion_md.trim();
    if (opts.assignee) payload.assignee = opts.assignee;
    const c = await this.#write(() => post<DocComment>(`/docs/${encodeURIComponent(id)}/comments`, payload));
    if (c && this.current?.id === id) {
      this.comments = [...this.comments.filter((x) => x.id !== c.id), c];
      void this.loadActivity(id);
    }
    return c;
  }

  async editComment(commentId: string, body: string) {
    const c = await this.#write(() => patch<DocComment>(`/doc-comments/${encodeURIComponent(commentId)}`, { body_md: body }));
    if (c) this.comments = this.comments.map((x) => (x.id === c.id ? c : x));
    return c;
  }

  async resolveComment(commentId: string, reopen = false) {
    const c = await this.#write(() => post<DocComment>(`/doc-comments/${encodeURIComponent(commentId)}/${reopen ? 'reopen' : 'resolve'}`));
    if (c) {
      this.comments = this.comments.map((x) => (x.id === c.id ? c : x));
      void this.loadActivity(c.doc_id);
    }
    return c;
  }

  async acceptSuggestion(commentId: string) {
    const out = await this.#write(() => post<{ comment: DocComment; doc: Doc }>(`/doc-comments/${encodeURIComponent(commentId)}/accept`));
    if (out) {
      if (this.current?.id === out.doc.id) this.#show(out.doc, true);
      board.flash('Suggestion applied to the page');
      void this.loadActivity(out.doc.id);
    }
    return out;
  }

  /** The bytes of an uploaded file (fetched with the token). */
  fileBlob(fileId: string): Promise<Blob> {
    return fetch(`/api/v1/doc-files/${encodeURIComponent(fileId)}`, { headers: authHeaders(board.token) }).then((r) =>
      r.ok ? r.blob() : Promise.reject(new Error(String(r.status))),
    );
  }

  /** An object URL for an uploaded image (fetched once with the token, then cached). */
  fileUrl(fileId: string): Promise<string> {
    const epoch = this.#epoch;
    let p = this.#files.get(fileId);
    if (!p) {
      p = this.fileBlob(fileId).then((b) => {
        if (epoch !== this.#epoch) throw new Error('Document session changed');
        return URL.createObjectURL(b);
      });
      p.catch(() => { if (epoch === this.#epoch) this.#files.delete(fileId); });
      this.#files.set(fileId, p);
    }
    return p;
  }

  /** Server search over titles and bodies (the palette's doc hits). */
  search(q: string, limit = 8): Promise<DocSummary[]> {
    return get<DocSummary[]>(`/docs?q=${encodeURIComponent(q)}&limit=${limit}`);
  }

  /** Pages that embed or link a task (the task window's "In docs"). */
  embedding(id: string): Promise<DocSummary[]> {
    return get<DocSummary[]>(`/docs?embeds=${encodeURIComponent(id)}`);
  }

  #touch(d: DocSummary) {
    this.list = this.list.map((x) => (x.id === d.id ? { ...x, ...pick(d) } : x));
  }

  async #write<T>(fn: () => Promise<T>): Promise<T | null> {
    const epoch = this.#epoch;
    try {
      const result = await fn();
      return epoch === this.#epoch ? result : null;
    } catch (e) {
      if (epoch === this.#epoch) board.flash((e as Error).message);
      return null;
    }
  }
}

function pick(d: DocSummary): DocSummary {
  const { id, kind, file, pinned, pinned_to_top, comment_count, open_threads, tags, title, icon, parent_id, section_id, list_id, position, revision, version_no, created_by, created_at, updated_by, updated_at, archived_at } = d;
  return { id, kind, file, pinned, pinned_to_top, comment_count, open_threads, tags, title, icon, parent_id, section_id, list_id, position, revision, version_no, created_by, created_at, updated_by, updated_at, archived_at };
}

export const docs = new Docs();
