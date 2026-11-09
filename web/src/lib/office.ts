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
  const mammoth = await import('mammoth');
  const result = await mammoth.convertToHtml({ arrayBuffer: await blob.arrayBuffer() });
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
  const ExcelJS = await import('exceljs');
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(await blob.arrayBuffer());
  const sheets: Sheet[] = [];
  wb.eachSheet((ws) => {
    if (ws.state && ws.state !== 'visible') return;
    const merges = ((ws.model as { merges?: string[] }).merges ?? []).map(parseRange).filter((r): r is NonNullable<typeof r> => !!r);
    const span = new Map<string, { colspan: number; rowspan: number }>();
    const covered = new Set<string>();
    for (const r of merges) {
      span.set(`${r.top}:${r.left}`, { colspan: r.right - r.left + 1, rowspan: r.bottom - r.top + 1 });
      for (let y = r.top; y <= r.bottom; y++) for (let x = r.left; x <= r.right; x++) if (y !== r.top || x !== r.left) covered.add(`${y}:${x}`);
    }
    const cols = Math.max(1, ws.actualColumnCount || ws.columnCount || 1);
    const total = ws.actualRowCount || ws.rowCount || 0;
    const widths: number[] = [];
    for (let c = 1; c <= cols; c++) {
      const w = ws.getColumn(c).width;
      widths.push(Math.round(((w && w > 0 ? w : 8.43) * 7 + 5) * 1.0));
    }
    const rows: SheetCell[][] = [];
    const shown = Math.min(total, SHEET_ROWS);
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
    sheets.push({ name: ws.name, widths, rows, truncated: Math.max(0, total - shown) });
  });
  return sheets;
}
