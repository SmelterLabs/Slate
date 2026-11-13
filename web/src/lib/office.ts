// Word and Excel files shown in the board itself: a .docx as a page of HTML (mammoth, loaded on
// first use), a workbook as sheets of cells (exceljs, same). Both parse in the browser from the
// fetched blob; the server stores bytes and knows nothing about either format (rule 8: no
// converter service). Pure parts (kind, sanitizer, cell formatting) are here and unit-tested;
// DocumentPreview.svelte renders.

import type { ViewerKind } from './docs';

/** Larger than this is offered as a download: parsing it would hang the tab. */
export const OFFICE_LIMIT = 25 * 1024 * 1024;
/** Rows shown per sheet; a note says when there are more. */
export const SHEET_ROWS = 2000;
/** Columns shown per sheet; a note says when there are more. */
export const SHEET_COLS = 200;

const DOCX_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.template',
]);
const XLSX_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.template',
]);

/** `docx` or `xlsx` for the Office formats the board can show, by content type then filename
 * (uploads from some apps arrive as application/octet-stream). Old binary .doc/.xls, and
 * PowerPoint, are not read: they download. */
export function officeKind(contentType: string, filename: string): 'docx' | 'xlsx' | null {
  const ct = (contentType || '').toLowerCase();
  if (DOCX_TYPES.has(ct) || /\.(docx|docm|dotx)$/i.test(filename)) return 'docx';
  if (XLSX_TYPES.has(ct) || /\.(xlsx|xlsm|xltx)$/i.test(filename)) return 'xlsx';
  return null;
}

/** Whether a viewer kind is a document DocumentPreview draws (not the browser's own PDF or
 * image rendering). */
export function isDocumentKind(kind: ViewerKind): kind is 'docx' | 'xlsx' | 'table' | 'text' {
  return kind === 'docx' || kind === 'xlsx' || kind === 'table' || kind === 'text';
}

// --- Word -------------------------------------------------------------------------------------

const ALLOWED_TAGS = new Set([
  'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i', 'u', 's',
  'sup', 'sub', 'br', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'blockquote',
  'pre', 'code', 'span', 'div', 'hr', 'figure', 'figcaption', 'caption',
]);
const SPAN_ATTRS = new Set(['colspan', 'rowspan']);

/** Mammoth's HTML with only known tags, hrefs that are http(s) or mailto, images only as the
 * data: URIs mammoth inlines, and nothing else (no event handlers, no styles, no ids). The
 * document came from whoever uploaded it; it is shown, never trusted. */
