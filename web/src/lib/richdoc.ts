// The page editor edits the rendered page in place (contenteditable), and this file turns what
// is on screen back into the Markdown the page is stored as. The person editing never sees Markdown; agents
// and the API keep reading and writing it.
//
// The editor shows one wrapper per source block (renderDoc with `split`). When the page is
// saved, a wrapper whose content still means what it meant when it was drawn gives back its
// exact source text; only blocks someone changed are written out again. So typing in one
// paragraph never reformats the rest of a page an agent wrote.
//
// Everything here is plain DOM, so it runs the same in the browser and under happy-dom.

const FILE_PATH = (id: string) => `/api/v1/doc-files/${id}`;
const BLOCK_TAGS = new Set([
  'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'BLOCKQUOTE', 'PRE', 'HR', 'TABLE', 'FIGURE', 'SECTION',
]);

const isEl = (n: Node | null | undefined): n is HTMLElement => !!n && n.nodeType === 1;
/** The checkbox children of a list entry (no `:scope`: older engines lack it). */
const boxesOf = (li: Element): HTMLInputElement[] =>
  Array.from(li.children).filter((c): c is HTMLInputElement => c.tagName === 'INPUT' && (c as HTMLInputElement).type === 'checkbox');

export function isBlockNode(n: Node): boolean {
  if (!isEl(n)) return false;
  return BLOCK_TAGS.has(n.tagName) || n.classList.contains('embed-row') || n.classList.contains('md-table-wrap');
}

/** Move spaces outside a mark: `** bold **` is not bold in Markdown. */
function mark(inner: string, open: string, close = open): string {
  if (!inner.trim()) return inner;
  const lead = /^\s*/.exec(inner)![0];
  const trail = /\s*$/.exec(inner)![0];
  return `${lead}${open}${inner.trim()}${close}${trail}`;
}

function styleMarks(el: HTMLElement, inner: string): string {
  const st = el.style;
  if (!st) return inner;
  let out = inner;
  const weight = st.fontWeight;
  if (weight === 'bold' || Number(weight) >= 600) out = mark(out, '**');
  if (st.fontStyle === 'italic') out = mark(out, '_');
  if ((st.textDecoration || st.textDecorationLine || '').includes('line-through')) out = mark(out, '~~');
  return out;
}

function imageMd(img: HTMLImageElement): string {
  const alt = (img.getAttribute('alt') ?? '').replace(/[[\]]/g, '');
  const file = img.dataset.file;
  if (file) return `![${alt}](${FILE_PATH(file)})`;
  const src = img.getAttribute('src') ?? '';
  return /^https:/i.test(src) ? `![${alt}](${src})` : '';
}

/** The Markdown for inline content (text, marks, links, chips). `<br>` is a line break. */
export function inlineMd(node: Node): string {
  let out = '';
  for (const child of Array.from(node.childNodes)) out += inlineNode(child);
  return out;
}

