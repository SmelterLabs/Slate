import { describe, expect, it } from 'vitest';
import {
  buildTree,
  descendants,
  diffLines,
  docEventLine,
  docGlyph,
  feedRows,
  matchCommands,
  occurrenceAt,
  parseTable,
  pathOf,
  taskIdFromLink,
  threadsOf,
  titleFrom,
  viewerKind,
  notIndexed,
  anchorLabel,
  cellChanges,
  fileGroup,
  filterDocs,
  filterEntries,
  sideBySide,
  sortTree,
  tagCounts,
  type DocComment,
  type DocEvent,
  type DocSummary,
} from './docs';

function doc(id: string, over: Partial<DocSummary> = {}): DocSummary {
  return {
    id,
    kind: 'page',
    file: null,
    title: id,
    icon: null,
    parent_id: null,
    section_id: null,
    list_id: null,
    position: 0,
    revision: 1,
    version_no: 1,
    created_by: 'me',
    created_at: '2026-10-24T00:00:00.000000Z',
    updated_by: 'me',
    updated_at: '2026-10-24T00:00:00.000000Z',
    archived_at: null,
    ...over,
  };
}

describe('the page tree', () => {
  const docs = [
    doc('b', { position: 1 }),
    doc('a', { position: 0 }),
    doc('l', { list_id: 'L1', section_id: 'S1' }),
    doc('a2', { parent_id: 'a', position: 1 }),
    doc('a1', { parent_id: 'a', position: 0 }),
    doc('a1x', { parent_id: 'a1' }),
    doc('orphan', { parent_id: 'gone' }),
  ];

  it('groups top pages by place, siblings in order, orphans at the top', () => {
    const tree = buildTree(docs);
    expect(tree.get('ws')!.map((n) => n.doc.id)).toEqual(['a', 'orphan', 'b']);
    expect(tree.get('l:L1')!.map((n) => n.doc.id)).toEqual(['l']);
    const a = tree.get('ws')![0];
    expect(a.children.map((n) => n.doc.id)).toEqual(['a1', 'a2']);
    expect(a.children[0].children[0]).toMatchObject({ depth: 2, doc: { id: 'a1x' } });
  });

  it('walks paths and descendants', () => {
    const byId = Object.fromEntries(docs.map((d) => [d.id, d]));
    expect(pathOf('a1x', byId).map((d) => d.id)).toEqual(['a', 'a1']);
    expect([...descendants('a', docs)].sort()).toEqual(['a1', 'a1x', 'a2']);
  });
});

describe('slash commands', () => {
  it('matches slash commands', () => {
    expect(matchCommands('tab')[0].id).toBe('table');
    expect(matchCommands('todo')[0].id).toBe('check');
  });
});

describe('selection helpers', () => {
  it('counts which appearance a selection is', () => {
    expect(occurrenceAt('call Bob, call Bob', 'call Bob', 10)).toBe(1);
    expect(occurrenceAt('aaa', 'aa', 1)).toBe(1);
  });

  it('cleans a title from Markdown', () => {
    expect(titleFrom('\n- [ ] **Call** [Bob](https://b.c)\nmore')).toBe('Call Bob');
  });

  it('reads pasted task links', () => {
    expect(taskIdFromLink('https://x/#/list/L?task=AbCdEfGhIjKl')).toBe('AbCdEfGhIjKl');
    expect(taskIdFromLink('see https://x/#/list/L?task=AbCdEfGhIjKl')).toBeNull();
  });

  it('classifies diff lines', () => {
    expect(diffLines('--- v1\n+++ v2\n@@ -1 +1 @@\n-a\n+b\n c').map((l) => l.kind)).toEqual([
      'hunk',
      'del',
      'add',
      'ctx',
    ]);
  });
});

