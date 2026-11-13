// The board comes back the way you left it: the last view tab per scope and its unsaved toolbar
// changes (layout, sort, filters...) live in the account prefs (`last_view`, `view_drafts`), so
// a reload, another list and back, or the phone all show the same thing.
import { withKnownLayout, type ViewConfig } from './views';

/** Most remembered scopes and unsaved views; the oldest are forgotten first. */
export const MAX_LAST_VIEWS = 100;
export const MAX_VIEW_DRAFTS = 40;

/** The settings of `draft` that differ from the view's saved `base`: what is worth storing. */
export function draftDiff(draft: ViewConfig, base: ViewConfig): Partial<ViewConfig> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(draft)) {
    if (JSON.stringify(v) !== JSON.stringify((base as unknown as Record<string, unknown>)[k])) out[k] = v;
  }
  return out as Partial<ViewConfig>;
}

/** A stored diff laid over the view's saved settings. A draft kept from before a layout was
 *  removed shows that view as a list. */
export function applyDiff(base: ViewConfig, diff: Partial<ViewConfig> | null | undefined): ViewConfig {
  return withKnownLayout({ ...base, ...(diff ?? {}) });
}

/** `map` with `key` set (moved to the newest end) or removed, keeping at most `max` entries.
 *  Null when nothing is left, so the pref is dropped rather than kept as `{}`. */
export function withEntry<T>(
  map: Record<string, T> | null | undefined,
  key: string,
  value: T | null,
  max: number,
): Record<string, T> | null {
  const entries = Object.entries(map ?? {}).filter(([k]) => k !== key);
  if (value !== null) entries.push([key, value]);
  const kept = entries.slice(Math.max(0, entries.length - max));
  return kept.length ? Object.fromEntries(kept) : null;
}
