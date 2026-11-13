// The quick-add line: "Call the bank tomorrow 4pm #personal !high @my-agent ~bills every month" ->
// fields. The only "magic" in the board. Pure; `now` is passed in so tests are
// deterministic.
//
// Words it reads (anywhere in the line; the last date wins):
//   #tag  @handle  ~list  !urgent !high !normal !low (or !1..!4)
//   due:<when>  start:<when>
//   dates   today tonight tomorrow  monday..sunday  next friday  this weekend  next week|month
//           in 3 days|weeks|months|hours  eow eom  jan 5  5 january  11/2  2026-11-01
//           (optionally after "due", "by" or "on"; 3-letter weekdays only after one of those)
//   times   4pm  4:30pm  4 pm  16:00  noon  at 4 (1-7 means pm)
//   repeat  daily weekly monthly yearly  every day|weekday|week|month|year|2 weeks
//           every monday and thursday  every other friday  every 15th
// A word that starts with a backslash is kept as typed ("\friday" stays "friday").

import { DAYS, type Day, toRrule } from './rrule';
import type { Priority } from './types';

export interface QuickAdd {
  title: string;
  tags: string[];
  assignees: string[];
  priority: Priority | null;
  /** `YYYY-MM-DD` (Slate reads it as 17:00 in the workspace zone) or workspace `YYYY-MM-DDTHH:MM`. */
  due: string | null;
  start: string | null;
  /** The `~list` word as typed (without the ~); the caller resolves it to a list. */
  list: string | null;
  /** An RRULE when the line says how it repeats; the caller then makes a recurring rule. */
  rrule: string | null;
}

import { workspaceFormatter } from './timezone';
import { addDays, weekday } from './when';

const PRIORITIES: Record<string, Priority> = {
  urgent: 'urgent',
  high: 'high',
  normal: 'normal',
  low: 'low',
  '1': 'urgent',
  '2': 'high',
  '3': 'normal',
  '4': 'low',
};
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
const MONTHS = [
  'january',
  'february',
  'march',
  'april',
  'may',
  'june',
  'july',
  'august',
  'september',
  'october',
  'november',
  'december',
];
/** Words that introduce a date; they are dropped with it. */
const LEADS = new Set(['due', 'by', 'on']);

// en-CA formats as YYYY-MM-DD. Cache per zone, resolving the workspace on every use.
const ymdFmt = workspaceFormatter('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});
const hmFmt = workspaceFormatter('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** The workspace calendar date of an instant, as YYYY-MM-DD. */
export function workspaceDay(at: Date): string {
  return ymdFmt.format(at);
}

function addMonths(ymd: string, months: number): string {
  const [y, m, d] = ymd.split('-').map(Number);
  const first = new Date(Date.UTC(y, m - 1 + months, 1, 12));
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0, 12)).getUTCDate();
  first.setUTCDate(Math.min(d, last));
  return first.toISOString().slice(0, 10);
}

function validYmd(y: number, m: number, d: number): string | null {
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
  return dt.toISOString().slice(0, 10);
}

/** The next date (today or later) with this month and day; a given year is taken as is. */
function monthDay(m: number, d: number, year: number | null, today: string): string | null {
  if (year !== null) return validYmd(year < 100 ? 2000 + year : year, m, d);
  const y = Number(today.slice(0, 4));
  const thisYear = validYmd(y, m, d);
  if (thisYear && thisYear >= today) return thisYear;
  return validYmd(y + 1, m, d);
}

/** A weekday word: full name always; 3+ letters when `loose` (after due/by/on/next/this). */
function weekdayIndex(word: string, loose: boolean): number {
  if (!word) return -1;
  const exact = WEEKDAYS.indexOf(word);
  if (exact >= 0) return exact;
  if (!loose || word.length < 3) return -1;
  return WEEKDAYS.findIndex((day) => day.startsWith(word));
}

function monthIndex(word: string): number {
  if (word.length < 3) return -1;
  return MONTHS.findIndex((m) => m.startsWith(word.replace(/\.$/, '')));
}

