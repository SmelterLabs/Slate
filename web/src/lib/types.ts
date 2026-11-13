// Shapes of the Slate REST API (src/slate/api/schemas.py). Times are UTC ISO 8601 strings.

export type StatusKind = 'open' | 'active' | 'done' | 'cancelled';
export type Priority = 'urgent' | 'high' | 'normal' | 'low';
export type AccountKind = 'person' | 'agent';
export type Source = 'ui' | 'api' | 'mcp' | 'system';

export interface Section {
  whiteboard_url?: string | null;
  id: string;
  name: string;
  slug: string;
  position: number;
  audience: 'personal' | 'ai' | 'work' | 'family';
  created_at: string;
  archived_at: string | null;
  /** Hidden from members unless shared with them (owners and admins still see it). */
  private?: boolean;
}

export interface Status {
  id: string;
  name: string;
  kind: StatusKind;
  color: string | null;
  position?: number | null;
}

export interface List {
  id: string;
  section_id: string;
  name: string;
  slug: string;
  position: number;
  default_status_id: string | null;
  created_at: string;
  archived_at: string | null;
  private?: boolean;
  /** `tasks` (a board) or `drawer` (a list of documents). */
  kind?: 'tasks' | 'drawer';
  statuses: Status[];
}

export interface Assignee {
  id: string;
  handle: string;
  display_name: string;
  kind: AccountKind;
}

export type Role = 'owner' | 'admin' | 'member' | 'guest';
export type Level = 'view' | 'comment' | 'edit';

/** How an agent is woken for a task (accounts.wake). */
export type Wake = 'push' | 'pull' | 'manual';
/** What a task's run control does: Run now, Nudge or Copy brief. */
export type RunMode = 'run' | 'nudge' | 'brief';

export interface Account extends Assignee {
  wake?: Wake;
  created_at?: string;
  disabled_at?: string | null;
  role?: Role;
}

/** What this token reaches (GET /me `access`); the server enforces it either way. */
export interface MyAccess {
  all: boolean;
  level: Level;
  /** Owner or admin with an unlimited token: the Members page and sharing. */
  manage: boolean;
  general: boolean;
  /** The token is narrowed below its account (an agent's one-list token). */
  limited?: boolean;
  /** Section id -> level; null = seen only through one of its lists. */
  sections: Record<string, Level | null>;
  lists: Record<string, Level>;
}

export interface Share {
  id: string;
  account_id: string;
  scope_kind: 'section' | 'list';
  scope_id: string;
  level: Level;
  created_by: string;
  created_at: string;
}

export interface AccessRow {
  account_id: string;
  level: Level;
  via: 'role' | 'section' | 'share';
  share_id: string | null;
}

export interface Member {
  account: Account;
  role: Role;
  shares: number;
  last_active_at: string | null;
}

export interface AuditActor {
  id: string;
  handle: string | null;
  display_name: string | null;
  kind: 'person' | 'agent' | null;
}

export interface AuditEntry {
  id: string;
  at: string;
  type: string;
  field: string | null;
  before: unknown;
  after: unknown;
  source: string;
  session_ref: string | null;
  actor: AuditActor;
  item_id: string | null;
  subject_id: string | null;
  item: { id: string; title: string; list_id: string; list_name: string | null } | null;
  summary: string;
}

export interface AuditPage {
  entries: AuditEntry[];
  next_before: string | null;
  has_more: boolean;
}

export interface Item {
  id: string;
  list_id: string;
  parent_id: string | null;
  kind: 'task';
  title: string;
  body_md: string;
  status: Status | null;
  priority: Priority | null;
  start_at: string | null;
  due_at: string | null;
  /** The due date was set without a time (stored as 17:00 workspace time; the time is not shown). */
  due_date_only?: boolean;
  completed_at: string | null;
  waiting_on_account_id: string | null;
  acceptance_md: string | null;
  recurrence_id: string | null;
  occurrence_at: string | null;
  external_ref: string | null;
  position: number;
  /** Null to a project's guest when a workspace agent filed it. */
  created_by: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  assignees: Assignee[];
  tags: string[];
  subtask_count: number;
  comment_count: number;
  /** Account ids of the open Asks (assigned comments) on it: the card's "?" badge. */
  open_asks?: string[];
  // Task window (checklists and attachments); optional so older fixtures still type-check.
  checklist_done?: number;
  checklist_total?: number;
  attachment_count?: number;
  // ClickUp-parity fields; optional so older payloads and fixtures still type-check.
  estimate_minutes?: number | null;
  blocked_by?: string[];
  blocking?: string[];
  related?: string[];
  /** Items this one duplicates (the originals), and the duplicates of this one. */
  duplicate_of?: string[];
  duplicated_by?: string[];
  /** Waits on at least one open item. */
  blocked?: boolean;
  /** The latest agent report while an agent is on it (POST /items/{id}/report). */
  agent?: AgentFlag | null;
  /** Each agent's own conversation for this task (its session ref and a link to open it). */
  agent_threads?: AgentThread[];
  /** A Run now (or the due-time run stage) newer than the last take: asked, not picked up. */
  run_requested_at?: string | null;
  /** In the trash since (also sets archived_at); purge_at is when it goes for good. */
  trashed_at?: string | null;
  purge_at?: string | null;
}

