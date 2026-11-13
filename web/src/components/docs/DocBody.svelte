<script lang="ts">
  // The page body, edited in place: what you see is what you type into. Headings, lists,
  // checklists, tables, quotes, links, images and live task cards stay formatted while you
  // write; nobody sees Markdown. The page is still stored as Markdown (agents and the API read
  // and write it): lib/richdoc.ts turns the screen back into text, and blocks nobody touched keep
  // their exact source. It saves itself a moment after you stop (lib/docs.svelte.ts).
  //
  // "/" at the start of a line opens the block menu; "# ", "- ", "1. ", "[] " and "> " at the
  // start of a line turn it into a heading, list, checklist or quote. Select text to bold it,
  // link it, turn it into a task or comment on it. Paste or drop an image to upload it. The
  // toolbar does the same on a phone and a PC.
  import { mount, onDestroy, tick, unmount } from 'svelte';
  import { statusColor } from '../../lib/activity';
  import { renderDoc, escape, type Block, type DocCtx } from '../../lib/docmd';
  import { matchCommands, occurrenceAt, taskIdFromLink, TABLE_BLOCK, titleFrom, topOf, type SlashCommand } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { post } from '../../lib/api';
  import { drawEditor, editorMarkdown, isBlockNode, lockAtoms, normalizeChecklists, type Drawn } from '../../lib/richdoc';
  import { board } from '../../lib/store.svelte';
  import { dateFormatter } from '../../lib/timezone';
  import type { Item } from '../../lib/types';
  import Icon from '../Icon.svelte';
  import NewTaskForm from './NewTaskForm.svelte';
  import Picker, { type PickerOption } from './Picker.svelte';
  import TaskEmbed from './TaskEmbed.svelte';

  let { docId }: { docId: string } = $props();

  const text = $derived(docs.draft?.body_md ?? '');
  let ed = $state<HTMLElement>();
  let fileInput = $state<HTMLInputElement>();
  let shell = $state<HTMLElement>();
  let focused = $state(false);
  let empty = $state(false);

  const ctx: DocCtx = {
    task: (id) => {
      const t = board.items[id] ?? docs.current?.tasks.find((x) => x.id === id);
      if (!t) return null;
      return { title: t.title, done: t.status?.kind === 'done' || t.status?.kind === 'cancelled', color: statusColor(t.status) };
    },
    doc: (id) => {
      const d = docs.byId[id] ?? docs.current?.docs.find((x) => x.id === id);
      return d ? { title: d.title, icon: d.icon } : null;
    },
  };
  // Re-rendered when the text, a task or a page title changes (the ctx reads the stores).
  const blocks = $derived.by((): Block[] => renderDoc(text, ctx, { split: true }));

  // --- drawing the page into the editor ---------------------------------------------------

  let drawn: Drawn[] = [];
  /** The text the editor's content stands for right now. */
  let shown: string | null = null;
  let embeds: ReturnType<typeof mount>[] = [];

  /** Where the caret is, as a count of characters from the start (survives a redraw). */
  function caretOffset(): number | null {
    const sel = window.getSelection();
    if (!ed || !sel?.rangeCount) return null;
    const r = sel.getRangeAt(0);
    if (!ed.contains(r.endContainer)) return null;
    const pre = document.createRange();
    pre.selectNodeContents(ed);
    pre.setEnd(r.endContainer, r.endOffset);
    return pre.toString().length;
  }

  function placeCaret(offset: number) {
    if (!ed) return;
    const walk = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
    let left = offset;
    let node: Node | null;
    let lastText: Text | null = null;
    while ((node = walk.nextNode())) {
      const t = node as Text;
      const atom = t.parentElement?.closest('[contenteditable="false"]');
      if (left <= t.length) {
        if (!atom || !ed.contains(atom)) return setCaret(t, left);
        // Inside a chip or card: just after it.
        const r = document.createRange();
        r.setStartAfter(atom);
        r.collapse(true);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(r);
        return;
      }
      if (!atom) lastText = t;
      left -= t.length;
    }
    if (lastText) setCaret(lastText, lastText.length);
  }

  /** The browser can leave a list or table inside a paragraph; lift it out (caret kept). */
  function fixNesting() {
    if (!ed) return;
    const bad = Array.from(ed.querySelectorAll<HTMLElement>('p > ul, p > ol, p > div, p > table, p > blockquote, p > pre, p > hr, p > h1, p > h2, p > h3, p > h4, p > h5, p > h6'));
    // Text typed straight into the page (after leaving a list) goes into a paragraph.
    const loose = [ed, ...Array.from(ed.children).filter((c) => c.classList.contains('md-block'))].flatMap((box) =>
      Array.from(box.childNodes).filter((n) => !isBlockNode(n) && (n.nodeType === 1 || n.textContent?.trim())),
    );
    if (!bad.length && !loose.length) return;
    // The caret's node moves along with its block, so it can be put back exactly.
    const r = selection();
    const keep = r ? { node: r.startContainer, offset: r.startOffset } : null;
    for (const n of loose) {
      if (!n.parentNode) continue;
      const prev = n.previousSibling as HTMLElement | null;
      if (prev?.dataset?.loose) prev.appendChild(n);
      else {
        const p = document.createElement('p');
        p.dataset.loose = '1';
        n.parentNode.insertBefore(p, n);
        p.appendChild(n);
      }
    }
    for (const p of Array.from(ed.querySelectorAll<HTMLElement>('p[data-loose]'))) delete p.dataset.loose;
    for (const el of bad) {
      const p = el.parentElement;
      if (!p || p.tagName !== 'P' || !ed.contains(p)) continue;
      const before = document.createElement('p');
      const after = document.createElement('p');
      let seen = false;
      for (const c of Array.from(p.childNodes)) {
        if (c === el) seen = true;
        else (seen ? after : before).appendChild(c);
      }
      const parts: Node[] = [];
      if (before.textContent?.trim() || before.querySelector('img')) parts.push(before);
      parts.push(el);
      if (after.textContent?.trim() || after.querySelector('img')) parts.push(after);
      p.replaceWith(...parts);
    }
    if (keep && ed.contains(keep.node)) setCaret(keep.node, keep.offset);
  }

  function setCaret(node: Node, offset: number) {
    const r = document.createRange();
    r.setStart(node, offset);
    r.collapse(true);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(r);
  }

  /** Something to type into after a table, card or divider at the end of the page. */
  function ensureTail() {
    if (!ed) return;
    const last = ed.lastElementChild as HTMLElement | null;
    const lastInner = last?.classList.contains('md-block') ? (last.lastElementChild as HTMLElement | null) : last;
    if (!last || last.classList.contains('embed-row') || lastInner?.matches('hr, .md-table-wrap, table, pre, img')) {
      const p = document.createElement('p');
      p.appendChild(document.createElement('br'));
      ed.appendChild(p);
    }
  }

  function loadImages() {
    for (const img of ed?.querySelectorAll<HTMLImageElement>('img[data-file]:not([src])') ?? []) {
      void docs.fileUrl(img.dataset.file!).then(
        (url) => (img.src = url),
        () => img.classList.add('broken'),
      );
    }
  }

  function updateEmpty() {
    empty = !!ed && !ed.textContent?.trim() && !ed.querySelector('img, hr, table, .embed-row, input');
  }

  function draw() {
    if (!ed) return;
    const caret = focused ? caretOffset() : null;
    for (const m of embeds) void unmount(m);
    embeds = [];
    drawn = drawEditor(ed, blocks);
    shown = text;
    for (const row of ed.querySelectorAll<HTMLElement>('.embed-row[data-task]')) {
      embeds.push(mount(TaskEmbed, { target: row, props: { id: row.dataset.task! } }));
    }
    ensureTail();
    loadImages();
    updateEmpty();
    if (caret !== null) placeCaret(caret);
  }

  // Draw when the page changes from outside (an agent, another device, a restore) and, while
  // nobody is typing here, when a task or page title in it changes.
  let stale = false;
  $effect(() => {
    void blocks;
    if (!ed) return;
    if (text !== shown) draw();
    else if (!focused) draw();
    else stale = true;
  });

  /** Read the screen back into the page text and save it. */
  function sync() {
    if (!ed) return;
    fixNesting();
    normalizeChecklists(ed);
    lockAtoms(ed);
    caretPastBox();
    updateEmpty();
    const md = editorMarkdown(ed, drawn, shown ?? text);
    shown = md;
    if (md !== text) docs.edit({ body_md: md });
  }

  // --- selection --------------------------------------------------------------------------

  let saved: Range | null = null;

  function inEditor(n: Node | null): boolean {
    return !!n && !!ed && ed.contains(n);
  }

  function selection(): Range | null {
    const sel = window.getSelection();
    if (!sel?.rangeCount) return null;
    const r = sel.getRangeAt(0);
    return inEditor(r.commonAncestorContainer) ? r : null;
  }

  /** The element the caret is in, closest of `sel`. */
  function at<T extends Element = HTMLElement>(sel: string): T | null {
    const r = selection() ?? saved;
    if (!r) return null;
    const n = r.startContainer;
    const el: Element | null | undefined = (n.nodeType === 1 ? (n as Element) : n.parentElement)?.closest(sel);
    return el && inEditor(el) && el !== ed ? (el as unknown as T) : null;
  }

  function block(): HTMLElement | null {
    return at('p, h1, h2, h3, h4, h5, h6, li, pre, blockquote, td, th, div:not(.md-block):not(.md-table-wrap)');
  }

  /** The editor's top-level child that holds the caret (or the last one). */
  function topLevel(): HTMLElement | null {
    if (!ed) return null;
    const r = selection() ?? saved;
    let n: Node | null = r?.startContainer ?? null;
    while (n && n.parentNode !== ed) n = n.parentNode;
    return (n as HTMLElement | null) ?? (ed.lastElementChild as HTMLElement | null);
  }

  /** Focus the editor with the caret where it last was. */
  function restore() {
    if (!ed) return;
    if (document.activeElement !== ed) ed.focus({ preventScroll: true });
    if (selection()) return;
    const sel = window.getSelection();
    sel?.removeAllRanges();
    if (saved && inEditor(saved.startContainer)) sel?.addRange(saved);
    else placeCaret(Number.MAX_SAFE_INTEGER);
  }

  /** After the browser splits a checklist entry, keep the caret after the new entry's box. */
  function caretPastBox() {
    const sel = window.getSelection();
    if (!sel?.rangeCount || !sel.isCollapsed) return;
    const r = sel.getRangeAt(0);
    const li = r.startContainer as HTMLElement;
    if (li.nodeType === 1 && li.classList?.contains('md-li-check') && r.startOffset === 0) setCaret(li, 1);
  }

  // --- toolbar ----------------------------------------------------------------------------

  function exec(cmd: string, value?: string) {
    document.execCommand(cmd, false, value);
  }

  function run(fn: () => void) {
    restore();
    fn();
    sync();
  }

  function heading(tag: 'h1' | 'h2' | 'h3') {
    run(() => exec('formatBlock', block()?.tagName.toLowerCase() === tag ? 'p' : tag));
  }

  function inlineCode() {
    run(() => {
      const code = at('code');
      if (code && !code.closest('pre')) {
        code.replaceWith(...Array.from(code.childNodes));
        return;
      }
      const picked = window.getSelection()?.toString() ?? '';
      exec('insertHTML', `<code>${escape(picked || 'code')}</code>`);
    });
  }

  function list(kind: 'ul' | 'ol') {
    run(() => {
      const ul = at<HTMLElement>('ul, ol');
      if (ul?.classList.contains('checks') && kind === 'ul') {
        ul.classList.remove('checks');
        for (const box of ul.querySelectorAll('input')) box.remove();
        return;
      }
      exec(kind === 'ul' ? 'insertUnorderedList' : 'insertOrderedList');
    });
  }

  function checklist() {
    run(() => {
      const ul = at<HTMLElement>('ul, ol');
      if (ul?.classList.contains('checks')) {
        for (const box of ul.querySelectorAll(':scope > li > input')) box.remove();
        ul.classList.remove('checks');
        exec('insertUnorderedList');
        return;
      }
      if (!ul || ul.tagName !== 'UL') exec('insertUnorderedList');
      at<HTMLElement>('ul')?.classList.add('checks');
      normalizeChecklists(ed!);
      caretPastBox();
    });
  }

  function quote(callout = false) {
    run(() => {
      const q = at<HTMLElement>('blockquote');
      if (q) {
        const p = document.createElement('p');
        const hasBlocks = Array.from(q.children).some((c) => /^(P|DIV|H\d|UL|OL)$/.test(c.tagName));
        if (hasBlocks) q.replaceWith(...Array.from(q.childNodes));
        else {
          p.append(...Array.from(q.childNodes));
          q.replaceWith(p);
          setCaret(p, p.childNodes.length);
        }
        return;
      }
      exec('formatBlock', 'blockquote');
      if (callout) exec('insertText', '💡 ');
    });
  }

  /** Put new blocks (written as Markdown) after the block that holds the caret. */
  function insertBlocks(md: string, then?: (added: HTMLElement[]) => void) {
    if (!ed) return;
    restore();
    const tmp = document.createElement('div');
    drawEditor(tmp, renderDoc(md, ctx, { split: true }));
    const added = Array.from(tmp.children) as HTMLElement[];
    for (const el of added) delete el.dataset.b;
    const anchor = topLevel();
    // An empty line the caret sits on is replaced, not kept above the new block.
    const blank = anchor && !anchor.textContent?.trim() && !anchor.querySelector('img, input, hr, table') && !anchor.classList.contains('embed-row');
    if (anchor && blank) anchor.replaceWith(...added);
    else if (anchor) anchor.after(...added);
    else ed.append(...added);
    sync();
    const needsDraw = added.some((el) => el.querySelector('img') || el.classList.contains('embed-row'));
    if (needsDraw) draw();
    ensureTail();
    then?.(added);
  }

  function insertTable() {
    insertBlocks(TABLE_BLOCK, (added) => {
      const th = added[0]?.querySelector('th');
      if (!th) return;
      const r = document.createRange();
      r.selectNodeContents(th);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(r);
    });
  }

  function insertDivider() {
    insertBlocks('---');
  }

  function insertCode() {
    insertBlocks('```\n\n```', (added) => {
      const code = added[0]?.querySelector('code');
      if (!code) return;
      code.appendChild(document.createElement('br'));
      setCaret(code, 0);
    });
  }

  function insertDate() {
    const label = dateFormatter('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date());
    run(() => exec('insertText', label));
  }

  // --- links ------------------------------------------------------------------------------

  let linkForm = $state<{ x: number; y: number; url: string } | null>(null);
  let linkRange: Range | null = null;

  function openLinkForm() {
    restore();
    const r = selection();
    linkRange = r?.cloneRange() ?? null;
    const existing = at<HTMLAnchorElement>('a[href]');
    const rect = r ? r.getBoundingClientRect() : ed!.getBoundingClientRect();
    linkForm = { ...rel(rect.left, rect.bottom + 6), url: existing?.getAttribute('href') ?? '' };
  }

  function applyLink(raw: string) {
    linkForm = null;
    let url = raw.trim();
    if (url && !/^(https?:|mailto:|obsidian:|#\/)/i.test(url)) url = `https://${url}`;
    ed?.focus({ preventScroll: true });
    if (linkRange) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(linkRange);
    }
    run(() => {
      const existing = at<HTMLAnchorElement>('a[href]');
      if (!url) {
        if (existing) existing.replaceWith(...Array.from(existing.childNodes));
        return;
      }
      if (existing) existing.setAttribute('href', url);
      else if (window.getSelection()?.isCollapsed) exec('insertHTML', `<a href="${escape(url)}">${escape(raw.trim())}</a>`);
      else exec('createLink', url);
    });
  }

  // --- slash menu -------------------------------------------------------------------------

  let slash = $state<{ query: string; x: number; y: number } | null>(null);
  let slashCursor = $state(0);
  const slashHits = $derived(slash ? matchCommands(slash.query) : []);

  /** The text of the caret's line before the caret. */
  function beforeCaret(): { text: string; range: Range } | null {
    const r = selection();
    const b = block();
    if (!r || !r.collapsed || !b) return null;
    const range = document.createRange();
    range.selectNodeContents(b);
    range.setEnd(r.endContainer, r.endOffset);
    return { text: range.toString().replace(/ /g, ' '), range };
  }

  function updateSlash() {
    const hit = beforeCaret();
    const m = hit ? /^\/([\w-]*)$/.exec(hit.text) : null;
    if (!m || !hit) {
      slash = null;
      return;
    }
    const rect = hit.range.getBoundingClientRect();
    if (!slash || slash.query !== m[1]) slashCursor = 0;
    slash = { query: m[1], ...rel(rect.left, rect.bottom + 4) };
  }

  /** Remove what was typed on this line before the caret ("/tab", "# "). */
  function clearTyped() {
    const hit = beforeCaret();
    if (!hit) return;
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(hit.range);
    exec('delete');
  }

  function runSlash(cmd: SlashCommand) {
    slash = null;
    clearTyped();
    sync();
    command(cmd.id);
  }

  function command(id: string) {
    saved = selection()?.cloneRange() ?? saved;
    switch (id) {
      case 'h1':
      case 'h2':
      case 'h3':
        return heading(id);
      case 'check':
        return checklist();
      case 'bullet':
        return list('ul');
      case 'number':
        return list('ol');
      case 'quote':
        return quote();
      case 'callout':
        return quote(true);
      case 'table':
        return insertTable();
      case 'code':
        return insertCode();
      case 'divider':
        return insertDivider();
      case 'date':
        return insertDate();
      case 'image':
        return fileInput?.click();
      case 'task':
        picking = 'task';
        return;
      case 'page':
        picking = 'page';
        return;
      case 'newtask':
        return openTaskForm('');
      case 'subpage':
        return void newSubpage();
    }
  }

  // Typing "# ", "- ", "1. ", "[] " or "> " at the start of a line formats it.
  const SHORTCUTS: Record<string, string> = { '# ': 'h1', '## ': 'h2', '### ': 'h3', '- ': 'bullet', '* ': 'bullet', '1. ': 'number', '[] ': 'check', '[ ] ': 'check', '> ': 'quote' };

  function shortcut(): boolean {
    const b = block();
    if (!b || !/^(P|DIV)$/.test(b.tagName) || b.closest('li, td, th, pre, blockquote')) return false;
    const hit = beforeCaret();
    const id = hit ? SHORTCUTS[hit.text] : undefined;
    if (!id || (b.textContent ?? '').replace(/ /g, ' ') !== hit!.text) return false;
    clearTyped();
    command(id);
    return true;
  }

  // --- pickers and inserts ----------------------------------------------------------------

  let picking = $state<'task' | 'page' | null>(null);

  const taskOptions = $derived.by((): PickerOption[] =>
    Object.values(board.items)
      .filter((i) => !i.archived_at)
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .map((i) => ({ id: i.id, label: i.title, hint: board.lists[i.list_id]?.name ?? '', icon: i.status?.kind === 'done' ? '✓' : '◯' })),
  );
  const pageOptions = $derived.by((): PickerOption[] =>
    docs.list.filter((d) => d.id !== docId).map((d) => ({ id: d.id, label: d.title, icon: d.icon ?? '📄' })),
  );

  function insertPageLink(id: string, title: string) {
    run(() => exec('insertHTML', `<a class="doc-link" href="#/docs/${id}" data-doc="${id}" contenteditable="false">${escape(title)}</a>&nbsp;`));
  }

  async function newSubpage() {
    const d = await docs.create({ parent_id: docId, title: 'Untitled' });
    if (!d) return;
    insertPageLink(d.id, d.title);
    await docs.flush();
    location.hash = `#/docs/${d.id}`;
  }

  async function uploadFiles(files: File[]) {
    for (const f of files) {
      const out = await docs.upload(docId, f);
      if (out) insertBlocks(out.markdown);
    }
  }

  // --- create task from a selection -------------------------------------------------------

  let taskForm = $state<{ title: string; replace: string | null; occurrence: number; x: number; y: number } | null>(null);
  let bubble = $state<{ x: number; y: number; text: string } | null>(null);

  const homeList = $derived.by(() => {
    const top = topOf(docId, docs.byId);
    return top?.list_id ?? null;
  });

  function rel(x: number, y: number) {
    const r = shell?.getBoundingClientRect();
    return { x: Math.max(8, Math.min(x - (r?.left ?? 0), (r?.width ?? 600) - 300)), y: y - (r?.top ?? 0) };
  }

  function selectionChanged() {
    const r = selection();
    if (r) saved = r.cloneRange();
    if (focused) updateSlash();
    const sel = window.getSelection();
    const picked = sel?.toString() ?? '';
    if (!r || !picked.trim() || slash) return void (bubble = null);
    const rect = r.getBoundingClientRect();
    bubble = { ...rel(rect.left, rect.top - 40), text: picked };
  }

  function openTaskForm(selected: string) {
    const r = selection() ?? saved;
    const rect = r?.getBoundingClientRect();
    const anchor = bubble ?? (rect ? rel(rect.left, rect.top) : { x: 16, y: 16 });
    // Only text that is in the page can become the card; it is looked up in the page text.
    let replace: string | null = selected.trim() ? selected.trim() : null;
    let occurrence = 0;
    if (replace && text.includes(replace)) {
      const b = block();
      const before = b ? text.indexOf((b.textContent ?? '').trim().slice(0, 40)) : -1;
      occurrence = before >= 0 ? occurrenceAt(text, replace, before) : 0;
    } else replace = null;
    taskForm = { title: titleFrom(selected), replace, occurrence, x: anchor.x, y: anchor.y + 44 };
    bubble = null;
  }

  async function createTask(title: string, listId: string) {
    const form = taskForm;
    if (!form) return;
    if (form.replace) {
      const item = await docs.createTask(docId, { title, list_id: listId, replace: form.replace, occurrence: form.occurrence });
      if (!item) return;
    } else {
      // "/New task" or no matching text: file it and drop its card at the cursor.
      const item = await post<Item>('/items', { list_id: listId, title, body_md: `From doc [[doc:${docId}]]` }).catch((e) => {
        board.flash((e as Error).message);
        return null;
      });
      if (!item) return;
      board.items[item.id] ??= item;
      insertBlocks(`[[task:${item.id}]]`);
    }
    taskForm = null;
    board.flash(`Task “${title}” created`);
  }

  // --- keys, clicks, paste, drop ----------------------------------------------------------

  function keys(e: KeyboardEvent) {
    const mod = e.metaKey || e.ctrlKey;
    if (slash && slashHits.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        slashCursor = (slashCursor + (e.key === 'ArrowDown' ? 1 : -1) + slashHits.length) % slashHits.length;
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        runSlash(slashHits[slashCursor]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        slash = null;
        return;
      }
    }
    const key = e.key.toLowerCase();
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      ed?.blur();
    } else if (mod && key === 's') {
      e.preventDefault();
      void docs.flush();
    } else if (mod && key === 'b') {
      e.preventDefault();
      run(() => exec('bold'));
    } else if (mod && key === 'i') {
      e.preventDefault();
      run(() => exec('italic'));
    } else if (mod && e.shiftKey && key === 'x') {
      e.preventDefault();
      run(() => exec('strikeThrough'));
    } else if (mod && key === 'k') {
      e.preventDefault();
      e.stopPropagation();
      openLinkForm();
    } else if (mod && e.key === 'Enter') {
      e.preventDefault();
      ed?.blur();
    } else if (e.key === 'Tab') {
      e.preventDefault();
      const cell = at<HTMLTableCellElement>('td, th');
      if (cell) {
        const all = Array.from(cell.closest('table')!.querySelectorAll<HTMLTableCellElement>('th, td'));
        const next = all[all.indexOf(cell) + (e.shiftKey ? -1 : 1)];
        if (next) {
          const r = document.createRange();
          r.selectNodeContents(next);
          window.getSelection()?.removeAllRanges();
          window.getSelection()?.addRange(r);
        }
      } else if (at('li')) run(() => exec(e.shiftKey ? 'outdent' : 'indent'));
    } else if (e.key === 'Enter' && !e.shiftKey) {
      if (at('pre')) {
        e.preventDefault();
        run(() => exec('insertLineBreak'));
        return;
      }
      const li = at<HTMLElement>('li.md-li-check');
      if (li && !li.textContent?.trim()) {
        // Enter on an empty checklist entry ends the checklist.
        e.preventDefault();
        run(() => {
          for (const box of li.querySelectorAll('input')) box.remove();
          li.classList.remove('md-li-check', 'ticked');
          exec('insertUnorderedList');
        });
      }
    } else if (e.key === 'Backspace') {
      const li = at<HTMLElement>('li.md-li-check');
      const hit = beforeCaret();
      if (li && hit && !hit.text) {
        // Backspace at the start of a checklist entry makes it a plain line.
        e.preventDefault();
        run(() => {
          for (const box of li.querySelectorAll('input')) box.remove();
          li.classList.remove('md-li-check', 'ticked');
          exec('insertUnorderedList');
        });
      }
    }
  }

  let pasted = false;

  function onInput(e: Event) {
    const ie = e as InputEvent;
    if (ie.inputType === 'insertText' && ie.data === ' ' && shortcut()) return;
    sync();
    updateSlash();
    if (pasted) {
      // Pasted content shows the way the page stores it (only the formatting pages support).
      pasted = false;
      draw();
    }
  }

  function paste(e: ClipboardEvent) {
    const files = [...(e.clipboardData?.files ?? [])];
    if (files.length) {
      e.preventDefault();
      void uploadFiles(files);
      return;
    }
    const plain = e.clipboardData?.getData('text/plain') ?? '';
    const id = taskIdFromLink(plain);
    if (id) {
      e.preventDefault();
      insertBlocks(`[[task:${id}]]`);
      return;
    }
    if (!e.clipboardData?.types.includes('text/html')) {
      // Plain text goes in as typed text, line breaks and all.
      e.preventDefault();
      exec('insertText', plain);
      return;
    }
    pasted = true;
  }

  let wasFocused = false;

  function click(e: MouseEvent) {
    const t = e.target as HTMLElement;
    if (t instanceof HTMLInputElement && t.type === 'checkbox') {
      t.toggleAttribute('checked', t.checked);
      sync();
      void docs.flush();
      return;
    }
    const chip = t.closest<HTMLElement>('.task-chip[data-task]');
    if (chip) {
      e.preventDefault();
      void board.open(chip.dataset.task!);
      return;
    }
    const link = t.closest<HTMLAnchorElement>('a[href]');
    if (link && (link.classList.contains('doc-link') || e.ctrlKey || e.metaKey || !wasFocused)) {
      // Reading: a link opens. Typing: a click puts the caret in it (Ctrl/⌘-click opens).
      e.preventDefault();
      const href = link.getAttribute('href')!;
      if (href.startsWith('#')) location.hash = href;
      else window.open(href, '_blank', 'noopener,noreferrer');
    }
  }

  let dropping = $state(false);
  function drop(e: DragEvent) {
    const files = [...(e.dataTransfer?.files ?? [])];
    dropping = false;
    if (!files.length) return;
    e.preventDefault();
    const pos = document.caretRangeFromPoint?.(e.clientX, e.clientY);
    if (pos && inEditor(pos.startContainer)) saved = pos;
    void uploadFiles(files);
  }

  function blur(e: FocusEvent) {
    const to = e.relatedTarget as HTMLElement | null;
    if (to?.closest('.doc-toolbar, .slash-menu, .doc-bubble, .doc-linkform')) return;
    void docs.flush();
    setTimeout(() => {
      if (document.activeElement === ed || picking || taskForm || linkForm) return;
      focused = false;
      slash = null;
      // Show exactly what was saved (tidied formatting, fresh task titles).
      stale = false;
      draw();
    }, 120);
  }

  onDestroy(() => {
    for (const m of embeds) void unmount(m);
    void docs.flush();
  });

  const mouse = (e: MouseEvent) => e.preventDefault();
</script>

<svelte:document onselectionchange={selectionChanged} />

<div class="doc-body" bind:this={shell} class:dropping>
  <div class="doc-toolbar" role="toolbar" aria-label="Formatting">
    <button title="Heading 1" onmousedown={mouse} onclick={() => heading('h1')}>H1</button>
    <button title="Heading 2" onmousedown={mouse} onclick={() => heading('h2')}>H2</button>
    <button title="Heading 3" onmousedown={mouse} onclick={() => heading('h3')}>H3</button>
    <span class="sep"></span>
    <button title="Bold (Ctrl+B)" onmousedown={mouse} onclick={() => run(() => exec('bold'))}><b>B</b></button>
    <button title="Italic (Ctrl+I)" onmousedown={mouse} onclick={() => run(() => exec('italic'))}><i>I</i></button>
    <button title="Strikethrough (Ctrl+Shift+X)" onmousedown={mouse} onclick={() => run(() => exec('strikeThrough'))}><s>S</s></button>
    <button title="Code" onmousedown={mouse} onclick={inlineCode}>&lt;/&gt;</button>
    <button title="Link (Ctrl+K)" onmousedown={mouse} onclick={openLinkForm}><Icon name="link" size={14} /></button>
    <span class="sep"></span>
    <button title="Checklist" onmousedown={mouse} onclick={checklist}><Icon name="checklist" size={14} /></button>
    <button title="Bulleted list" onmousedown={mouse} onclick={() => list('ul')}><Icon name="list" size={14} /></button>
    <button title="Numbered list" onmousedown={mouse} onclick={() => list('ol')}>1.</button>
    <button title="Quote" onmousedown={mouse} onclick={() => quote()}>❝</button>
    <button title="Table" onmousedown={mouse} onclick={insertTable}>⊞</button>
    <button title="Divider" onmousedown={mouse} onclick={insertDivider}>—</button>
    <span class="sep"></span>
    <button title="Upload an image" onmousedown={mouse} onclick={() => fileInput?.click()}><Icon name="clip" size={14} /></button>
    <button title="Embed a task" onmousedown={mouse} onclick={() => (picking = 'task')}><Icon name="subtask" size={14} /></button>
    <button title="Link to a page" onmousedown={mouse} onclick={() => (picking = 'page')}><Icon name="open" size={14} /></button>
    <button
      class="mk-task"
      title="Create a task from the selected text"
      onmousedown={mouse}
      onclick={() => openTaskForm(window.getSelection()?.toString() ?? '')}><Icon name="plus" size={14} /><span>Task</span></button
    >
  </div>

  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    bind:this={ed}
    class="doc-rendered doc-rich md"
    class:empty
    contenteditable="true"
    role="textbox"
    tabindex="0"
    aria-label="Page text"
    aria-multiline="true"
    spellcheck="true"
    data-placeholder="Start writing. Type / for headings, checklists, tables, images and tasks."
    onpointerdown={() => (wasFocused = document.activeElement === ed)}
    onfocus={() => {
      focused = true;
      document.execCommand('defaultParagraphSeparator', false, 'p');
      document.execCommand('styleWithCSS', false, 'false');
    }}
    onblur={blur}
    oninput={onInput}
    onkeydown={keys}
    onclick={click}
    onpaste={paste}
    ondragover={(e) => {
      if (e.dataTransfer?.types.includes('Files')) {
        e.preventDefault();
        dropping = true;
      }
    }}
    ondragleave={() => (dropping = false)}
    ondrop={drop}
  ></div>

  {#if slash && slashHits.length}
    <div class="slash-menu" role="listbox" aria-label="Insert" style="left:{slash.x}px;top:{slash.y}px">
      {#each slashHits as c, n (c.id)}
        <button
          role="option"
          aria-selected={n === slashCursor}
          class:active={n === slashCursor}
          tabindex="-1"
          onmousedown={mouse}
          onmouseenter={() => (slashCursor = n)}
          onclick={() => runSlash(c)}
        >
          <span class="sl-icon">{c.icon}</span>
          <span class="sl-label">{c.label}</span>
          <span class="sl-hint">{c.hint}</span>
        </button>
      {/each}
    </div>
  {/if}

  {#if bubble && !taskForm && !linkForm}
    <div class="doc-bubble" style="left:{bubble.x}px;top:{bubble.y}px">
      <button title="Bold" onmousedown={mouse} onclick={() => run(() => exec('bold'))}><b>B</b></button>
      <button title="Italic" onmousedown={mouse} onclick={() => run(() => exec('italic'))}><i>I</i></button>
      <button title="Link" onmousedown={mouse} onclick={openLinkForm}><Icon name="link" size={13} /></button>
      <button onmousedown={mouse} onclick={() => openTaskForm(bubble!.text)}><Icon name="plus" size={13} /> Create task</button>
      <button
        onmousedown={mouse}
        onclick={() => {
          const quote = bubble!.text.trim();
          // The quote must be page text for Accept to find it; it is looked up in the page.
          const start = text.includes(quote) ? text.indexOf(quote) : null;
          docs.startReview(start !== null ? { quote, start, end: start + quote.length } : { quote });
          bubble = null;
          window.getSelection()?.removeAllRanges();
        }}><Icon name="comment" size={13} /> Comment</button
      >
    </div>
  {/if}

  {#if linkForm}
    <form
      class="doc-linkform"
      style="left:{linkForm.x}px;top:{linkForm.y}px"
      onsubmit={(e) => {
        e.preventDefault();
        applyLink(linkForm!.url);
      }}
    >
      <!-- svelte-ignore a11y_autofocus -->
      <input
        type="text"
        inputmode="url"
        aria-label="Link address"
        placeholder="Paste a link"
        bind:value={linkForm.url}
        autofocus
        onkeydown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault();
            e.stopPropagation();
            linkForm = null;
            restore();
          }
        }}
      />
      <button class="primary" type="submit">Apply</button>
      <button class="secondary" type="button" onclick={() => { linkForm = null; restore(); }}>Cancel</button>
    </form>
  {/if}

  {#if taskForm}
    <NewTaskForm
      title={taskForm.title}
      listId={homeList}
      x={taskForm.x}
      y={taskForm.y}
      oncreate={createTask}
      onclose={() => (taskForm = null)}
    />
  {/if}

  <input
    bind:this={fileInput}
    type="file"
    accept="image/*,application/pdf"
    multiple
    hidden
    onchange={(e) => {
      const input = e.target as HTMLInputElement;
      void uploadFiles([...(input.files ?? [])]);
      input.value = '';
    }}
  />
</div>

{#if picking === 'task'}
  <Picker
    title="Embed a task"
    placeholder="Find a task"
    options={taskOptions}
    onclose={() => (picking = null)}
    onpick={(id) => {
      picking = null;
      insertBlocks(`[[task:${id}]]`);
    }}
    oncreate={(t) => {
      picking = null;
      taskForm = { title: t, replace: null, occurrence: 0, x: 16, y: 60 };
    }}
    createLabel="New task"
  />
{:else if picking === 'page'}
  <Picker
    title="Link to a page"
    placeholder="Find a page"
    options={pageOptions}
    onclose={() => (picking = null)}
    onpick={(id) => {
      picking = null;
      const d = docs.byId[id] ?? docs.list.find((x) => x.id === id);
      insertPageLink(id, d ? `${d.icon ? `${d.icon} ` : ''}${d.title}` : 'Page');
    }}
  />
{/if}
