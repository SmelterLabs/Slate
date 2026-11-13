// The board's single in-memory store. One instance (`board`), exported as an object whose
// fields are $state, so components read `board.items[id]` reactively (Svelte: exported state
// must not be reassigned, only its properties).
//
// The store is one class built in layers, one module each under lib/board/, each layer
// extending the one before it (docs/board.md, "The store"):
//
//   state      the $state fields, the plain mirror of the items, derived reads, the toast
//   prefs      board preferences on the account, and this device's quick-add list and AI add
//   views      saved views and their drafts, the URL for a scope and view
//   load       the REST load and the caches (tags, rules, favorites...), the open task's detail
//   stream     sign in and out, start and retry, the live stream and how each event applies
//   items      item writes: optimistic updates and rollbacks, undo, drops, bulk
//   run        a task's run control (Run now, Nudge, Copy brief) and its pickup state
//   editors    sections, lists, statuses, rules, tags, capacity, the favorites bar
//   selection  the multi-select set and the dialogs the board opens

import { BoardEditors } from './board/editors.svelte';

export { scopeKey, type ActiveView, type Conn, type Selection } from './board/state.svelte';

class Board extends BoardEditors {

















  // --- writes -------------------------------------------------------------------------------






  // --- views, drops and bulk ----------------------------------------------------------------


  // --- board prefs (theme, folded columns): on the account, the same on every device -------






  /** Visible item ids in screen order, set by the page: shift-click selects a range of it. */
  order: string[] = [];
  #anchor: string | null = null;

  toggleSelect(id: string, range = false) {
    const from = this.#anchor ? this.order.indexOf(this.#anchor) : -1;
    const to = this.order.indexOf(id);
    if (range && from >= 0 && to >= 0) {
      const [lo, hi] = from < to ? [from, to] : [to, from];
      for (const x of this.order.slice(lo, hi + 1)) this.selected.add(x);
    } else if (this.selected.has(id)) {
      this.selected.delete(id);
    } else {
      this.selected.add(id);
    }
    this.#anchor = id;
  }

  clearSelection() {
    this.selected.clear();
  }

  // --- favorites bar ------------------------------------------------------------------------




  // --- structure & editors ------------------------------------------------------------------






  openShare(kind: 'section' | 'list', id: string) {
    this.shareTarget = { kind, id };
  }

  closeShare() {
    this.shareTarget = null;
  }

  openListSettings(listId: string) {
    this.listSettingsId = listId;
  }

  closeListSettings() {
    this.listSettingsId = null;
  }




  // Recurrence rules ---------------------------------------------------------------------------



  // --- ClickUp parity: dependencies, tags ----------------------------------------------------



  openTagManager() {
    this.tagManagerOpen = true;
    void this.loadTags();
  }

  closeTagManager() {
    this.tagManagerOpen = false;
  }


  // --- capacity ---------------------------------------------------------------------


}

export const board = new Board();
