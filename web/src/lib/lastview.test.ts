import { describe, expect, it } from 'vitest';
import { MAX_VIEW_DRAFTS, applyDiff, draftDiff, withEntry } from './lastview';
import { defaultConfig, type Scope, type ViewConfig } from './views';

const scope: Scope = { kind: 'list', id: 'l1' };

describe('remembered views', () => {
  it('stores only what differs from the saved view and lays it back over it', () => {
    const base = defaultConfig(scope);
    const draft = { ...base, layout: 'list' as const, sort: [{ field: 'due' as const, dir: 'desc' as const }] };
    const diff = draftDiff(draft, base);
    expect(Object.keys(diff).sort()).toEqual(['layout', 'sort']);
    expect(applyDiff(base, diff)).toEqual(draft);
    expect(draftDiff(base, base)).toEqual({});
  });

  it('a newer change to the saved view still shows under the unsaved one', () => {
    const base = defaultConfig(scope);
    const diff = draftDiff({ ...base, layout: 'list' }, base);
    const savedLater = { ...base, density: 'compact' as const };
    expect(applyDiff(savedLater, diff)).toMatchObject({ layout: 'list', density: 'compact' });
  });

  it('an unsaved switch to a removed layout shows the list, and drops its old settings', () => {
    const base = defaultConfig(scope);
    const stored = { layout: 'calendar', calendar_span: 'week' } as unknown as Partial<ViewConfig>;
    const shown = applyDiff(base, stored);
    expect(shown.layout).toBe('list');
    expect('calendar_span' in shown).toBe(false);
  });

  it('keeps the newest entries, moves a touched one to the end, and drops an empty map', () => {
    let map: Record<string, number> | null = null;
    for (let i = 0; i < MAX_VIEW_DRAFTS + 5; i++) map = withEntry(map, `v${i}`, i, MAX_VIEW_DRAFTS);
    expect(Object.keys(map!)).toHaveLength(MAX_VIEW_DRAFTS);
    expect(map!.v0).toBeUndefined();
    map = withEntry(map, 'v5', 99, MAX_VIEW_DRAFTS);
    expect(Object.keys(map!).at(-1)).toBe('v5');
    expect(withEntry({ a: 1 }, 'a', null, 10)).toBeNull();
  });
});
