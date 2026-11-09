// The chat panel's live state: whether it is open, on which channel and thread. Per device
// (localStorage, not the account prefs): a desktop keeps its panel open across pages and reloads
// while a phone, which never shows it, is unaffected. See chatpanel.ts.
import {
  clampPanelWidth,
  DEFAULT_PANEL,
  PANEL_DEFAULT_WIDTH,
  panelFits,
  readPanelState,
  STORAGE_KEY,
  writePanelState,
  type PanelState,
} from './chatpanel';

function stored(): PanelState {
  try {
    return readPanelState(localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_PANEL;
  }
}

class ChatPanel {
  open = $state(false);
  channel = $state('general');
  thread = $state<string | null>(null);
  width = $state(PANEL_DEFAULT_WIDTH);
  // Full screen is temporary; the saved docked width is restored on exit.
  fullscreen = $state(false);

  get expanded(): boolean {
    return this.fullscreen || this.width > PANEL_DEFAULT_WIDTH;
  }

  toggleWidth() {
    const compact = this.expanded;
    this.fullscreen = false;
    this.resize(compact ? PANEL_DEFAULT_WIDTH : 720);
  }

  toggleFullscreen() {
    this.fullscreen = !this.fullscreen;
  }
  /** The page the panel was expanded from (`#/...`), so the Chat page can put it back there. */
  returnTo = $state<string | null>(null);

  constructor() {
    const s = stored();
    this.open = s.open;
    this.channel = s.channel;
    this.thread = s.thread;
    this.width = s.width;
  }

  /** Dragging the left edge: `px` from the window's right edge, kept within what fits. */
  resize(px: number) {
    this.width = clampPanelWidth(px, typeof window === 'undefined' ? 1920 : window.innerWidth);
    this.#save();
  }

  resetWidth() {
    this.resize(PANEL_DEFAULT_WIDTH);
  }

  /** Whether this window is wide enough for the panel; else chat is its own page. */
  get fits(): boolean {
    return typeof window === 'undefined' || panelFits(window.innerWidth);
  }

  /** Open on `key` (or stay on the last channel), with the thread it had only when the channel is the same. */
  show(key?: string | null, thread: string | null = null) {
    if (key && key !== this.channel) {
      this.channel = key;
      this.thread = thread;
    } else if (thread) this.thread = thread;
    this.open = true;
    this.#save();
  }

  hide() {
    this.fullscreen = false;
    this.open = false;
    this.#save();
  }

  /** The panel's expand icon: close it and remember where you were, for `dock()`. */
  expand(fromHash: string) {
    this.returnTo = fromHash;
    this.hide();
  }

  /** The Chat page's "back to panel": open the panel on this chat and go back to the page the
   * panel was expanded from (My Work when it was not expanded from anywhere). */
  dock(key: string, thread: string | null = null): string {
    const to = this.returnTo && !/^#\/chat(\/|$|\?)/.test(this.returnTo) ? this.returnTo : '#/home';
    this.returnTo = null;
    this.show(key, thread);
    return to;
  }

  /** The sidebar's Chat entry: open on the last channel, or close when already open. */
  toggle() {
    if (this.open) this.hide();
    else this.show();
  }

  pick(key: string) {
    if (key === this.channel) return;
    this.channel = key;
    this.thread = null;
    this.#save();
  }

  setThread(id: string | null) {
    this.thread = id;
    this.#save();
  }

  #save() {
    try {
      localStorage.setItem(STORAGE_KEY, writePanelState({ open: this.open, channel: this.channel, thread: this.thread, width: this.width }));
    } catch {
      // no storage: the panel still works for this page
    }
  }
}

export const chatPanel = new ChatPanel();
