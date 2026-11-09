// Automations on the board: rules, the run log and the vocabulary (GET /automations/catalog),
// plus the pure pieces the builder uses (presets, blank rule, a client-side summary for the live
// preview). Live: automation.* events reload the rules; any change an automation makes (an event
// whose session_ref is `automation:<id>`) reloads the run log and the rules' counters.

import { api, get, patch, post } from './api';
import { board } from './store.svelte';
import type { SlateEvent } from './types';

export type ParamKind =
  | 'status'
  | 'status_kind'
  | 'agent_state'
  | 'account'
  | 'tag'
  | 'priority'
  | 'list'
  | 'number'
  | 'text'
  | 'url'
  | 'channel';

export interface CatalogParam {
  name: string;
  kind: ParamKind;
  required: boolean;
  help: string;
}

export interface CatalogEntry {
  type: string;
  label: string;
  params: CatalogParam[];
}

export interface Catalog {
  triggers: (CatalogEntry & { event: string })[];
  conditions: { fields: { field: string; label: string }[]; ops: string[] };
  actions: CatalogEntry[];
}

export type Step = { type: string } & Record<string, string | number | undefined>;

export interface Condition {
  field: string;
  op: 'is' | 'is_not';
  value: string;
}

export interface RuleDraft {
  name: string;
  list_id: string | null;
  section_id: string | null;
  trigger: Step;
  conditions: Condition[];
  actions: Step[];
  enabled: boolean;
}

export interface Rule extends RuleDraft {
  id: string;
  description: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  signed: boolean;
  webhook_secret?: string | null;
  run_count: number;
  last_run_at: string | null;
  last_status: 'ok' | 'partial' | 'failed' | null;
}

export interface Run {
  id: string;
  automation_id: string;
  automation_name: string | null;
  event_id: string | null;
  item_id: string | null;
  item_title: string | null;
  triggered_by: string | null;
  status: 'ok' | 'partial' | 'failed';
  results: { action: string; ok: boolean; detail: string }[];
  at: string;
}

export const SESSION_PREFIX = 'automation:';

/** The rule id behind an automation's change, or null. */
export function automationOf(ev: Pick<SlateEvent, 'session_ref'>): string | null {
  const ref = ev.session_ref ?? '';
  return ref.startsWith(SESSION_PREFIX) ? ref.slice(SESSION_PREFIX.length) : null;
}

export function blankRule(listId: string | null = null): RuleDraft {
  return {
    name: '',
    list_id: listId,
    section_id: null,
    trigger: { type: 'status_changed', to_kind: 'done' },
    conditions: [],
    actions: [{ type: 'add_tag', tag: '' }],
    enabled: true,
  };
}

/** One-click starting points, ClickUp's "templates" gallery. Each opens the builder filled in. */
export interface Preset {
  key: string;
  title: string;
  hint: string;
  rule: (me: string | null) => Omit<RuleDraft, 'list_id' | 'section_id' | 'enabled'>;
}