export function sanitizeDocHtml(html: string, parser: DOMParser = new DOMParser()): string {
  const doc = parser.parseFromString(`<body>${html}</body>`, 'text/html');
  const out = doc.createElement('div');
  const walk = (from: Node, to: Element) => {
    for (const node of [...from.childNodes]) {
      if (node.nodeType === 3) {
        to.appendChild(doc.createTextNode(node.textContent ?? ''));
        continue;
      }
      if (node.nodeType !== 1) continue;
      const el = node as Element;
      const tag = el.tagName.toLowerCase();
      if (!ALLOWED_TAGS.has(tag)) {
        // Unknown wrapper: keep what it contains (a `<section>` or `<font>` is just a box).
        if (tag !== 'script' && tag !== 'style' && tag !== 'iframe' && tag !== 'object') walk(el, to);
        continue;
      }
      const clean = doc.createElement(tag);
      if (tag === 'a') {
        const href = el.getAttribute('href') ?? '';
        if (/^(https?:|mailto:)/i.test(href.trim())) {
          clean.setAttribute('href', href.trim());
          clean.setAttribute('target', '_blank');
          clean.setAttribute('rel', 'noopener noreferrer');
        }
      } else if (tag === 'img') {
        const src = el.getAttribute('src') ?? '';
        if (!/^data:image\//i.test(src)) continue;
        clean.setAttribute('src', src);
        clean.setAttribute('alt', el.getAttribute('alt') ?? '');
      } else if (tag === 'td' || tag === 'th') {
        for (const name of SPAN_ATTRS) {
          const v = el.getAttribute(name);
          if (v && /^\d{1,3}$/.test(v)) clean.setAttribute(name, v);
        }
      }
      walk(el, clean);
      to.appendChild(clean);
    }
  };
  walk(doc.body, out);
  return out.innerHTML;
}

/** The .docx as sanitized HTML, with mammoth's warnings (unsupported styles) for the console. */
export async function renderDocx(blob: Blob): Promise<{ html: string; warnings: string[] }> {
  const buf = await blob.arrayBuffer();
  await checkOfficeBounds(buf, 'docx');
  const mammoth = await import('mammoth');
  const result = await mammoth.convertToHtml({ arrayBuffer: buf });
  return { html: sanitizeDocHtml(result.value), warnings: result.messages.map((m) => m.message) };
}

// --- Excel ------------------------------------------------------------------------------------

export interface SheetCell {
  text: string;
  colspan: number;
  rowspan: number;
  /** Numbers and dates sit right, like Excel. */
  right: boolean;
  bold: boolean;
  /** The first cell of a merged block draws; the others are left out of the row. */
}

export interface Sheet {
  name: string;
  /** Column widths in px, from the sheet's own widths (Excel's unit is characters). */
  widths: number[];
  rows: SheetCell[][];
  /** Rows the sheet has beyond SHEET_ROWS. */
  truncated: number;
  /** Columns the sheet has beyond SHEET_COLS. */
  truncatedCols: number;
}

const grouped = new Intl.NumberFormat('en-US', { maximumFractionDigits: 10 });
const dateFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', year: 'numeric', month: 'numeric', day: 'numeric' });
const dateTimeFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'numeric',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});

/** A cell's value as Excel would roughly show it: dates as dates (with the time when the
 * format has one), percent and currency formats honoured, decimals as the format says,
 * thousands grouped when the format groups them. Not a number-format engine; a preview. */
export function formatCell(value: unknown, numFmt = ''): string {
  if (value === null || value === undefined) return '';
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return '';
    return /[hs]|AM\/PM/i.test(numFmt) ? dateTimeFmt.format(value) : dateFmt.format(value);
  }
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '';
    const fmt = numFmt.split(';')[0];
    const decimals = /0\.(0+)/.exec(fmt)?.[1].length;
    if (fmt.includes('%')) return `${(value * 100).toFixed(decimals ?? 0)}%`;
    const currency = /\$|\[\$/.test(fmt) ? '$' : '';
    const negative = value < 0 && currency ? '-' : '';
    const abs = currency ? Math.abs(value) : value;
    const text =
      decimals !== undefined
        ? fmt.includes(',')
          ? abs.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
          : abs.toFixed(decimals)
        : fmt.includes(',')
          ? grouped.format(abs)
          : String(abs);
    return `${negative}${currency}${text}`;
  }
  if (typeof value === 'object') {
    const v = value as { richText?: { text: string }[]; text?: string; result?: unknown; error?: string; hyperlink?: string };
    if (Array.isArray(v.richText)) return v.richText.map((r) => r.text).join('');
    if (v.error) return v.error;
    if ('result' in v) return formatCell(v.result, numFmt);
    if (typeof v.text === 'string') return v.text;
  }
  return String(value);
}

/** "A1:C3" -> rows and columns, 1-based inclusive. */
export function parseRange(ref: string): { top: number; left: number; bottom: number; right: number } | null {
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/i.exec(ref.replace(/\$/g, ''));
  if (!m) return null;
  const col = (s: string) => [...s.toUpperCase()].reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0);
  return { top: Number(m[2]), left: col(m[1]), bottom: Number(m[4]), right: col(m[3]) };
}

/** Excel's column letters for a 1-based index: 1 -> A, 27 -> AA. */
export function columnLetter(n: number): string {
  let s = '';
  for (let x = n; x > 0; x = Math.floor((x - 1) / 26)) s = String.fromCharCode(65 + ((x - 1) % 26)) + s;
  return s;
}

