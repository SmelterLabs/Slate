import { authHeaders } from './api';
// The task window's own state and writes: the activity column, reactions, checklists,
// attachments and watchers. The item itself (fields, description) still goes through
// `board.update`, so cards and the window stay one source of truth. Every write here is one REST
// call; the stream then refreshes `board.detail`, which reloads the activity.

import { ApiError, api, get, patch, post, put } from './api';
import { toggleReaction } from './activity';
import { pastedName } from './chat';
import { viewerKind } from './docs';
import { isDocumentKind } from './office';
import { board } from './store.svelte';
import type { ActivityEntry, Assignee, Attachment, Checklist, Comment } from './types';

class TaskWindow {
  activity = $state<ActivityEntry[] | null>(null);
  /** Which entries the right column shows. */
  filter = $state<'all' | 'comments' | 'events'>('all');
  /** Attachment id -> object URL for thumbnails (images are fetched with the token). */
  thumbs = $state<Record<string, string>>({});
  uploading = $state(0);
  /** The attachment open in the full-screen viewer (images, videos, PDFs), with its object URL. */
  viewing = $state<{ a: Viewable; url: string; blob: Blob } | null>(null);
  /** What the viewer steps through when it was opened somewhere other than a task (a chat
   *  message's files): the files and where each one's bytes are. */
  #among: Among | null = null;
  /** A request to the activity column: scroll to this comment, light it, and focus the reply box
   * (the "waiting on you" banner's Answer). `n` makes a repeat click a new request. */
  revealing = $state<{ commentId: string | null; n: number } | null>(null);
  #loading: string | null = null;
  #again = false;

  get me(): string {
    return board.me?.account.id ?? '';
  }

  reveal(commentId: string | null) {
    this.revealing = { commentId, n: (this.revealing?.n ?? 0) + 1 };
  }

  reset() {
    this.activity = null;
    this.revealing = null;
    this.closeViewer();
    for (const url of Object.values(this.thumbs)) URL.revokeObjectURL(url);
    this.thumbs = {};
  }

  async loadActivity(id: string) {
    if (this.#loading === id) {
      this.#again = true;
      return;
    }
    this.#loading = id;
    try {
      const entries = await get<ActivityEntry[]>(`/items/${id}/activity`);
      if (board.openId === id) this.activity = entries;
    } catch {
      // the detail's own comments and events still show
    } finally {
      this.#loading = null;
      if (this.#again) {
        this.#again = false;
        if (board.openId === id) void this.loadActivity(id);
      }
    }
  }

