// The chat panel: chat beside whatever page you are on, instead of a page of its own. On a wide
// screen the Chat entry in the sidebar, a list's or section's Chat tab and the section chat icon
// open it on the right; the page underneath stays (a doc is left-aligned, so the panel sits in
// the empty space beside it; a board is covered on its right). The full Chat page (`#/chat`)
// stays for phones and for links. The pure parts live here; the live state is chatpanel.svelte.ts.

export const STORAGE_KEY = 'slate.chatPanel';

/** Narrower than this, chat opens as the full page: the panel would cover everything. */
export const PANEL_MIN_WIDTH = 900;

export interface PanelState {
  open: boolean;
  channel: string;
  thread: string | null;
  /** The panel's width in px: drag its left edge; remembered per device. */
  width: number;
}

export const PANEL_DEFAULT_WIDTH = 420;
/** Narrower and the composer's buttons and a thread no longer fit. */
export const PANEL_NARROWEST = 320;
/** What the panel must always leave of the page: the sidebar and a strip of the page. */
export const PANEL_KEEP_CLEAR = 248 + 160;

export const DEFAULT_PANEL: PanelState = { open: false, channel: 'general', thread: null, width: PANEL_DEFAULT_WIDTH };

/** `width` kept between the narrowest panel and what the window can give (never under the
 * narrowest, even in a window too small for both). */
export function clampPanelWidth(width: number, viewport: number): number {
  const widest = Math.max(PANEL_NARROWEST, viewport - PANEL_KEEP_CLEAR);
  return Math.round(Math.min(widest, Math.max(PANEL_NARROWEST, width)));
}

/** The channel the panel should show given the ones this account can see: `current` when it is
 * one of them; otherwise the first with a mention, else the first with unread, else the first.
 * A fresh browser remembers `general`, which a guest cannot read (service/access.py), and a
 * remembered list may since have been archived or unshared; either would be a "not found". */
export function panelChannelFor(
  current: string,
  channels: { key: string; unread: number; mentions: number }[],
): string | null {
  if (channels.length === 0) return null;
  if (channels.some((c) => c.key === current)) return current;
  return (channels.find((c) => c.mentions > 0) ?? channels.find((c) => c.unread > 0) ?? channels[0]).key;
}

/** The window's title and taskbar badge, "(n) Slate": the Needs you count, so the title, the
 * app icon and the pill say the same number. Not unread chat
 * (Chat has its own badge) and not overdue (Everything's badge and the Overdue column). */
export function windowBadge(needsYou: number): { title: string; count: number } {
  const count = Math.max(0, needsYou);
  return { title: count ? `(${count}) Slate` : 'Slate', count };
}

/** The other title the window flashes while chat is unread and the window is not in front:
 * "New in #Todo", or "New in #Todo +2" with more channels. Null with nothing unread. */
export function flashTitle(channels: { name: string; unread: number; last_message_at: string | null }[]): string | null {
  const unread = channels.filter((c) => c.unread > 0);
  if (unread.length === 0) return null;
  unread.sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''));
  const more = unread.length - 1;
  return `New in #${unread[0].name}${more ? ` +${more}` : ''}`;
}

/** How often the title flips while flashing. */
export const FLASH_MS = 1500;

/** `?chat=<key>[&thread=<id>]` on any hash: a link that opens the panel on that channel. */
export function chatPanelOf(hash: string): { channel: string; thread: string | null } | null {
  const q = new URLSearchParams(hash.split('?')[1] ?? '');
  const channel = q.get('chat');
  return channel ? { channel, thread: q.get('thread') } : null;
}

/** Whether a screen this wide gets the panel (else the full Chat page). */
export function panelFits(width: number): boolean {
  return width >= PANEL_MIN_WIDTH;
}

/** The stored panel state, or the default when there is none or it is unreadable. */
export function readPanelState(raw: string | null | undefined): PanelState {
  if (!raw) return DEFAULT_PANEL;
  try {
    const v = JSON.parse(raw) as Partial<PanelState>;
    if (!v || typeof v !== 'object' || typeof v.channel !== 'string' || !v.channel) return DEFAULT_PANEL;
    const width = typeof v.width === 'number' && Number.isFinite(v.width) && v.width > 0 ? v.width : PANEL_DEFAULT_WIDTH;
    return { open: v.open === true, channel: v.channel, thread: typeof v.thread === 'string' ? v.thread : null, width };
  } catch {
    return DEFAULT_PANEL;
  }
}

export function writePanelState(s: PanelState): string {
  return JSON.stringify({ open: s.open, channel: s.channel, thread: s.thread, width: s.width });
}
