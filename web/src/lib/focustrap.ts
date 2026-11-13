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
// - Focus never rests on nothing while a modal dialog is open: when the control that has it
//   disappears (a suggestion list that closes, a panel that re-renders) or focus otherwise
//   falls back to the page itself, the dialog takes it, and the next Tab carries on from
//   where the lost control was rather than from the top. Every Tab reads the dialog's
//   controls as they are at that moment.
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

/** Nothing has focus: the page itself does (or nothing at all). */
const lost = (active: Element | null) => !active || active === document.body;

/** Where a control that lost focus was: Tab carries on at `at` (`before`) or just past it. */
interface Spot {
  at: Element;
  before: boolean;
}

const follows = (a: Element, el: Element) => !!(a.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);

/**
 * Tab inside `node`: wrap from the last control to the first and back. From the dialog itself
 * (or from nothing) after a control was lost, `spot` says where Tab carries on: the first
 * control past it, or with Shift the last one before it.
 */
function cycle(node: HTMLElement, e: KeyboardEvent, spot: Spot | null) {
  const active = document.activeElement;
  if (!lost(active) && !node.contains(active)) return;
  const all = controls(node);
  // Where Tab can land; the rest (tabindex -1: a menu's items, a list picked with the arrows)
  // take focus only from a click or their own keys.
  const stops = all.filter((el) => el.tabIndex >= 0);
  const first = stops[0];
  const last = stops.at(-1);
  if (!first || !last) {
    e.preventDefault();
    node.focus({ preventScroll: true });
  } else if (stops.includes(active as HTMLElement)) {
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  } else if (all.includes(active as HTMLElement)) {
    // Not a Tab stop itself: the browser moves on from it, unless nothing inside is that way.
    const on = e.shiftKey ? stops.some((el) => follows(el, active!)) : stops.some((el) => follows(active!, el));
    if (!on) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    }
  } else {
    e.preventDefault();
    let next: HTMLElement | undefined;
    if (spot?.at.isConnected && (active === node || lost(active))) {
      const { at, before } = spot;
      next = e.shiftKey
        ? stops.filter((el) => (before ? el !== at && !follows(at, el) : !follows(at, el) || at.contains(el))).at(-1)
        : stops.find((el) => (before ? el === at || follows(at, el) : follows(at, el) && !at.contains(el)));
    }
    (next ?? (e.shiftKey ? last : first)).focus();
  }
}

export function trapFocus(node: HTMLElement, options: TrapOptions = {}) {
  let opts = options;
  const previous = openers(node);
  open.push(node);
  if (!node.hasAttribute('tabindex')) node.tabIndex = -1;
  if (!node.contains(document.activeElement)) node.focus({ preventScroll: true });

  const top = () => open.at(-1) === node;
  const modal = () => opts.enabled !== false;

  // The control inside that last had focus and its ancestors, up to the dialog, each with the
  // element after it then. If the control goes, the nearest of these still on the page marks
  // the place Tab carries on from: just before the part that followed what went, or just past
  // the nearest surviving ancestor.
  let path: { el: Element; next: Element | null }[] = [];
  let resume: Spot | null = null;
  function surviving(): Spot | null {
    for (let i = 0; i < path.length; i++) {
      const { el } = path[i];
      if (!el.isConnected || !node.contains(el)) continue;
      const next = i ? path[i - 1].next : null;
      return next?.isConnected && el.contains(next) ? { at: next, before: true } : { at: el, before: false };
    }
    return null;
  }

  // Focus fell back to the page while this dialog is open and on top: take it into the dialog.
  function regain() {
    if (!top() || !modal() || !node.isConnected || !lost(document.activeElement)) return;
    resume = surviving();
    node.focus({ preventScroll: true });
  }

  // While focus moves from one control to the next, the page holds it for a moment, and the
  // blur handlers that run then may change the dialog (a box that closes when you leave it):
  // that is not focus lost, so the check waits for the move to finish. `moving` is set before
  // any of those handlers run (a capture listener on the document) and cleared when focus lands.
  let moving = false;
  let later = 0;
  function settle() {
    cancelAnimationFrame(later);
    later = requestAnimationFrame(() => {
      moving = false;
      regain();
    });
  }
  function onBlur(e: FocusEvent) {
    moving = !!e.relatedTarget;
    settle();
  }
  function onFocus(e: FocusEvent) {
    moving = false;
    const el = e.target as Element;
    if (el === node || !node.contains(el)) return;
    path = [];
    for (let at: Element | null = el; at && at !== node; at = at.parentElement) {
      path.push({ el: at, next: at.nextElementSibling });
    }
    path.push({ el: node, next: null });
    resume = null;
  }
  // Removing the focused element hands focus to the page at once, with no blur event: catch it
  // as the dialog changes, so focus is back inside before anything else runs.
  const watch = new MutationObserver(() => (moving ? settle() : regain()));
  watch.observe(node, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['disabled', 'hidden', 'inert', 'class', 'style'],
  });
  document.addEventListener('blur', onBlur, true);
  document.addEventListener('focus', onFocus, true);

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
    if (e.key === 'Tab' && modal()) {
      cycle(node, e, resume ?? (lost(document.activeElement) ? surviving() : null));
      resume = null;
    } else if (e.key === 'Escape') escape(e);
  }

  document.addEventListener('keydown', onKey);
  return {
    update(next: TrapOptions = {}) {
      opts = next;
    },
    destroy() {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('blur', onBlur, true);
      document.removeEventListener('focus', onFocus, true);
      watch.disconnect();
      cancelAnimationFrame(later);
      const at = open.indexOf(node);
      if (at >= 0) open.splice(at, 1);
      const active = document.activeElement;
      if (lost(active) || node.contains(active)) previous.find((el) => el.isConnected)?.focus({ preventScroll: true });
    },
  };
}
