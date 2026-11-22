// @vitest-environment happy-dom
// One priority picker, two looks: the card's pill opens the board menu (filled flags in the
// flag colours, the current one bold, Clear with its icon); the task window's field opens its
// own list.
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import PriorityPicker from './PriorityPicker.svelte';

const pill = createRawSnippet(() => ({ render: () => '<span>High</span>' }));
const label = (b: Element) => b.textContent!.trim();

it('the card menu keeps the board look and sets the picked priority', () => {
  const onchange = vi.fn();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(PriorityPicker, { target, props: { value: 'high', onchange, children: pill } });
  try {
    target.querySelector<HTMLButtonElement>('.dd-btn')!.click();
    flushSync();
    const buttons = [...document.body.querySelectorAll('.dd-panel .menu button')];
    expect(buttons.map(label)).toEqual(['Urgent', 'High', 'Normal', 'Low', 'Clear']);
    const flags = buttons.slice(0, 4);
    expect(flags.map((b) => ['urgent', 'high', 'normal', 'low'].find((p) => b.classList.contains(`prio-flag-${p}`))))
      .toEqual(['urgent', 'high', 'normal', 'low']);
    expect(flags.map((b) => b.querySelector('svg')?.getAttribute('fill'))).toEqual(Array(4).fill('currentColor'));
    expect(buttons.filter((b) => b.classList.contains('on')).map(label)).toEqual(['High']);
    expect(buttons[4].querySelector('svg')).not.toBeNull();
    expect(document.body.querySelector('.dd-panel [role="listbox"]')).toBeNull();
    (buttons[0] as HTMLButtonElement).click();
    expect(onchange).toHaveBeenCalledWith('urgent');
  } finally {
    unmount(app);
    target.remove();
  }
});

it('the task window field opens its own list of priorities', () => {
  const onchange = vi.fn();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(PriorityPicker, { target, props: { value: null, onchange } });
  try {
    const field = target.querySelector<HTMLButtonElement>('button[aria-label="Priority: none"]')!;
    expect(label(field)).toBe('Empty');
    field.click();
    flushSync();
    const options = [...target.querySelectorAll('[role="listbox"] [role="option"] button')];
    expect(options.map(label)).toEqual(['Urgent', 'High', 'Normal', 'Low']);
    (options[3] as HTMLButtonElement).click();
    expect(onchange).toHaveBeenCalledWith('low');
  } finally {
    unmount(app);
    target.remove();
  }
});