/** GET /capacities: how much one person or agent can take on (the Box view). Null = no limit. */
export interface Capacity {
  account_id: string;
  /** False: the defaults for the account's kind apply. */
  custom: boolean;
  hours_per_day: number | null;
  tasks_per_day: number | null;
  /** Weekdays worked, 0 = Monday. */
  work_days: number[];
}

export type AgentState = 'working' | 'blocked' | 'review';

export interface AgentFlag {
  state: AgentState;
  /** Account id of the reporting agent. */
  by: string;
  at: string;
  note?: string | null;
  progress?: number | null;
  /** Where the agent is working (its chat, session or PR): the badge's Open link. */
  url?: string | null;
}

/** An agent's own session for a task in its runtime: opaque to Slate. */
export interface AgentThread {
  account_id: string;
  ref: string;
  url?: string | null;
}

/** One row of GET /agents: an account's presence and current work. */
export interface AgentEntry {
  account: Assignee;
  /** How a task reaches it: a runner (push), its own app (pull) or a person (manual). */
  wake?: Wake;
  last_seen_at: string | null;
  last_seen_via: string | null;
  online: boolean;
  live_streams: number;
  working: {
    item_id: string;
    title: string;
    list_id: string;
    state: AgentState;
    note: string | null;
    progress: number | null;
    at: string;
  }[];
  open_assigned: number;
  events_24h: number;
  last_event_at: string | null;
  throttled: number;
}

export interface Comment {
  id: string;
  item_id: string;
  author_id: string;
  body_md: string;
  created_at: string;
  edited_at: string | null;
  reactions?: Reaction[];
  /** Handles @mentioned in the body. */
  mentions?: string[];
  /** An Ask: the account it is addressed to; open while resolved_at is null. */
  assignee_id?: string | null;
  resolved_at?: string | null;
  resolved_by_id?: string | null;
  /** person, agent or guest: who wrote it (the server's view, for agents' adapters). */
  author_kind?: 'person' | 'agent' | 'guest' | null;
}

export interface Reaction {
  emoji: string;
  account_ids: string[];
}

export interface ChecklistEntry {
  id: string;
  checklist_id: string;
  title: string;
  done: boolean;
  done_at: string | null;
  assignee_id: string | null;
  position: number;
}

export interface Checklist {
  id: string;
  item_id: string;
  name: string;
  position: number;
  created_at: string;
  items: ChecklistEntry[];
}

export interface Attachment {
  id: string;
  item_id: string;
  filename: string;
  content_type: string;
  size: number;
  uploaded_by: string;
  created_at: string;
}

/** GET /items/{id}/activity: comments and history in one time-ordered list. */
export interface ActivityEntry {
  kind: 'comment' | 'event';
  at: string;
  comment: Comment | null;
  event: SlateEvent | null;
}

export interface SlateEvent {
  id: string;
  type: string;
  item_id: string | null;
  subject_id: string | null;
  actor_id: string;
  source: Source;
  field: string | null;
  before: unknown;
  after: unknown;
  session_ref: string | null;
  at: string;
}

export interface ItemDetail extends Item {
  subtasks: Item[];
  comments: Comment[];
  checklists?: Checklist[];
  attachments?: Attachment[];
  watchers?: Assignee[];
  events: SlateEvent[];
}

/** `GET /today?fields=id`: the board only counts these (the Agenda view runs over the store). */
export interface Today {
  now: string;
  day_ends_at: string;
  overdue: { id: string }[];
  today: { id: string }[];
  waiting: { id: string }[];
  asked?: { id: string }[];
}

export interface Occurrence {
  local: string;
  utc: string;
  due_at: string;
}

export type MissedPolicy = 'spawn_always' | 'roll_forward' | 'skip_if_open';

