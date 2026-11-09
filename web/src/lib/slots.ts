// Manual order: where a dragged card, row or checklist entry lands among its neighbours, as the
// fewest position writes. Positions are integers; a drop takes the midpoint of the gap it lands
// in, and only when there is no gap left does it renumber the group (STEP apart, so the next
// many drops fit again).

export const STEP = 1024;

export interface Placed {
  id: string;
  position: number;
}

/**
 * The writes that put `movingId` just before `beforeId` (`null`: at the end) in `ordered`, the
 * group as it is shown (manual order, `movingId` included or arriving from another group).
 * Empty when it is already there.
 */
export function slotFor(ordered: Placed[], movingId: string, beforeId: string | null, moving?: Placed): Placed[] {
  const me = ordered.find((p) => p.id === movingId) ?? moving;
  if (!me) return [];
  const rest = ordered.filter((p) => p.id !== movingId);
  let at = beforeId === null ? rest.length : rest.findIndex((p) => p.id === beforeId);
  if (at < 0) at = rest.length;
  const was = ordered.findIndex((p) => p.id === movingId);
  if (was >= 0 && was === at) return []; // dropped back where it was
  const prev = rest[at - 1];
  const next = rest[at];
  let position: number | null = null;
  if (!prev && !next) return [];
  if (!prev) position = next.position - STEP;
  else if (!next) position = prev.position + STEP;
  else if (next.position - prev.position >= 2) position = Math.floor((prev.position + next.position) / 2);
  if (position !== null) return position === me.position ? [] : [{ id: movingId, position }];
  // No room between two neighbours: renumber the whole group.
  const order = [...rest.slice(0, at), me, ...rest.slice(at)];
  return order
    .map((p, i) => ({ id: p.id, position: (i + 1) * STEP, old: p.position }))
    .filter((p) => p.position !== p.old)
    .map(({ id, position }) => ({ id, position }));
}

/** The id of the element (among `rects`, top to bottom) a pointer at `y` lands in front of, or
 * null below the last one: above an element's middle is in front of it. */
export function beforeAt(rects: { id: string; top: number; bottom: number }[], y: number): string | null {
  for (const r of rects) if (y < (r.top + r.bottom) / 2) return r.id;
  return null;
}
