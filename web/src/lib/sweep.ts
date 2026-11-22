// Weekly sweep helpers for old overdue cards (Decide panel and Settings).
import { workspaceDay } from './quickadd';
import type { List, Status } from './types';
import { statusOfKind } from './views';
import { addDays, weekday } from './when';

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const;

/** Workspace weekday as 0 = Monday .. 6 = Sunday (matching `sweep_weekday` pref). */
function mondayZeroWeekday(day: string): number {
  return (weekday(day) + 6) % 7;
}

/** True when today (workspace time) is the viewer's configured sweep day (default 4 = Friday). */
export function isSweepDay(now: Date, sweepWeekday = 4): boolean {
  return mondayZeroWeekday(workspaceDay(now)) === sweepWeekday;
}

/** Coming sweep day as YYYY-MM-DD in workspace time (today if today is the sweep day). */
export function nextSweepDay(now: Date, sweepWeekday = 4): string {
  const today = workspaceDay(now);
  const delta = (sweepWeekday - mondayZeroWeekday(today) + 7) % 7;
  return addDays(today, delta);
}

/** Where Drop moves a stale card: first cancelled-kind status, else first done-kind status. */
export function dropStatus(list: List | undefined): Status | undefined {
  return statusOfKind(list, 'cancelled') ?? statusOfKind(list, 'done');
}
