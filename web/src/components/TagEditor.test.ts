// @vitest-environment happy-dom
// A second tag is chosen before the first save comes back. The editor must send the whole
// set, and it must not let the slower first save land last.
import { mount, tick, unmount } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../lib/store.svelte', () => ({
  board: {
    tags: [{ id: 't1', name: 'tools', color: null }],
    tagMap: {},
    loadTags: () => Promise.resolve(),
  },
}));

import TagEditor from './TagEditor.svelte';

async function typeTag(input: HTMLInputElement, name: string) {
  input.focus();
  input.value = name;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await tick();
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await tick();
}

describe('TagEditor', () => {
  it('keeps the draft at the configured tag limit', async () => {
    const onchange = vi.fn();
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TagEditor, { target, props: { tags: ['tools'], onchange, maxTags: 1 } });
    try {
      await typeTag(target.querySelector('input')!, 'outdoor');
      expect(onchange).not.toHaveBeenCalled();
      expect([...target.querySelectorAll('.tag-pill')].map((el) => el.childNodes[0]?.textContent)).toEqual(['tools']);
    } finally {
      await unmount(app);
      target.remove();
    }
  });

  it('keeps the first tag when the next one is added before the save returns', async () => {
    const calls: string[][] = [];
    let releaseFirst!: () => void;
    const firstSave = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const onchange = (next: string[]) => {
      calls.push([...next]);
      return calls.length === 1 ? firstSave : Promise.resolve();
    };

    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(TagEditor, { target, props: { tags: [], onchange } });
    const input = target.querySelector('input') as HTMLInputElement;

    await typeTag(input, 'tools');
    await typeTag(input, 'outdoor');

    expect([...target.querySelectorAll('.tag-pill')].map((el) => el.childNodes[0]?.textContent)).toEqual([
      'tools',
      'outdoor',
    ]);
    // The second set waits, so the first write cannot land after it and drop outdoor.
    expect(calls).toEqual([['tools']]);

    releaseFirst();
    await vi.waitFor(() => expect(calls).toEqual([['tools'], ['tools', 'outdoor']]));

    await unmount(app);
    target.remove();
  });
});
