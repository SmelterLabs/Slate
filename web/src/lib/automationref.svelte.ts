// Which automation made a change, and its name, for the task window's activity. A change an
// automation makes carries `session_ref` = `automation:<id>`. Rules are set up through the API
// (GET/POST /automations); the board only reads their names, once, when an activity needs one.

import { get } from './api';
import type { SlateEvent } from './types';

export const SESSION_PREFIX = 'automation:';

/** The rule id behind an automation's change, or null. */
export function automationOf(ev: Pick<SlateEvent, 'session_ref'>): string | null {
  const ref = ev.session_ref ?? '';
  return ref.startsWith(SESSION_PREFIX) ? ref.slice(SESSION_PREFIX.length) : null;
}

class AutomationNames {
  #names = $state<Record<string, string>>({});
  #requested = false;

  /** Load the rule names once; a token that cannot read them shows no names. */
  ensure() {
    if (this.#requested) return;
    this.#requested = true;
    void get<{ id: string; name: string }[]>('/automations')
      .then((rules) => (this.#names = Object.fromEntries(rules.map((r) => [r.id, r.name]))))
      .catch(() => {});
  }

  name(id: string): string | undefined {
    return this.#names[id];
  }
}

export const automationNames = new AutomationNames();
