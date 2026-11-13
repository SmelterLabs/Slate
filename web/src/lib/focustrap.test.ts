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
