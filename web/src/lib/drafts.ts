// Unsent text survives closing a card, switching views or reloading: every box you type into
// keeps what you typed in this browser until you send it, clear it or cancel. Pure storage
// helpers live here so they can be tested without Svelte; the reactive layer is drafts.svelte.ts.

export const DRAFT_PREFIX = 'slate.draft.';
/** A draft nobody came back to in this long is dropped. */
export const DRAFT_TTL_MS = 30 * 24 * 3600 * 1000;
/** A runaway paste is not worth filling the browser's storage with. */
export const DRAFT_MAX_CHARS = 20_000;

export interface StoredDraft {
  t: string;
  at: number;
}

/** The parts of Storage the drafts use (so tests can pass a plain map). */
export type DraftStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem' | 'key' | 'length'>;

function storage(): DraftStore | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null; // private mode or blocked storage: drafts just don't persist
  }
}

export function parseDraft(raw: string | null, now = Date.now()): string {
  if (!raw) return '';
  try {
    const d = JSON.parse(raw) as StoredDraft;
    if (typeof d?.t !== 'string' || typeof d.at !== 'number') return '';
    return now - d.at > DRAFT_TTL_MS ? '' : d.t;
  } catch {
    return '';
  }
}

export function readDraft(key: string, store = storage(), now = Date.now()): string {
  if (!store) return '';
  try {
    return parseDraft(store.getItem(DRAFT_PREFIX + key), now);
  } catch {
    return '';
  }
}

/** Blank text removes the draft; anything else replaces it. Returns whether one is now kept. */
export function writeDraft(key: string, text: string, store = storage(), now = Date.now()): boolean {
  if (!store) return false;
  try {
    if (!text.trim()) {
      store.removeItem(DRAFT_PREFIX + key);
      return false;
    }
    const d: StoredDraft = { t: text.slice(0, DRAFT_MAX_CHARS), at: now };
    store.setItem(DRAFT_PREFIX + key, JSON.stringify(d));
    return true;
  } catch {
    return false; // storage full: the box still works, the draft just isn't kept
  }
}

/** Every live draft key (without the prefix), dropping expired ones on the way. */
export function liveDraftKeys(store = storage(), now = Date.now()): string[] {
  if (!store) return [];
  const keys: string[] = [];
  const stale: string[] = [];
  try {
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (!k?.startsWith(DRAFT_PREFIX)) continue;
      if (parseDraft(store.getItem(k), now)) keys.push(k.slice(DRAFT_PREFIX.length));
      else stale.push(k);
    }
    for (const k of stale) store.removeItem(k);
  } catch {
    // unreadable storage: no drafts
  }
  return keys;
}

/** Draft keys, one per box. Kept in one place so the card marker can find a card's drafts. */
export const draftKey = {
  doc: (account: string, doc: string, field: string, context = '') => `doc:${JSON.stringify([account, doc, field, context])}`,
  comment: (itemId: string) => `comment:${itemId}`,
  editComment: (commentId: string) => `edit-comment:${commentId}`,
  subtask: (itemId: string) => `subtask:${itemId}`,
  checklist: (checklistId: string) => `checklist:${checklistId}`,
  handoff: (itemId: string) => `handoff:${itemId}`,
  chat: (channel: string, parentId: string | null) => `chat:${channel}:${parentId ?? ''}`,
  editMessage: (messageId: string) => `edit-msg:${messageId}`,
  newCard: (viewKey: string, groupKey: string) => `newcard:${viewKey}:${groupKey}`,
  quickAdd: () => 'quickadd',
};

/** The card-level drafts a board card shows its marker for (a comment, a subtask, a hand-off). */
export function itemDraftKeys(itemId: string): string[] {
  return [draftKey.comment(itemId), draftKey.subtask(itemId), draftKey.handoff(itemId)];
}