/** The workbook's sheets as rows of formatted cells (exceljs, loaded on first use). Hidden
 * sheets are left out; an empty sheet is one empty row so its tab still shows. */
export async function readWorkbook(blob: Blob): Promise<Sheet[]> {
  const buf = await blob.arrayBuffer();
  await checkOfficeBounds(buf, 'xlsx');
  const ExcelJS = await import('exceljs');
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(buf);
  const sheets: Sheet[] = [];
  wb.eachSheet((ws) => {
    if (ws.state && ws.state !== 'visible') return;
    // rowCount and columnCount are the last row and column used; the "actual" counts are how
    // many hold values, which is fewer whenever a blank spacer row or column sits between.
    const allCols = Math.max(1, ws.columnCount || 1);
    const cols = Math.min(allCols, SHEET_COLS);
    const total = ws.rowCount || 0;
    const shown = Math.min(total, SHEET_ROWS);
    const lastRow = Math.max(shown, 1);
    // Merges are clipped to the window shown, so their cost is bounded by it.
    const merges = ((ws.model as { merges?: string[] }).merges ?? []).map(parseRange).filter((r): r is NonNullable<typeof r> => !!r);
    const span = new Map<string, { colspan: number; rowspan: number }>();
    const covered = new Set<string>();
    for (const r of merges) {
      if (r.top > lastRow || r.left > cols) continue;
      const bottom = Math.min(r.bottom, lastRow);
      const right = Math.min(r.right, cols);
      span.set(`${r.top}:${r.left}`, { colspan: right - r.left + 1, rowspan: bottom - r.top + 1 });
      for (let y = r.top; y <= bottom; y++) for (let x = r.left; x <= right; x++) if (y !== r.top || x !== r.left) covered.add(`${y}:${x}`);
    }
    const widths: number[] = [];
    for (let c = 1; c <= cols; c++) {
      const w = ws.getColumn(c).width;
      widths.push(Math.round(((w && w > 0 ? w : 8.43) * 7 + 5) * 1.0));
    }
    const rows: SheetCell[][] = [];
    for (let r = 1; r <= Math.max(shown, 1); r++) {
      const row = ws.getRow(r);
      const cells: SheetCell[] = [];
      for (let c = 1; c <= cols; c++) {
        if (covered.has(`${r}:${c}`)) continue;
        const cell = row.getCell(c);
        const value = cell.value;
        const kind = cell.type;
        const text = formatCell(value, cell.numFmt ?? '');
        const numeric =
          kind === ExcelJS.ValueType.Number ||
          kind === ExcelJS.ValueType.Date ||
          (kind === ExcelJS.ValueType.Formula && (typeof cell.result === 'number' || cell.result instanceof Date));
        const align = cell.alignment?.horizontal;
        const s = span.get(`${r}:${c}`);
        cells.push({
          text,
          colspan: s?.colspan ?? 1,
          rowspan: s?.rowspan ?? 1,
          right: align ? align === 'right' : numeric,
          bold: !!cell.font?.bold,
        });
      }
      rows.push(cells);
    }
    sheets.push({ name: ws.name, widths, rows, truncated: Math.max(0, total - shown), truncatedCols: allCols - cols });
  });
  return sheets;
}

// --- Bounds ------------------------------------------------------------------------------------
// Both parsers run on the main thread, and exceljs expands every merged range cell by cell while
// it loads, so one `<mergeCell ref="A1:XFD1048576">` would hang the tab. Before either parser
// sees the file, a cheap pass reads the zip's directory, inflates only the parts that matter
// (streamed, with a byte budget) and counts merged cells. Past a limit the preview is refused and
// the file is offered as a download, like a file over OFFICE_LIMIT.

/** Inflated XML read before parsing, beyond which the preview is refused. */
export const INFLATE_LIMIT = 100 * 1024 * 1024;
/** Merged cells, summed over a workbook, beyond which the preview is refused. */
export const MERGE_LIMIT = 50_000;

