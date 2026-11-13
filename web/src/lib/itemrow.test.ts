import { beforeEach, describe, expect, it, vi } from 'vitest';

const board = vi.hoisted(() => ({
  focusId: null as string | null,
  selected: new Set<string>(),
  open: vi.fn(),
  toggleSelect: vi.fn(),
}));
vi.mock('./store.svelte', () => ({ board }));

import { rowHandlers } from './itemrow';

const hooks = () => ({ arm: vi.fn(), menu: vi.fn(), drag: vi.fn() });
const mouse = (over: Partial<MouseEvent> = {}) => ({ shiftKey: false, preventDefault: vi.fn(), ...over }) as unknown as MouseEvent;

beforeEach(() => {
  board.focusId = null;
  board.selected.clear();
  board.open.mockClear();
  board.toggleSelect.mockClear();
});

describe('rowHandlers (a card and a list row)', () => {
  it('a click opens the item; with Shift, or while a selection is going, it selects instead', () => {
    const h = rowHandlers('I1', hooks());
    h.onclick(mouse());
    expect(board.open).toHaveBeenCalledWith('I1');
    h.onclick(mouse({ shiftKey: true }));
    expect(board.toggleSelect).toHaveBeenLastCalledWith('I1', true);
    board.selected.add('I2');
    h.onclick(mouse());
    expect(board.toggleSelect).toHaveBeenLastCalledWith('I1', false);
    expect(board.open).toHaveBeenCalledTimes(1);
  });

  it('Enter opens only when the row itself has focus, not a control inside it', () => {
    const h = rowHandlers('I1', hooks());
    const row = {};
    h.onkeydown({ key: 'Enter', target: row, currentTarget: row } as unknown as KeyboardEvent);
    h.onkeydown({ key: 'Enter', target: {}, currentTarget: row } as unknown as KeyboardEvent);
    h.onkeydown({ key: ' ', target: row, currentTarget: row } as unknown as KeyboardEvent);
    expect(board.open).toHaveBeenCalledTimes(1);
  });

  it('hover and focus make it the card the shortcuts act on, and arm its menus', () => {
    const k = hooks();
    const h = rowHandlers('I1', k);
    h.onmouseenter();
    expect(board.focusId).toBe('I1');
    h.onfocusin();
    expect(k.arm).toHaveBeenCalledTimes(2);
    expect(k.menu).not.toHaveBeenCalled();
  });

  it("a right-click opens the row's menu instead of the browser's", () => {
    const k = hooks();
    const e = mouse();
    rowHandlers('I1', k).oncontextmenu(e);
    expect(e.preventDefault).toHaveBeenCalled();
    expect(k.arm).toHaveBeenCalled();
    expect(k.menu).toHaveBeenCalled();
    expect(board.focusId).toBe('I1');
  });

  it("a drag carries the item's id as a move, and says when it starts and ends", () => {
    const k = hooks();
    const h = rowHandlers('I1', k);
    const data = { setData: vi.fn(), effectAllowed: 'all' };
    h.ondragstart({ dataTransfer: data } as unknown as DragEvent);
    expect(data.setData).toHaveBeenCalledWith('text/plain', 'I1');
    expect(data.effectAllowed).toBe('move');
    h.ondragend();
    expect(k.drag.mock.calls).toEqual([[true], [false]]);
  });
});
