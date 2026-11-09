// The "Move to…" picker's choices: live lists grouped by section, filtered by a search.
import type { List, Section } from './types';

export interface MoveGroup {
  section: Section;
  lists: List[];
}

/**
 * Sections in board order, each with its live lists in order. A query keeps lists whose name
 * matches, or every list of a section whose name matches (case and accents ignored, every word
 * must appear). Empty sections are dropped.
 */
export function moveGroups(sections: Section[], lists: List[], query = ''): MoveGroup[] {
  const words = fold(query).split(/\s+/).filter(Boolean);
  const has = (text: string) => words.every((w) => text.includes(w));
  const out: MoveGroup[] = [];
  for (const section of [...sections].sort((a, b) => a.position - b.position)) {
    if (section.archived_at) continue;
    const own = lists
      .filter((l) => l.section_id === section.id && !l.archived_at)
      .sort((a, b) => a.position - b.position);
    const sec = fold(section.name);
    const keep = words.length ? own.filter((l) => has(`${sec} ${fold(l.name)}`)) : own;
    if (keep.length) out.push({ section, lists: keep });
  }
  return out;
}

/** The lists of `groups` in the order the picker shows them, for arrow keys and Enter. */
export function flatChoices(groups: MoveGroup[]): List[] {
  return groups.flatMap((g) => g.lists);
}

function fold(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}
