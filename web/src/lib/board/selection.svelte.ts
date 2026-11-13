// Selection and the panels the board opens: the multi-select set (shift-click ranges)
// and the share, list settings and tag manager dialogs.

import { BoardEditors } from './editors.svelte';

export class BoardSelection extends BoardEditors {
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

  openTagManager() {
    this.tagManagerOpen = true;
    void this.loadTags();
  }

  closeTagManager() {
    this.tagManagerOpen = false;
  }
}
