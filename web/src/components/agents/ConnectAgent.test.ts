// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import { board } from '../../lib/store.svelte';
import { post } from '../../lib/api';
import ConnectAgent from './ConnectAgent.svelte';

vi.mock('../../lib/api', async (original) => ({
  ...await original<typeof import('../../lib/api')>(),
  post: vi.fn(async () => ({ token: 'test-token' })),
}));

it('points a newly connected MCP agent to the core work tool', async () => {
  const agent = { id: 'A1', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' } as const;
  const target = document.createElement('div');
  document.body.appendChild(target);
  board.me = {
    account: { id: 'P1', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    scopes: ['admin'], prefs: {}, token_id: 'T1',
    access: { all: true, level: 'edit', manage: true, general: true, sections: {}, lists: {} },
  };
  const app = mount(ConnectAgent, {
    target, props: { agents: [agent], preset: agent.id, onclose: vi.fn(), oncreated: vi.fn() },
  });
  try {
    target.querySelector<HTMLInputElement>('[aria-label="Let my agent use Slate"]')!.click();
    flushSync();
    target.querySelector<HTMLInputElement>('input[value="all"]')!.click();
    flushSync();
    target.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await vi.waitFor(() => expect(target.querySelector('[aria-label="Token"]')).not.toBeNull());
    expect(post).toHaveBeenCalledWith('/accounts/A1/tokens', { label: null, scopes: ['write'] });
    expect(target.textContent).toContain('slate_work next');
    expect(target.textContent).not.toContain('slate_checkin');
  } finally {
    await unmount(app);
    target.remove();
  }
});
