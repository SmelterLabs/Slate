// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import AgentWake from './AgentWake.svelte';

it('copies a task-only prompt with the core lease calls', async () => {
  const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(AgentWake, {
    target,
    props: {
      account: { id: 'A1', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' },
      wake: 'pull', admin: false, onchange: vi.fn(),
    },
  });
  try {
    target.querySelector('button')!.click();
    flushSync();
    target.querySelector<HTMLButtonElement>('.pull-prompt button')!.click();
    const prompt = target.querySelector('pre')!.textContent;
    expect(write).toHaveBeenCalledWith(prompt);
    expect(prompt).toContain('kinds: ["task"]');
    expect(prompt).toContain('lease.run_id');
    expect(prompt).toContain('item_id');
    expect(prompt).toContain('"review" (set status');
    expect(prompt?.match(/slate_\w+/g)).toEqual(['slate_work', 'slate_work', 'slate_work']);
  } finally {
    write.mockRestore();
    await unmount(app);
    target.remove();
  }
});
