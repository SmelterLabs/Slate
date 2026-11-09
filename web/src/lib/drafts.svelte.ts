// The reactive side of drafts.ts: a live set of which boxes hold a draft (for the card's
// "Draft" marker), and `persisted()`, a text state that saves itself as you type.
import { untrack } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import { DRAFT_PREFIX, itemDraftKeys, liveDraftKeys, readDraft, writeDraft } from './drafts';

const keys = new SvelteSet<string>(liveDraftKeys());

// Another tab typed or sent: follow it, so its markers show here too.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === null) {
      keys.clear();
      for (const k of liveDraftKeys()) keys.add(k);
    } else if (e.key.startsWith(DRAFT_PREFIX)) {
      const k = e.key.slice(DRAFT_PREFIX.length);
      if (readDraft(k)) keys.add(k);
      else keys.delete(k);
    }
  });
}

// The page is going away (reload, navigation, tab closed). A request still in flight is cut off
// then, and a box that puts its text back when a send fails would save the message it just sent
// as a draft: the next page shows it again, ready to send twice. So no draft is written from the
// moment leaving starts. `beforeunload` comes first (the cut-off request can fail before
// `pagehide`); it can also fire for a navigation that turns out to be a download and leaves the
// page in place, so anything the person does next (typing, a click) or the page being shown
// again (back/forward cache) means it is not leaving after all.
let leaving = false;
if (typeof window !== 'undefined') {
  const going = () => (leaving = true);
  const staying = () => (leaving = false);
  window.addEventListener('beforeunload', going);
  window.addEventListener('pagehide', going);
  window.addEventListener('pageshow', staying);
  for (const ev of ['input', 'keydown', 'pointerdown']) window.addEventListener(ev, staying, true);
}

export function saveDraft(key: string, text: string) {
  if (leaving) return;
  if (writeDraft(key, text)) keys.add(key);
  else keys.delete(key);
}

export function hasDraft(key: string): boolean {
  return keys.has(key);
}

/** Does this card hold unsent text (a comment, a subtask or a hand-off)? */
export function itemHasDraft(itemId: string): boolean {
  return itemDraftKeys(itemId).some((k) => keys.has(k));
}

export interface Persisted {
  value: string;
}

/**
 * Text state for a box that keeps its draft. Call it while a component starts, like `$state`.
 * Each write is saved straight away under `key()`; when the key changes (the box now belongs to
 * another card or channel) it loads that one's draft. Setting it to '' (sent or cancelled)
 * removes the draft. A null key means "don't keep this one".
 */
export function persisted(key: () => string | null, fallback: () => string = () => ''): Persisted {
  let current = untrack(key);
  let text = $state(untrack(() => (current ? readDraft(current) : '') || fallback()));
  $effect.pre(() => {
    const next = key();
    if (next === current) return;
    current = next;
    text = untrack(() => (next ? readDraft(next) : '') || fallback());
  });
  return {
    get value() {
      return text;
    },
    set value(v: string) {
      text = v;
      if (current) saveDraft(current, v);
    },
  };
}
