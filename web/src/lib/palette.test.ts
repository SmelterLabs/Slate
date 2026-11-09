import { describe, expect, it } from 'vitest';
import { type Candidate, fuzzy, pushRecent, rank, segments } from './palette';

describe('fuzzy', () => {
  it('matches a substring and marks it', () => {
    expect(fuzzy('gut', 'Fix the gutter')?.marks).toEqual([8, 9, 10]);
  });

  it('prefers a word start to a mid-word substring', () => {
    expect(fuzzy('ter', 'Termite check')!.score).toBeGreaterThan(fuzzy('ter', 'Fix the gutter')!.score);
  });

  it('matches initials and in-order letters', () => {
    expect(fuzzy('ftg', 'Fix the gutter')).not.toBeNull();
    expect(fuzzy('gtf', 'Fix the gutter')).toBeNull();
  });

  it('rejects scattered noise', () => {
    expect(fuzzy('xqz', 'xylophone quiz zebra stripe')).not.toBeNull(); // word starts
    expect(fuzzy('aeiou', 'a big red car with blue tires on')).toBeNull();
  });

  it('matches each word separately', () => {
    expect(fuzzy('gutter fix', 'Fix the gutter')).not.toBeNull();
    expect(fuzzy('gutter paint', 'Fix the gutter')).toBeNull();
  });

  it('is case-insensitive and empty matches everything', () => {
    expect(fuzzy('BANK', 'Call the bank')).not.toBeNull();
    expect(fuzzy('', 'anything')).toEqual({ score: 0, marks: [] });
  });
});

describe('rank', () => {
  const c: Candidate[] = [
    { kind: 'task', id: 't1', label: 'Call the bank about the card' },
    { kind: 'task', id: 't2', label: 'Clean the car', extra: '#home' },
    { kind: 'list', id: 'l1', label: 'Cards' },
    { kind: 'action', id: 'theme', label: 'Toggle dark mode', keywords: 'theme light' },
    { kind: 'tag', id: 'home', label: '#home' },
    { kind: 'person', id: 'agent', label: 'Agent', hint: '@agent' },
  ];

  it('ranks best first and drops non-matches', () => {
    const ids = rank('card', c).map((h) => h.id);
    expect(ids[0]).toBe('l1');
    expect(ids).toContain('t1');
    expect(ids).not.toContain('agent');
  });

  it('finds by keywords and extra text, weaker than the label', () => {
    expect(rank('theme', c)[0].id).toBe('theme');
    expect(rank('home', c).map((h) => h.id)).toEqual(['home', 't2']);
  });

  it('narrows with a leading > # or @', () => {
    expect(rank('>', c).map((h) => h.id)).toEqual(['theme']);
    expect(rank('#ho', c).map((h) => h.id)).toEqual(['home']);
    expect(rank('@ag', c).map((h) => h.id)).toEqual(['agent']);
  });

  it('keeps the given order for an empty query', () => {
    expect(rank('', c, 3).map((h) => h.id)).toEqual(['t1', 't2', 'l1']);
  });

  it('boost breaks near-ties', () => {
    const two: Candidate[] = [
      { kind: 'task', id: 'a', label: 'Pay rent' },
      { kind: 'task', id: 'b', label: 'Pay rent', boost: 5 },
    ];
    expect(rank('rent', two)[0].id).toBe('b');
  });
});

describe('segments', () => {
  it('splits marked runs', () => {
    expect(segments('gutter', [0, 1, 4])).toEqual([
      { text: 'gu', mark: true },
      { text: 'tt', mark: false },
      { text: 'e', mark: true },
      { text: 'r', mark: false },
    ]);
  });
});

describe('pushRecent', () => {
  it('moves to the front without duplicates and caps at 12', () => {
    expect(pushRecent(['a', 'b', 'c'], 'b')).toEqual(['b', 'a', 'c']);
    const many = Array.from({ length: 12 }, (_, i) => `x${i}`);
    expect(pushRecent(many, 'new')).toHaveLength(12);
  });
});
