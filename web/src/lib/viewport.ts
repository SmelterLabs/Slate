// The on-screen keyboard. iOS (and Android Chrome by default) lay the page out as if there were
// no keyboard and slide it up over the bottom, so a composer pinned to the bottom of the task
// window or a chat ends up under the keys. This keeps `--kb` on <html> at the keyboard's height
// (0 when it is down) and `keyboard-open` on <html> while it is up; the shell, the task window
// and the chat size themselves to `100dvh - var(--kb)`. A focused field is scrolled back into
// view once the keyboard has come up. Desktop keyboards change nothing: --kb stays 0.

/** Keyboard height from the visual viewport; under `min` px it is a toolbar, not a keyboard. */
export function keyboardInset(innerHeight: number, vvHeight: number, vvOffsetTop: number, min = 80): number {
  const kb = Math.round(innerHeight - vvHeight - vvOffsetTop);
  return kb >= min ? kb : 0;
}

let installed = false;

export function installKeyboardInset() {
  if (installed || typeof window === 'undefined' || !window.visualViewport) return;
  installed = true;
  const vv = window.visualViewport;
  const root = document.documentElement;
  let last = -1;
  const set = () => {
    const kb = keyboardInset(window.innerHeight, vv.height, vv.offsetTop);
    if (kb === last) return;
    last = kb;
    root.style.setProperty('--kb', `${kb}px`);
    root.classList.toggle('keyboard-open', kb > 0);
    // iOS scrolls the whole page to show the field; with the shell now short enough, put it back.
    if (kb > 0 && window.scrollY) window.scrollTo(0, 0);
    if (kb > 0) reveal();
  };
  const reveal = () => {
    const el = document.activeElement as HTMLElement | null;
    if (el?.matches('input, textarea, select, [contenteditable="true"]')) el.scrollIntoView({ block: 'nearest' });
  };
  vv.addEventListener('resize', set);
  vv.addEventListener('scroll', set);
  document.addEventListener('focusin', () => setTimeout(reveal, 350));
  set();
}