export const PRESETS: Preset[] = [
  {
    key: 'overdue-urgent',
    title: 'Overdue tasks become urgent',
    hint: 'When a due date passes, set priority to Urgent.',
    rule: () => ({
      name: 'Overdue tasks become urgent',
      trigger: { type: 'due_passes' },
      conditions: [],
      actions: [{ type: 'set_priority', priority: 'urgent' }],
    }),
  },
  {
    key: 'assign-new',
    title: 'Assign new tasks to me',
    hint: 'When a task is created with no assignee, assign it to you.',
    rule: (me) => ({
      name: 'Assign new tasks to me',
      trigger: { type: 'task_created' },
      conditions: [{ field: 'assignee', op: 'is', value: 'nobody' }],
      actions: [{ type: 'assign', account: me ?? '' }],
    }),
  },
  {
    key: 'done-archive',
    title: 'Tidy up finished work',
    hint: 'When a task is done, tag it and clear its priority.',
    rule: () => ({
      name: 'Tidy up finished work',
      trigger: { type: 'status_changed', to_kind: 'done' },
      conditions: [],
      actions: [
        { type: 'add_tag', tag: 'done' },
        { type: 'set_priority', priority: 'none' },
      ],
    }),
  },
  {
    key: 'agent-handoff',
    title: 'Hand tagged work to an agent',
    hint: 'When the tag "agent" is added, assign an agent and tell it.',
    rule: () => ({
      name: 'Hand tagged work to an agent',
      trigger: { type: 'tag_added', tag: 'agent' },
      conditions: [],
      actions: [
        { type: 'assign', account: '' },
        { type: 'comment', body: 'Picked up by automation: please take "{title}".' },
        { type: 'set_status', status: 'In progress' },
      ],
    }),
  },
  {
    key: 'started',
    title: 'Mark started work',
    hint: 'When work starts (an active status), give it a due date in 3 days.',
    rule: () => ({
      name: 'Started work gets a due date',
      trigger: { type: 'status_changed', to_kind: 'active' },
      conditions: [],
      actions: [{ type: 'set_due', in_days: 3 }],
    }),
  },
  {
    key: 'agent-working',
    title: 'Agent work moves the card',
    hint: 'When an agent starts working on a task, move it to an active status.',
    rule: () => ({
      name: 'Agent work moves the card',
      trigger: { type: 'agent_reported', state: 'working' },
      conditions: [],
      actions: [{ type: 'set_status', status: 'In progress' }],
    }),
  },
  {
    key: 'mention-webhook',
    title: 'Ping a webhook on mentions',
    hint: 'When a comment mentions someone, POST it to a URL (Slack, n8n, your agent).',
    rule: (me) => ({
      name: 'Ping a webhook on mentions',
      trigger: { type: 'comment_added', mentions: me ?? '' },
      conditions: [],
      actions: [{ type: 'webhook', url: 'https://' }],
    }),
  },
];

const KIND_WORDS: Record<string, string> = {
  open: 'an open status',
  active: 'an active status',
  done: 'a done status',
  cancelled: 'a cancelled status',
};

/** The rule in words, as the builder fills it in (the server's `description` is the record). */
export function summarize(r: RuleDraft, catalog: Catalog | null, listName: (id: string) => string): string {
  const t = catalog?.triggers.find((x) => x.type === r.trigger.type);
  let when = `When ${(t?.label ?? r.trigger.type).toLowerCase()}`;
  const tr = r.trigger;
  if (tr.type === 'agent_reported') {
    when = `When an agent reports ${tr.state || 'a new state'}`;
  } else if (tr.type === 'status_changed') {
    if (tr.from) when += ` from ${tr.from}`;
    if (tr.to) when += ` to ${tr.to}`;
    else if (tr.to_kind) when += ` to ${KIND_WORDS[String(tr.to_kind)] ?? tr.to_kind}`;
  } else if (tr.type === 'assignee_added' || tr.type === 'assignee_removed') {
    if (tr.account) when += ` (@${tr.account})`;
  } else if (tr.type === 'tag_added' || tr.type === 'tag_removed') {
    if (tr.tag) when += ` (${tr.tag})`;
  } else if (tr.type === 'priority_changed' && tr.to) {
    when += ` to ${tr.to}`;
  } else if (tr.type === 'comment_added' && tr.mentions) {
    when += ` mentioning @${tr.mentions}`;
  }
  const ifs = r.conditions
    .filter((c) => c.value)
    .map((c) => `${c.field.replace('_', ' ')} ${c.op === 'is_not' ? 'is not' : 'is'} ${c.field === 'list' ? listName(c.value) : c.value}`);
  const thens = r.actions.map((a) => {
    const label = (catalog?.actions.find((x) => x.type === a.type)?.label ?? a.type).toLowerCase();
    switch (a.type) {
      case 'set_status':
        return `${label} to ${a.status || '…'}`;
      case 'set_priority':
        return `${label} to ${a.priority || '…'}`;
      case 'assign':
      case 'unassign':
        return `${label} @${a.account || '…'}`;
      case 'add_tag':
      case 'remove_tag':
        return `${label} ${a.tag || '…'}`;
      case 'move_list':
        return `${label} ${a.list_id ? listName(String(a.list_id)) : '…'}`;
      case 'set_due':
        return Number(a.in_days) < 0 ? 'clear the due date' : `set due in ${a.in_days ?? '…'} days`;
      default:
        return label;
    }
  });
  return `${when}${ifs.length ? `, if ${ifs.join(' and ')}` : ''}, then ${thens.join(', ') || '…'}.`;
}

