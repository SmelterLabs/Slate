export const COOKIE_SESSION = '@browser-session';
export const primaryDestinations = ['home', 'inbox', 'docs', 'chat', 'everything'];
export const moreDestination = (page: string | null) => !!page && !primaryDestinations.includes(page);
export function safeWhiteboard(value: string | null | undefined): boolean {
  if (!value || /[\s\\]/.test(value)) return false;
  try { const u = new URL(value); return u.protocol === 'https:' && !!u.hostname && !u.username && !u.password; }
  catch { return false; }
}
/** Recovery advice follows the HTTP outcome, not an error-message keyword guess. */
export function invitationRecovery(status: number | null): string {
  if (status === null) return '';
  if ([400, 403, 404, 409, 410, 422].includes(status)) {
    return 'Ask the person who invited you for a fresh invitation link for this browser. Use your own access; never borrow another person’s token.';
  }
  return 'Try again once your connection or Slate has recovered. If this keeps happening, contact the person who invited you.';
}

/** The invitation secret in pasted text: a whole invite link (`…#/invite?secret=…`, as sent by
 * the inviter) or the bare `invite_…` secret. Null when the text is an ordinary token. The
 * installed app has no address bar, so this is how a guest signs it in. */
export function inviteSecretFrom(text: string): string | null {
  const t = text.trim();
  const link = /#\/invite\?([^#\s]*)/.exec(t);
  if (link) return new URLSearchParams(link[1]).get('secret') ?? '';
  if (/^invite_[0-9A-Za-z]{12}_[A-Za-z0-9_-]{43}$/.test(t)) return t;
  return null;
}

/** Fragment only: remove the secret before rendering, never place it in a query or path. */
export function takeInvitation(): string | null {
  if (!location.hash.startsWith('#/invite?')) return null;
  const secret = new URLSearchParams(location.hash.split('?')[1]).get('secret');
  history.replaceState(null, '', location.pathname + location.search + '#/home');
  return secret || '';
}
