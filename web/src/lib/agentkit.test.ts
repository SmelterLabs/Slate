import { describe, expect, it } from 'vitest';
import { bars, shortDay, handleFrom, HANDLE_RE, scopeText, snippets, sourceShare, type Usage } from './agentkit';

describe('handleFrom', () => {
  it('slugs a display name into a valid handle', () => {
    expect(handleFrom('Research Bot')).toBe('research-bot');
    expect(handleFrom('  Agent!! ')).toBe('agent');
    expect(handleFrom('3D Render Agent')).toBe('d-render-agent');
    expect(handleFrom('Ü')).toBe('');
    for (const n of ['Research Bot', 'Agent', 'a'.repeat(60)]) {
      const h = handleFrom(n);
      if (h) expect(h).toMatch(HANDLE_RE);
    }
  });
});

describe('snippets', () => {
  const all = snippets('https://slate.example:8448/', 'slate_xxxxxxxxxxxxxxxx', 'agent');

  it('fills in the board address and the token', () => {
    const claude = all.find((s) => s.key === 'claude')!;
    expect(claude.code).toContain('https://slate.example:8448/mcp');
    expect(claude.code).toContain('Bearer slate_xxxxxxxxxxxxxxxx');
    const py = all.find((s) => s.key === 'python')!.code;
    expect(py).toContain('Slate("https://slate.example:8448", os.environ["SLATE_TOKEN"]');
    expect(py).toContain('export SLATE_TOKEN=slate_xxxxxxxxxxxxxxxx');
    expect(all.find((s) => s.key === 'curl')!.code).toContain('https://slate.example:8448/api/v1/checkin');
  });

  it('keeps the token out of .mcp.json (it is meant to be committed)', () => {
    const json = all.find((s) => s.key === 'mcp-json')!;
    expect(json.code).not.toContain('secret');
    const parsed = JSON.parse(json.code);
    expect(parsed.mcpServers.slate).toMatchObject({ type: 'http', url: 'https://slate.example:8448/mcp' });
    expect(parsed.mcpServers.slate.headers.Authorization).toBe('Bearer ${SLATE_TOKEN}');
  });
});

describe('usage helpers', () => {
  const days: Usage['days'] = [
    { day: '2026-10-22', events: 0, by_source: {} },
    { day: '2026-10-23', events: 4, by_source: { api: 3, mcp: 1 } },
    { day: '2026-10-24', events: 2, by_source: { mcp: 2 } },
  ];

  it('scales bars to the busiest day', () => {
    expect(bars(days).map((b) => b.h)).toEqual([0, 1, 0.5]);
    expect(bars([{ day: '2026-10-24', events: 0, by_source: {} }])[0].h).toBe(0);
  });

  it('splits events by source', () => {
    expect(sourceShare(days)).toEqual([
      ['api', 0.5],
      ['mcp', 0.5],
    ]);
    expect(sourceShare([])).toEqual([]);
  });

  it('labels a local day without shifting it', () => {
    expect(shortDay('2026-10-24')).toBe('Sat 24');
  });

  it('names the strongest scope', () => {
    expect(scopeText(['read'])).toBe('Read');
    expect(scopeText(['read', 'write'])).toBe('Write');
    expect(scopeText(['admin'])).toBe('Admin');
  });
});
