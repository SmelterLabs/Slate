// Docs on the board, the pure parts: shapes from /docs, the page tree, editor commands (the
// slash menu and the toolbar), and selection helpers for "create task from selection". The
// store with the API calls and the live stream is docs.svelte.ts. Vitest covers this file.

import { officeKind } from './office';

export type DocKind = 'page' | 'file' | 'folder';

/** One revision of a file doc: metadata only, the bytes are at `url`. */
export interface DocRevision {
  id: string;
  doc_id: string;
  revision_no: number;
  filename: string;
  content_type: string;
  size: number;
  note: string;
  uploaded_by: string;
  created_at: string;
  superseded_at: string | null;
  restored_from: number | null;
  /** Text was read out of it on upload (search sees inside it, compare can diff it). False
   * for a file over the server's extraction cap or one it could not read: stored and
   * viewable, not searchable. */
  has_text?: boolean;
  url: string;
}

/** A revision of a type the server normally indexes whose text it did not read. */
export function notIndexed(r: Pick<DocRevision, 'content_type' | 'filename' | 'has_text'>): boolean {
  if (r.has_text !== false) return false;
  return ['pdf', 'docx', 'xlsx', 'text', 'table'].includes(viewerKind(r.content_type, r.filename));
}

export interface DocSummary {
  id: string;
  /** `page`: Markdown written here. `file`: an uploaded file with revisions. */
  kind: DocKind;
  /** A file doc's current revision (null until the first upload). */
  file: DocRevision | null;
  comment_count?: number;
  open_threads?: number;
  tags?: string[];
  /** A file doc's pinned revision: the one in use. */
  pinned?: { revision_no: number; note: string | null; file_id: string | null } | null;
  /** Pinned to the top of its drawer or folder listing (shared), or null. */
  pinned_to_top?: { at: string; by: string | null } | null;
  title: string;
  icon: string | null;
  parent_id: string | null;
  section_id: string | null;
  list_id: string | null;
  position: number;
  revision: number;
  version_no: number;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
  archived_at: string | null;
  snippet?: string | null;
}

export interface DocRef {
  id: string;
  title: string;
  icon: string | null;
}

export interface DocFile {
  id: string;
  doc_id: string;
  filename: string;
  content_type: string;
  size: number;
  uploaded_by: string;
  created_at: string;
  url: string;
  markdown: string;
}

export interface Doc extends DocSummary {
  body_md: string;
  path: DocRef[];
  children: DocSummary[];
  outline: { level: number; text: string; line: number }[];
  /** `key: value` lines of a leading ```facts block (a record page); empty otherwise. */
  facts?: { key: string; value: string }[];
  tasks: import('./types').Item[];
  missing_tasks: string[];
  docs: DocRef[];
  backlinks: DocRef[];
  files: DocFile[];
  /** A file doc's revisions, newest first. */
  revisions: DocRevision[];
  words: number;
}

export interface DocVersion {
  id: string;
  doc_id: string;
  version_no: number;
  title: string;
  author_id: string;
  created_at: string;
  updated_at: string;
  note: string | null;
  chars: number;
  body_md?: string | null;
}

export interface DocDiff {
  from_version: number;
  to_version: number;
  title_before: string;
  title_after: string;
  added: number;
  removed: number;
  diff: string;
}

export interface DocTemplate {
  key: string;
  label: string;
  title: string;
  icon: string;
  body_md: string;
}

/** Where a top page lives: the workspace, a section, or a list. */
export type Place = { kind: 'workspace' } | { kind: 'section'; id: string } | { kind: 'list'; id: string };

export function placeKey(p: Place): string {
  return p.kind === 'workspace' ? 'ws' : `${p.kind === 'list' ? 'l' : 's'}:${p.id}`;
}

export function placeOf(d: Pick<DocSummary, 'list_id' | 'section_id'>): Place {
  if (d.list_id) return { kind: 'list', id: d.list_id };
  if (d.section_id) return { kind: 'section', id: d.section_id };
  return { kind: 'workspace' };
}

export interface TreeNode {
  doc: DocSummary;
  depth: number;
  children: TreeNode[];
}

