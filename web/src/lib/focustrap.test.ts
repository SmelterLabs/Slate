// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { trapFocus } from './focustrap';

function el(html: string): HTMLElement {
  const host = document.createElement('div');
  host.innerHTML = html;
  document.body.append(host);
  return host.firstElementChild as HTMLElement;
}

function tab(shift = false) {
  const e = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, bubbles: true, cancelable: true });
  (document.activeElement ?? document.body).dispatchEvent(e);
  return e.defaultPrevented;
}

function escape() {
  const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  (document.activeElement ?? document.body).dispatchEvent(e);
  return e;
}

beforeEach(() => {
  document.body.innerHTML = '';
  // happy-dom lays nothing out: every element counts as shown.
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{}] as unknown as DOMRectList);
});
afterEach(() => vi.restoreAllMocks());

describe('trapFocus', () => {
  it('moves focus in, wraps Tab both ways, and gives focus back on close', () => {
    const opener = el('<button>Open</button>');
    opener.focus();
    const dialog = el('<div role="dialog"><button id="a">A</button><button id="b">B</button></div>');
    const trap = trapFocus(dialog);
    expect(document.activeElement).toBe(dialog);
    expect(tab()).toBe(true); // from the dialog itself to its first control
    expect(document.activeElement?.id).toBe('a');
    document.getElementById('b')!.focus();
    expect(tab()).toBe(true);
    expect(document.activeElement?.id).toBe('a');
    expect(tab(true)).toBe(true);
    expect(document.activeElement?.id).toBe('b');
    expect(tab()).toBe(true);
    expect(document.activeElement?.id).toBe('a');
    expect(tab()).toBe(false); // a to b: the browser's own move
    trap.destroy();
    expect(document.activeElement).toBe(opener);
  });

  it('keeps focus that something inside took first, and still returns to the opener', () => {
    const opener = el('<button>Open</button>');
    opener.focus();
    const dialog = el('<div role="dialog"><input id="q" /></div>');
    (dialog.querySelector('#q') as HTMLElement).focus();
    const trap = trapFocus(dialog);
    expect(document.activeElement?.id).toBe('q');
    trap.destroy();
    expect(document.activeElement).toBe(opener);
  });

  it('a dialog opened from a menu item returns to the menu button once the menu is gone', () => {
    const button = el('<button>Options</button>');
    button.focus();
    const menu = el('<div role="menu"><button role="menuitem">Settings</button></div>');
    (menu.firstElementChild as HTMLElement).focus();
    const dialog = el('<div role="dialog"><button>Close</button></div>');
    const trap = trapFocus(dialog);
    menu.remove();
    trap.destroy();
    expect(document.activeElement).toBe(button);
  });

  it('does not take focus back when the dialog already put it somewhere else', () => {
    const opener = el('<button>Open</button>');
    opener.focus();
    const dialog = el('<div role="dialog"><button>Pick</button></div>');
    const trap = trapFocus(dialog);
    const editor = el('<div contenteditable="true" tabindex="0"></div>');
    editor.focus();
    trap.destroy();
    expect(document.activeElement).toBe(editor);
  });

  it('Escape closes the dialog on top only, and nobody behind hears it', () => {
    const behind = vi.fn();
    window.addEventListener('keydown', behind);
    const under = vi.fn();
    const over = vi.fn();
    const a = trapFocus(el('<div role="dialog"><button>A</button></div>'), { onescape: under });
    const b = trapFocus(el('<div role="dialog"><button>B</button></div>'), { onescape: over });
    escape();
    expect(over).toHaveBeenCalledTimes(1);
    expect(under).not.toHaveBeenCalled();
    expect(behind).not.toHaveBeenCalled();
    b.destroy();
    escape();
    expect(under).toHaveBeenCalledTimes(1);
    a.destroy();
    window.removeEventListener('keydown', behind);
  });

  it('an Escape a control inside already handled leaves the dialog open', () => {
    const close = vi.fn();
    const dialog = el('<div role="dialog"><input id="rename" /></div>');
    const input = dialog.querySelector('#rename') as HTMLElement;
    input.addEventListener('keydown', (e) => e.stopPropagation());
    const trap = trapFocus(dialog, { onescape: close });
    input.focus();
    escape();
    expect(close).not.toHaveBeenCalled();
    trap.destroy();
  });

  // A tag suggestion list closes a moment after its input loses focus: a suggestion Tab had
  // reached went with it, and focus fell back to the page behind the dialog.
  it('a control removed while it has focus: the dialog takes focus, and Tab carries on from there', async () => {
    const dialog = el(
      '<div role="dialog"><button id="a">A</button><span id="tags"><input id="q" /><span id="list"></span></span><button id="c">C</button></div>',
    );
    const list = dialog.querySelector('#list') as HTMLElement;
    const trap = trapFocus(dialog);
    for (const shift of [false, true]) {
      list.innerHTML = '<button id="s1">#one</button><button id="s2">#two</button>';
      (dialog.querySelector('#s2') as HTMLElement).focus();
      list.innerHTML = '';
      await Promise.resolve();
      expect(document.activeElement).toBe(dialog);
      expect(tab(shift)).toBe(true);
      expect(document.activeElement?.id).toBe(shift ? 'q' : 'c');
    }
    // Once it has moved on, Tab is the ordinary cycle again.
    expect(tab()).toBe(false);
    trap.destroy();
  });

  it('a lost list item: Tab carries on at the item after it', async () => {
    const dialog = el(
      '<div role="dialog"><button id="a">A</button><ul><li id="one"><button id="s1">1</button></li><li><button id="s2">2</button></li></ul><button id="c">C</button></div>',
    );
    const trap = trapFocus(dialog);
    document.getElementById('s1')!.focus();
    document.getElementById('one')!.remove();
    await Promise.resolve();
    expect(document.activeElement).toBe(dialog);
    expect(tab()).toBe(true);
    expect(document.activeElement?.id).toBe('s2');
    trap.destroy();
  });

  it('controls that are not Tab stops: wrapping skips them, and Tab from one stays inside', () => {
    const dialog = el(
      '<div role="dialog"><button id="a">A</button><button id="b">B</button><button id="x" tabindex="-1">X</button></div>',
    );
    const trap = trapFocus(dialog);
    document.getElementById('b')!.focus();
    expect(tab()).toBe(true); // b is the last stop: around to a, not on to x
    expect(document.activeElement?.id).toBe('a');
    document.getElementById('x')!.focus();
    expect(tab()).toBe(true); // nothing to Tab to after x inside: around to a
    expect(document.activeElement?.id).toBe('a');
    document.getElementById('x')!.focus();
    expect(tab(true)).toBe(false); // b is before it: the browser's own move
    trap.destroy();
  });

  it('focus that falls back to the page while the dialog is open comes back to it', async () => {
    const dialog = el('<div role="dialog"><button id="a">A</button><button id="b">B</button></div>');
    const trap = trapFocus(dialog);
    const b = document.getElementById('b')!;
    b.focus();
    b.blur();
    expect(document.activeElement).toBe(document.body);
    await new Promise((r) => setTimeout(r, 50));
    expect(document.activeElement).toBe(dialog);
    expect(tab()).toBe(true);
    expect(dialog.contains(document.activeElement)).toBe(true);
    trap.destroy();
  });

  it('a window that is not modal, or a dialog under another, leaves lost focus alone', async () => {
    const docked = el('<div role="dialog"><span id="d"><button id="x">X</button></span></div>');
    const a = trapFocus(docked, { enabled: false });
    document.getElementById('x')!.focus();
    document.getElementById('d')!.innerHTML = '';
    await new Promise((r) => setTimeout(r, 50));
    expect(document.activeElement).toBe(document.body);
    a.destroy();

    const under = el('<div role="dialog"><span id="u"><button id="y">Y</button></span></div>');
    const b = trapFocus(under);
    document.getElementById('y')!.focus();
    const over = el('<div role="dialog"><button id="z">Z</button></div>');
    const c = trapFocus(over);
    document.getElementById('u')!.innerHTML = '';
    await new Promise((r) => setTimeout(r, 50));
    expect(document.activeElement).toBe(over);
    c.destroy();
    b.destroy();
  });

  it('without onescape, Escape is left to the page; disabled, Tab is left alone', () => {
    const behind = vi.fn((e: KeyboardEvent) => e.key);
    window.addEventListener('keydown', behind);
    const dialog = el('<div role="dialog"><button id="only">Only</button></div>');
    const trap = trapFocus(dialog, { enabled: false });
    document.getElementById('only')!.focus();
    expect(tab()).toBe(false);
    escape();
    expect(behind.mock.results.map((r) => r.value)).toEqual(['Tab', 'Escape']);
    trap.update({ enabled: true });
    expect(tab()).toBe(true);
    trap.destroy();
    window.removeEventListener('keydown', behind);
  });
});
