import { describe, expect, it } from 'vitest';
import { daysLeft, exportPath, filenameOf, purgeLabel } from './archive';

describe('archive helpers', () => {
  it('reads the filename from Content-Disposition', () => {
    expect(filenameOf('attachment; filename="slate-todo-2026-10-24.csv"', 'x')).toBe('slate-todo-2026-10-24.csv');
    expect(filenameOf('attachment; filename=plain.json', 'x')).toBe('plain.json');
    expect(filenameOf(null, 'fallback.json')).toBe('fallback.json');
  });

  it('builds export paths', () => {
    expect(exportPath('json')).toBe('/export?format=json');
    expect(exportPath('csv', { list_id: 'L1' })).toBe('/export?format=csv&list_id=L1');
    expect(exportPath('json', { section_id: 'S1' })).toBe('/export?format=json&section_id=S1');
  });

  it('counts days left in the trash', () => {
    const now = new Date('2026-10-24T12:00:00Z');
    expect(daysLeft(null, now)).toBeNull();
    expect(daysLeft('2026-10-24T18:00:00Z', now)).toBe(0);
    expect(daysLeft('2026-10-25T06:00:00Z', now)).toBe(1);
    expect(daysLeft('2026-10-23T18:00:00Z', now)).toBe(0);
    expect(daysLeft('2026-11-23T12:00:00Z', now)).toBe(30);
    expect(purgeLabel('2026-10-25T12:00:00Z', now)).toBe('Deleted for good in 1 day');
    expect(purgeLabel('2026-10-24T11:00:00Z', now)).toBe('Deleted for good today');
    expect(purgeLabel(null, now)).toBe('Kept until the trash is emptied');
  });
});