describe('file docs', () => {
  const file = (content_type: string, filename: string) => ({ kind: 'file', file: { content_type, filename } }) as unknown as Parameters<typeof docGlyph>[0];

  it('picks a viewer by content type, then by filename', () => {
    expect(viewerKind('application/pdf', 'a.pdf')).toBe('pdf');
    expect(viewerKind('image/png', 'a.png')).toBe('image');
    expect(viewerKind('image/svg+xml', 'a.svg')).toBe('other');
    expect(viewerKind('text/csv', 'quote.csv')).toBe('table');
    expect(viewerKind('application/octet-stream', 'quote.csv')).toBe('table');
    expect(viewerKind('application/octet-stream', 'notes.md')).toBe('text');
    expect(viewerKind('application/json', 'x')).toBe('text');
    expect(viewerKind('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'q.xlsx')).toBe('xlsx');
    expect(viewerKind('application/octet-stream', 'scope.docx')).toBe('docx');
    expect(viewerKind('application/msword', 'old.doc')).toBe('other');
    expect(viewerKind('application/acad', 'floor-plan.dwg')).toBe('other');
  });

  it('gives files a glyph by type and pages a page', () => {
    expect(docGlyph({ kind: 'page', file: null })).toBe('📄');
    expect(docGlyph({ kind: 'file', file: null })).toBe('📎');
    expect(docGlyph(file('application/pdf', 'a.pdf'))).toBe('📕');
    expect(docGlyph(file('image/jpeg', 'a.jpg'))).toBe('🖼️');
    expect(docGlyph(file('application/octet-stream', 'quote.xlsx'))).toBe('📊');
    expect(docGlyph(file('application/octet-stream', 'floor-plan.dwg'))).toBe('📐');
    expect(docGlyph(file('application/zip', 'x.zip'))).toBe('🗜️');
  });

  it('parses CSV with quotes and TSV', () => {
    expect(parseTable('a,b\n"x, y","say ""hi"""\n')).toEqual([
      ['a', 'b'],
      ['x, y', 'say "hi"'],
    ]);
    expect(parseTable('a\tb\r\n1\t2')).toEqual([
      ['a', 'b'],
      ['1', '2'],
    ]);
    expect(parseTable('a,b\n1,2\n3,4', 2)).toHaveLength(2);
  });
});

describe('threads and the discussion feed', () => {
  const c = (id: string, over: Partial<DocComment> = {}): DocComment =>
    ({
      id,
      doc_id: 'd',
      parent_id: null,
      surface: 'discussion',
      author_id: 'me',
      author_handle: 'me',
      author_kind: 'person',
      body_md: id,
      created_at: `2026-11-01T10:0${id.length}:00Z`,
      edited_at: null,
      mentions: [],
      anchor_revision: 1,
      current_revision: 1,
      outdated: false,
      anchor_start: null,
      anchor_end: null,
      anchor_quote: null,
      anchor_page: null,
      anchor_x: null,
      anchor_y: null,
      suggestion_md: null,
      accepted_at: null,
      assignee_id: null,
      resolved_at: null,
      resolved_by_id: null,
      ...over,
    }) as DocComment;

  it('groups replies under their root and filters by surface', () => {
    const all = [c('a'), c('bb', { surface: 'review', anchor_quote: 'x' }), c('ccc', { parent_id: 'bb', surface: 'review' }), c('dddd', { parent_id: 'gone' })];
    const review = threadsOf(all, 'review');
    expect(review.map((t) => t.root.id)).toEqual(['bb']);
    expect(review[0].replies.map((r) => r.id)).toEqual(['ccc']);
    expect(threadsOf(all, 'discussion').map((t) => t.root.id)).toEqual(['a', 'dddd']);
  });

  it('labels anchors', () => {
    expect(anchorLabel({ anchor_quote: 'Seat  12 guests', anchor_page: null, anchor_x: null, anchor_y: null })).toBe('“Seat 12 guests”');
    expect(anchorLabel({ anchor_quote: null, anchor_page: 3, anchor_x: 0.2, anchor_y: 0.4 })).toBe('on page 3, a spot');
    expect(anchorLabel({ anchor_quote: null, anchor_page: null, anchor_x: null, anchor_y: null })).toBe('on the document');
  });

  it('phrases activity lines', () => {
    const name = () => 'Agent';
    const ev = (type: string, after: unknown, field: string | null = null, before: unknown = null): DocEvent => ({ id: 'e', type, actor_id: 'c', field, before, after, at: '2026-11-01T10:00:00Z' });
    expect(docEventLine(ev('doc.revision_added', { revision_no: 2, note: 'Fixed typos', filename: 'a.pdf' }), name)).toEqual({ text: 'Agent uploaded rev 2', detail: 'a.pdf · Fixed typos' });
    expect(docEventLine(ev('doc.revision_added', { revision_no: 3, restored_from: 1, note: 'Restored revision 1' }), name).text).toBe('Agent made revision 1 current again as rev 3');
    expect(docEventLine(ev('doc.updated', 'B', 'title', 'A'), name)).toEqual({ text: 'Agent renamed this', detail: '“A” → “B”' });
    expect(docEventLine(ev('doc.comment_resolved', { reason: 'accepted' }), name).text).toBe('Agent accepted a suggestion');
    expect(docEventLine(ev('doc.revision_pinned', { revision_no: 1, note: 'Signed copy' }), name)).toEqual({ text: 'Agent marked rev 1 IN USE', detail: 'Signed copy' });
    expect(docEventLine(ev('doc.revision_unpinned', null), name).text).toBe('Agent cleared IN USE');
  });

  it('folds consecutive events between threads', () => {
    const t = (id: string, at: string) => ({ root: c(id, { created_at: at }), replies: [] });
    const e = (id: string, at: string): DocEvent => ({ id, type: 'doc.updated', actor_id: 'x', field: 'title', before: null, after: null, at });
    const rows = feedRows([t('a', '2026-11-01T10:05:00Z')], [e('1', '2026-11-01T10:00:00Z'), e('2', '2026-11-01T10:01:00Z'), e('3', '2026-11-01T10:09:00Z')]);
    expect(rows.map((r) => (r.kind === 'activity' ? `act${r.events.length}` : r.thread.root.id))).toEqual(['act2', 'a', 'act1']);
  });
});

describe('side-by-side diff', () => {
  it('aligns removed and added lines as changes, keeps context on both sides', () => {
    const diff = ['--- v1', '+++ v2', '@@ -1,3 +1,3 @@', ' a', '-b', '+B', ' c', '@@ -10,1 +10,2 @@', ' x', '+y'].join('\n');
    const rows = sideBySide(diff);
    expect(rows.map((r) => r.kind)).toEqual(['hunk', 'same', 'change', 'same', 'hunk', 'same', 'add']);
    expect(rows[2]).toEqual({ left: { no: 2, text: 'b' }, right: { no: 2, text: 'B' }, kind: 'change' });
    expect(rows[5].left).toEqual({ no: 10, text: 'x' });
    expect(rows[6]).toEqual({ left: null, right: { no: 11, text: 'y' }, kind: 'add' });
  });

  it('pairs an uneven block as changes then extras', () => {
    const rows = sideBySide('@@ -1,2 +1,1 @@\n-a\n-b\n+c');
    expect(rows.slice(1).map((r) => r.kind)).toEqual(['change', 'del']);
  });

  it('indexes cell changes', () => {
    const m = cellChanges({ name: 's', changed: [{ row: 1, col: 2, before: 'a', after: 'b' }], added_rows: [], removed_rows: [], rows: [], before_rows: [] });
    expect(m.get('1:2')?.after).toBe('b');
  });
});

describe('tree filters, sort and the hub', () => {
  const f = (id: string, over: Partial<DocSummary> = {}) => doc(id, over);
  const pdf = { id: 'r', doc_id: 'x', revision_no: 1, filename: 'a.pdf', content_type: 'application/pdf', size: 1, note: 'n', uploaded_by: 'me', created_at: '', superseded_at: null, restored_from: null, url: '' };

  it('groups files by type', () => {
    expect(fileGroup(f('a'))).toBeNull();
    expect(fileGroup(f('b', { kind: 'file', file: null }))).toBe('other');
    expect(fileGroup(f('c', { kind: 'file', file: pdf }))).toBe('pdf');
    expect(fileGroup(f('d', { kind: 'file', file: { ...pdf, filename: 'q.xlsx', content_type: 'application/octet-stream' } }))).toBe('sheet');
    expect(fileGroup(f('e', { kind: 'file', file: { ...pdf, filename: 'p.png', content_type: 'image/png' } }))).toBe('image');
  });

  it('filters by kind, group and tag, keeping ancestors', () => {
    const all = [f('top'), f('spec', { kind: 'file', file: pdf, parent_id: 'top', tags: ['customer'] }), f('notes', { parent_id: 'top' }), f('other', { tags: ['customer'] })];
    expect(filterDocs(all, { kind: 'file' }).map((d) => d.id)).toEqual(['top', 'spec']);
    expect(filterDocs(all, { group: 'pdf' }).map((d) => d.id)).toEqual(['top', 'spec']);
    expect(filterDocs(all, { tag: 'customer' }).map((d) => d.id)).toEqual(['top', 'spec', 'other']);
    expect(filterDocs(all, {})).toBe(all);
  });

  it('sorts every level by name or by newest change', () => {
    const nodes = buildTree([f('b', { updated_at: '2026-01-02' }), f('a', { updated_at: '2026-01-01' }), f('a1', { parent_id: 'a', updated_at: '2026-01-01' }), f('a2', { parent_id: 'a', updated_at: '2026-01-03' })]).get('ws')!;
    expect(sortTree(nodes, 'name').map((n) => n.doc.id)).toEqual(['a', 'b']);
    expect(sortTree(nodes, 'updated').map((n) => n.doc.id)).toEqual(['b', 'a']);
    expect(sortTree(nodes, 'updated')[1].children.map((n) => n.doc.id)).toEqual(['a2', 'a1']);
    expect(sortTree(nodes, 'tree')).toBe(nodes);
  });

  it('counts tags most used first', () => {
    expect(tagCounts([f('a', { tags: ['x', 'y'] }), f('b', { tags: ['y'] })])).toEqual([
      { name: 'y', count: 2 },
      { name: 'x', count: 1 },
    ]);
  });
});

describe('drawer entries', () => {
  const e = (id: string, over: Partial<DocSummary> = {}) => ({ ...doc(id, over), children_count: 0 });
  const pdf = { id: 'r', doc_id: 'x', revision_no: 1, filename: 'contract.pdf', content_type: 'application/pdf', size: 1, note: 'n', uploaded_by: 'me', created_at: '', superseded_at: null, restored_from: null, url: '' };
  const all = [e('Folder', { kind: 'folder' }), e('Spec', { kind: 'file', file: pdf, tags: ['customer'] }), e('Plan'), e('Photo', { kind: 'file', file: { ...pdf, filename: 'p.png', content_type: 'image/png' } })];

  it('keeps folders, filters by group, tag and text', () => {
    expect(filterEntries(all, { group: 'pdf' }).map((x) => x.id)).toEqual(['Folder', 'Spec']);
    expect(filterEntries(all, { group: 'page' }).map((x) => x.id)).toEqual(['Folder', 'Plan']);
    expect(filterEntries(all, { tag: 'customer' }).map((x) => x.id)).toEqual(['Folder', 'Spec']);
    expect(filterEntries(all, { q: 'contract' }).map((x) => x.id)).toEqual(['Spec']);
    expect(filterEntries(all, {})).toHaveLength(4);
  });

  it('gives folders a folder glyph', () => {
    expect(docGlyph({ kind: 'folder', file: null })).toBe('📁');
  });
});

describe('notIndexed', () => {
  it('marks only a normally indexed type whose text the server did not read', () => {
    const xlsx = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    expect(notIndexed({ content_type: xlsx, filename: 'q.xlsx', has_text: false })).toBe(true);
    expect(notIndexed({ content_type: 'application/pdf', filename: 'a.pdf', has_text: false })).toBe(true);
    expect(notIndexed({ content_type: xlsx, filename: 'q.xlsx', has_text: true })).toBe(false);
    expect(notIndexed({ content_type: 'image/png', filename: 'a.png', has_text: false })).toBe(false);
    expect(notIndexed({ content_type: xlsx, filename: 'q.xlsx' })).toBe(false); // an older server
  });
});