const bySiblingOrder = (a: DocSummary, b: DocSummary) =>
  a.position - b.position || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);

/** Top pages grouped by place key, each with its subpages; an orphan (parent not in the set)
 * shows at the top of its own place rather than disappearing. */
export function buildTree(docs: DocSummary[]): Map<string, TreeNode[]> {
  const ids = new Set(docs.map((d) => d.id));
  const kids = new Map<string, DocSummary[]>();
  const tops: DocSummary[] = [];
  for (const d of docs) {
    if (d.parent_id && ids.has(d.parent_id)) {
      const list = kids.get(d.parent_id) ?? [];
      list.push(d);
      kids.set(d.parent_id, list);
    } else tops.push(d);
  }
  const seen = new Set<string>();
  const node = (d: DocSummary, depth: number): TreeNode => {
    seen.add(d.id);
    const children = (kids.get(d.id) ?? [])
      .filter((c) => !seen.has(c.id))
      .sort(bySiblingOrder)
      .map((c) => node(c, depth + 1));
    return { doc: d, depth, children };
  };
  const out = new Map<string, TreeNode[]>();
  for (const d of tops.sort(bySiblingOrder)) {
    const key = placeKey(placeOf(d));
    const list = out.get(key) ?? [];
    list.push(node(d, 0));
    out.set(key, list);
  }
  return out;
}

/** Ancestors of `id`, top page first (not including the page). */
export function pathOf(id: string, byId: Record<string, DocSummary>): DocSummary[] {
  const out: DocSummary[] = [];
  const seen = new Set([id]);
  let cur = byId[id]?.parent_id ? byId[byId[id].parent_id!] : undefined;
  while (cur && !seen.has(cur.id)) {
    out.unshift(cur);
    seen.add(cur.id);
    cur = cur.parent_id ? byId[cur.parent_id] : undefined;
  }
  return out;
}

export function topOf(id: string, byId: Record<string, DocSummary>): DocSummary | undefined {
  return pathOf(id, byId)[0] ?? byId[id];
}

/** Every page under `id` (any depth). */
export function descendants(id: string, docs: DocSummary[]): Set<string> {
  const out = new Set<string>();
  let frontier = [id];
  while (frontier.length) {
    const next: string[] = [];
    for (const d of docs) if (d.parent_id && frontier.includes(d.parent_id) && !out.has(d.id)) {
      out.add(d.id);
      next.push(d.id);
    }
    frontier = next;
  }
  return out;
}

/** Which appearance (from 0) of `needle` begins at `at`; matches the server's search. */
export function occurrenceAt(text: string, needle: string, at: number): number {
  let n = 0;
  let p = text.indexOf(needle);
  while (p >= 0 && p < at) {
    n++;
    p = text.indexOf(needle, p + 1);
  }
  return n;
}

