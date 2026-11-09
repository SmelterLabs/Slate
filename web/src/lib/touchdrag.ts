// Drag by finger. Browsers only start an HTML drag from a mouse, so on a phone or tablet nothing
// that is `draggable="true"` moved. This turns a long press into that same drag: hold a card,
// row, calendar chip, favorite, list or page still for a moment and it lifts under the finger,
// with a tick of haptic feedback; then dragstart, dragenter, dragover, dragleave, drop and
// dragend go to whatever is under the finger, exactly as the mouse sends them. So every drop
// target written for the mouse works by touch unchanged, and phone and desktop share one code
// path. Near the edge of a scrolling column, board or page it scrolls, faster the closer you go.
// Holding and letting go without moving opens the element's right-click menu, as on a desktop.
//
// Pointer-driven drags (timeline bars) use `holdToDrag` for the same long
// press, so a quick swipe still scrolls and only a held finger grabs.

export const HOLD_MS = 350;
/** How far a finger may wander (px) before a press counts as a scroll instead. */
export const SLOP = 8;
/** Distance (px) from a scroll edge where auto-scroll starts, and its top speed (px/frame). */
export const EDGE = 56;
export const MAX_SPEED = 18;

/** Auto-scroll speed for a finger at `pos` in a box from `start` to `end`: negative scrolls
 * back, positive forward, 0 away from both edges. Ramps up linearly to MAX_SPEED at the edge. */
export function edgeSpeed(pos: number, start: number, end: number, edge = EDGE, max = MAX_SPEED): number {
  const size = end - start;
  const zone = Math.min(edge, size / 4);
  if (zone <= 0) return 0;
  if (pos < start + zone) return -Math.ceil(max * Math.min(1, (start + zone - pos) / zone));
  if (pos > end - zone) return Math.ceil(max * Math.min(1, (pos - (end - zone)) / zone));
  return 0;
}

/** True once a finger has moved further than SLOP from where it went down. */
export function beyondSlop(x0: number, y0: number, x: number, y: number, slop = SLOP): boolean {
  return Math.hypot(x - x0, y - y0) > slop;
}

/** A small "tick": the vibration motor where there is one (Android), and the switch-toggle
 * haptic that iOS 18 Safari plays when a `<input switch>` flips (there is no vibrate there). */
export function haptic() {
  try {
    if (typeof navigator.vibrate === 'function') {
      navigator.vibrate(12);
      return;
    }
    const label = document.createElement('label');
    label.ariaHidden = 'true';
    label.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.setAttribute('switch', '');
    label.appendChild(input);
    document.body.appendChild(label);
    label.click();
    label.remove();
  } catch {
    // feedback is a nicety; never let it break a drag
  }
}

/** The drag's data, standing in for the browser's DataTransfer (which only a real drag gets). */
class TouchData {
  #data = new Map<string, string>();
  dropEffect: DataTransfer['dropEffect'] = 'none';
  effectAllowed: DataTransfer['effectAllowed'] = 'all';
  readonly files = [] as unknown as FileList;
  readonly items = [] as unknown as DataTransferItemList;
  get types(): string[] {
    return [...this.#data.keys()];
  }
  #key(type: string) {
    const t = type.toLowerCase();
    return t === 'text' ? 'text/plain' : t === 'url' ? 'text/uri-list' : t;
  }
  setData(type: string, value: string) {
    this.#data.set(this.#key(type), String(value));
  }
  getData(type: string): string {
    return this.#data.get(this.#key(type)) ?? '';
  }
  clearData(type?: string) {
    if (type) this.#data.delete(this.#key(type));
    else this.#data.clear();
  }
  setDragImage() {}
}

const NO_CANCEL = new Set(['dragleave', 'dragend']);

function fire(type: string, target: EventTarget, x: number, y: number, data: TouchData, related: Element | null = null) {
  const init = {
    bubbles: true,
    cancelable: !NO_CANCEL.has(type),
    composed: true,
    clientX: x,
    clientY: y,
    screenX: x,
    screenY: y,
    relatedTarget: related,
  };
  const e = typeof DragEvent === 'function' ? new DragEvent(type, init) : new MouseEvent(type, init);
  Object.defineProperty(e, 'dataTransfer', { value: data });
  target.dispatchEvent(e);
  return e.defaultPrevented;
}

/** Text fields keep their own long press (select, magnifier). */
function editable(el: Element | null): boolean {
  return !!el?.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]');
}

function scrollable(el: Element, axis: 'x' | 'y'): boolean {
  const s = getComputedStyle(el);
  const over = axis === 'x' ? s.overflowX : s.overflowY;
  if (el === document.scrollingElement) {
    return axis === 'x' ? el.scrollWidth > el.clientWidth : el.scrollHeight > el.clientHeight;
  }
  if (over !== 'auto' && over !== 'scroll') return false;
  return axis === 'x' ? el.scrollWidth > el.clientWidth + 1 : el.scrollHeight > el.clientHeight + 1;
}

function chain(from: Element | null): Element[] {
  const out: Element[] = [];
  for (let el = from; el; el = el.parentElement) out.push(el);
  return out;
}