/** The draft as the API wants it: empty optional params dropped, numbers as numbers. */
export function toPayload(r: RuleDraft): RuleDraft {
  const clean = (s: Step): Step => {
    const out: Step = { type: s.type };
    for (const [k, v] of Object.entries(s)) {
      if (k === 'type' || v === undefined || v === '') continue;
      out[k] = k === 'in_days' ? Number(v) : v;
    }
    return out;
  };
  return {
    name: r.name.trim() || 'Untitled automation',
    list_id: r.list_id || null,
    section_id: r.list_id ? null : r.section_id || null,
    trigger: clean(r.trigger),
    conditions: r.conditions.filter((c) => c.value !== ''),
    actions: r.actions.map(clean),
    enabled: r.enabled,
  };
}

export function toDraft(r: Rule): RuleDraft {
  return {
    name: r.name,
    list_id: r.list_id,
    section_id: r.section_id,
    trigger: { ...r.trigger },
    conditions: r.conditions.map((c) => ({ ...c })),
    actions: r.actions.map((a) => ({ ...a })),
    enabled: r.enabled,
  };
}

class Automations {
  rules = $state<Rule[]>([]);
  runs = $state<Run[]>([]);
  catalog = $state<Catalog | null>(null);
  loaded = $state(false);
  error = $state<string | null>(null);
  /** A webhook signing secret just made: shown once on the page, then gone. */
  revealed = $state<{ name: string; secret: string } | null>(null);
  #off: (() => void) | null = null;
  #timer: ReturnType<typeof setTimeout> | undefined;

  #requested = false;

  /** Load once for a rule's name elsewhere (the task window's activity); no live follow. */
  ensure() {
    if (this.#requested || this.loaded) return;
    this.#requested = true;
    void this.load();
  }

  name(id: string): string | undefined {
    return this.rules.find((r) => r.id === id)?.name;
  }

  async load() {
    try {
      const [rules, runs, catalog] = await Promise.all([
        get<Rule[]>('/automations'),
        get<Run[]>('/automation-runs?limit=100'),
        this.catalog ? Promise.resolve(this.catalog) : get<Catalog>('/automations/catalog'),
      ]);
      this.rules = rules;
      this.runs = runs;
      this.catalog = catalog;
      this.error = null;
    } catch (e) {
      this.error = (e as Error).message;
    }
    this.loaded = true;
  }

  /** Follow the board's stream while the page is open. */
  start() {
    void this.load();
    this.#off ??= board.onEvent((ev) => this.#onEvent(ev));
  }

  stop() {
    this.#off?.();
    this.#off = null;
    clearTimeout(this.#timer);
  }

  #onEvent(ev: SlateEvent) {
    if (ev.type.startsWith('automation.') || automationOf(ev)) {
      clearTimeout(this.#timer);
      this.#timer = setTimeout(() => void this.load(), 250);
    }
  }

  async save(draft: RuleDraft, id?: string): Promise<Rule> {
    const body = toPayload(draft);
    const rule = id ? await patch<Rule>(`/automations/${id}`, body) : await post<Rule>('/automations', body);
    if (rule.webhook_secret) this.revealed = { name: rule.name, secret: rule.webhook_secret };
    const at = this.rules.findIndex((r) => r.id === rule.id);
    if (at >= 0) this.rules[at] = rule;
    else this.rules.push(rule);
    return rule;
  }

  async toggle(rule: Rule) {
    const want = !rule.enabled;
    rule.enabled = want;
    try {
      await patch<Rule>(`/automations/${rule.id}`, { enabled: want });
    } catch (e) {
      rule.enabled = !want;
      board.toast = (e as Error).message;
    }
  }

  async remove(rule: Rule) {
    await api('DELETE', `/automations/${rule.id}`);
    this.rules = this.rules.filter((r) => r.id !== rule.id);
    this.runs = this.runs.filter((r) => r.automation_id !== rule.id);
  }

  async rotateSecret(rule: Rule) {
    const { secret } = await post<{ secret: string }>(`/automations/${rule.id}/secret`);
    this.revealed = { name: rule.name, secret };
  }

  async runNow(rule: Rule, itemId: string): Promise<Run> {
    const run = await post<Run>(`/automations/${rule.id}/run`, { item_id: itemId });
    this.runs = [run, ...this.runs];
    return run;
  }
}

export const automations = new Automations();