/** The file is too big to preview in the tab; DocumentPreview offers the download instead. */
export class TooLargeToPreview extends Error {
  constructor(why: string) {
    super(why);
    this.name = 'TooLargeToPreview';
  }
}

interface ZipEntry {
  name: string;
  method: number;
  compressed: number;
  local: number;
}

function zipEntries(buf: ArrayBuffer): ZipEntry[] {
  const v = new DataView(buf);
  let end = -1;
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 22 - 0xffff); i--) {
    if (v.getUint32(i, true) === 0x06054b50) {
      end = i;
      break;
    }
  }
  if (end < 0) throw new Error('not a zip file');
  const count = v.getUint16(end + 10, true);
  let p = v.getUint32(end + 16, true);
  const names = new TextDecoder();
  const out: ZipEntry[] = [];
  for (let k = 0; k < count && p + 46 <= buf.byteLength && v.getUint32(p, true) === 0x02014b50; k++) {
    const nameLen = v.getUint16(p + 28, true);
    const compressed = v.getUint32(p + 20, true);
    const local = v.getUint32(p + 42, true);
    if (compressed === 0xffffffff || local === 0xffffffff) throw new TooLargeToPreview('zip64 entry');
    out.push({ name: names.decode(new Uint8Array(buf, p + 46, nameLen)), method: v.getUint16(p + 10, true), compressed, local });
    p += 46 + nameLen + v.getUint16(p + 30, true) + v.getUint16(p + 32, true);
  }
  return out;
}

/** Stream one entry's text to `each`, spending its inflated size from `budget`. */
async function readEntry(buf: ArrayBuffer, e: ZipEntry, budget: { left: number }, each: (text: string) => void): Promise<void> {
  if (e.local + 30 > buf.byteLength) return;
  const v = new DataView(buf);
  const start = e.local + 30 + v.getUint16(e.local + 26, true) + v.getUint16(e.local + 28, true);
  const raw = new Uint8Array(buf, Math.min(start, buf.byteLength), Math.max(0, Math.min(e.compressed, buf.byteLength - start)));
  let stream = new Blob([raw]).stream();
  if (e.method === 8) stream = stream.pipeThrough(new DecompressionStream('deflate-raw'));
  else if (e.method !== 0) return;
  const reader = stream.getReader();
  const text = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    budget.left -= value.byteLength;
    if (budget.left < 0) {
      void reader.cancel();
      throw new TooLargeToPreview('inflates past the preview limit');
    }
    each(text.decode(value, { stream: true }));
  }
}

const MERGE_REF = /<(?:\w+:)?mergeCell\b[^>]*?\bref="([A-Za-z]+\d+):([A-Za-z]+\d+)"/g;

/** Throws TooLargeToPreview when the file would cost the tab too much to parse. */
export async function checkOfficeBounds(buf: ArrayBuffer, kind: 'docx' | 'xlsx', limit = INFLATE_LIMIT): Promise<void> {
  const budget = { left: limit };
  const parts =
    kind === 'docx'
      ? /^word\/(document|footnotes|endnotes|comments)\d*\.xml$/
      : /^xl\/(worksheets\/[^/]+\.xml|sharedStrings\.xml)$/;
  let merged = 0;
  for (const e of zipEntries(buf)) {
    if (!parts.test(e.name)) continue;
    let carry = '';
    await readEntry(buf, e, budget, (chunk) => {
      if (kind !== 'xlsx') return;
      const s = carry + chunk;
      let last = 0;
      for (const m of s.matchAll(MERGE_REF)) {
        const r = parseRange(`${m[1]}:${m[2]}`);
        if (r) merged += (Math.abs(r.bottom - r.top) + 1) * (Math.abs(r.right - r.left) + 1);
        last = m.index + m[0].length;
      }
      if (merged > MERGE_LIMIT) throw new TooLargeToPreview('too many merged cells');
      // A tag cut at the chunk boundary starts in the last few hundred characters.
      carry = s.slice(Math.max(last, s.length - 300));
    });
  }
}
