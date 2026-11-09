// The Health page (`#/health`, owners and admins): the shape of GET /health and the small
// formatters it uses. The server decides every status (service/health.py); the board only
// shows it.

export type HealthStatus = 'ok' | 'warn' | 'fail' | 'off';

export interface HealthCheck {
  key: string;
  label: string;
  status: HealthStatus;
  detail: string | null;
}

export interface HealthPoller extends HealthCheck {
  every_s: number;
  configured: boolean;
  runs: number;
  failures: number;
  consecutive_failures: number;
  last_run_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  last_ms: number | null;
  running_since: string | null;
}

export interface HealthCall {
  at: string;
  token_id: string;
  account_id: string;
  door: 'api' | 'mcp';
  route: string;
  status: number;
  ms: number;
  error?: string | null;
  handle: string | null;
  display_name: string | null;
  kind: 'person' | 'agent' | null;
  token_label: string | null;
}

export interface HealthToken {
  token_id: string;
  account_id: string;
  handle: string | null;
  display_name: string | null;
  kind: 'person' | 'agent' | null;
  token_label: string | null;
  calls: number;
  client_errors: number;
  server_errors: number;
  error_rate: number;
  slow: number;
  avg_ms: number;
  max_ms: number;
  last_at: string;
  last_error: HealthCall | null;
}

export interface HealthRun {
  id: string;
  automation_id: string;
  name: string;
  item_id: string | null;
  item_title: string | null;
  status: 'failed' | 'partial';
  at: string;
  detail: string | null;
}

export interface HealthRoute {
  method: string;
  route: string;
  count: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
}

export interface HealthReport {
  status: Exclude<HealthStatus, 'off'>;
  at: string;
  version: string;
  uptime_s: number;
  window_minutes: number;
  slow_ms: number;
  alert_after: number;
  checks: HealthCheck[];
  scheduler: { enabled: boolean; interval_s: number; last_tick_at: string | null; last_tick_age_s: number | null };
  pollers: HealthPoller[];
  engines: HealthCheck[];
  agents: { tokens: HealthToken[]; recent_errors: HealthCall[]; slow_calls: HealthCall[] };
  latency: { samples: number; p50_ms?: number; p95_ms?: number; p99_ms?: number; max_ms?: number; slowest: HealthRoute[] };
  automations: { failed_24h: number; partial_24h: number; recent: HealthRun[] };
  queues: {
    webhooks_pending: number;
    webhooks_failed_24h: number;
    engine_lag: Record<string, number | null>;
  };
  db: { path: string; bytes: number | null; wal_bytes: number | null; disk_free_bytes: number | null; events: number; items: number; blob_bytes: number };
}

export const STATUS_LABEL: Record<HealthStatus, string> = {
  ok: 'Working',
  warn: 'Needs a look',
  fail: 'Broken',
  off: 'Off',
};

export const VERDICT: Record<Exclude<HealthStatus, 'off'>, string> = {
  ok: 'Everything is working',
  warn: 'Working, with something to look at',
  fail: 'Something is broken',
};

/** Problems first (fail, then warn), then what works, then what is off; stable otherwise. */
export function byUrgency<T extends { status: HealthStatus }>(rows: T[]): T[] {
  const rank: Record<HealthStatus, number> = { fail: 0, warn: 1, ok: 2, off: 3 };
  return rows
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank[a.r.status] - rank[b.r.status] || a.i - b.i)
    .map((x) => x.r);
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v >= 100 || u === 0 ? Math.round(v) : v.toFixed(1)} ${units[u]}`;
}

export function ms(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} s`;
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} ms`;
}

export function percent(rate: number): string {
  if (!rate) return '0%';
  const p = rate * 100;
  return `${p < 1 ? p.toFixed(1) : Math.round(p)}%`;
}

export function uptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

/** Who made a call: the account's name, and the token's label when it has one. */
export function caller(row: { display_name: string | null; handle: string | null; token_label: string | null; token_id: string }): string {
  const who = row.display_name ?? (row.handle ? `@${row.handle}` : 'A deleted account');
  return row.token_label ? `${who} · ${row.token_label}` : who;
}

/** Should an event refresh the page at once? Alerts do; anything else waits for the throttle. */
export function isHealthEvent(type: string): boolean {
  return type.startsWith('health.');
}
