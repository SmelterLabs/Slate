// The command palette's engine (Ctrl/Cmd+K): fuzzy matching and ranking over tasks, places and
// actions. Pure and unit-tested; the component feeds it the store's plain data.
//
// Typing ">" first narrows to actions (as in VS Code), "#" to tags, "@" to people. Doc pages are
// candidates by title; the component adds the server's full-text hits on their bodies.

export type HitKind = 'action' | 'task' | 'list' | 'section' | 'view' | 'channel' | 'doc' | 'tag' | 'person';

export interface Candidate {
  kind: HitKind;
  id: string;
  /** What is matched and shown. */
  label: string;
  /** Shown dimmed after the label: the list a task is in, an action's shortcut. */
  hint?: string;
  /** Matched with less weight than the label (a task's tags and description). */
  extra?: string;
  /** Raises a candidate among equals: recently opened, open rather than done. */
  boost?: number;
  /** Search words that also find it ("theme" for "Dark mode"). */
  keywords?: string;
}

export interface Hit extends Candidate {
  score: number;
  /** Indexes in `label` that matched, for highlighting. */
  marks: number[];
}

/**
 * How well `query` matches `text`, or null. Every query character must appear in order;
 * consecutive runs and word starts score higher, and a plain substring beats a scattered match.
 */
export function fuzzy(query: string, text: string): { score: number; marks: number[] } | null {
  const q = query.toLowerCase().trim();
  if (!q) return { score: 0, marks: [] };
  const t = text.toLowerCase();
  const sub = t.indexOf(q);
  if (sub >= 0) {
    const marks = Array.from({ length: q.length }, (_, k) => sub + k);
    const wordStart = sub === 0 || /[\s\-_/.(#@]/.test(t[sub - 1]);
    return { score: 100 + q.length * 4 + (wordStart ? 30 : 0) + (sub === 0 ? 20 : 0) - t.length * 0.1, marks };
  }
  // Each query word must match somewhere ("gut fix" finds "Fix the gutter").
  const words = q.split(/\s+/);
  if (words.length > 1) {
    let score = 0;
    const marks: number[] = [];
    for (const w of words) {
      const m = fuzzy(w, text);
      if (!m) return null;
      score += m.score;
      marks.push(...m.marks);
    }
    return { score: score / words.length - 10, marks: [...new Set(marks)].sort((a, b) => a - b) };
  }
  const marks: number[] = [];
  let score = 0;
  let ti = 0;
  let prev = -2;
  for (const ch of q) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return null;
    const start = at === 0 || /[\s\-_/.(#@]/.test(t[at - 1]);
    score += at === prev + 1 ? 6 : start ? 5 : 1;
    marks.push(at);
    prev = at;
    ti = at + 1;
  }
  // Too scattered to be a real match: under half the characters adjacent or at word starts.
  if (score < q.length * 2.5) return null;
  return { score: score - (marks[marks.length - 1] - marks[0]) * 0.2 - t.length * 0.05, marks };
}

const KIND_WEIGHT: Record<HitKind, number> = {
  action: 8,
  list: 6,
  section: 5,
  view: 5,
  channel: 4,
  person: 3,
  tag: 3,
  doc: 2,
  task: 0,
};

/** Rank candidates for `query`; an empty query keeps the given order (recents first). */
export function rank(query: string, candidates: Candidate[], limit = 50): Hit[] {
  let q = query.trim();
  let only: HitKind | null = null;
  if (q.startsWith('>')) {
    only = 'action';
    q = q.slice(1).trim();
  } else if (q.startsWith('#') && q.length > 1) {
    only = 'tag';
    q = q.slice(1);
  } else if (q.startsWith('@') && q.length > 1) {
    only = 'person';
    q = q.slice(1);
  }
  const pool = only ? candidates.filter((c) => c.kind === only) : candidates;
  if (!q) return pool.slice(0, limit).map((c) => ({ ...c, score: c.boost ?? 0, marks: [] }));
  const hits: Hit[] = [];
  for (const c of pool) {
    const main = fuzzy(q, c.label);
    const alt = main ? null : fuzzy(q, `${c.keywords ?? ''} ${c.extra ?? ''}`);
    if (!main && !alt) continue;
    const score = (main ? main.score : alt!.score * 0.5) + KIND_WEIGHT[c.kind] + (c.boost ?? 0);
    hits.push({ ...c, score, marks: main?.marks ?? [] });
  }
  hits.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label));
  return hits.slice(0, limit);
}

/** Split `label` into plain and marked runs for highlighting. */
export function segments(label: string, marks: number[]): { text: string; mark: boolean }[] {
  const set = new Set(marks);
  const out: { text: string; mark: boolean }[] = [];
  for (let i = 0; i < label.length; i++) {
    const mark = set.has(i);
    const last = out[out.length - 1];
    if (last && last.mark === mark) last.text += label[i];
    else out.push({ text: label[i], mark });
  }
  return out;
}

const RECENT_MAX = 12;

/** Most-recent-first ids, without duplicates, capped. */
export function pushRecent(recent: string[], id: string): string[] {
  return [id, ...recent.filter((x) => x !== id)].slice(0, RECENT_MAX);
}
