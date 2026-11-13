import { describe, expect, it } from 'vitest';
import { statusColor } from './activity';
import { tagColor } from './cards';
import { inline } from './docmd';
import { safeColor } from './mdcore';

const BAD = ['red;position:fixed', '0;inset:0', 'url(x)', '#12', 'a b', '"red"', 'expression(x)'];

describe('colours in style values', () => {
  it('safeColor keeps hex colours, colour names and theme variables only', () => {
    for (const c of ['#c00', '#1d2127', '#1d2127cc', 'red', 'SlateGray', 'var(--kind-done)']) expect(safeColor(c)).toBe(c);
    for (const c of BAD) expect(safeColor(c, 'x')).toBe('x');
  });

  it('a stored colour that is not a colour falls back to the status kind', () => {
    for (const c of BAD) expect(statusColor({ color: c, kind: 'done' })).toBe('var(--kind-done)');
    expect(statusColor({ color: '#c00', kind: 'done' })).toBe('#c00');
  });

  it('a tag keeps a six-digit hex colour and drops anything else', () => {
    expect(tagColor('home', { home: { id: 't', name: 'home', color: '#cc0000' } }).bg).toBe('#cc0000');
    expect(tagColor('home', { home: { id: 't', name: 'home', color: 'red;x:y' } }).bg).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('a task chip in a doc page never carries an invalid colour', () => {
    const out = inline('[[task:AAAAAAAAAAAA]]', {
      task: () => ({ title: 'T', done: false, color: 'red;position:fixed' }),
      doc: () => null,
    });
    expect(out).toContain('<span class="dot"></span>');
    expect(out).not.toContain('position');
  });
});
