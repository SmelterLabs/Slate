import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { exposure } from './exposure';

let mutations: () => void;
let intersections: ((entries: { intersectionRatio: number }[]) => void)[];
let covered: boolean;
let connected: boolean;
let visible: boolean;
let focused: boolean;
let actions: ReturnType<typeof exposure>[];
const disconnect = vi.fn();
const observe = vi.fn();
const node = {
  get isConnected() { return connected; },
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 100 }),
  contains: (hit: unknown) => hit === node,
} as unknown as HTMLElement;

beforeEach(() => {
  vi.useFakeTimers();
  covered = true; connected = true; visible = true; focused = true;
  actions = []; intersections = [];
  disconnect.mockClear(); observe.mockClear();
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('document', Object.assign(new EventTarget(), {
    body: {},
    get visibilityState() { return visible ? 'visible' : 'hidden'; },
    hasFocus: () => focused,
    elementFromPoint: () => covered ? null : node,
  }));
  // Keep lifecycle getters live (Object.assign would copy their initial value).
  Object.defineProperty(document, 'visibilityState', { get: () => visible ? 'visible' : 'hidden' });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: typeof intersections[number]) { intersections.push(callback); }
    observe() {} disconnect() {}
  });
  vi.stubGlobal('MutationObserver', class {
    constructor(callback: () => void) { mutations = callback; }
    observe = observe;
    disconnect = disconnect;
  });
  vi.stubGlobal('requestAnimationFrame', (callback: () => void) => setTimeout(callback, 16));
  vi.stubGlobal('cancelAnimationFrame', (id: ReturnType<typeof setTimeout>) => clearTimeout(id));
});
afterEach(() => {
  for (const action of actions) action.destroy();
  vi.unstubAllGlobals(); vi.useRealTimers();
});
function mount(read = vi.fn()) {
  actions.push(exposure(node, read));
  intersections.at(-1)!([{ intersectionRatio: 1 }]);
  return read;
}

it('shares one bounded observer and cancels pending checks/dwells on final unmount', () => {
  const first = mount(); const second = mount();
  expect(observe).toHaveBeenCalledTimes(1);
  covered = false; mutations(); mutations();
  expect(vi.getTimerCount()).toBe(1); // DOM bursts coalesce, not a recurring loop.
  vi.advanceTimersByTime(16);
  actions.shift()!.destroy();
  expect(disconnect).not.toHaveBeenCalled();
  actions.shift()!.destroy();
  expect(disconnect).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
  vi.advanceTimersByTime(1000);
  expect(first).not.toHaveBeenCalled(); expect(second).not.toHaveBeenCalled();
});

it('unrelated DOM churn does not starve dwell; occlusion cancels it and requires a fresh dwell', () => {
  const read = mount();
  covered = false; mutations(); vi.advanceTimersByTime(216);
  mutations(); vi.advanceTimersByTime(116);
  covered = true; mutations(); vi.advanceTimersByTime(216);
  expect(read).not.toHaveBeenCalled();
  covered = false; mutations(); vi.advanceTimersByTime(216);
  mutations(); vi.advanceTimersByTime(299);
  expect(read).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(read).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it('rechecks focus, visibility and attachment at write time', () => {
  const read = mount();
  covered = false; mutations(); vi.advanceTimersByTime(16);
  focused = false; vi.advanceTimersByTime(500);
  expect(read).not.toHaveBeenCalled();
  focused = true; mutations(); vi.advanceTimersByTime(16);
  visible = false; vi.advanceTimersByTime(500);
  expect(read).not.toHaveBeenCalled();
  visible = true; mutations(); vi.advanceTimersByTime(16);
  connected = false; vi.advanceTimersByTime(500);
  expect(read).not.toHaveBeenCalled();
});
