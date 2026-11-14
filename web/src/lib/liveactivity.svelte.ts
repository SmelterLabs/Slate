// What an agent is doing right now on the task it works on: the lines its run posts
// (`POST /work/{id}/activity`), shown in the small box under "is typing…". They are not events:
// the stream delivers them without an id (`item.activity`), nothing stores them, and they never
// refresh the task or its history. One list per item, for the current run only.
import { get } from './api';

export interface LiveLine {
  at: string;
  text: string;
}

/** How far back the box scrolls (the server keeps the same number per run). */
export const LIVE_KEEP = 60;

/** `have` with `more` added, oldest first, without the lines `have` already ends with (a board
 * that loads the run's lines and hears the same ones on the stream shows each once). */
export function mergeLines(have: LiveLine[], more: LiveLine[], keep = LIVE_KEEP): LiveLine[] {
  const seen = new Set(have.map((l) => `${l.at}\n${l.text}`));
  const fresh = more.filter((l) => l && typeof l.text === 'string' && !seen.has(`${l.at}\n${l.text}`));
  if (!fresh.length) return have;
  return [...have, ...fresh].sort((a, b) => a.at.localeCompare(b.at)).slice(-keep);
}

/** The time of day a line was posted, to the second, in the viewer's locale. */
export function lineTime(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }).replace(/\s?[AP]M$/i, '');
}

class LiveActivity {
  lines = $state<Record<string, LiveLine[]>>({});

  /** An `item.activity` message from the stream. */
  take(ev: { item_id?: string | null; after?: unknown }) {
    const more = (ev.after as { lines?: LiveLine[] } | null)?.lines;
    if (!ev.item_id || !Array.isArray(more)) return;
    this.lines[ev.item_id] = mergeLines(this.lines[ev.item_id] ?? [], more);
  }

  /** The current run's lines so far, for a task opened while its agent works. */
  async load(itemId: string) {
    try {
      const got = await get<{ lines: LiveLine[] }>(`/work/${itemId}/activity`);
      this.lines[itemId] = mergeLines(this.lines[itemId] ?? [], got.lines ?? []);
    } catch {
      // the box fills from the stream instead
    }
  }

  /** The run is over: the next one starts an empty box. */
  clear(itemId: string) {
    if (itemId in this.lines) delete this.lines[itemId];
  }
}

export const liveActivity = new LiveActivity();
