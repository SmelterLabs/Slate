// One behaviour for every modal dialog: `use:trapFocus={{ onescape: close }}` on the element with
// role="dialog".
//
// - Opening moves focus into the dialog (to the dialog itself, unless something inside already
//   took it, such as a search box), so a screen reader announces it and Escape reaches it.
// - Tab and Shift+Tab cycle through the dialog's controls and never reach the page behind.
//   `enabled: false` turns this off: the docked task window is not modal, so Tab moves on to
//   the board beside it.
// - Escape calls `onescape`, once, for the dialog on top only: a dialog opened over another
//   closes alone, and nothing behind it (the task window, the selection) hears that Escape.
// - Closing gives focus back to the control that opened it (a card, a button), if it is still
//   on the page and the dialog's own action has not already put focus somewhere else (a picker
//   that inserts into the editor and focuses it keeps that).
//
// Something drawn over the dialog that is not one of these (a floated menu, a picker) keeps
// its own keys: while focus is outside the dialog, Tab is left alone.

export interface TrapOptions {
  /** False: Tab is not held inside (a non-modal window). Default true. */
  enabled?: boolean;
  /** What Escape does. Without it, Escape is left to whoever else listens. */
  onescape?: () => void;
}

const FOCUSABLE =
  'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [contenteditable="true"], [tabindex]:not([tabindex="-1"])';

/** Open dialogs, the last one on top. */
const open: HTMLElement[] = [];

// The last few elements that had focus, newest last. A dialog's own code (an autofocus, an
// onMount) may move focus inside it before the trap starts, and a dialog opened from a menu item
// outlives the menu: the opener is the newest of these outside the dialog that is still on the
// page when it closes (the menu's button, once the menu itself is gone).
const recent: Element[] = [];
if (typeof document !== 'undefined') {
  document.addEventListener(
    'focusin',
    (e) => {
      const el = e.target as Element;
      const at = recent.indexOf(el);
      if (at >= 0) recent.splice(at, 1);
      recent.push(el);
      if (recent.length > 8) recent.shift();
    },
    true,
  );
}

function openers(node: HTMLElement): HTMLElement[] {
  const active = document.activeElement;
  const all = active && !recent.includes(active) ? [...recent, active] : [...recent];
  return all.filter((el): el is HTMLElement => el instanceof HTMLElement && el !== document.body && !node.contains(el)).reverse();
}

function controls(node: HTMLElement): HTMLElement[] {
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length);
}

/** Tab inside `node`: wrap from the last control to the first and back. */
function cycle(node: HTMLElement, e: KeyboardEvent) {
  const active = document.activeElement;
  if (active && active !== document.body && !node.contains(active)) return;
  const all = controls(node);
  const first = all[0];
  const last = all.at(-1);
  if (!first || !last) {
    e.preventDefault();
    node.focus({ preventScroll: true });
  } else if (!all.includes(active as HTMLElement)) {
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  } else if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

export function trapFocus(node: HTMLElement, options: TrapOptions = {}) {
  let opts = options;
  const previous = openers(node);
  open.push(node);
  if (!node.hasAttribute('tabindex')) node.tabIndex = -1;
  if (!node.contains(document.activeElement)) node.focus({ preventScroll: true });

  const top = () => open.at(-1) === node;

  function escape(e: KeyboardEvent) {
    if (!opts.onescape || e.defaultPrevented) return;
    e.preventDefault();
    e.stopPropagation();
    opts.onescape();
  }

  // On the document, not the dialog: Svelte hands its own onkeydown handlers their events at the
  // app's root, so a control inside the dialog with its own Escape (a rename box, a picker)
  // handles it first and, by stopping it, keeps the dialog open. The board's own shortcuts
  // listen on the window, after this, so an Escape the dialog took never reaches them.
  function onKey(e: KeyboardEvent) {
    if (!top()) return;
    if (e.key === 'Tab' && opts.enabled !== false) cycle(node, e);
    else if (e.key === 'Escape') escape(e);
  }

  document.addEventListener('keydown', onKey);
  return {
    update(next: TrapOptions = {}) {
      opts = next;
    },
    destroy() {
      document.removeEventListener('keydown', onKey);
      const at = open.indexOf(node);
      if (at >= 0) open.splice(at, 1);
      const active = document.activeElement;
      const lost = !active || active === document.body || node.contains(active);
      if (lost) previous.find((el) => el.isConnected)?.focus({ preventScroll: true });
    },
  };
}
