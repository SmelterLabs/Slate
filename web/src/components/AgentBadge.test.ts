// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import { item } from '../lib/fixtures';

vi.mock('../lib/store.svelte', () => ({
  board: {
    accounts: { sc: { id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' } },
    me: { account: { id: 'me', display_name: 'Alex' }, link_schemes: undefined },
    now: new Date('2026-10-29T12:00:00Z'),
    canRun: () => true,
    runRequested: {},
    pickup: () => null,
    runMode: () => 'run',
    runLabel: () => 'Run now',
    runAction: vi.fn(),
  },
}));
vi.mock('../lib/agents.svelte', () => ({
  agents: { entry: () => undefined },
  STATE_LABEL: { working: 'Working', blocked: 'Agent blocked', review: 'Ready for review', failed: 'Could not run' },
}));
vi.mock('../lib/task.svelte', () => ({ task: { clearAgentFlag: vi.fn() } }));

import AgentBadge from './AgentBadge.svelte';

describe('AgentBadge failed', () => {
  it('reads Could not run in a neutral tone with Run now', async () => {
    const target = document.createElement('div');
    const app = mount(AgentBadge, {
      target,
      props: {
        flag: { state: 'failed' as const, by: 'sc', at: '2026-10-29T10:00:00Z', note: 'Boom' },
        item: item({ agent: { state: 'failed', by: 'sc', at: '2026-10-29T10:00:00Z' } }),
        full: true,
      },
    });
    const badge = target.querySelector('[data-agent-state="failed"]');
    expect(badge?.getAttribute('data-agent-label')).toBe('Could not run');
    expect(badge?.classList.contains('state-failed')).toBe(true);
    expect(target.querySelector('[data-failed-run]')?.textContent).toContain('Run now');
    await unmount(app);
  });
});
