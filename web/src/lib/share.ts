// Share-target capture: the phone's share sheet opens the installed app at
// `/?title=…&text=…&url=…` (manifest `share_target`, GET), or a link inside the app opens
// `#/share?…`. Both become a quick-add draft: the title line for the input, the link (and the
// rest of the text) for the item's description. Pure functions here; `takeShare` reads and
// strips the address, like the invite route does with its secret.

export interface ShareParams {
  title?: string | null;
  text?: string | null;
  url?: string | null;
}

export interface ShareDraft {
  /** The quick-add line (the title; quick-add words in it are escaped so it stays literal). */
  line: string;
  /** The item's description: the link, then whatever text came with it. */
  body: string;
}

const URL_RE = /https?:\/\/[^\s<>"')\]]+/i;
/** Hosts where a shared link is something to watch rather than read. */
const WATCH_HOSTS = /(^|\.)(youtube\.com|youtu\.be|vimeo\.com|twitch\.tv|tiktok\.com|netflix\.com)$/i;
const MAX_LABEL = 60;

/** The share params in a page address: the query (`/?title=…`, how the share sheet launches
 * the app) or the `#/share?…` route. Null when neither names a title, text or url. */
export function shareParamsFrom(search: string, hash: string): ShareParams | null {
  const pick = (q: URLSearchParams): ShareParams | null =>
    q.has('title') || q.has('text') || q.has('url')
      ? { title: q.get('title'), text: q.get('text'), url: q.get('url') }
      : null;
  return (
    pick(new URLSearchParams(search)) ??
    (hash.startsWith('#/share') ? pick(new URLSearchParams(hash.split('?')[1] ?? '')) : null)
  );
}

function validUrl(value: string | null | undefined): string | null {
  const v = (value ?? '').trim();
  if (!v) return null;
  try {
    const u = new URL(v);
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

/** "Read nytimes.com/2026/…" or "Watch youtu.be/…": the title for a link shared on its own. */
export function linkTitle(url: string): string {
  const u = new URL(url);
  const host = u.hostname.replace(/^www\./, '');
  const path = (u.pathname + u.search).replace(/\/$/, '');
  const label = host + (path && path !== '/' ? path : '');
  const verb = WATCH_HOSTS.test(host) ? 'Watch' : 'Read';
  return `${verb} ${label.length > MAX_LABEL ? label.slice(0, MAX_LABEL - 1) + '…' : label}`;
}

/** Keep a shared title literal: quick add would read `#tag`, `@handle`, `~list` and `!high`
 * in it as fields, and a word starting with `\` is kept as typed. */
function literal(title: string): string {
  return title
    .split(/\s+/)
    .map((w) => (w.length > 1 && /^[#@~!]/.test(w) ? `\\${w}` : w))
    .join(' ');
}

/** Turn what the share sheet sent into a quick-add draft, or null when it sent nothing. */
export function shareDraft(p: ShareParams): ShareDraft | null {
  const text = (p.text ?? '').trim();
  const givenTitle = (p.title ?? '').trim();
  const url = validUrl(p.url) ?? validUrl(URL_RE.exec(text)?.[0]) ?? validUrl(URL_RE.exec(givenTitle)?.[0]);
  const strip = (s: string) => (url ? s.replace(URL_RE, '') : s).replace(/\s+/g, ' ').trim();
  const lines = text
    .split(/\r?\n/)
    .map(strip)
    .filter(Boolean);
  let title = strip(givenTitle);
  let rest = lines;
  if (!title && lines.length) [title, ...rest] = lines;
  if (!title && !url && !lines.length) return null;
  if (!title && url) title = linkTitle(url);
  const body = [url, rest.join('\n')].filter(Boolean).join('\n\n');
  return { line: literal(title), body };
}

/** Read the share in the address, strip it (`history.replaceState`) and land on a scope with
 * quick add: the current list or section, else Everything (its picker remembers the last
 * list). Null when the address carries no share. */
export function takeShare(): ShareDraft | null {
  const params = shareParamsFrom(location.search, location.hash);
  if (!params) return null;
  const draft = shareDraft(params);
  const path = location.hash.replace(/^#/, '').split('?')[0];
  const keep = /^\/(list|section)\/[^/]+\/?$/.test(path) ? `#${path}` : '#/everything';
  history.replaceState(null, '', location.pathname + keep);
  return draft;
}