/** Scroll the nearest box under the finger that can still move toward the edge it is near,
 * one axis at a time; past a box's edge (a finger in the page gutter, beyond the board) the
 * boxes the drag started in count too. Returns true when something scrolled. */
export function autoScroll(under: Element | null, x: number, y: number, home: Element | null = null): boolean {
  let moved = false;
  const boxes = [...new Set([...chain(under), ...chain(home)])];
  for (const axis of ['x', 'y'] as const) {
    for (const el of boxes) {
      if (!scrollable(el, axis)) continue;
      const r =
        el === document.scrollingElement
          ? { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight }
          : el.getBoundingClientRect();
      const v = axis === 'x' ? edgeSpeed(x, r.left, r.right) : edgeSpeed(y, r.top, r.bottom);
      if (!v) continue;
      const before = axis === 'x' ? el.scrollLeft : el.scrollTop;
      if (axis === 'x') el.scrollLeft += v;
      else el.scrollTop += v;
      const after = axis === 'x' ? el.scrollLeft : el.scrollTop;
      if (after !== before) {
        moved = true;
        break;
      }
      // At its end already: let an outer box (the page, the board) take over.
    }
  }
  return moved;
}

// --- a touch session (shared by HTML drags and holdToDrag) ------------------------------------

/** True from a finger going down on something draggable until it lifts. While set, the native
 * long-press menu and the browser's own touch drag (iOS, Android) stay out of the way. */
let pressing = false;

/** Stops the page from scrolling under a held drag. */
function blockScroll(e: TouchEvent) {
  if (e.cancelable) e.preventDefault();
}

let installed = false;

