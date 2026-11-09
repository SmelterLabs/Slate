// What the board calls each priority flag: Urgent, High, Normal, Low (or none).

import type { Priority } from './types';

const NAME: Record<Priority, string> = { urgent: 'Urgent', high: 'High', normal: 'Normal', low: 'Low' };

/** "Urgent": what a pill, column, table cell, menu or history line shows. */
export function priorityLabel(p: Priority): string {
  return NAME[p];
}

/** The same word; kept so menus and tooltips have one name to call. */
export function priorityLong(p: Priority): string {
  return NAME[p];
}
