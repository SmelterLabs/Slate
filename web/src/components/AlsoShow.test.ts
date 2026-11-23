// @vitest-environment happy-dom
import { mount, tick, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import type { Section } from '../lib/types';
import AlsoShow from './AlsoShow.svelte';

const section: Section = {
  id: 'S2', name: 'Work', slug: 'work', audience: 'work', position: 0,
  created_at: '', archived_at: null, color: '#1684aa',
};

it('shows section toggles and shows Mine / Everyone only when a section is on', async () => {
  const target = document.createElement('div');
  const onchange = vi.fn();
  const options = [{ section, count: 3 }];
  const setting = { sections: [] as string[], everyone: false };
  const app = mount(AlsoShow, { target, props: { setting, options, onchange } });
  try {
    const toggle = target.querySelector('button')!;
    expect(toggle.textContent).toMatch(/Work\s+3/);
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(target.textContent).not.toContain('Everyone');
    toggle.click();
    expect(onchange).toHaveBeenCalledWith({ sections: ['S2'], everyone: false });
  } finally {
    await unmount(app);
  }

  const enabled = mount(AlsoShow, { target, props: {
    setting: { sections: ['S2'], everyone: false }, options, onchange,
  } });
  try {
    await tick();
    const [toggle, mine, everyone] = target.querySelectorAll('button');
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(mine.textContent?.trim()).toBe('Mine');
    expect(mine.getAttribute('aria-pressed')).toBe('true');
    everyone.click();
    expect(onchange).toHaveBeenLastCalledWith({ sections: ['S2'], everyone: true });
  } finally {
    await unmount(enabled);
  }
});

it('renders nothing without an eligible section', async () => {
  const target = document.createElement('div');
  const app = mount(AlsoShow, { target, props: {
    setting: { sections: [], everyone: false }, options: [], onchange: vi.fn(),
  } });
  expect(target.childElementCount).toBe(0);
  await unmount(app);
});