/** Turn long presses into HTML drags, app-wide. Safe to call twice. */
export function installTouchDrag() {
  if (installed || typeof document === 'undefined') return;
  installed = true;

  let timer: ReturnType<typeof setTimeout> | null = null;
  let src: HTMLElement | null = null;
  let origin: Element | null = null;
  let x0 = 0;
  let y0 = 0;
  let x = 0;
  let y = 0;
  let lifted = false;
  let travelled = false;
  let data: TouchData | null = null;
  let ghost: HTMLElement | null = null;
  let dx = 0;
  let dy = 0;
  let over: Element | null = null;
  let accepted = false;
  let frame = 0;
  let lastOver = 0;
  let wasDraggable = false;

  function reset() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    ghost?.remove();
    ghost = null;
    src?.classList.remove('touch-lifted', 'touch-pressed');
    if (src && wasDraggable) src.setAttribute('draggable', 'true');
    wasDraggable = false;
    document.documentElement.classList.remove('touch-dragging');
    document.removeEventListener('touchmove', onMove, { capture: true } as EventListenerOptions);
    document.removeEventListener('touchend', onEnd, true);
    document.removeEventListener('touchcancel', onCancel, true);
    window.removeEventListener('scroll', onScroll, true);
    src = null;
    origin = null;
    data = null;
    over = null;
    accepted = false;
    lifted = false;
    travelled = false;
    // The native contextmenu and click that follow a touchend arrive after it; keep the guard
    // up through them.
    setTimeout(() => (pressing = false), 400);
  }

  function hover() {
    if (!data || !src) return;
    const el = document.elementFromPoint(x, y);
    if (el !== over) {
      if (el) fire('dragenter', el, x, y, data, over);
      if (over) fire('dragleave', over, x, y, data, el);
      over = el;
    }
    accepted = el ? fire('dragover', el, x, y, data) : false;
    lastOver = performance.now();
  }

  function tick() {
    if (!lifted) return;
    const under = document.elementFromPoint(x, y);
    // Browsers repeat dragover while the pointer rests; so do we, and after every scroll step.
    if (autoScroll(under, x, y, src) || performance.now() - lastOver > 100) hover();
    frame = requestAnimationFrame(tick);
  }

  function place() {
    if (ghost) ghost.style.transform = `translate3d(${x - dx}px, ${y - dy}px, 0) scale(1.03) rotate(1.2deg)`;
  }

  function lift() {
    timer = null;
    if (!src) return;
    data = new TouchData();
    if (fire('dragstart', src, x, y, data)) {
      // The element said no (a draggable that is not ready): treat it as a plain press.
      reset();
      return;
    }
    lifted = true;
    haptic();
    const r = src.getBoundingClientRect();
    dx = x - r.left;
    dy = y - r.top;
    ghost = src.cloneNode(true) as HTMLElement;
    ghost.removeAttribute('id');
    for (const n of ghost.querySelectorAll('[id]')) n.removeAttribute('id');
    ghost.classList.add('touch-ghost');
    ghost.setAttribute('aria-hidden', 'true');
    Object.assign(ghost.style, {
      position: 'fixed',
      left: '0',
      top: '0',
      width: `${r.width}px`,
      height: `${r.height}px`,
      margin: '0',
      pointerEvents: 'none',
      zIndex: '9999',
    });
    place();
    document.body.appendChild(ghost);
    src.classList.add('touch-lifted');
    document.documentElement.classList.add('touch-dragging');
    hover();
    frame = requestAnimationFrame(tick);
  }

  function onStart(e: TouchEvent) {
    if (src || e.touches.length !== 1) {
      if (src && !lifted) reset();
      return;
    }
    const t = e.touches[0];
    const target = e.target as Element | null;
    const el = target?.closest?.('[draggable="true"]') as HTMLElement | null;
    if (!el || editable(target) || target?.closest?.('[data-no-drag]')) return;
    src = el;
    origin = target;
    // While a finger is on it, the element is not natively draggable. iOS 15+ and Android start
    // their own drag from a long press on a draggable element; it claims the touch and sends
    // touchcancel, which dropped Slate's drag half a second after the card lifted. Ours does not
    // need the attribute: it fires the drag events itself. reset() puts it back.
    el.setAttribute('draggable', 'false');
    el.classList.add('touch-pressed');
    wasDraggable = true;
    x0 = x = t.clientX;
    y0 = y = t.clientY;
    pressing = true;
    timer = setTimeout(lift, HOLD_MS);
    document.addEventListener('touchmove', onMove, { capture: true, passive: false });
    document.addEventListener('touchend', onEnd, true);
    document.addEventListener('touchcancel', onCancel, true);
    window.addEventListener('scroll', onScroll, true);
  }

  function onScroll() {
    // The page moved under a finger that has not grabbed yet: it was a scroll.
    if (!lifted) reset();
  }

  function onMove(e: TouchEvent) {
    const t = e.touches[0];
    if (!t) return;
    x = t.clientX;
    y = t.clientY;
    if (!lifted) {
      if (beyondSlop(x0, y0, x, y) || e.touches.length > 1) reset();
      return;
    }
    if (e.cancelable) e.preventDefault();
    if (beyondSlop(x0, y0, x, y)) travelled = true;
    place();
    hover();
  }

  function onEnd(e: TouchEvent) {
    if (!lifted || !src || !data) {
      reset();
      return;
    }
    if (e.cancelable) e.preventDefault(); // no click on whatever is under the finger
    const from = src;
    const at = origin;
    const d = data;
    if (!travelled) {
      // Held and let go in place: the right-click menu, not a drop.
      if (over) fire('dragleave', over, x, y, d);
      fire('dragend', from, x, y, d);
      reset();
      at?.dispatchEvent(
        new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 2 }),
      );
      return;
    }
    hover();
    if (over && accepted) {
      d.dropEffect = d.effectAllowed === 'link' ? 'link' : d.effectAllowed === 'copy' ? 'copy' : 'move';
      fire('drop', over, x, y, d);
    } else if (over) {
      d.dropEffect = 'none';
      fire('dragleave', over, x, y, d);
    }
    fire('dragend', from, x, y, d);
    reset();
  }

  function onCancel() {
    if (lifted && src && data) {
      if (over) fire('dragleave', over, x, y, data);
      data.dropEffect = 'none';
      fire('dragend', src, x, y, data);
    }
    reset();
  }

  document.addEventListener('touchstart', onStart, { capture: true, passive: true });
  // The browser's own reactions to a long press: its menu, its text-selection callout, and (on
  // iOS 15+ and newer Android) a native drag that would race this one. Synthetic events (ours)
  // pass through.
  const guard = (e: Event) => {
    if (pressing && e.isTrusted) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  };
  document.addEventListener('contextmenu', guard, true);
  document.addEventListener('dragstart', guard, true);
  document.addEventListener('selectstart', guard, true);
}

/**
 * A pointer drag that waits for a long press when it is a finger. Mouse and pen start at once
 * (`start` runs inside this call); a finger must hold still for HOLD_MS first, so a swipe across
 * a timeline bar or a widget still scrolls the page. `start` gets the pointer event to begin from.
 * While a finger drag is on, the page does not scroll.
 */
export function holdToDrag(e: PointerEvent, start: (from: PointerEvent) => void) {
  if (e.pointerType !== 'touch') {
    start(e);
    return;
  }
  const x0 = e.clientX;
  const y0 = e.clientY;
  const id = e.pointerId;
  pressing = true;
  let started = false;
  const timer = setTimeout(() => {
    started = true;
    haptic();
    document.addEventListener('touchmove', blockScroll, { capture: true, passive: false });
    start(e);
  }, HOLD_MS);
  const move = (m: PointerEvent) => {
    if (m.pointerId === id && !started && beyondSlop(x0, y0, m.clientX, m.clientY)) done();
  };
  const done = (u?: PointerEvent) => {
    if (u && u.pointerId !== id) return;
    clearTimeout(timer);
    window.removeEventListener('pointermove', move, true);
    window.removeEventListener('pointerup', done, true);
    window.removeEventListener('pointercancel', done, true);
    document.removeEventListener('touchmove', blockScroll, { capture: true } as EventListenerOptions);
    setTimeout(() => (pressing = false), 400);
  };
  window.addEventListener('pointermove', move, true);
  window.addEventListener('pointerup', done, true);
  window.addEventListener('pointercancel', done, true);
}