export interface Recurrence {
  id: string;
  list_id: string;
  title_template: string;
  rrule: string;
  timezone: string;
  paused_at: string | null;
  ended_at: string | null;
  next_occurrences: Occurrence[];
  // Full rule fields (GET /recurrences/{id}); optional so older fixtures still type-check.
  description?: string;
  body_template_md?: string | null;
  priority?: Priority | null;
  tags?: string[];
  assignee_ids?: string[];
  dtstart?: string;
  lead_days?: number;
  missed_policy?: MissedPolicy;
  next_run_at?: string | null;
  trigger?: RecurTrigger;
  skip_weekends?: boolean;
  reopen?: boolean;
  copy_checklists?: boolean;
}

export type RecurTrigger = 'schedule' | 'on_done';

/** GET /recurrences/upcoming: a date a rule will fill that is not an item yet. */
export interface UpcomingRepeat {
  recurrence_id: string;
  list_id: string;
  title: string;
  occurrence_at: string;
  due_at: string;
  local_date: string;
  priority: Priority | null;
  tags: string[];
  assignee_ids: string[];
}

/** GET /recurrences/preview: a rule tried without saving it. */
export interface RulePreview {
  rrule: string;
  description: string;
  timezone: string;
  dtstart: string;
  occurrences: Occurrence[];
}

export interface Tag {
  id: string;
  name: string;
  color: string | null;
  item_count?: number | null;
}

/** Board looks that follow the account to every device (PATCH /me/prefs). */
export interface Prefs {
  theme?: 'auto' | 'light' | 'dark';
  /** Per view key: column key -> folded (true) or open (false), where the viewer flipped it. */
  collapsed?: Record<string, Record<string, boolean>>;
  /** Task ids most recently opened from the command palette, newest first. */
  recent_tasks?: string[];
  /** Daily digest on (default: on for people, off for agents) and its hour, workspace time. */
  digest?: boolean;
  digest_hour?: number;
  /** Inbox reasons pushed to phone and desktop (default lib/push DEFAULT_PUSH_REASONS). */
  push_reasons?: string[];
  /** Where email notifications go, and which inbox reasons are emailed when still unseen
   * after five minutes (default lib/push DEFAULT_EMAIL_REASONS). */
  email_to?: string;
  email_reasons?: string[];
  /** Start watching a task when I create it, am assigned it, or comment on it (default: on for
   * people, off for agents; service/watchers.py). */
  auto_watch?: { created?: boolean; assigned?: boolean; commented?: boolean };
  /** Minutes after which chat still unread is pushed once more (default 10; 0 = never). */
  chat_renudge_minutes?: number;
  /** Doc pages unfolded in the Docs tree (their subpages show). */
  docs_expanded?: string[];
  /** Per scope key (`everything`, `list:<id>`...): the view tab last open there. */
  last_view?: Record<string, string>;
  /** Per view key: unsaved toolbar changes, as the settings that differ from the saved view. */
  view_drafts?: Record<string, Record<string, unknown>>;
}

export interface Me {
  /** Workspace IANA zone; when it is missing the board falls back to its default zone. */
  timezone?: string;
  account: Account;
  scopes: string[];
  token_id: string;
  prefs: Prefs;
  access?: MyAccess;
  /** URL schemes the server accepts for an agent's Open link (SLATE_LINK_SCHEMES); the board
   * opens no other. Absent on an older server: http(s) only. */
  link_schemes?: string[];
  /** AI add is set up on the server (POST /items/ai). */
  ai_add?: boolean;
  /** Email notifications are set up on the server (SLATE_SMTP_HOST). */
  email?: boolean;
  /** When this browser session (an accepted invitation) ends; null for a bearer token. */
  session_expires_at?: string | null;
}

export interface SavedView {
  id: string;
  name: string;
  scope_kind: 'everything' | 'section' | 'list';
  scope_id: string | null;
  /** A ViewConfig (lib/views.ts); stored as JSON and validated by the server. */
  config: Record<string, unknown>;
  position: number;
  owner_id: string;
  shared: boolean;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export type FavoriteKind = 'list' | 'section' | 'view' | 'item' | 'doc';

/** One of your pinned lists, sections, views or items: a chip on the favorites bar. */
export interface Favorite {
  id: string;
  kind: FavoriteKind;
  target_id: string;
  position: number;
  created_at: string;
  /** The target's name when the server answered; the board prefers its own live copy. */
  name: string | null;
  scope_kind: 'everything' | 'section' | 'list' | 'doc' | null;
  scope_id: string | null;
  /** False when the target is archived or gone: the chip hides until it is restored. */
  available: boolean;
}

/** A link from one item's side (POST /items/{id}/links). */
export type LinkKind = 'blocks' | 'blocked_by' | 'relates' | 'duplicate_of' | 'duplicated_by';
