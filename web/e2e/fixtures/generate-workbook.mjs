// Synthetic fixture generated with the same library used by the board's preview.
// Run from any directory: node web/e2e/fixtures/generate-workbook.mjs
import ExcelJS from 'exceljs';
import { fileURLToPath } from 'node:url';

const workbook = new ExcelJS.Workbook();
workbook.creator = 'Slate contributors';
workbook.lastModifiedBy = 'Slate contributors';
workbook.created = new Date('2026-01-01T00:00:00Z');
workbook.modified = new Date('2026-01-01T00:00:00Z');
const quote = workbook.addWorksheet('Quote');
quote.columns = [{ width: 28 }, { width: 10 }, { width: 14 }];
quote.mergeCells('A1:C1');
quote.getCell('A1').value = 'Example Project quote';
quote.getCell('A1').font = { bold: true };
quote.addRow(['Item', 'Qty', 'Price']);
quote.addRow(['Banner, 12 ft', 1, 480]);
quote.addRow(['Banner stand', 4, 35.5]);
quote.getColumn(3).numFmt = '$#,##0.00';
const notes = workbook.addWorksheet('Notes');
notes.addRow(['Delivery by', new Date('2026-11-20T00:00:00Z')]);
notes.getCell('B1').numFmt = 'm/d/yyyy';
await workbook.xlsx.writeFile(fileURLToPath(new URL('./quote.xlsx', import.meta.url)));
