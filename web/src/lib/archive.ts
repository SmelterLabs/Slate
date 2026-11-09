import { authHeaders } from './api';
// The Archive page's helpers: export and backup downloads (they need the token, so they go
// through a blob), and the plain-English bits of the trash. Pure apart from `download`.

import { ApiError } from './api';

/** The filename in a Content-Disposition header, or `fallback`. */
export function filenameOf(header: string | null, fallback: string): string {
  const m = /filename="?([^";]+)"?/i.exec(header ?? '');
  return m ? m[1] : fallback;
}

/** `/export?...` for a scope and format. */
export function exportPath(format: 'json' | 'csv', scope: { list_id?: string; section_id?: string } = {}): string {
  const q = new URLSearchParams({ format });
  if (scope.list_id) q.set('list_id', scope.list_id);
  if (scope.section_id) q.set('section_id', scope.section_id);
  return `/export?${q}`;
}

/** Days (rounded) until the trash deletes it for good (0 = today), or null when it never will. */
export function daysLeft(purgeAt: string | null | undefined, now: Date): number | null {
  if (!purgeAt) return null;
  return Math.max(0, Math.round((new Date(purgeAt).getTime() - now.getTime()) / 86_400_000));
}

export function purgeLabel(purgeAt: string | null | undefined, now: Date): string {
  const days = daysLeft(purgeAt, now);
  if (days === null) return 'Kept until the trash is emptied';
  if (days === 0) return 'Deleted for good today';
  return `Deleted for good in ${days} day${days === 1 ? '' : 's'}`;
}

/** Fetch an API path with the token and save what comes back as a file. */
export async function download(path: string, token: string | null, fallback: string): Promise<string> {
  const res = await fetch(`/api/v1${path}`, {
    headers: authHeaders(token),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, (body as { detail?: string } | null)?.detail ?? res.statusText);
  }
  const name = filenameOf(res.headers.get('Content-Disposition'), fallback);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return name;
}
