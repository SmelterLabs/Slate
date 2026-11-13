// What a board card and a list row both do with the pointer and the keyboard: drag carries the
// item's id, a click opens it (or, with Shift or while a selection is going, selects it), Enter
// on the row opens it, a right-click opens its ⋯ menu, and the first hover or focus makes it the
// card `x` and `s` act on and mounts its menus.
//
// Spread the result on the row's element: `{...rowHandlers(item.id, { arm, menu })}`. They are
// event attributes, not a `use:` action, on purpose: Svelte runs attribute handlers in the same
// delegated pass as the buttons inside the row, so a button that stops its click (the circle,
// the checkbox, a pill) keeps the row from opening. A native listener on the row would run first.
import { board } from './store.svelte';

export interface RowHooks {
  /** First hover, focus or right-click: mount what the row only needs once someone is there. */
  arm: () => void;
  /** Right-click: open the row's ⋯ menu (after `arm`). */
  menu: () => void;
  /** A drag of the row starts (true) or ends (false). */
  drag?: (dragging: boolean) => void;
}

export function rowHandlers(id: string, hooks: RowHooks) {
  const arm = () => {
    board.focusId = id;
    hooks.arm();
  };
  return {
    ondragstart: (e: DragEvent) => {
      e.dataTransfer?.setData('text/plain', id);
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
      hooks.drag?.(true);
    },
    ondragend: () => hooks.drag?.(false),
    onclick: (e: MouseEvent) => (e.shiftKey || board.selected.size ? board.toggleSelect(id, e.shiftKey) : board.open(id)),
    onkeydown: (e: KeyboardEvent) => e.key === 'Enter' && e.target === e.currentTarget && board.open(id),
    oncontextmenu: (e: MouseEvent) => {
      e.preventDefault();
      arm();
      hooks.menu();
    },
    onmouseenter: arm,
    onfocusin: arm,
  };
}