function dayNumber(word: string | undefined): number | null {
  const m = /^(\d{1,2})(st|nd|rd|th)?,?$/.exec(word ?? '');
  return m ? Number(m[1]) : null;
}

/** The coming `target` weekday, today included. */
function coming(today: string, target: number): string {
  return addDays(today, (target - weekday(today) + 7) % 7);
}

/** `target` weekday in the week after this one (weeks start on Monday). */
function nextWeeks(today: string, target: number): string {
  const monday = addDays(today, -((weekday(today) + 6) % 7));
  return addDays(monday, 7 + ((target + 6) % 7));
}

interface When {
  date?: string;
  time?: string;
  /** Only a time was typed; the date was inferred (today if still ahead, else tomorrow). */
  timeOnly?: boolean;
  /** Words consumed. */
  n: number;
}

/** A time at w[0]: 4pm, 4:30pm, 4 pm, 16:00, noon, midnight; `at` allows a bare hour. */
function readTime(w: string[], afterAt: boolean): When | null {
  const word = w[0] ?? '';
  if (word === 'noon') return { time: '12:00', n: 1 };
  if (word === 'midnight') return { time: '23:59', n: 1 };
  let m = /^(\d{1,2})(?::(\d{2}))?(am|pm|a|p)$/.exec(word);
  let n = 1;
  if (!m && /^(am|pm)$/.test(w[1] ?? '')) {
    const bare = /^(\d{1,2})(?::(\d{2}))?$/.exec(word);
    if (bare) {
      m = [bare[0], bare[1], bare[2], w[1]] as unknown as RegExpExecArray;
      n = 2;
    }
  }
  if (m) {
    let h = Number(m[1]);
    const min = Number(m[2] ?? 0);
    if (h < 1 || h > 12 || min > 59) return null;
    const pm = m[3].startsWith('p');
    if (h === 12) h = pm ? 12 : 0;
    else if (pm) h += 12;
    return { time: hhmm(h, min), n };
  }
  const h24 = /^(\d{1,2}):(\d{2})$/.exec(word);
  if (h24) {
    const h = Number(h24[1]);
    const min = Number(h24[2]);
    return h < 24 && min < 60 ? { time: hhmm(h, min), n: 1 } : null;
  }
  if (afterAt && /^\d{1,2}$/.test(word)) {
    let h = Number(word);
    if (h < 1 || h > 23) return null;
    if (h <= 7) h += 12;
    return { time: hhmm(h, 0), n: 1 };
  }
  return null;
}

/** Today if `time` is still ahead in workspace time, otherwise tomorrow. */
function dayFor(time: string, today: string, now: Date): string {
  return time > hmFmt.format(now) ? today : addDays(today, 1);
}

