// Workspace-wide display/input zone. UTC instants and stored rule zones never change.
import { SvelteMap } from 'svelte/reactivity';

export const DEFAULT_TIMEZONE = 'UTC';
const workspace = new SvelteMap([['timezone', DEFAULT_TIMEZONE]]);

const zones = new Map<string, string>();
const formatters = new Map<string, Intl.DateTimeFormat>();

function safeZone(zone?: string | null): string {
  if (!zone) return DEFAULT_TIMEZONE;
  const known = zones.get(zone);
  if (known) return known;
  let valid = zone;
  try { new Intl.DateTimeFormat('en-US', { timeZone: zone }); }
  catch { valid = DEFAULT_TIMEZONE; }
  zones.set(zone, valid);
  return valid;
}

export function workspaceTimezone(): string {
  return workspace.get('timezone')!;
}

/** Missing metadata (old servers or sign-out) and invalid IANA names fall back safely. */
export function setWorkspaceTimezone(zone?: string | null): void {
  workspace.set('timezone', safeZone(zone));
}

/** Cache by zone and options: a workspace change cannot reuse an old-zone formatter. */
export function dateFormatter(
  locale: string,
  options: Intl.DateTimeFormatOptions,
  zone = workspaceTimezone(),
): Intl.DateTimeFormat {
  const timeZone = safeZone(zone);
  const opts = { ...options, timeZone };
  const key = JSON.stringify([locale, opts]);
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, opts);
    formatters.set(key, formatter);
  }
  return formatter;
}

/** Safe to hold at module scope: resolve the reactive workspace zone on every use. */
export function workspaceFormatter(locale: string, options: Intl.DateTimeFormatOptions) {
  return {
    format: (at: Date | number) => dateFormatter(locale, options).format(at),
    formatToParts: (at: Date | number) => dateFormatter(locale, options).formatToParts(at),
  };
}

export function workspaceDate(at: Date, zone = workspaceTimezone()): string {
  return dateFormatter('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit' }, zone).format(at);
}