  async #write<T>(run: () => Promise<T>): Promise<T | null> {
    try {
      const out = await run();
      void board.refreshDetail();
      return out;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  // --- comments -----------------------------------------------------------------------------

  /** Post a comment; with `assignee` (a handle) it is an Ask for that account. */
  async comment(itemId: string, body: string, assignee: string | null = null): Promise<boolean> {
    const payload = assignee ? { body_md: body, assignee } : { body_md: body };
    return (await this.#write(() => post<Comment>(`/items/${itemId}/comments`, payload))) !== null;
  }

  /** Dismiss an agent's "Ready for review" or blocked flag: an `idle` report by you, no comment. */
  async clearAgentFlag(itemId: string): Promise<boolean> {
    return (
      (await this.#write(() => post(`/items/${itemId}/report`, { state: 'idle', comment: false }))) !== null
    );
  }

  /** Close an Ask (its assignee, its author or an admin). */
  async resolveComment(id: string): Promise<boolean> {
    return (await this.#write(() => post<Comment>(`/comments/${id}/resolve`))) !== null;
  }

  async editComment(id: string, body: string): Promise<boolean> {
    return (await this.#write(() => patch<Comment>(`/comments/${id}`, { body_md: body }))) !== null;
  }

  /** Optimistic: the chip changes at once; the server's answer replaces it. */
  async react(c: Comment, emoji: string) {
    const mine = c.reactions?.find((r) => r.emoji === emoji)?.account_ids.includes(this.me);
    c.reactions = toggleReaction(c.reactions ?? [], emoji, this.me);
    const path = `/comments/${c.id}/reactions`;
    await this.#write(() =>
      mine ? api<Comment>('DELETE', `${path}/${encodeURIComponent(emoji)}`) : post<Comment>(path, { emoji }),
    );
  }

  // --- checklists ---------------------------------------------------------------------------

  addChecklist(itemId: string, name = 'Checklist', items: string[] = []) {
    return this.#write(() => post<Checklist>(`/items/${itemId}/checklists`, { name, items }));
  }

  renameChecklist(id: string, name: string) {
    return this.#write(() => patch<Checklist>(`/checklists/${id}`, { name }));
  }

  deleteChecklist(id: string) {
    return this.#write(() => api<void>('DELETE', `/checklists/${id}`));
  }

  addEntry(checklistId: string, title: string) {
    return this.#write(() => post<Checklist>(`/checklists/${checklistId}/items`, { title }));
  }

  updateEntry(id: string, changes: { title?: string; done?: boolean; assignee?: string | null; position?: number }) {
    return this.#write(() => patch<Checklist>(`/checklist-items/${id}`, changes));
  }

  removeEntry(id: string) {
    return this.#write(() => api<void>('DELETE', `/checklist-items/${id}`));
  }

  // --- attachments --------------------------------------------------------------------------

  async upload(itemId: string, files: Iterable<File>): Promise<Attachment[]> {
    const done: Attachment[] = [];
    for (const file of files) {
      this.uploading += 1;
      // A clipboard screenshot has no name of its own (`image.png` every time).
      const name = pastedName(file.name, file.type, new Date());
      try {
        const res = await fetch(`/api/v1/items/${itemId}/attachments?filename=${encodeURIComponent(name)}`, {
          method: 'POST',
          headers: {
            ...authHeaders(board.token),
            'Content-Type': file.type || 'application/octet-stream',
          },
          body: file,
        });
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new ApiError(res.status, (data as { detail?: string })?.detail ?? res.statusText);
        done.push(data as Attachment);
      } catch (e) {
        board.flash(`Could not attach ${name}: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        this.uploading -= 1;
      }
    }
    if (done.length) void board.refreshDetail();
    return done;
  }

  removeAttachment(id: string) {
    return this.#write(() => api<void>('DELETE', `/attachments/${id}`));
  }

  /** Move an attachment into Docs & Files as a file doc (revision 1) in the task's section;
   * the task keeps a comment linking it. Returns the new doc's id. */
  async promoteAttachment(a: Attachment, listId: string): Promise<string | null> {
    const sectionId = board.lists[listId]?.section_id;
    const doc = await this.#write(() => post<{ id: string }>(`/attachments/${a.id}/promote`, sectionId ? { section_id: sectionId } : {}));
    if (!doc) return null;
    void board.refreshDetail();
    board.flash(`${a.filename} moved to Docs & Files`, { label: 'Open', run: () => (location.hash = `#/docs/${doc.id}`) });
    return doc.id;
  }

  async #blob(id: string, path: (id: string) => string = (i) => `/attachments/${i}`): Promise<Blob> {
    const res = await fetch(`/api/v1${path(id)}`, {
      headers: authHeaders(board.token),
    });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.blob();
  }

  /** A thumbnail URL for an image attachment (fetched once, with the token). */
  async thumb(a: Attachment) {
    if (this.thumbs[a.id] || !a.content_type.startsWith('image/')) return;
    try {
      this.thumbs[a.id] = URL.createObjectURL(await this.#blob(a.id));
    } catch {
      // leave the file icon
    }
  }

  /** Images, videos, PDFs and documents the board can draw (Word, Excel, CSV, text;
   *  lib/office) open in the viewer; anything else downloads. The file needs the token, so it
   *  goes through a blob either way. */
  async open(a: Viewable, among: Among | null = null) {
    try {
      const blob = await this.#blob(a.id, among?.path);
      const url = URL.createObjectURL(blob);
      if (isMedia(a)) {
        this.closeViewer();
        this.#among = among;
        this.viewing = { a, url, blob };
        return;
      }
      const link = document.createElement('a');
      link.href = url;
      link.download = a.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  /** The attachments of the open task the viewer can step through (media and documents). */
  media(): Viewable[] {
    return (this.#among?.files ?? board.detail?.attachments ?? []).filter(isMedia);
  }

  /** Step the viewer to the previous or next image or video of the task. */
  step(delta: number) {
    const all = this.media();
    const at = all.findIndex((x) => x.id === this.viewing?.a.id);
    if (at < 0 || all.length < 2) return;
    void this.open(all[(at + delta + all.length) % all.length], this.#among);
  }

  closeViewer() {
    if (!this.viewing) return;
    URL.revokeObjectURL(this.viewing.url);
    this.viewing = null;
    this.#among = null;
  }

  // --- watchers -----------------------------------------------------------------------------

  watch(itemId: string, on: boolean) {
    return this.#write(() => post<Assignee[]>(`/items/${itemId}/${on ? 'watch' : 'unwatch'}`));
  }

  setWatchers(itemId: string, handles: string[]) {
    return this.#write(() => put<Assignee[]>(`/items/${itemId}/watchers`, { watchers: handles }));
  }

  // --- links --------------------------------------------------------------------------------

  /** A link that opens this task: the current page with `?task=<id>` in the hash. */
  link(itemId: string): string {
    const [path] = location.hash.split('?');
    return `${location.origin}${location.pathname}${path || '#/everything'}?task=${itemId}`;
  }
}

/** A stored file the viewer can show: a task's attachment or a chat message's file. */
export type Viewable = Pick<Attachment, 'id' | 'filename' | 'content_type' | 'size'>;
/** The set a viewed file belongs to, and the API path of each one's bytes. */
export interface Among {
  files: Viewable[];
  path: (id: string) => string;
}

/** What the viewer shows: images, videos, PDFs, and the documents lib/office can draw. */
export function isMedia(a: Viewable): boolean {
  return /^(image|video)\//.test(a.content_type) || a.content_type === 'application/pdf' || isDocumentKind(viewerKind(a.content_type, a.filename));
}

export const task = new TaskWindow();
