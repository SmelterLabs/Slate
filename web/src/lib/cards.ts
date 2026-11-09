// What a board card says and how it is coloured: the due label in ClickUp's words, the colour of
// a tag pill, and whether a column is folded. Pure.

import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';
import type { Tag } from './types';
import { addDays } from './views';

export type DueTone = 'overdue' | 'today' | 'soon' | 'later' | 'done';

const time = workspaceFormatter('en-US', { hour: 'numeric', minute: '2-digit' });
const weekdayFmt = workspaceFormatter('en-US', { weekday: 'short' });
const monthDay = workspaceFormatter('en-US', { month: 'short', day: 'numeric' });
const monthDayYear = workspaceFormatter('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
});

/** "4:00 pm", or '' for 5:00 pm: Slate's stand-in for "no time" (a date-only due). */
function clock(d: Date): string {
  const t = time.format(d).replace(' AM', ' am').replace(' PM', ' pm');
  return t === '5:00 pm' ? '' : t;
}

/**
 * The due pill on a card, as ClickUp writes it: "Today, 4:00 pm", "Tomorrow", "Sun, 4:00 am"
 * within the week, "Nov 1" later, "Yesterday" / "4 days ago" just past, "Aug 2 · 82d late" long past
 * ("Aug 2" once closed).
 */
export function cardDue(iso: string, now: Date, closed = false): { text: string; tone: DueTone } {
  const d = new Date(iso);
  const day = workspaceDay(d);
  const today = workspaceDay(now);
  const t = clock(d);
  const withTime = (s: string) => (t ? `${s}, ${t}` : s);
  const tone = (open: DueTone): DueTone => (closed ? 'done' : open);
  if (day === today) {
    const past = d.getTime() < now.getTime();
    return { text: withTime('Today'), tone: tone(past ? 'overdue' : 'today') };
  }
  if (day < today) {
    const days = Math.round(
      (Date.parse(`${today}T12:00:00Z`) - Date.parse(`${day}T12:00:00Z`)) / 86_400_000,
    );
    // Past a week the date alone hides the age, so open items say how late they are.
    const old = closed ? sameYear(d, now) : `${sameYear(d, now)} · ${days}d late`;
    const text = days === 1 ? 'Yesterday' : days < 7 ? `${days} days ago` : old;
    return { text, tone: tone('overdue') };
  }
  if (day === addDays(today, 1)) return { text: withTime('Tomorrow'), tone: tone('soon') };
  if (day <= addDays(today, 6)) return { text: withTime(weekdayFmt.format(d)), tone: tone('soon') };
  return { text: sameYear(d, now), tone: tone('later') };
}

function sameYear(d: Date, now: Date): string {
  return (workspaceDay(d).slice(0, 4) === workspaceDay(now).slice(0, 4) ? monthDay : monthDayYear).format(d);
}

/** Start dates read like due dates but never turn red. */
export function cardStart(iso: string, now: Date): string {
  return cardDue(iso, now, true).text;
}

// ClickUp's tag palette: saturated pills with white or dark text.
const PALETTE = [
  '#1f9d55', // green
  '#e8590c', // orange
  '#f2c200', // yellow
  '#7048e8', // violet
  '#0c8599', // teal
  '#d6336c', // pink
  '#1971c2', // blue
  '#2b2f36', // near-black
  '#a0782c', // ochre
  '#5f3dc4', // indigo
  '#2f9e44', // leaf
  '#c92a2a', // red
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Dark text on light backgrounds, white on dark (WCAG relative luminance). */
export function textOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return '#fff';
  const n = parseInt(m[1], 16);
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const L = 0.2126 * lin((n >> 16) & 255) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
  return L > 0.4 ? '#1d2127' : '#fff';
}

/** A tag's pill colours: its own colour when it has one, else a stable pick from the palette. */
export function tagColor(name: string, tags: Record<string, Tag>): { bg: string; fg: string } {
  const own = tags[name]?.color;
  const bg = own && /^#[0-9a-f]{6}$/i.test(own) ? own : PALETTE[hash(name) % PALETTE.length];
  return { bg, fg: textOn(bg) };
}

// --- column collapse (the per-view choices live in the account prefs, board.collapsedFor) ----

/** Whether a column shows as a thin strip: the viewer's choice, else empty columns collapse. */
export function isCollapsed(
  key: string,
  count: number,
  manual: Record<string, boolean>,
  collapseEmpty: boolean,
): boolean {
  if (key in manual) return manual[key];
  return collapseEmpty && count === 0;
}
