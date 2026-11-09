// The recurrence editor's presets <-> one RRULE line (RFC 5545). Pure, unit-tested.
//
// A preset is what the editor shows ("monthly on the 2nd and 4th Wednesday"); the RRULE is
// what Slate stores. `toRrule` always writes one canonical form; `fromRrule` also reads the
// equivalent spellings agents use (BYDAY=WE;BYSETPOS=2,4, DAILY;BYDAY=MO..FR) and returns
// null for anything a preset cannot express exactly, which the editor then shows as Advanced.

export type Day = 'MO' | 'TU' | 'WE' | 'TH' | 'FR' | 'SA' | 'SU';
export const DAYS: Day[] = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
export const DAY_NAMES: Record<Day, string> = {
  MO: 'Monday',
  TU: 'Tuesday',
  WE: 'Wednesday',
  TH: 'Thursday',
  FR: 'Friday',
  SA: 'Saturday',
  SU: 'Sunday',
};
export const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];
/** Positions offered for "the Nth weekday": 1st..4th and last (5th is rare and confusing). */
export const NTHS = [1, 2, 3, 4, -1];
const WEEKDAYS: Day[] = ['MO', 'TU', 'WE', 'TH', 'FR'];

export type Preset =
  | { kind: 'daily'; interval: number }
  | { kind: 'weekdays' }
  /** `days` empty: the start date's weekday. */
  | { kind: 'weekly'; interval: number; days: Day[] }
  /** Days of the month; -1 is the last day. Empty: the start date's day. */
  | { kind: 'monthly_day'; interval: number; monthDays: number[] }
  /** "the 2nd and 4th Wednesday"; `day: 'weekday'` means Monday to Friday. */
  | { kind: 'monthly_nth'; interval: number; nths: number[]; day: Day | 'weekday' }
  | { kind: 'yearly_date'; interval: number; month: number; monthDay: number }
  | { kind: 'yearly_nth'; interval: number; month: number; nth: number; day: Day };

export type PresetKind = Preset['kind'];

export type Ends =
  | { kind: 'never' }
  | { kind: 'count'; count: number }
  /** Through the end of this local day (YYYY-MM-DD). */
  | { kind: 'until'; date: string };

export interface Rule {
  preset: Preset;
  ends: Ends;
}

