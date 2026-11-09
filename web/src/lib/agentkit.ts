// The Agents page's "Add my agent" kit: the copy-paste setup an agent needs (Claude Code,
// Python, TypeScript, curl) with this board's own address and a fresh token filled in, plus
// the shapes of GET /accounts/{id}/tokens and /usage. Pure functions, unit-tested in
// agentkit.test.ts; the snippets mirror docs/agents/mcp.md and docs/agents/quickstart.md.

export type Scope = 'read' | 'write' | 'admin';

export const SCOPES: { key: Scope; label: string; text: string }[] = [
  { key: 'read', label: 'Read', text: 'Sees everything, changes nothing. For watchers and reporters.' },
  { key: 'write', label: 'Write', text: 'Works tasks: claim, report, comment, file, complete. The usual choice.' },
  { key: 'admin', label: 'Admin', text: 'Also manages accounts, tokens and webhooks. Rarely needed.' },
];

export interface TokenLimit {
  section_ids?: string[];
  list_ids?: string[];
  level?: 'view' | 'comment' | 'edit';
}

/** "2 lists, comment" for a narrowed token; null for one with its account's full reach. */
export function limitText(limit: TokenLimit | null | undefined): string | null {
  if (!limit) return null;
  const n = (limit.list_ids?.length ?? 0) + (limit.section_ids?.length ?? 0);
  const where = n ? `${n} ${limit.section_ids?.length && !limit.list_ids?.length ? 'section' : 'list'}${n === 1 ? '' : 's'} only` : 'all it can reach';
  return limit.level ? `${where}, ${limit.level}` : where;
}

export interface TokenRow {
  id: string;
  account_id: string;
  scopes: Scope[];
  limit?: TokenLimit | null;
  label: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  requests?: { read: number; write: number };
}

export interface Usage {
  account_id: string;
  days: { day: string; events: number; by_source: Record<string, number> }[];
  events_in_window: number;
  events_all_time: number;
  last_event_at: string | null;
  top_types: { type: string; count: number }[];
  tokens: TokenRow[];
  throttled: number;
  rate_limit: { reads: number; writes: number };
}

export interface CreatedToken {
  id: string;
  account_id: string;
  scopes: Scope[];
  label: string | null;
  created_at: string;
  token: string;
}

export type SnippetKey = 'claude' | 'mcp-json' | 'python' | 'typescript' | 'curl';

export interface Snippet {
  key: SnippetKey;
  label: string;
  hint: string;
  code: string;
}

/** Handle rules from the API: a lowercase letter, then 1-31 of a-z 0-9 _ -. */
export const HANDLE_RE = /^[a-z][a-z0-9_-]{1,31}$/;

/** A handle suggestion from a display name: "Research Bot" -> "research-bot". */
export function handleFrom(name: string): string {
  const slug = name
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/^[^a-z]+/, '')
    .slice(0, 32)
    .replace(/-+$/, '');
  return slug.length >= 2 ? slug : '';
}

/** The setup snippets, in the order the dialog shows them. */
export function snippets(origin: string, token: string, handle: string): Snippet[] {
  const base = origin.replace(/\/+$/, '');
  const mcpJson = JSON.stringify(
    {
      mcpServers: {
        slate: {
          type: 'http',
          url: `${base}/mcp`,
          headers: { Authorization: 'Bearer ${SLATE_TOKEN}', 'X-Slate-Session-Ref': '${SLATE_SESSION_REF:-claude-code}' },
        },
      },
    },
    null,
    2,
  );
  return [
    {
      key: 'claude',
      label: 'Claude Code',
      hint: 'Run once on the machine the agent uses. Then add the Slate skill from integrations/claude-code/skills/slate.',
      code: `claude mcp add --transport http --scope user slate \\\n  ${base}/mcp \\\n  --header "Authorization: Bearer ${token}"`,
    },
    {
      key: 'mcp-json',
      label: '.mcp.json',
      hint: `Commit this as .mcp.json in a project, and set SLATE_TOKEN=${token} in the agent's environment.`,
      code: mcpJson,
    },
    {
      key: 'python',
      label: 'Python',
      hint: 'Copy clients/python/slate_client.py next to the agent (needs httpx).',
      code: [
        `# export SLATE_TOKEN=${token}`,
        'import os',
        '',
        'from slate_client import Slate',
        '',
        `slate = Slate("${base}", os.environ["SLATE_TOKEN"], session_ref="${handle}")`,
        '',
        'def handle(task):',
        '    item = task["item"]  # yours until you finish; task["context"] = recent comments',
        '    ...  # do the work',
        '    return "Done: <link to the result>"',
        '',
        'slate.work_forever(handle)  # take the next task, heartbeat, report back, repeat',
      ].join('\n'),
    },
    {
      key: 'typescript',
      label: 'TypeScript',
      hint: 'Copy clients/typescript/slate.ts next to the agent (any runtime with fetch).',
      code: [
        `// export SLATE_TOKEN=${token}`,
        "import { Slate } from './slate';",
        '',
        `const slate = new Slate('${base}', process.env.SLATE_TOKEN!, { sessionRef: '${handle}' });`,
        '',
        'await slate.workForever(async ({ item, context }) => {',
        '  // do the work: the task is yours (with a lease) until this returns',
        "  return 'Done: <link to the result>';",
        '});',
      ].join('\n'),
    },
    {
      key: 'curl',
      label: 'curl',
      hint: 'Check the token works: this is the agent’s check-in.',
      code: `curl -s ${base}/api/v1/checkin \\\n  -H "Authorization: Bearer ${token}"`,
    },
  ];
}

/** Bar heights (0..1) for the events-per-day chart; the busiest day is 1. */
export function bars(days: Usage['days']): { day: string; events: number; h: number }[] {
  const max = Math.max(1, ...days.map((d) => d.events));
  return days.map((d) => ({ day: d.day, events: d.events, h: d.events / max }));
}

/** "Mon 20" for a YYYY-MM-DD (already a local date: no timezone shift). */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return `${dt.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' })} ${d}`;
}

/** Share of events per source over the window, biggest first: [["api", 0.8], ["mcp", 0.2]]. */
export function sourceShare(days: Usage['days']): [string, number][] {
  const totals: Record<string, number> = {};
  for (const d of days) for (const [s, n] of Object.entries(d.by_source)) totals[s] = (totals[s] ?? 0) + n;
  const all = Object.values(totals).reduce((a, b) => a + b, 0);
  if (!all) return [];
  return Object.entries(totals)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([s, n]) => [s, n / all]);
}

export function scopeText(scopes: Scope[]): string {
  const top = scopes.includes('admin') ? 'admin' : scopes.includes('write') ? 'write' : 'read';
  return SCOPES.find((s) => s.key === top)!.label;
}
