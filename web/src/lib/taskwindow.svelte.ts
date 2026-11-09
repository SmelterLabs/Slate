// The task window's live position and size (see taskwindow.ts). Per device, remembered in
// localStorage: a dock or a size chosen once is where the next card opens.
import {
  clampActivity,
  clampHeight,
  clampWidth,
  DEFAULT_ACTIVITY,
  DEFAULT_HEIGHT,
  DEFAULT_STATE,
  DEFAULT_WIDTH,
  readState,
  stacks,
  STORAGE_KEY,
  windowWidth,
  writeState,
  type Dock,
  type WindowState,
} from './taskwindow';

function stored(): WindowState {
  try {
    return readState(localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_STATE;
  }
}

function viewport(): { w: number; h: number } {
  return typeof window === 'undefined' ? { w: 1920, h: 1080 } : { w: window.innerWidth, h: window.innerHeight };
}

class TaskWindow {
  dock = $state<Dock>('center');
  width = $state(DEFAULT_WIDTH);
  height = $state(DEFAULT_HEIGHT);
  activity = $state(DEFAULT_ACTIVITY);

  constructor() {
    const s = stored();
    this.dock = s.dock;
    this.width = s.width;
    this.height = s.height;
    this.activity = s.activity;
  }

  get state(): WindowState {
    return { dock: this.dock, width: this.width, height: this.height, activity: this.activity };
  }

  get docked(): boolean {
    return this.dock !== 'center';
  }

  /** The window's width on screen now. */
  get shown(): number {
    return windowWidth(this.state, viewport().w);
  }

  /** Whether the activity stacks under the task (a narrow dock, or a phone). */
  get stacked(): boolean {
    return stacks(this.state, viewport().w);
  }

  setDock(dock: Dock) {
    this.dock = dock;
    this.#save();
  }

  /** The corner grip: the centered window's size, within the screen. */
  resize(width: number, height: number) {
    const v = viewport();
    this.width = clampWidth(width, v.w);
    this.height = clampHeight(height, v.h);
    this.#save();
  }

  resetSize() {
    this.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT);
  }

  /** The activity grip: `px` from the window's right edge, leaving the main column its room. */
  resizeActivity(px: number) {
    this.activity = clampActivity(px, this.shown);
    this.#save();
  }

  resetActivity() {
    this.resizeActivity(DEFAULT_ACTIVITY);
  }

  #save() {
    try {
      localStorage.setItem(STORAGE_KEY, writeState(this.state));
    } catch {
      // no storage: the window still works for this page
    }
  }
}

export const taskWindow = new TaskWindow();