const KNOWN = new Set(['FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'BYMONTH', 'BYSETPOS', 'COUNT', 'UNTIL']);

function sortDays(days: Day[]): Day[] {
  return [...new Set(days)].sort((a, b) => DAYS.indexOf(a) - DAYS.indexOf(b));
}

/** 1, 2, 3, 4, 5, then -1 (last): the order people read them in. */
function sortNths(nths: number[]): number[] {
  const key = (n: number) => (n < 0 ? 100 - n : n);
  return [...new Set(nths)].sort((a, b) => key(a) - key(b));
}

function sortMonthDays(days: number[]): number[] {
  const key = (n: number) => (n < 0 ? 100 - n : n);
  return [...new Set(days)].sort((a, b) => key(a) - key(b));
}

function interval(n: number): string {
  return n > 1 ? `;INTERVAL=${n}` : '';
}

/** The canonical RRULE for a rule (no `RRULE:` prefix, uppercase). */
export function toRrule({ preset: p, ends }: Rule): string {
  let s: string;
  switch (p.kind) {
    case 'daily':
      s = `FREQ=DAILY${interval(p.interval)}`;
      break;
    case 'weekdays':
      s = 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR';
      break;
    case 'weekly':
      s = `FREQ=WEEKLY${interval(p.interval)}`;
      if (p.days.length) s += `;BYDAY=${sortDays(p.days).join(',')}`;
      break;
    case 'monthly_day':
      s = `FREQ=MONTHLY${interval(p.interval)}`;
      if (p.monthDays.length) s += `;BYMONTHDAY=${sortMonthDays(p.monthDays).join(',')}`;
      break;
    case 'monthly_nth': {
      const nths = sortNths(p.nths.length ? p.nths : [1]);
      s = `FREQ=MONTHLY${interval(p.interval)}`;
      s +=
        p.day === 'weekday'
          ? `;BYDAY=${WEEKDAYS.join(',')};BYSETPOS=${nths.join(',')}`
          : `;BYDAY=${nths.map((n) => `${n}${p.day}`).join(',')}`;
      break;
    }
    case 'yearly_date':
      s = `FREQ=YEARLY${interval(p.interval)};BYMONTH=${p.month};BYMONTHDAY=${p.monthDay}`;
      break;
    case 'yearly_nth':
      s = `FREQ=YEARLY${interval(p.interval)};BYMONTH=${p.month};BYDAY=${p.nth}${p.day}`;
      break;
  }
  if (ends.kind === 'count') s += `;COUNT=${Math.max(1, Math.floor(ends.count))}`;
  if (ends.kind === 'until') s += `;UNTIL=${ends.date.replaceAll('-', '')}`;
  return s;
}

function parts(text: string): Record<string, string> | null {
  const out: Record<string, string> = {};
  const body = text.trim().replace(/^RRULE:/i, '');
  if (!body) return null;
  for (const piece of body.split(';')) {
    if (!piece) continue;
    const [k, v, ...rest] = piece.split('=');
    if (!k || v === undefined || rest.length) return null;
    out[k.trim().toUpperCase()] = v.trim().toUpperCase();
  }
  return out;
}

function ints(value: string | undefined): number[] | null {
  if (!value) return [];
  const out = value.split(',').map((x) => Number(x));
  return out.every((n) => Number.isInteger(n) && n !== 0) ? out : null;
}

function byday(value: string | undefined): { n: number | null; day: Day }[] | null {
  if (!value) return [];
  const out = [];
  for (const token of value.split(',')) {
    const m = /^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$/.exec(token);
    if (!m) return null;
    out.push({ n: m[1] ? Number(m[1]) : null, day: m[2] as Day });
  }
  return out;
}

function sameSet<T>(a: T[], b: T[]): boolean {
  return a.length === b.length && new Set(a).size === b.length && b.every((x) => a.includes(x));
}

/** The anchor date's month (1-12) and day, for a bare FREQ=YEARLY. */
function anchorParts(anchor: string): { month: number; day: number } {
  return { month: Number(anchor.slice(5, 7)), day: Number(anchor.slice(8, 10)) };
}

/**
 * The preset for an RRULE, or null when only the Advanced field can show it exactly.
 * `anchor` (the rule's start date, YYYY-MM-DD) lets a bare FREQ=YEARLY read as a date.
 */
export function fromRrule(text: string, anchor?: string): Rule | null {
  const p = parts(text);
  if (!p || !p.FREQ) return null;
  if (Object.keys(p).some((k) => !KNOWN.has(k))) return null;
  if (p.COUNT && p.UNTIL) return null;

  let ends: Ends = { kind: 'never' };
  if (p.COUNT) {
    const n = Number(p.COUNT);
    if (!Number.isInteger(n) || n < 1) return null;
    ends = { kind: 'count', count: n };
  } else if (p.UNTIL) {
    const m = /^(\d{4})(\d{2})(\d{2})(T\d{6}Z?)?$/.exec(p.UNTIL);
    if (!m) return null;
    ends = { kind: 'until', date: `${m[1]}-${m[2]}-${m[3]}` };
  }

  const n = p.INTERVAL ? Number(p.INTERVAL) : 1;
  if (!Number.isInteger(n) || n < 1) return null;
  const days = byday(p.BYDAY);
  const monthDays = ints(p.BYMONTHDAY);
  const months = ints(p.BYMONTH);
  const setpos = ints(p.BYSETPOS);
  if (!days || !monthDays || !months || !setpos) return null;
  const plain = days.filter((d) => d.n === null).map((d) => d.day);
  const numbered = days.filter((d) => d.n !== null);
  const ok = (preset: Preset): Rule => ({ preset, ends });

  switch (p.FREQ) {
    case 'DAILY':
      if (monthDays.length || months.length || setpos.length || numbered.length) return null;
      if (!plain.length) return ok({ kind: 'daily', interval: n });
      return n === 1 && sameSet(plain, WEEKDAYS) ? ok({ kind: 'weekdays' }) : null;
    case 'WEEKLY':
      if (monthDays.length || months.length || setpos.length || numbered.length) return null;
      if (n === 1 && sameSet(plain, WEEKDAYS)) return ok({ kind: 'weekdays' });
      return ok({ kind: 'weekly', interval: n, days: sortDays(plain) });
    case 'MONTHLY':
      if (months.length) return null;
      if (!days.length) {
        if (setpos.length || monthDays.some((d) => d < -31 || d > 31)) return null;
        return ok({ kind: 'monthly_day', interval: n, monthDays: sortMonthDays(monthDays) });
      }
      if (monthDays.length) return null;
      if (numbered.length && !plain.length && !setpos.length) {
        const day = numbered[0].day;
        if (!numbered.every((d) => d.day === day)) return null;
        return ok({ kind: 'monthly_nth', interval: n, nths: sortNths(numbered.map((d) => d.n!)), day });
      }
      if (plain.length && !numbered.length && setpos.length) {
        if (plain.length === 1) {
          return ok({ kind: 'monthly_nth', interval: n, nths: sortNths(setpos), day: plain[0] });
        }
        if (sameSet(plain, WEEKDAYS)) {
          return ok({ kind: 'monthly_nth', interval: n, nths: sortNths(setpos), day: 'weekday' });
        }
      }
      return null;
    case 'YEARLY': {
      if (!days.length && !monthDays.length && !months.length && !setpos.length) {
        if (!anchor) return null;
        const a = anchorParts(anchor);
        return ok({ kind: 'yearly_date', interval: n, month: a.month, monthDay: a.day });
      }
      if (months.length !== 1 || months[0] < 1 || months[0] > 12) return null;
      const month = months[0];
      if (monthDays.length === 1 && !days.length && !setpos.length && monthDays[0] > 0) {
        return ok({ kind: 'yearly_date', interval: n, month, monthDay: monthDays[0] });
      }
      if (monthDays.length) return null;
      if (numbered.length === 1 && !plain.length && !setpos.length) {
        return ok({ kind: 'yearly_nth', interval: n, month, nth: numbered[0].n!, day: numbered[0].day });
      }
      if (plain.length === 1 && !numbered.length && setpos.length === 1) {
        return ok({ kind: 'yearly_nth', interval: n, month, nth: setpos[0], day: plain[0] });
      }
      return null;
    }
    default:
      return null;
  }
}

// --- defaults from a start date ---------------------------------------------------------------

function utcNoon(ymd: string): Date {
  return new Date(`${ymd}T12:00:00Z`);
}

export function weekdayOf(ymd: string): Day {
  return DAYS[(utcNoon(ymd).getUTCDay() + 6) % 7];
}

function daysInMonth(ymd: string): number {
  const d = utcNoon(ymd);
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
}

/** Which "Nth weekday" a date is: 1-4, or -1 when it is that weekday's last in the month. */
export function nthOf(ymd: string): number {
  const day = Number(ymd.slice(8, 10));
  if (day + 7 > daysInMonth(ymd)) return -1;
  return Math.ceil(day / 7);
}

/** A sensible preset of `kind` for a rule starting on `anchor` (YYYY-MM-DD). */
export function presetFor(kind: PresetKind, anchor: string): Preset {
  const { month, day } = anchorParts(anchor);
  const weekday = weekdayOf(anchor);
  switch (kind) {
    case 'daily':
      return { kind, interval: 1 };
    case 'weekdays':
      return { kind };
    case 'weekly':
      return { kind, interval: 1, days: [weekday] };
    case 'monthly_day':
      return { kind, interval: 1, monthDays: [day === daysInMonth(anchor) && day > 28 ? -1 : day] };
    case 'monthly_nth':
      return { kind, interval: 1, nths: [nthOf(anchor)], day: weekday };
    case 'yearly_date':
      return { kind, interval: 1, month, monthDay: day };
    case 'yearly_nth':
      return { kind, interval: 1, month, nth: nthOf(anchor), day: weekday };
  }
}

/** "1st", "2nd", "last", "2nd to last". */
export function ordinal(n: number): string {
  if (n === -1) return 'last';
  if (n < 0) return `${ordinal(-n)} to last`;
  const v = n % 100;
  const s = v >= 11 && v <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${s}`;
}

const UNITS: Record<string, [string, string]> = {
  DAILY: ['Daily', 'day'],
  WEEKLY: ['Weekly', 'week'],
  MONTHLY: ['Monthly', 'month'],
  YEARLY: ['Yearly', 'year'],
};

/** A card-sized name for a rule: "Weekly", "Every 2 weeks", "Weekdays", "Daily · when done". */
export function shortRule(rule: { rrule: string; trigger?: string }): string {
  const parts = Object.fromEntries(
    rule.rrule
      .replace(/^RRULE:/i, '')
      .split(';')
      .map((p) => p.split('=') as [string, string]),
  );
  const [word, unit] = UNITS[parts.FREQ] ?? ['Repeats', ''];
  const n = Number(parts.INTERVAL ?? 1);
  let label = n > 1 && unit ? `Every ${n} ${unit}s` : word;
  if (parts.FREQ === 'WEEKLY' && parts.BYDAY === 'MO,TU,WE,TH,FR' && n === 1) label = 'Weekdays';
  return rule.trigger === 'on_done' ? `${label} · when done` : label;
}
