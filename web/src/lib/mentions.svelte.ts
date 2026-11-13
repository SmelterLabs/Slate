// The @mention picker every composer shares (task comments, chat, doc discussion): which people
// match what is typed after `@`, how the arrow, Enter, Tab and Escape keys drive the list, and
// putting the picked `@handle ` into the text. One copy, so the composers cannot drift apart.

import { insertMention, mentionAt } from './activity';
import type { Account } from './types';

/** Up to `limit` people whose handle starts with, or whose name contains, `query`; handle
 * matches first, then by name. */
export function mentionCandidates(pool: Account[], query: string, limit = 6): Account[] {
  const q = query.toLowerCase();
  return pool
    .filter((a) => !q || a.handle.startsWith(q) || a.display_name.toLowerCase().includes(q))
    .sort(
      (a, b) =>
        Number(!a.handle.startsWith(q)) - Number(!b.handle.startsWith(q)) || a.display_name.localeCompare(b.display_name),
    )
    .slice(0, limit);
}

type Key = Pick<KeyboardEvent, 'key' | 'shiftKey' | 'isComposing'> & { keyCode?: number };

/** Whether a key is still part of an input method's composition (Japanese, Chinese, Korean…):
 * the Enter that confirms the characters is not the Enter that sends. Safari reports it only
 * through keyCode 229. */
export function composing(e: Key): boolean {
  return !!e.isComposing || e.keyCode === 229;
}

/** Enter without Shift, outside a composition: the key that sends a message. */
export function isSendKey(e: Key): boolean {
  return e.key === 'Enter' && !e.shiftKey && !composing(e);
}

/** What a key does in an open picker of `count` options with `active` highlighted: a new
 * highlight, `pick`, `close`, or null (not the picker's key). */
export function pickerKey(e: Key, active: number, count: number): { active: number } | 'pick' | 'close' | null {
  if (!count || composing(e)) return null;
  if (e.key === 'ArrowDown') return { active: (active + 1) % count };
  if (e.key === 'ArrowUp') return { active: (active + count - 1) % count };
  if (e.key === 'Enter' || e.key === 'Tab') return 'pick';
  if (e.key === 'Escape') return 'close';
  return null;
}

/** A composer's @mention state: the `@query` being typed, the matching people and the
 * highlighted one. `pool` is who can be mentioned (usually `board.pickable`). */
export class MentionPicker {
  mention = $state<{ start: number; query: string } | null>(null);
  active = $state(0);
  #pool: () => Account[];
  readonly people = $derived.by(() => (this.mention ? mentionCandidates(this.#pool(), this.mention.query) : []));

  constructor(pool: () => Account[]) {
    this.#pool = pool;
  }

  get open(): boolean {
    return this.people.length > 0;
  }

  /** Follow the caret: is an `@query` being typed right before it? */
  track(text: string, caret: number) {
    this.mention = mentionAt(text, caret);
    this.active = 0;
  }

  close() {
    this.mention = null;
  }

  /** The text with `a`'s handle in place of the `@query`, and where the caret goes. */
  insert(text: string, caret: number, a: Account): { text: string; caret: number } | null {
    if (!this.mention) return null;
    const out = insertMention(text, this.mention.start, caret, a.handle);
    this.mention = null;
    return out;
  }

  /** Handle a key while the list is open; true when it was the picker's (the composer then
   * does nothing else with it). Enter and Tab pick through `pick`. */
  keydown(e: KeyboardEvent, pick: (a: Account) => void): boolean {
    const k = pickerKey(e, this.active, this.people.length);
    if (!k) return false;
    if (k === 'close') {
      e.stopPropagation();
      this.close();
    } else {
      e.preventDefault();
      if (k === 'pick') pick(this.people[this.active]);
      else this.active = k.active;
    }
    return true;
  }
}