function hhmm(h: number, m: number): string {
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** A date at w[0] (lower-cased words). `loose` allows short weekday names. */
function readDate(w: string[], today: string, now: Date, loose: boolean): When | null {
  const [a = '', b = '', c = ''] = w;
  if (a === 'today') return { date: today, n: 1 };
  if (a === 'tonight') return { date: today, time: '20:00', n: 1 };
  if (a === 'tomorrow' || a === 'tmrw' || a === 'tmr') return { date: addDays(today, 1), n: 1 };
  if (a === 'yesterday') return { date: addDays(today, -1), n: 1 };
  if (a === 'eod') return { date: today, n: 1 };
  if (a === 'eow' || (a === 'end' && b === 'of' && c === 'week')) {
    const wd = weekday(today);
    return { date: wd === 6 || wd === 0 ? nextWeeks(today, 5) : coming(today, 5), n: a === 'eow' ? 1 : 3 };
  }
  if (a === 'eom' || (a === 'end' && b === 'of' && c === 'month')) {
    const first = addMonths(`${today.slice(0, 8)}01`, 1);
    return { date: addDays(first, -1), n: a === 'eom' ? 1 : 3 };
  }
  if ((a === 'this' || a === 'next') && (b === 'weekend' || b === 'wknd')) {
    const sat = coming(today, 6);
    return { date: a === 'next' ? nextWeeks(today, 6) : sat, n: 2 };
  }
  if (a === 'weekend') return { date: coming(today, 6), n: 1 };
  if (a === 'next' && b === 'week') return { date: nextWeeks(today, 1), n: 2 };
  if (a === 'next' && b === 'month') return { date: addMonths(`${today.slice(0, 8)}01`, 1), n: 2 };
  if (a === 'next' && b === 'year') return { date: `${Number(today.slice(0, 4)) + 1}-01-01`, n: 2 };
  if (a === 'next' || a === 'this') {
    const i = weekdayIndex(b, true);
    if (i >= 0) return { date: a === 'next' ? nextWeeks(today, i) : coming(today, i), n: 2 };
  }
  if (a === 'in') {
    const count = b === 'a' || b === 'an' ? 1 : /^\d{1,3}$/.test(b) ? Number(b) : null;
    const unit = c.replace(/s$/, '');
    if (count !== null) {
      if (unit === 'day') return { date: addDays(today, count), n: 3 };
      if (unit === 'week') return { date: addDays(today, 7 * count), n: 3 };
      if (unit === 'month') return { date: addMonths(today, count), n: 3 };
      if (unit === 'year') return { date: addMonths(today, 12 * count), n: 3 };
      if (unit === 'hour' || unit === 'hr' || unit === 'minute' || unit === 'min') {
        const ms = count * (unit.startsWith('h') ? 3_600_000 : 60_000);
        const at = new Date(now.getTime() + ms);
        return { date: workspaceDay(at), time: hmFmt.format(at), n: 3 };
      }
    }
  }
  const wd = weekdayIndex(a, loose);
  if (wd >= 0) return { date: coming(today, wd), n: 1 };
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(a);
  if (iso) {
    const d = validYmd(Number(iso[1]), Number(iso[2]), Number(iso[3]));
    return d ? { date: d, n: 1 } : null;
  }
  const us = /^(\d{1,2})\/(\d{1,2})(?:\/(\d{2}|\d{4}))?$/.exec(a);
  if (us) {
    const d = monthDay(Number(us[1]), Number(us[2]), us[3] ? Number(us[3]) : null, today);
    return d ? { date: d, n: 1 } : null;
  }
  // "jan 5", "january 5th", "jan 5 2027"
  const mi = monthIndex(a);
  const dn = dayNumber(b);
  if (mi >= 0 && dn !== null) {
    const year = /^\d{4}$/.test(c) ? Number(c) : null;
    const d = monthDay(mi + 1, dn, year, today);
    return d ? { date: d, n: year === null ? 2 : 3 } : null;
  }
  // "5 jan", "5th of january"
  const da = dayNumber(a);
  if (da !== null) {
    const off = b === 'of' ? 1 : 0;
    const m2 = monthIndex(w[1 + off] ?? '');
    if (m2 >= 0) {
      const d = monthDay(m2 + 1, da, null, today);
      return d ? { date: d, n: 2 + off } : null;
    }
  }
  return null;
}

/** A date and/or time at w[0], with an optional lead word and a trailing time. */
function readWhen(w: string[], today: string, now: Date, explicit = false): When | null {
  let lead = 0;
  if (LEADS.has(w[0]) && w.length > 1) lead = 1;
  const rest = w.slice(lead);
  const loose = explicit || lead > 0;
  const date = readDate(rest, today, now, loose);
  if (date) {
    let n = lead + date.n;
    let time = date.time;
    if (!time) {
      const at = w[n] === 'at';
      const t = readTime(w.slice(n + (at ? 1 : 0)), at);
      if (t) {
        time = t.time;
        n += t.n + (at ? 1 : 0);
      }
    }
    return { date: date.date, time, n };
  }
  // A time alone ("4pm", "at 4", "by noon"): today if still ahead, otherwise tomorrow.
  const at = rest[0] === 'at';
  const t = readTime(rest.slice(at ? 1 : 0), at);
  if (!t || (lead && at)) return null;
  return { date: dayFor(t.time!, today, now), time: t.time, timeOnly: true, n: lead + (at ? 1 : 0) + t.n };
}

interface Repeat {
  rrule: string;
  /** The first occurrence's date, when the rule pins a weekday or month day. */
  date?: string;
  n: number;
}

function dayCode(i: number): Day {
  return DAYS[(i + 6) % 7];
}

/** daily/weekly/monthly/yearly, or "every …" at w[0]. */
function readRepeat(w: string[], today: string): Repeat | null {
  const [a = '', b = ''] = w;
  const never = { kind: 'never' } as const;
  const simple: Record<string, string> = {
    daily: toRrule({ preset: { kind: 'daily', interval: 1 }, ends: never }),
    weekly: toRrule({ preset: { kind: 'weekly', interval: 1, days: [] }, ends: never }),
    monthly: toRrule({ preset: { kind: 'monthly_day', interval: 1, monthDays: [] }, ends: never }),
    yearly: 'FREQ=YEARLY',
    annually: 'FREQ=YEARLY',
    weekdays: toRrule({ preset: { kind: 'weekdays' }, ends: never }),
  };
  if (a in simple && a !== 'weekdays') return { rrule: simple[a], n: 1 };
  if (a !== 'every') return null;
  let i = 1;
  let interval = 1;
  if (b === 'other') {
    interval = 2;
    i = 2;
  } else if (/^\d{1,2}$/.test(b)) {
    interval = Math.max(1, Number(b));
    i = 2;
  }
  const unit = (w[i] ?? '').replace(/s$/, '');
  if (unit === 'day') {
    return { rrule: toRrule({ preset: { kind: 'daily', interval }, ends: never }), n: i + 1 };
  }
  if (unit === 'weekday' && interval === 1) return { rrule: simple.weekdays, n: i + 1 };
  if (unit === 'week') {
    return {
      rrule: toRrule({ preset: { kind: 'weekly', interval, days: [] }, ends: never }),
      n: i + 1,
    };
  }
  if (unit === 'month') {
    return {
      rrule: toRrule({ preset: { kind: 'monthly_day', interval, monthDays: [] }, ends: never }),
      n: i + 1,
    };
  }
  if (unit === 'year') return { rrule: `FREQ=YEARLY${interval > 1 ? `;INTERVAL=${interval}` : ''}`, n: i + 1 };
  // "every 15th" (of the month)
  const md = /^(\d{1,2})(st|nd|rd|th)$/.exec(w[i] ?? '');
  if (md && Number(md[1]) >= 1 && Number(md[1]) <= 31) {
    const day = Number(md[1]);
    let n = i + 1;
    if (w[n] === 'of' && w[n + 1] === 'the' && w[n + 2] === 'month') n += 3;
    const thisMonth = validYmd(Number(today.slice(0, 4)), Number(today.slice(5, 7)), day);
    const date =
      thisMonth && thisMonth >= today ? thisMonth : (() => {
        let probe = addMonths(`${today.slice(0, 8)}01`, 1);
        for (let k = 0; k < 12; k++) {
          const d = validYmd(Number(probe.slice(0, 4)), Number(probe.slice(5, 7)), day);
          if (d) return d;
          probe = addMonths(probe, 1);
        }
        return undefined;
      })();
    return {
      rrule: toRrule({ preset: { kind: 'monthly_day', interval, monthDays: [day] }, ends: never }),
      date,
      n,
    };
  }
  // "every monday and thursday", "every mon, wed, fri"
  const days: number[] = [];
  let n = i;
  while (n < w.length) {
    const word = w[n].replace(/,$/, '');
    const d = weekdayIndex(word.replace(/s$/, ''), true);
    if (d >= 0) {
      if (!days.includes(d)) days.push(d);
      n++;
      continue;
    }
    if ((word === 'and' || word === '&') && days.length && weekdayIndex((w[n + 1] ?? '').replace(/[,s]$/, ''), true) >= 0) {
      n++;
      continue;
    }
    break;
  }
  if (!days.length) return null;
  const first = days.map((d) => coming(today, d)).sort()[0];
  return {
    rrule: toRrule({ preset: { kind: 'weekly', interval, days: days.map(dayCode) }, ends: never }),
    date: first,
    n,
  };
}

function join(when: When): string | null {
  if (!when.date) return null;
  return when.time ? `${when.date}T${when.time}` : when.date;
}

export function parseQuickAdd(text: string, now: Date = new Date()): QuickAdd {
  const out: QuickAdd = {
    title: '',
    tags: [],
    assignees: [],
    priority: null,
    due: null,
    start: null,
    list: null,
    rrule: null,
  };
  const today = workspaceDay(now);
  const raw = text.trim().split(/\s+/).filter(Boolean);
  const lower = raw.map((w) => w.toLowerCase().replace(/[.,;]$/, ''));
  const words: string[] = [];
  let repeatDate: string | undefined;
  let repeatTime: string | undefined;
  let due: When | null = null;
  let i = 0;
  while (i < raw.length) {
    const word = raw[i];
    const low = lower[i];
    if (word.length > 1 && word.startsWith('\\')) {
      words.push(word.slice(1));
      i++;
      continue;
    }
    if (word.length > 1 && word.startsWith('#')) {
      const tag = low.slice(1);
      if (!out.tags.includes(tag)) out.tags.push(tag);
      i++;
      continue;
    }
    if (word.length > 1 && word.startsWith('@')) {
      const handle = low.slice(1);
      if (!out.assignees.includes(handle)) out.assignees.push(handle);
      i++;
      continue;
    }
    if (word.length > 1 && word.startsWith('~')) {
      out.list = word.slice(1);
      i++;
      continue;
    }
    if (word.startsWith('!') && low.slice(1) in PRIORITIES) {
      out.priority = PRIORITIES[low.slice(1)];
      i++;
      continue;
    }
    const prefixed = /^(due|start):(.+)$/.exec(low);
    if (prefixed) {
      const tail = [prefixed[2], ...lower.slice(i + 1)];
      const when = readWhen(tail, today, now, true);
      if (when) {
        if (prefixed[1] === 'due') {
          due = when;
          out.due = join(when);
        } else out.start = join(when);
        i += when.n;
        continue;
      }
      words.push(word);
      i++;
      continue;
    }
    const rep = readRepeat(lower.slice(i), today);
    if (rep) {
      out.rrule = rep.rrule;
      repeatDate = rep.date;
      i += rep.n;
      // "every monday at 9am"
      const at = lower[i] === 'at';
      const t = readTime(lower.slice(i + (at ? 1 : 0)), at);
      if (t) {
        repeatTime = t.time;
        i += t.n + (at ? 1 : 0);
      }
      continue;
    }
    const when = readWhen(lower.slice(i), today, now);
    if (when) {
      due = when;
      out.due = join(when);
      i += when.n;
      continue;
    }
    words.push(word);
    i++;
  }
  // A repeating line: the first occurrence is the date typed, else the day the rule pins
  // ("every monday"), else the time's day; the time comes from either place.
  if (out.rrule) {
    const time = repeatTime ?? due?.time;
    const date =
      (due && !due.timeOnly ? due.date : undefined) ??
      repeatDate ??
      (time ? (due?.date ?? dayFor(time, today, now)) : undefined);
    out.due = date ? join({ date, time, n: 0 }) : null;
  }
  out.title = words.join(' ');
  return out;
}

function squash(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** The list a `~hint` names: exact name or slug first, then the first that starts with it. */
export function matchList<T extends { name: string; slug: string; archived_at?: string | null }>(
  hint: string,
  lists: T[],
): T | null {
  const h = squash(hint);
  if (!h) return null;
  const live = lists.filter((l) => !l.archived_at);
  return (
    live.find((l) => squash(l.name) === h || squash(l.slug) === h) ??
    live.find((l) => squash(l.name).startsWith(h) || squash(l.slug).startsWith(h)) ??
    live.find((l) => squash(l.name).includes(h)) ??
    null
  );
}
