// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { columnLetter, formatCell, officeKind, parseRange, sanitizeDocHtml } from './office';

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

describe('ranges', () => {
  it('reads merge ranges and names columns', () => {
    expect(parseRange('B2:D3')).toEqual({ top: 2, left: 2, bottom: 3, right: 4 });
    expect(parseRange('nope')).toBeNull();
    expect(columnLetter(1)).toBe('A');
    expect(columnLetter(27)).toBe('AA');
    expect(columnLetter(702)).toBe('ZZ');
  });
});