function inlineNode(n: Node): string {
  if (n.nodeType === 3) return (n.textContent ?? '').replace(/ /g, ' ').replace(/​/g, '');
  if (!isEl(n)) return '';
  const el = n;
  const tag = el.tagName;
  if (el.dataset.task && (el.classList.contains('task-chip') || el.classList.contains('embed-row')))
    return `[[task:${el.dataset.task}]]`;
  if (el.dataset.doc && el.classList.contains('doc-link')) return `[[doc:${el.dataset.doc}]]`;
  switch (tag) {
    case 'BR':
      return '\n';
    case 'INPUT':
    case 'BUTTON':
    case 'SCRIPT':
    case 'STYLE':
      return '';
    case 'IMG':
      return imageMd(el as HTMLImageElement);
    case 'STRONG':
    case 'B':
      return styleMarks(el, mark(inlineMd(el), '**'));
    case 'EM':
    case 'I':
      return mark(inlineMd(el), '_');
    case 'DEL':
    case 'S':
    case 'STRIKE':
      return mark(inlineMd(el), '~~');
    case 'CODE': {
      const text = (el.textContent ?? '').replace(/ /g, ' ').replace(/`/g, "'");
      return text ? mark(text, '`') : '';
    }
    case 'A': {
      const href = el.getAttribute('href') ?? '';
      const doc = /^#\/docs\/([0-9A-Za-z]{12})$/.exec(href);
      if (doc && el.classList.contains('doc-link')) return `[[doc:${doc[1]}]]`;
      const text = inlineMd(el);
      if (!href || !text.trim()) return text;
      if (text === href) return href;
      return mark(text, '[', `](${href})`);
    }
    default:
      // A block inside inline content (a pasted <div> in a list entry): a line break.
      if (BLOCK_TAGS.has(tag) && tag !== 'DIV') return `\n${inlineMd(el)}\n`;
      if (tag === 'DIV' || tag === 'P') {
        const inner = inlineMd(el);
        return el.previousSibling ? `\n${inner}` : inner;
      }
      return styleMarks(el, inlineMd(el));
  }
}

/** Tidy one paragraph's text: no trailing line breaks, no blank lines inside it. */
function tidy(text: string): string {
  return text
    .split('\n')
    .map((l) => l.replace(/\s+$/, ''))
    .filter((l, i, all) => l.trim() || (i > 0 && i < all.length - 1 && all[i - 1].trim()))
    .join('\n')
    .replace(/\n{2,}/g, '\n')
    .replace(/^\n+|\n+$/g, '');
}

function cellMd(cell: Element): string {
  return tidy(inlineMd(cell)).replace(/\n/g, ' ').replace(/\|/g, '\\|').trim();
}

function tableMd(table: HTMLTableElement): string {
  const rows = Array.from(table.querySelectorAll('tr'));
  if (!rows.length) return '';
  const width = Math.max(...rows.map((r) => r.cells.length), 1);
  const line = (cells: string[]) => `| ${cells.join(' | ')} |`;
  const fill = (r: HTMLTableRowElement) => Array.from({ length: width }, (_, k) => (r.cells[k] ? cellMd(r.cells[k]) : ''));
  const head = rows[0];
  const sep = Array.from({ length: width }, (_, k) => {
    const align = (head.cells[k] as HTMLElement | undefined)?.style.textAlign;
    return align === 'center' ? ':---:' : align === 'right' ? '---:' : '---';
  });
  return [line(fill(head)), line(sep), ...rows.slice(1).map((r) => line(fill(r)))].join('\n');
}

function listMd(list: HTMLElement, depth: number): string {
  const ordered = list.tagName === 'OL';
  let n = Number(list.getAttribute('start') ?? 1) || 1;
  const pad = '  '.repeat(depth);
  const out: string[] = [];
  for (const li of Array.from(list.children)) {
    if (li.tagName === 'UL' || li.tagName === 'OL') {
      out.push(listMd(li as HTMLElement, depth + 1));
      continue;
    }
    if (li.tagName !== 'LI') continue;
    const wrapped = Array.from(li.children).find((c) => c.classList.contains('md-check'));
    const box = boxesOf(li)[0] ?? (wrapped ? boxesOf(wrapped)[0] : undefined);
    let marker = ordered ? `${n++}.` : '-';
    if (box) marker += box.hasAttribute('checked') || box.checked ? ' [x]' : ' [ ]';
    const own: Node[] = [];
    const nested: HTMLElement[] = [];
    for (const c of Array.from(li.childNodes)) {
      if (isEl(c) && (c.tagName === 'UL' || c.tagName === 'OL')) nested.push(c);
      else own.push(c);
    }
    const holder = li.ownerDocument.createElement('span');
    for (const c of own) holder.appendChild(c.cloneNode(true));
    const text = tidy(inlineMd(holder));
    const [first, ...rest] = text.split('\n');
    out.push(`${pad}${marker} ${first ?? ''}`.trimEnd());
    for (const r of rest) out.push(`${pad}  ${r}`);
    for (const sub of nested) out.push(listMd(sub, depth + 1));
  }
  return out.filter(Boolean).join('\n');
}

function preMd(pre: HTMLElement): string {
  const code = pre.querySelector('code');
  const lang = /lang-([\w+-]+)/.exec(code?.className ?? '')?.[1] ?? '';
  const text = (function walk(n: Node): string {
    if (n.nodeType === 3) return n.textContent ?? '';
    if (!isEl(n)) return '';
    if (n.tagName === 'BR') return '\n';
    const inner = Array.from(n.childNodes).map(walk).join('');
    return (n.tagName === 'DIV' || n.tagName === 'P') && n.previousSibling ? `\n${inner}` : inner;
  })(pre)
    .replace(/ /g, ' ')
    .replace(/\n$/, '');
  return '```' + lang + '\n' + text.replace(/```/g, "'''") + '\n```';
}

/** The Markdown for one block element (paragraph, heading, list, table…). '' when empty. */
export function blockMd(el: Node): string {
  if (!isEl(el)) return tidy(inlineNode(el));
  const tag = el.tagName;
  if (el.classList.contains('embed-row') && el.dataset.task) return `[[task:${el.dataset.task}]]`;
  // The browser sometimes puts a list or table inside a paragraph (a list started on an empty
  // line): write each part as its own block.
  if (/^(P|H[1-6])$/.test(tag) && Array.from(el.childNodes).some(isBlockNode)) return blocksMd(el);
  if (el.classList.contains('md-table-wrap')) {
    const t = el.querySelector('table');
    return t ? tableMd(t) : '';
  }
  switch (tag) {
    case 'H1':
    case 'H2':
    case 'H3':
    case 'H4':
    case 'H5':
    case 'H6': {
      const text = tidy(inlineMd(el)).replace(/\n/g, ' ');
      return text.trim() ? `${'#'.repeat(Number(tag[1]))} ${text.trim()}` : '';
    }
    case 'UL':
    case 'OL':
      return listMd(el, 0);
    case 'TABLE':
      return tableMd(el as HTMLTableElement);
    case 'HR':
      return '---';
    case 'PRE':
      return preMd(el);
    case 'IMG':
      return imageMd(el as HTMLImageElement);
    case 'BLOCKQUOTE': {
      const inner = blocksMd(el);
      return inner ? inner.split('\n').map((l) => (l ? `> ${l}` : '>')).join('\n') : '';
    }
    case 'DIV':
    case 'SECTION':
    case 'FIGURE':
      if (Array.from(el.childNodes).some(isBlockNode)) return blocksMd(el);
      return tidy(inlineMd(el));
    default:
      return tidy(inlineMd(el));
  }
}

/** The Markdown for a run of nodes: blocks are separated by a blank line, and loose inline
 * nodes between blocks (typed straight into the editor) make a paragraph. */
export function blocksMd(parent: Node): string {
  const parts: string[] = [];
  let run: Node[] = [];
  const flushRun = () => {
    if (!run.length) return;
    const holder = parent.ownerDocument!.createElement('p');
    for (const n of run) holder.appendChild(n.cloneNode(true));
    const md = blockMd(holder);
    if (md) parts.push(md);
    run = [];
  };
  for (const n of Array.from(parent.childNodes)) {
    if (isBlockNode(n)) {
      flushRun();
      const md = blockMd(n);
      if (md) parts.push(md);
    } else run.push(n);
  }
  flushRun();
  return parts.join('\n\n');
}

/** What a wrapper held when the editor drew it: its source and what its content meant then. */
export interface Drawn {
  src: string;
  md: string;
}

/**
 * The page text for what the editor shows. `drawn[k]` belongs to the wrapper marked
 * `data-b="k"`; a wrapper that still means what it did gives back its exact source. Returns the
 * original text untouched when nothing changed.
 */
export function editorMarkdown(root: HTMLElement, drawn: Drawn[], original: string): string {
  const used = new Set<number>();
  let out = '';
  let changed = false;
  let last = -2; // the wrapper whose source was appended last (-1: something new)
  const gap = () => {
    if (out && !out.endsWith('\n\n')) out += out.endsWith('\n') ? '\n' : '\n\n';
  };
  const fresh = (md: string) => {
    changed = true;
    if (!md) return;
    gap();
    out += `${md}\n\n`;
    last = -1;
  };
  let run: Node[] = [];
  const flushRun = () => {
    if (!run.length) return;
    const holder = root.ownerDocument.createElement('p');
    for (const n of run) holder.appendChild(n.cloneNode(true));
    run = [];
    fresh(blockMd(holder));
  };
  for (const n of Array.from(root.childNodes)) {
    if (!isBlockNode(n)) {
      if (n.nodeType === 3 && !n.textContent?.trim() && !run.length) continue;
      run.push(n);
      continue;
    }
    flushRun();
    const el = n as HTMLElement;
    const k = el.dataset.b === undefined ? -1 : Number(el.dataset.b);
    const was = k >= 0 && !used.has(k) ? drawn[k] : undefined;
    const wrapper = k >= 0 && el.tagName === 'DIV' && !el.classList.contains('embed-row');
    const md = wrapper ? blocksMd(el) : blockMd(el);
    if (was && md === was.md) {
      used.add(k);
      // Next to the block it followed before: exactly as it was. Anywhere else: a blank line.
      if (last !== k - 1) {
        changed ||= last !== -2 || k !== 0;
        gap();
      }
      out += was.src;
      last = k;
      continue;
    }
    if (was) used.add(k);
    fresh(md);
  }
  flushRun();
  if (!changed && used.size === drawn.length) return out;
  const body = out.replace(/\s+$/, '');
  return body + (original.endsWith('\n') && body ? '\n' : '');
}

/** Checklist entries are edited as `<li class="md-li-check"><input>text</li>`, so the caret
 * sits in the text and Enter makes another entry. Make every entry of a checklist look so. */
export function normalizeChecklists(root: HTMLElement): void {
  for (const span of Array.from(root.querySelectorAll<HTMLElement>('li > .md-check'))) {
    const li = span.parentElement!;
    const box = span.querySelector('input');
    const text = Array.from(span.children).find((c) => c.tagName === 'SPAN');
    const frag = root.ownerDocument.createDocumentFragment();
    if (box) frag.appendChild(box);
    if (text) while (text.firstChild) frag.appendChild(text.firstChild);
    span.replaceWith(frag);
    li.classList.add('md-li-check');
  }
  // A list nested in a checklist entry is a checklist too.
  for (const ul of Array.from(root.querySelectorAll<HTMLElement>('ul.checks li > ul'))) ul.classList.add('checks');
  for (const li of Array.from(root.querySelectorAll<HTMLElement>('ul.checks > li'))) {
    let box: HTMLInputElement | undefined = boxesOf(li)[0];
    if (!box) {
      box = root.ownerDocument.createElement('input');
      box.type = 'checkbox';
      box.className = 'md-task';
      li.insertBefore(box, li.firstChild);
    } else if (box !== li.firstChild) {
      // A copy carried along by the browser when an entry was split: keep the first box only.
      li.insertBefore(box, li.firstChild);
    }
    for (const extra of boxesOf(li).slice(1)) extra.remove();
    box.setAttribute('contenteditable', 'false');
    li.classList.add('md-li-check');
    li.classList.toggle('ticked', box.hasAttribute('checked'));
  }
  for (const li of Array.from(root.querySelectorAll<HTMLElement>('li.md-li-check'))) {
    if (!li.parentElement?.classList.contains('checks')) {
      li.classList.remove('md-li-check', 'ticked');
      for (const b of boxesOf(li)) b.remove();
    }
  }
}

/** Mark chips, page links and images as single pieces the caret steps over. */
export function lockAtoms(root: HTMLElement): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('.task-chip, .doc-link, img.doc-img'))) {
    el.setAttribute('contenteditable', 'false');
  }
}

export interface DrawBlock {
  kind: 'html' | 'task';
  html?: string;
  id?: string;
  src?: string;
}

/** Fill the editor with the page's blocks; returns what each wrapper means, for editorMarkdown. */
export function drawEditor(root: HTMLElement, blocks: DrawBlock[]): Drawn[] {
  const doc = root.ownerDocument;
  root.textContent = '';
  const drawn: Drawn[] = [];
  blocks.forEach((b, k) => {
    const el = doc.createElement('div');
    el.dataset.b = String(k);
    if (b.kind === 'task') {
      el.className = 'embed-row';
      el.dataset.task = b.id!;
      el.setAttribute('contenteditable', 'false');
    } else {
      el.className = 'md-block';
      el.innerHTML = b.html ?? '';
      normalizeChecklists(el);
      lockAtoms(el);
    }
    root.appendChild(el);
    drawn.push({ src: b.src ?? '', md: b.kind === 'task' ? blockMd(el) : blocksMd(el) });
  });
  if (!blocks.length) root.innerHTML = '<p><br></p>';
  return drawn;
}
