// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import {
  checkOfficeBounds,
  columnLetter,
  formatCell,
  officeKind,
  parseRange,
  readWorkbook,
  sanitizeDocHtml,
  SHEET_COLS,
  TooLargeToPreview,
} from './office';

describe('officeKind', () => {
  it('knows Word and Excel by type, then by name; not the old binary formats', () => {
    expect(officeKind('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'x')).toBe('docx');
    expect(officeKind('application/octet-stream', 'Scope.DOCX')).toBe('docx');
    expect(officeKind('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'q')).toBe('xlsx');
    expect(officeKind('', 'quote.xlsm')).toBe('xlsx');
    expect(officeKind('application/msword', 'old.doc')).toBeNull();
    expect(officeKind('application/vnd.ms-excel', 'old.xls')).toBeNull();
    expect(officeKind('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'deck.pptx')).toBeNull();
  });
});

describe('sanitizeDocHtml', () => {
  it('keeps document structure and drops scripts, handlers and styles', () => {
    const html = '<h1 style="color:red" onclick="x()">Scope</h1><p>Hi <strong>there</strong><script>alert(1)</script></p><style>p{}</style>';
    expect(sanitizeDocHtml(html)).toBe('<h1>Scope</h1><p>Hi <strong>there</strong></p>');
  });
  it('keeps safe links and inline images only', () => {
    const html = '<p><a href="javascript:alert(1)">bad</a> <a href="https://example.com/q">ok</a></p><img src="data:image/png;base64,AAAA" alt="logo"><img src="https://evil/track.png">';
    expect(sanitizeDocHtml(html)).toBe(
      '<p><a>bad</a> <a href="https://example.com/q" target="_blank" rel="noopener noreferrer">ok</a></p><img src="data:image/png;base64,AAAA" alt="logo">',
    );
  });
  it('keeps table spans and unwraps unknown boxes', () => {
    const html = '<section><table><tr><td colspan="2" id="x">a</td></tr></table></section>';
    expect(sanitizeDocHtml(html)).toBe('<table><tbody><tr><td colspan="2">a</td></tr></tbody></table>');
  });
});

describe('formatCell', () => {
  it('formats numbers roughly as the cell format says', () => {
    expect(formatCell(1234.5, '#,##0.00')).toBe('1,234.50');
    expect(formatCell(1234.5, '0')).toBe('1234.5');
    expect(formatCell(0.125, '0.0%')).toBe('12.5%');
    expect(formatCell(-42, '"$"#,##0.00')).toBe('-$42.00');
    expect(formatCell(7, '')).toBe('7');
  });
  it('formats dates, booleans, rich text, formulas and errors', () => {
    expect(formatCell(new Date(Date.UTC(2026, 10, 2)), 'm/d/yyyy')).toBe('11/2/2026');
    expect(formatCell(new Date(Date.UTC(2026, 10, 2, 14, 30)), 'm/d/yyyy h:mm')).toBe('11/2/2026, 2:30 PM');
    expect(formatCell(true)).toBe('TRUE');
    expect(formatCell({ richText: [{ text: 'Example ' }, { text: 'Project' }] })).toBe('Example Project');
    expect(formatCell({ formula: 'A1*2', result: 10 }, '0.00')).toBe('10.00');
    expect(formatCell({ error: '#DIV/0!' })).toBe('#DIV/0!');
    expect(formatCell(null)).toBe('');
  });
});

/** A zip with the given entries, stored or deflated (no CRCs: the bounds check does not read them). */
async function zip(entries: Record<string, string>, deflate = false): Promise<ArrayBuffer> {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, text] of Object.entries(entries)) {
    const n = enc.encode(name);
    const raw = enc.encode(text);
    const data = deflate
      ? new Uint8Array(await new Response(new Blob([raw]).stream().pipeThrough(new CompressionStream('deflate-raw'))).arrayBuffer())
      : raw;
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(8, deflate ? 8 : 0, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, raw.length, true);
    local.setUint16(26, n.length, true);
    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(10, deflate ? 8 : 0, true);
    cd.setUint32(20, data.length, true);
    cd.setUint32(24, raw.length, true);
    cd.setUint16(28, n.length, true);
    cd.setUint32(42, offset, true);
    parts.push(new Uint8Array(local.buffer), n, data);
    central.push(new Uint8Array(cd.buffer), n);
    offset += 30 + n.length + data.length;
  }
  const cdSize = central.reduce((s, p) => s + p.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, Object.keys(entries).length, true);
  end.setUint16(10, Object.keys(entries).length, true);
  end.setUint32(12, cdSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(end.buffer)] as BlobPart[]).arrayBuffer();
}

const sheetXml = (merges: string[]) =>
  `<worksheet><sheetData/><mergeCells count="${merges.length}">${merges.map((m) => `<mergeCell ref="${m}"/>`).join('')}</mergeCells></worksheet>`;

describe('readWorkbook', () => {
  it('shows every row and column up to the last one used, blank spacers included', async () => {
    const ExcelJS = await import('exceljs');
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet('Quote');
    ws.getCell('A1').value = 'first';
    ws.getCell('C5').value = 'last';
    const [sheet] = await readWorkbook(new Blob([await wb.xlsx.writeBuffer()]));
    expect(sheet.rows).toHaveLength(5);
    expect(sheet.widths).toHaveLength(3);
    expect(sheet.rows[4][2].text).toBe('last');
    expect(sheet.truncated).toBe(0);
  });

  it('keeps the grid to the shown window and clips merges to it', async () => {
    const ExcelJS = await import('exceljs');
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet('Wide');
    ws.getCell(1, SHEET_COLS + 50).value = 'far';
    ws.mergeCells(1, SHEET_COLS - 1, 3, SHEET_COLS + 10);
    const [sheet] = await readWorkbook(new Blob([await wb.xlsx.writeBuffer()]));
    expect(sheet.widths).toHaveLength(SHEET_COLS);
    expect(sheet.rows[0]).toHaveLength(SHEET_COLS - 1);
    expect(sheet.rows[0][SHEET_COLS - 2].colspan).toBe(2);
  });
});

describe('checkOfficeBounds', () => {
  it('refuses a workbook whose merged cells would be expanded one by one', async () => {
    const huge = await zip({ 'xl/worksheets/sheet1.xml': sheetXml(['A1:XFD1048576']) });
    await expect(checkOfficeBounds(huge, 'xlsx')).rejects.toBeInstanceOf(TooLargeToPreview);
    const many = await zip({ 'xl/worksheets/sheet1.xml': sheetXml(Array.from({ length: 30 }, (_, i) => `A${i * 100 + 1}:Z${i * 100 + 200}`)) }, true);
    await expect(checkOfficeBounds(many, 'xlsx')).rejects.toBeInstanceOf(TooLargeToPreview);
    const fine = await zip({ 'xl/worksheets/sheet1.xml': sheetXml(['A1:B2', 'C3:D40']) }, true);
    await expect(checkOfficeBounds(fine, 'xlsx')).resolves.toBeUndefined();
  });

  it('refuses a file that inflates past the limit', async () => {
    const big = await zip({ 'word/document.xml': `<w:document>${'x'.repeat(5000)}</w:document>` }, true);
    await expect(checkOfficeBounds(big, 'docx', 4000)).rejects.toBeInstanceOf(TooLargeToPreview);
    await expect(checkOfficeBounds(big, 'docx')).resolves.toBeUndefined();
  });

  it('refuses a real workbook through readWorkbook before exceljs loads it', async () => {
    const huge = await zip({ 'xl/worksheets/sheet1.xml': sheetXml(['A1:XFD1048576']) }, true);
    await expect(readWorkbook(new Blob([huge]))).rejects.toBeInstanceOf(TooLargeToPreview);
  });
});

describe('ranges', () => {
  it('reads merge ranges and names columns', () => {
    expect(parseRange('B2:D3')).toEqual({ top: 2, left: 2, bottom: 3, right: 4 });
    expect(parseRange('nope')).toBeNull();
    expect(columnLetter(1)).toBe('A');
    expect(columnLetter(27)).toBe('AA');
    expect(columnLetter(702)).toBe('ZZ');
  });
});
