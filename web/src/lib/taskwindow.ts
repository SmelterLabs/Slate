// Where the task window sits and how big it is: centered (the default, resizable from its
// corner and always kept centered), or docked to the right third or right quarter of the
// screen, where the board beside it stays usable. The activity column's width is dragged
// from its left edge. All of it is remembered per device (localStorage, like the chat panel).
// The pure parts live here; the live state is taskwindow.svelte.ts.

export const STORAGE_KEY = 'slate.taskWindow';

export type Dock = 'center' | 'third' | 'quarter';
export const DOCKS: Dock[] = ['center', 'third', 'quarter'];

export interface WindowState {
  dock: Dock;
  /** The centered window's size in px (docked, the screen decides). */
  width: number;
  height: number;
  /** The activity column's width in px. */
  activity: number;
}

export const DEFAULT_WIDTH = 1280;
export const DEFAULT_HEIGHT = 900;
export const DEFAULT_ACTIVITY = 420;
/** Narrower than this and the fields no longer fit beside their labels. */
export const NARROWEST_WIDTH = 720;
export const SHORTEST_HEIGHT = 480;
/** Narrower and the composer's buttons no longer fit. */
export const ACTIVITY_NARROWEST = 300;
/** What the main column must always keep. */
export const MAIN_KEEP = 360;
/** The margin the centered window keeps from the screen's edges (both sides together). */
export const SCREEN_MARGIN = 48;
/** Below this the activity stacks under the task instead of beside it (the phone layout). */
export const STACK_BELOW = 760;

export const DOCK_FRACTION: Record<Dock, number> = { center: 1, third: 1 / 3, quarter: 1 / 4 };

export const DEFAULT_STATE: WindowState = {
  dock: 'center',
  width: DEFAULT_WIDTH,
  height: DEFAULT_HEIGHT,
  activity: DEFAULT_ACTIVITY,
};

export function clampWidth(width: number, viewport: number): number {
  const widest = Math.max(NARROWEST_WIDTH, viewport - SCREEN_MARGIN);
  return Math.round(Math.min(widest, Math.max(NARROWEST_WIDTH, width)));
}

export function clampHeight(height: number, viewport: number): number {
  const tallest = Math.max(SHORTEST_HEIGHT, viewport - SCREEN_MARGIN);
  return Math.round(Math.min(tallest, Math.max(SHORTEST_HEIGHT, height)));
}

/** The activity width kept between its narrowest and what leaves the main column its keep. */
export function clampActivity(activity: number, windowWidth: number): number {
  const widest = Math.max(ACTIVITY_NARROWEST, windowWidth - MAIN_KEEP);
  return Math.round(Math.min(widest, Math.max(ACTIVITY_NARROWEST, activity)));
}

/** The window's width on screen for a state: its own when centered (within the screen), the
 * dock's share of the screen otherwise. */
export function windowWidth(state: WindowState, viewport: number): number {
  if (state.dock === 'center') return clampWidth(state.width, viewport);
  return Math.round(viewport * DOCK_FRACTION[state.dock]);
}

/** Whether the activity should stack under the task: a window too narrow for two columns. */
export function stacks(state: WindowState, viewport: number): boolean {
  return windowWidth(state, viewport) < STACK_BELOW;
}

export function readState(raw: string | null): WindowState {
  if (!raw) return DEFAULT_STATE;
  try {
    const v = JSON.parse(raw) as Partial<WindowState>;
    const dock = DOCKS.includes(v.dock as Dock) ? (v.dock as Dock) : 'center';
    const num = (x: unknown, d: number) => (typeof x === 'number' && Number.isFinite(x) && x > 0 ? x : d);
    return {
      dock,
      width: num(v.width, DEFAULT_WIDTH),
      height: num(v.height, DEFAULT_HEIGHT),
      activity: num(v.activity, DEFAULT_ACTIVITY),
    };
  } catch {
    return DEFAULT_STATE;
  }
}

export function writeState(state: WindowState): string {
  return JSON.stringify(state);
}

export const DOCK_LABELS: Record<Dock, string> = {
  center: 'Centered',
  third: 'Right third',
  quarter: 'Right quarter',
};