/** A pasted Slate task link (`…?task=<id>`) becomes an embed. */
export function taskIdFromLink(text: string): string | null {
  const m = /^\s*\S*[#?&]task=([0-9A-Za-z]{12})\s*$/.exec(text);
  return m ? m[1] : null;
}

/** A tidy title from a selection: the first line without Markdown marks, at most 200 chars. */
export function titleFrom(selection: string): string {
  const line = selection.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  return line
    .replace(/^([-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+|#{1,6}\s+|>\s*)/, '')
    .replace(/\*\*|__|~~|`/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .trim()
    .slice(0, 200);
}

/** The page as a Markdown file: "# Title" then the body. */
export function asMarkdownFile(doc: Pick<Doc, 'title' | 'icon' | 'body_md'>): string {
  return `# ${doc.icon ? `${doc.icon} ` : ''}${doc.title}\n\n${doc.body_md.replace(/^\n+/, '')}`;
}

export function filenameFor(title: string): string {
  return `${title.replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim() || 'doc'}.md`;
}

export const ICON_CHOICES = [
  '📄', '📝', '📘', '📒', '🗒️', '📌', '🧭', '🗓️', '✅', '💡', '⚖️', '🎯', '🚀', '🛠️', '🧪', '🤖',
  '🏠', '🌱', '💰', '🧾', '✈️', '❤️', '⭐', '🔥', '📦', '🔒', '📈', '🧠', '🎨', '🗂️',
];

/** Lines of a unified diff, classified for colouring. */
export function diffLines(diff: string): { kind: 'add' | 'del' | 'hunk' | 'ctx'; text: string }[] {
  return diff
    .split('\n')
    .filter((l) => l && !l.startsWith('---') && !l.startsWith('+++'))
    .map((l) => ({
      kind: l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : 'ctx',
      text: l.startsWith('@@') ? l : l.slice(1),
    }));
}

// --- file docs --------------------------------------------------------------------------------

export type ViewerKind = 'pdf' | 'image' | 'docx' | 'xlsx' | 'text' | 'table' | 'other';

const TEXT_EXT = /\.(txt|md|markdown|json|ya?ml|toml|xml|log|gcode|nc|ini|cfg|csv|tsv|py|js|ts|sh|sql|html?|css)$/i;

/** How the board shows a revision: the browser's PDF viewer, an image, text, a table or a
 * download card. Decided by content type first, then the filename. */
export function viewerKind(contentType: string, filename: string): ViewerKind {
  const ct = contentType.toLowerCase();
  if (ct === 'application/pdf') return 'pdf';
  if (ct.startsWith('image/') && !ct.includes('svg')) return 'image';
  const office = officeKind(ct, filename);
  if (office) return office;
  if (ct === 'text/csv' || ct === 'text/tab-separated-values' || /\.(csv|tsv)$/i.test(filename)) return 'table';
  if (ct.startsWith('text/') || ct === 'application/json' || ct === 'application/xml' || TEXT_EXT.test(filename)) return 'text';
  return 'other';
}

/** The glyph for a doc in the tree, cards and the sidebar when it has no icon of its own. */
export function docGlyph(d: Pick<DocSummary, 'kind' | 'file'>): string {
  if (d.kind === 'folder') return '📁';
  if (d.kind !== 'file') return '📄';
  if (!d.file) return '📎';
  const ct = d.file.content_type.toLowerCase();
  const name = d.file.filename.toLowerCase();
  if (ct === 'application/pdf') return '📕';
  if (ct.startsWith('image/')) return '🖼️';
  if (ct.startsWith('video/')) return '🎞️';
  if (ct.startsWith('audio/')) return '🎵';
  if (ct.includes('spreadsheet') || ct.includes('excel') || /\.(xlsx?|csv|tsv|ods|numbers)$/.test(name)) return '📊';
  if (ct.includes('presentation') || ct.includes('powerpoint') || /\.(pptx?|key)$/.test(name)) return '📽️';
  if (ct.includes('word') || /\.(docx?|odt|rtf)$/.test(name)) return '📝';
  if (ct.includes('zip') || ct.includes('compressed') || /\.(zip|7z|rar|tar|gz)$/.test(name)) return '🗜️';
  if (/\.(dwg|dxf|step|stp|iges|igs|stl|3mf|obj)$/.test(name)) return '📐';
  if (viewerKind(ct, name) === 'text') return '📃';
  return '📎';
}

/** Split a CSV or TSV into rows (quotes and doubled quotes as in RFC 4180). */
export function parseTable(text: string, max = 500): string[][] {
  const sep = text.includes('\t') && !text.split('\n')[0]?.includes(',') ? '\t' : ',';
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === sep) {
      row.push(cell);
      cell = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
      if (rows.length >= max) return rows;
    } else cell += c;
  }
  if (cell !== '' || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

// --- discussion and review threads ----------------------------------------------------------

export type Surface = 'review' | 'discussion';

export interface DocComment {
  id: string;
  doc_id: string;
  parent_id: string | null;
  surface: Surface;
  author_id: string;
  author_handle: string | null;
  author_kind: 'person' | 'agent' | 'guest' | null;
  body_md: string;
  created_at: string;
  edited_at: string | null;
  mentions: string[];
  /** The page version or file revision current when it was written. */
  anchor_revision: number;
  anchor_page_revision: number | null;
  current_revision: number;
  outdated: boolean;
  anchor_start: number | null;
  anchor_end: number | null;
  anchor_quote: string | null;
  anchor_page: number | null;
  anchor_x: number | null;
  anchor_y: number | null;
  suggestion_md: string | null;
  accepted_at: string | null;
  assignee_id: string | null;
  resolved_at: string | null;
  resolved_by_id: string | null;
}

export interface Anchor {
  quote?: string;
  start?: number;
  end?: number;
  page?: number;
  x?: number;
  y?: number;
}

export interface Thread {
  root: DocComment;
  replies: DocComment[];
}

/** Root comments with their replies, oldest first; replies whose root is gone stand alone. */
export function threadsOf(comments: DocComment[], surface?: Surface): Thread[] {
  const byId = new Map(comments.map((c) => [c.id, c]));
  const out = new Map<string, Thread>();
  for (const c of comments) {
    if (c.parent_id && byId.has(c.parent_id)) continue;
    if (!surface || c.surface === surface) out.set(c.id, { root: c, replies: [] });
  }
  for (const c of comments) {
    if (c.parent_id && out.has(c.parent_id)) out.get(c.parent_id)!.replies.push(c);
  }
  return [...out.values()];
}

/** A short label for where a review thread is anchored. */
export function anchorLabel(c: Pick<DocComment, 'anchor_quote' | 'anchor_page' | 'anchor_x' | 'anchor_y'>): string {
  if (c.anchor_quote) {
    const q = c.anchor_quote.replace(/\s+/g, ' ').trim();
    return `“${q.length > 80 ? `${q.slice(0, 79)}…` : q}”`;
  }
  const bits: string[] = [];
  if (c.anchor_page) bits.push(`page ${c.anchor_page}`);
  if (c.anchor_x !== null && c.anchor_x !== undefined) bits.push('a spot');
  return bits.length ? `on ${bits.join(', ')}` : 'on the document';
}

export interface DocEvent {
  id: string;
  type: string;
  actor_id: string;
  field: string | null;
  before: unknown;
  after: unknown;
  at: string;
}

/** One line for an activity event in the discussion (small, grey, folded). */
export function docEventLine(ev: DocEvent, name: (id: string) => string | undefined): { text: string; detail: string | null } {
  const who = name(ev.actor_id) ?? 'Someone';
  const after = (ev.after && typeof ev.after === 'object' ? ev.after : {}) as Record<string, unknown>;
  switch (ev.type) {
    case 'doc.created':
      return { text: `${who} created this`, detail: null };
    case 'doc.revision_pinned':
      return { text: `${who} marked rev ${after.revision_no} IN USE`, detail: typeof after.note === 'string' ? after.note : null };
    case 'doc.revision_unpinned':
      return { text: `${who} cleared IN USE`, detail: null };
    case 'doc.revision_added': {
      const n = after.revision_no as number | undefined;
      const from = after.restored_from as number | undefined;
      return {
        text: from ? `${who} made revision ${from} current again as rev ${n}` : `${who} uploaded rev ${n}`,
        detail: typeof after.note === 'string' ? `${after.filename ? `${after.filename} · ` : ''}${after.note}` : null,
      };
    }
    case 'doc.updated':
      if (ev.field === 'title') return { text: `${who} renamed this`, detail: `“${String(ev.before ?? '')}” → “${String(ev.after ?? '')}”` };
      if (ev.field === 'icon') return { text: `${who} changed the icon`, detail: null };
      if (ev.field === 'version_no') return { text: `${who} named a version`, detail: typeof after.note === 'string' ? after.note : null };
      return { text: `${who} edited the text`, detail: null };
    case 'doc.moved':
      return { text: `${who} moved this`, detail: null };
    case 'doc.archived':
      return { text: `${who} archived this`, detail: null };
    case 'doc.unarchived':
      return { text: `${who} restored this from the archive`, detail: null };
    case 'doc.version_restored':
      return { text: `${who} restored version ${String(after.restored_from ?? '')}`, detail: null };
    case 'doc.comment_resolved': {
      const reason = String(after.reason ?? 'resolved');
      const text =
        reason === 'reopened'
          ? `${who} reopened a thread`
          : reason === 'accepted'
            ? `${who} accepted a suggestion`
            : reason === 'replied'
              ? `${who} answered an ask`
              : `${who} resolved a thread`;
      return { text, detail: null };
    }
    case 'doc.suggestion_accepted':
      return {
        text: `${who} accepted a suggestion`,
        detail: `“${String(after.quote ?? '')}” → “${String(after.suggestion ?? '')}”`,
      };
    default:
      return { text: `${who} ${ev.type.replace('doc.', '').replace(/_/g, ' ')}`, detail: null };
  }
}

export type FeedRow = { kind: 'thread'; at: string; thread: Thread } | { kind: 'activity'; at: string; events: DocEvent[] };

/** Discussion threads and activity events in time order; consecutive events fold into one row. */
export function feedRows(threads: Thread[], events: DocEvent[]): FeedRow[] {
  const rows: FeedRow[] = [];
  const items: { at: string; thread?: Thread; event?: DocEvent }[] = [
    ...threads.map((t) => ({ at: t.root.created_at, thread: t })),
    ...events.map((e) => ({ at: e.at, event: e })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  for (const it of items) {
    if (it.thread) rows.push({ kind: 'thread', at: it.at, thread: it.thread });
    else if (it.event) {
      const last = rows[rows.length - 1];
      if (last?.kind === 'activity') last.events.push(it.event);
      else rows.push({ kind: 'activity', at: it.at, events: [it.event] });
    }
  }
  return rows;
}

// --- compare --------------------------------------------------------------------------------

export interface CellChange {
  row: number;
  col: number;
  before: string;
  after: string;
}

export interface SheetDiff {
  name: string;
  changed: CellChange[];
  added_rows: number[];
  removed_rows: number[];
  rows: string[][];
  before_rows: string[][];
}

export interface RevisionCompare {
  doc_id: string;
  from_revision: number;
  to_revision: number;
  from: DocRevision;
  to: DocRevision;
  kind: 'text' | 'table' | 'none';
  approximate: boolean;
  diff: string;
  added: number;
  removed: number;
  sheets: SheetDiff[];
  /** Why `kind` is `none` for a type that normally compares: a revision is not indexed. */
  message?: string | null;
}

export interface SideRow {
  left: { no: number; text: string } | null;
  right: { no: number; text: string } | null;
  kind: 'same' | 'add' | 'del' | 'change' | 'hunk';
}

/** A unified diff as two aligned columns: removed lines on the left, added on the right, a
 * removed/added pair on one row as a change, context on both sides. */
export function sideBySide(diff: string): SideRow[] {
  const rows: SideRow[] = [];
  let l = 0;
  let r = 0;
  let dels: string[] = [];
  let adds: string[] = [];
  const flush = () => {
    const n = Math.max(dels.length, adds.length);
    for (let i = 0; i < n; i++) {
      const d = dels[i];
      const a = adds[i];
      rows.push({
        left: d !== undefined ? { no: ++l, text: d } : null,
        right: a !== undefined ? { no: ++r, text: a } : null,
        kind: d !== undefined && a !== undefined ? 'change' : d !== undefined ? 'del' : 'add',
      });
    }
    dels = [];
    adds = [];
  };
  for (const line of diff.split('\n')) {
    if (!line || line.startsWith('---') || line.startsWith('+++')) continue;
    if (line.startsWith('@@')) {
      flush();
      const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (m) {
        l = Number(m[1]) - 1;
        r = Number(m[2]) - 1;
      }
      rows.push({ left: null, right: null, kind: 'hunk' });
      continue;
    }
    if (line.startsWith('-')) dels.push(line.slice(1));
    else if (line.startsWith('+')) adds.push(line.slice(1));
    else {
      flush();
      rows.push({ left: { no: ++l, text: line.slice(1) }, right: { no: ++r, text: line.slice(1) }, kind: 'same' });
    }
  }
  flush();
  return rows;
}

/** {row,col} -> change, for painting a sheet. */
export function cellChanges(sheet: SheetDiff): Map<string, CellChange> {
  return new Map(sheet.changed.map((c) => [`${c.row}:${c.col}`, c]));
}

// --- the tree's filters and the hub -------------------------------------------------------------

export type FileGroup = 'pdf' | 'sheet' | 'image' | 'text' | 'other';

/** Which group of files a file doc belongs to, for the tree's type filter. */
export function fileGroup(d: Pick<DocSummary, 'kind' | 'file'>): FileGroup | null {
  if (d.kind !== 'file' || !d.file) return d.kind === 'file' ? 'other' : null;
  const ct = d.file.content_type.toLowerCase();
  const name = d.file.filename.toLowerCase();
  if (ct === 'application/pdf') return 'pdf';
  if (ct.startsWith('image/')) return 'image';
  if (ct.includes('spreadsheet') || ct.includes('excel') || /\.(xlsx?|csv|tsv|ods|numbers)$/.test(name)) return 'sheet';
  if (viewerKind(ct, name) === 'text') return 'text';
  return 'other';
}

export interface TreeFilter {
  kind?: DocKind | null;
  group?: FileGroup | null;
  tag?: string | null;
}

export type TreeSort = 'tree' | 'updated' | 'name';

/** Keep the docs that match (and their ancestors, so the tree still reads), then build it. */
export function filterDocs(docs: DocSummary[], f: TreeFilter): DocSummary[] {
  if (!f.kind && !f.group && !f.tag) return docs;
  const byId = new Map(docs.map((d) => [d.id, d]));
  const keep = new Set<string>();
  for (const d of docs) {
    if (f.kind && d.kind !== f.kind) continue;
    if (f.group && fileGroup(d) !== f.group) continue;
    if (f.tag && !(d.tags ?? []).includes(f.tag)) continue;
    let cur: DocSummary | undefined = d;
    while (cur && !keep.has(cur.id)) {
      keep.add(cur.id);
      cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
    }
  }
  return docs.filter((d) => keep.has(d.id));
}

/** Reorder every level of the tree: by position (the default), newest change first, or name. */
export function sortTree(nodes: TreeNode[], sort: TreeSort): TreeNode[] {
  if (sort === 'tree') return nodes;
  const cmp =
    sort === 'name'
      ? (a: TreeNode, b: TreeNode) => a.doc.title.localeCompare(b.doc.title, undefined, { sensitivity: 'base' })
      : (a: TreeNode, b: TreeNode) => b.doc.updated_at.localeCompare(a.doc.updated_at);
  return [...nodes].sort(cmp).map((n) => ({ ...n, children: sortTree(n.children, sort) }));
}

/** Every tag used by the given docs, with how many carry it, most used first. */
export function tagCounts(docs: DocSummary[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const d of docs) for (const t of d.tags ?? []) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

export interface HubNeed {
  kind: 'ask' | 'thread';
  doc: DocRef & { kind: DocKind };
  comment: DocComment;
  url: string;
}

export interface DrawerCounts {
  docs: number;
  files: number;
  folders: number;
  open_threads: number;
}

export interface SectionDocuments {
  drawers: { list: import('./types').List; counts: DrawerCounts; needs_you: number }[];
  pages: DocSummary[];
  needs_you: (HubNeed & { list_id: string | null })[];
  counts: { docs: number; files: number; open_threads: number };
}

export interface DrawerEntry extends DocSummary {
  children_count: number;
}

export interface Drawer {
  list: import('./types').List;
  folder: DocSummary | null;
  path: DocRef[];
  entries: DrawerEntry[];
  counts: DrawerCounts;
}

/** The entries a drawer shows after its chips: by kind/type group, tag and a text search. */
export function filterEntries(entries: DrawerEntry[], f: { group?: FileGroup | 'page' | null; tag?: string | null; q?: string }): DrawerEntry[] {
  const q = (f.q ?? '').trim().toLowerCase();
  return entries.filter((e) => {
    if (e.kind === 'folder') return !q || e.title.toLowerCase().includes(q);
    if (f.group === 'page' && e.kind !== 'page') return false;
    if (f.group && f.group !== 'page' && fileGroup(e) !== f.group) return false;
    if (f.tag && !(e.tags ?? []).includes(f.tag)) return false;
    if (q && !e.title.toLowerCase().includes(q) && !(e.file?.filename ?? '').toLowerCase().includes(q)) return false;
    return true;
  });
}

