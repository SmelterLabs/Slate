// Swipe a row sideways with a finger: right completes it, left snoozes it to tomorrow (the same
// two actions the row's check circle and its date menu have, on every device). The row slides
// with the finger over a coloured strip naming the action; past THRESHOLD it ticks and the
// action runs on release, short of it the row springs back. A mostly-vertical move is a scroll
// and is left alone; a held finger is a drag (lib/touchdrag.ts) and is left alone too.
import { haptic } from './touchdrag';

export const THRESHOLD = 88;

export interface SwipeOptions {
  /** Swipe right (finger moves toward the right edge). */
  right?: { label: string; tone: 'done' | 'snooze'; run: () => void };
  /** Swipe left. */
  left?: { label: string; tone: 'done' | 'snooze'; run: () => void };
}

/** Which way a move is going once it has gone far enough to tell: 'x', 'y', or null (not yet). */
export function axisOf(dx: number, dy: number, min = 10): 'x' | 'y' | null {
  if (Math.hypot(dx, dy) < min) return null;
  return Math.abs(dx) > Math.abs(dy) * 1.3 ? 'x' : 'y';
}

/** How far the row follows the finger: 1:1 up to the threshold, then with resistance. */
export function follow(dx: number, threshold = THRESHOLD): number {
  const a = Math.abs(dx);
  const d = a <= threshold ? a : threshold + (a - threshold) * 0.35;
  return Math.sign(dx) * d;
}

/** Svelte action: `use:swipe={{ right, left }}`. */
export function swipe(node: HTMLElement, initial: SwipeOptions) {
  let opts = initial;
  let x0 = 0;
  let y0 = 0;
  let dx = 0;
  let axis: 'x' | 'y' | null = null;
  let armed = false;
  let under: HTMLElement | null = null;

  function strip(side: 'right' | 'left') {
    const action = opts[side];
    if (!action) return null;
    const host = node.parentElement;
    if (!host) return null;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const el = document.createElement('div');
    el.className = `swipe-under tone-${action.tone} from-${side}`;
    el.setAttribute('aria-hidden', 'true');
    el.textContent = action.label;
    Object.assign(el.style, {
      top: `${node.offsetTop}px`,
      left: `${node.offsetLeft}px`,
      width: `${node.offsetWidth}px`,
      height: `${node.offsetHeight}px`,
    });
    host.insertBefore(el, node);
    return el;
  }

  function settle(run?: () => void) {
    node.style.transition = 'transform 180ms ease-out';
    node.style.transform = run ? `translateX(${Math.sign(dx) * node.offsetWidth}px)` : '';
    const gone = under;
    setTimeout(() => {
      node.style.transition = '';
      node.style.transform = '';
      node.classList.remove('swiping');
      gone?.remove();
      run?.();
    }, 180);
    under = null;
    axis = null;
    armed = false;
    dx = 0;
  }

  function start(e: TouchEvent) {
    if (e.touches.length !== 1 || (!opts.left && !opts.right)) return;
    if ((e.target as Element).closest('input, textarea, select, [contenteditable="true"]')) return;
    x0 = e.touches[0].clientX;
    y0 = e.touches[0].clientY;
    dx = 0;
    axis = null;
  }

  function move(e: TouchEvent) {
    const t = e.touches[0];
    if (!t || axis === 'y') return;
    if (document.documentElement.classList.contains('touch-dragging')) return;
    const mx = t.clientX - x0;
    const my = t.clientY - y0;
    if (axis === null) {
      axis = axisOf(mx, my);
      if (axis !== 'x') return;
    }
    const side = mx > 0 ? 'right' : 'left';
    if (!opts[side]) {
      dx = 0;
      node.style.transform = '';
      return;
    }
    if (e.cancelable) e.preventDefault();
    if (!under || !under.classList.contains(`from-${side}`)) {
      under?.remove();
      under = strip(side);
      node.classList.add('swiping');
    }
    dx = mx;
    node.style.transform = `translateX(${follow(dx)}px)`;
    const past = Math.abs(dx) >= THRESHOLD;
    if (past !== armed) {
      armed = past;
      under?.classList.toggle('armed', past);
      if (past) haptic();
    }
  }

  function end() {
    if (axis !== 'x') {
      axis = null;
      return;
    }
    const side = dx > 0 ? 'right' : 'left';
    settle(armed ? opts[side]?.run : undefined);
  }

  // A swipe that started must not also click the row.
  function click(e: MouseEvent) {
    if (node.classList.contains('swiping')) {
      e.preventDefault();
      e.stopPropagation();
    }
  }

  node.addEventListener('touchstart', start, { passive: true });
  node.addEventListener('touchmove', move, { passive: false });
  node.addEventListener('touchend', end);
  node.addEventListener('touchcancel', () => settle());
  node.addEventListener('click', click, true);
  return {
    update(next: SwipeOptions) {
      opts = next;
    },
    destroy() {
      under?.remove();
      node.removeEventListener('touchstart', start);
      node.removeEventListener('touchmove', move);
      node.removeEventListener('touchend', end);
      node.removeEventListener('click', click, true);
    },
  };
}
