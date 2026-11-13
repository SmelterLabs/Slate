<script lang="ts">
  // The drawer's recurrence editor. An item without a rule offers "Make recurring"; an item
  // with one shows the rule in plain words with Edit / Pause / Resume / End. The editor works
  // in presets (lib/rrule.ts) with a raw RRULE "Custom" mode that round-trips when it can, and
  // asks Slate for a live preview (next dates + the rule in words) before anything is saved.
  import { untrack } from 'svelte';
  import { ApiError } from '../lib/api';
  import { describeRrule, shortDate } from '../lib/format';
  import { workspaceDate, workspaceTimezone } from '../lib/timezone';
  import {
    DAYS,
    DAY_NAMES,
    MONTH_NAMES,
    NTHS,
    fromRrule,
    ordinal,
    presetFor,
    toRrule,
    type Day,
    type Ends,
    type Preset,
    type PresetKind,
  } from '../lib/rrule';
  import { board } from '../lib/store.svelte';
  import type { Assignee, ItemDetail, MissedPolicy, RecurTrigger, Recurrence, RulePreview } from '../lib/types';
  import { longDay } from '../lib/when';
  import AssigneePicker from './AssigneePicker.svelte';

  let { item, rule }: { item: ItemDetail; rule: Recurrence | undefined } = $props();

  const TZ = $derived(rule?.timezone ?? workspaceTimezone());
  const KINDS: { kind: PresetKind | 'custom'; label: string }[] = [
    { kind: 'daily', label: 'Daily' },
    { kind: 'weekdays', label: 'Every weekday (Mon–Fri)' },
    { kind: 'weekly', label: 'Weekly' },
    { kind: 'monthly_day', label: 'Monthly on a date' },
    { kind: 'monthly_nth', label: 'Monthly on a weekday (e.g. 2nd Wed)' },
    { kind: 'yearly_date', label: 'Yearly on a date' },
    { kind: 'yearly_nth', label: 'Yearly on a weekday (e.g. 2nd Sun of May)' },
    { kind: 'custom', label: 'Custom (RRULE)' },
  ];
  const POLICIES: { value: MissedPolicy; label: string; hint: string }[] = [
    {
      value: 'spawn_always',
      label: 'Still create the next one',
      hint: 'Every date gets its own item, even if an earlier one is still open.',
    },
    {
      value: 'roll_forward',
      label: 'Move it forward',
      hint: 'An overdue one moves to the next date instead of a new one appearing.',
    },
    {
      value: 'skip_if_open',
      label: 'Skip while one is open',
      hint: 'No new one while the last is unfinished; dates that pass are skipped.',
    },
  ];
  const DAY_SHORT: Record<Day, string> = { MO: 'M', TU: 'T', WE: 'W', TH: 'T', FR: 'F', SA: 'S', SU: 'S' };

  // --- editor state -------------------------------------------------------------------------

  let editing = $state(false);
  let confirmEnd = $state(false);
  let kind = $state<PresetKind | 'custom'>('weekly');
  let preset = $state<Preset>({ kind: 'weekly', interval: 1, days: [] });
  let ends = $state<Ends>({ kind: 'never' });
  let raw = $state('');
  let customNote = $state('');
  let title = $state('');
  let policy = $state<MissedPolicy>('spawn_always');
  let lead = $state(14);
  let trigger = $state<RecurTrigger>('schedule');
  let skipWeekends = $state(false);
  let reopen = $state(false);
  let copyChecklists = $state(true);
  /** Who each new one is assigned to, by handle. The rule keeps its own list (the item's
   * assignees are only the starting point), so reassigning one item never changes the next. */
  let who = $state<string[]>([]);
  let saving = $state(false);

  const whoAccounts = $derived(
    who.map((h) => board.pickable.find((a) => a.handle === h)).filter((a): a is Assignee => !!a),
  );
  /** The rule's assignees as handles; ids the board does not know are kept out of the picker. */
  function ruleHandles(r: Recurrence): string[] {
    return (r.assignee_ids ?? []).map((id) => board.accounts[id]?.handle).filter((h): h is string => !!h);
  }
  function sameSet(a: string[], b: string[]) {
    return a.length === b.length && a.every((x) => b.includes(x));
  }

  let preview = $state<RulePreview | null>(null);
  let previewError = $state('');

  /** The local date the rule is anchored on: the rule's start, else the item's due day, else today. */
  const anchor = $derived(
    rule?.dtstart
      ? workspaceDate(new Date(rule.dtstart), TZ)
      : item.due_at
        ? workspaceDate(new Date(item.due_at), TZ)
        : workspaceDate(board.now, TZ),
  );
  const rrule = $derived(kind === 'custom' ? raw.trim() : toRrule({ preset, ends }));
  /** For a new rule the server anchors on the item's due day (or today); preview the same. */
  const dtstart = $derived(rule?.dtstart ?? anchor);

  function open() {
    const a = untrack(() => anchor);
    const parsed = rule ? fromRrule(rule.rrule, a) : null;
    if (rule && !parsed) {
      kind = 'custom';
      raw = rule.rrule;
    } else {
      const start = parsed ?? { preset: presetFor('weekly', a), ends: { kind: 'never' } as Ends };
      preset = start.preset;
      ends = start.ends;
      kind = start.preset.kind;
      raw = toRrule(start);
    }
    customNote = '';
    title = rule?.title_template ?? item.title;
    policy = rule?.missed_policy ?? 'spawn_always';
    lead = rule?.lead_days ?? 14;
    trigger = rule?.trigger ?? 'schedule';
    skipWeekends = rule?.skip_weekends ?? false;
    reopen = rule?.reopen ?? false;
    copyChecklists = rule?.copy_checklists ?? true;
    who = rule ? ruleHandles(rule) : item.assignees.map((a) => a.handle);
    preview = null;
    previewError = '';
    editing = true;
  }

  function pickKind(next: PresetKind | 'custom') {
    customNote = '';
    if (next === 'custom') {
      raw = rrule;
      kind = 'custom';
      return;
    }
    if (kind === 'custom') {
      const parsed = fromRrule(raw, anchor);
      if (parsed && parsed.preset.kind === next) {
        preset = parsed.preset;
        ends = parsed.ends;
        kind = next;
        return;
      }
      if (!parsed && raw.trim()) customNote = 'That rule has no simple form; starting fresh.';
    }
    const fresh = presetFor(next, anchor);
    // Keep "every N" when moving between kinds that have it.
    if ('interval' in fresh && 'interval' in preset) fresh.interval = preset.interval;
    preset = fresh;
    kind = next;
  }

  /** Leaving Custom: the raw rule back into presets when it has a simple form. */
  function rawChanged() {
    const parsed = fromRrule(raw, anchor);
    customNote = parsed ? '' : 'Custom only: no preset says this exactly.';
  }

  function toggleIn<T>(list: T[], v: T): T[] {
    return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
  }

  // Live preview, debounced; only the latest answer counts.
  let seq = 0;
  $effect(() => {
    if (!editing) return;
    const text = rrule;
    const start = dtstart;
    const opts = { trigger, skip_weekends: skipWeekends };
    const mine = ++seq;
    const t = setTimeout(async () => {
      if (!text) {
        preview = null;
        previewError = 'Write a rule, e.g. FREQ=WEEKLY;BYDAY=WE';
        return;
      }
      try {
        const p = await board.previewRule(text, start, TZ, 6, opts);
        if (mine !== seq) return;
        preview = p;
        previewError = '';
      } catch (e) {
        if (mine !== seq) return;
        preview = null;
        previewError = e instanceof ApiError ? e.detail : String(e);
      }
    }, 250);
    return () => clearTimeout(t);
  });

  /** The preview's dates, minus the one this item already is. */
  const itemDay = $derived(item.due_at ? workspaceDate(new Date(item.due_at), TZ) : null);
  const upcoming = $derived.by(() => {
    const days = (preview?.occurrences ?? []).map((o) => o.local.slice(0, 10));
    if (rule || trigger === 'on_done') return days.slice(0, 5);
    if (!itemDay) return days.slice(1, 6);
    return days.filter((d) => d > itemDay).slice(0, 5);
  });
  const becomes = $derived(
    !rule && !itemDay && trigger === 'schedule' && preview?.occurrences[0]
      ? preview.occurrences[0].local.slice(0, 10)
      : null,
  );

  async function save() {
    if (!preview || previewError || saving) return;
    saving = true;
    const fields: Record<string, unknown> = {
      rrule: preview.rrule,
      title_template: title.trim() || item.title,
      missed_policy: policy,
      lead_days: Math.max(0, Math.min(366, Math.floor(lead || 0))),
      trigger,
      skip_weekends: skipWeekends,
      reopen: trigger === 'on_done' && reopen,
      copy_checklists: copyChecklists,
    };
    let ok: boolean;
    if (rule) {
      const changes = Object.fromEntries(
        Object.entries(fields).filter(([k, v]) => (rule as unknown as Record<string, unknown>)[k] !== v),
      );
      if (!sameSet(who, ruleHandles(rule))) changes.assignees = who;
      ok = Object.keys(changes).length ? await board.updateRule(rule.id, changes) : true;
    } else {
      ok = !!(await board.createRule(item, { ...fields, timezone: preview.timezone, assignees: who }));
    }
    saving = false;
    if (ok) editing = false;
  }

  const line = $derived.by(() => {
    if (!rule) return '';
    const words = rule.description ?? describeRrule(rule.rrule);
    const next = rule.next_occurrences[0];
    const state = rule.ended_at ? ' · ended' : rule.paused_at ? ' · paused' : '';
    const how = rule.trigger === 'on_done' && rule.reopen ? ' · reopens this task' : '';
    return `${words}${next && !rule.ended_at ? ` · next ${shortDate(next.due_at, board.now)}` : ''}${how}${state}`;
  });
</script>

{#if !editing}
  {#if rule}
    <div class="rule-line">
      <span class="recurrence" data-rule-line>↻ {line}</span>
      {#if !rule.ended_at}
        <span class="inline small">
          <button class="link" onclick={open}>Edit</button>
          {#if rule.paused_at}
            <button class="link" onclick={() => board.ruleAction(rule.id, 'resume')}>Resume</button>
          {:else}
            <button class="link" onclick={() => board.ruleAction(rule.id, 'pause')}>Pause</button>
          {/if}
          <button class="link danger" onclick={() => (confirmEnd = true)}>End</button>
        </span>
      {/if}
    </div>
    {#if confirmEnd}
      <div class="nav-confirm" role="alertdialog" aria-label="Confirm end">
        <span>End this rule for good? Items it already made stay.</span>
        <span class="inline">
          <button
            class="primary danger-fill"
            onclick={() => {
              confirmEnd = false;
              void board.ruleAction(rule.id, 'end');
            }}>End rule</button
          >
          <button class="link" onclick={() => (confirmEnd = false)}>Cancel</button>
        </span>
      </div>
    {/if}
  {:else if item.recurrence_id}
    <span class="recurrence">↻ recurring</span>
  {:else if !item.parent_id}
    <button class="link" onclick={open}>↻ Make recurring</button>
  {/if}
{:else}
  <div class="rule-editor" role="group" aria-label="Recurrence">
    <div class="seg trigger-seg" role="radiogroup" aria-label="When the next one comes">
      <button
        role="radio"
        aria-checked={trigger === 'schedule'}
        class:on={trigger === 'schedule'}
        onclick={() => (trigger = 'schedule')}>On a schedule</button
      >
      <button
        role="radio"
        aria-checked={trigger === 'on_done'}
        class:on={trigger === 'on_done'}
        onclick={() => (trigger = 'on_done')}>When done</button
      >
    </div>
    <p class="muted small">
      {trigger === 'schedule'
        ? 'A new one arrives on every date, done or not.'
        : 'The next one is made when you mark this done, counted from that day.'}
    </p>
    <label class="field">
      <span class="muted small">Repeats</span>
      <select aria-label="Repeats" value={kind} onchange={(e) => pickKind(e.currentTarget.value as PresetKind | 'custom')}>
        {#each KINDS as k (k.kind)}<option value={k.kind}>{k.label}</option>{/each}
      </select>
    </label>

    {#if kind === 'custom'}
      <label class="field">
        <span class="muted small">RRULE (RFC 5545)</span>
        <input
          aria-label="RRULE"
          class="mono"
          spellcheck="false"
          bind:value={raw}
          oninput={rawChanged}
          placeholder="FREQ=MONTHLY;BYDAY=2WE,4WE"
        />
      </label>
    {:else}
      {#if 'interval' in preset}
        {@const unit = { daily: 'day', weekly: 'week', monthly_day: 'month', monthly_nth: 'month', yearly_date: 'year', yearly_nth: 'year' }[preset.kind]}
        <label class="inline small">
          <span>Every</span>
          <input
            class="num"
            type="number"
            min="1"
            max="99"
            aria-label="Interval"
            value={preset.interval}
            oninput={(e) => {
              if ('interval' in preset) preset.interval = Math.max(1, Number(e.currentTarget.value) || 1);
            }}
          />
          <span>{unit}{preset.interval === 1 ? '' : 's'}</span>
        </label>
      {/if}

      {#if preset.kind === 'weekly'}
        {@const p = preset}
        <div class="day-toggles" role="group" aria-label="On days">
          {#each DAYS as d (d)}
            <button
              class="toggle"
              class:on={p.days.includes(d)}
              aria-pressed={p.days.includes(d)}
              aria-label={DAY_NAMES[d]}
              title={DAY_NAMES[d]}
              onclick={() => (p.days = toggleIn(p.days, d))}>{DAY_SHORT[d]}</button
            >
          {/each}
        </div>
      {:else if preset.kind === 'monthly_day'}
        {@const p = preset}
        <div class="month-grid" role="group" aria-label="On days of the month">
          {#each Array.from({ length: 31 }, (_, i) => i + 1) as n (n)}
            <button
              class="toggle"
              class:on={p.monthDays.includes(n)}
              aria-pressed={p.monthDays.includes(n)}
              aria-label="Day {n}"
              onclick={() => (p.monthDays = toggleIn(p.monthDays, n))}>{n}</button
            >
          {/each}
          <button
            class="toggle wide"
            class:on={p.monthDays.includes(-1)}
            aria-pressed={p.monthDays.includes(-1)}
            aria-label="Last day"
            onclick={() => (p.monthDays = toggleIn(p.monthDays, -1))}>Last day</button
          >
        </div>
        {#if p.monthDays.some((n) => n > 28)}
          <p class="muted small">Months without that day are skipped. Use "Last day" for month-end.</p>
        {/if}
      {:else if preset.kind === 'monthly_nth'}
        {@const p = preset}
        <div class="inline wrap small" role="group" aria-label="Which ones">
          <span>On the</span>
          {#each NTHS as n (n)}
            <button
              class="toggle wide"
              class:on={p.nths.includes(n)}
              aria-pressed={p.nths.includes(n)}
              onclick={() => {
                const next = toggleIn(p.nths, n);
                if (next.length) p.nths = next;
              }}>{ordinal(n)}</button
            >
          {/each}
          <select aria-label="Weekday" bind:value={p.day}>
            {#each DAYS as d (d)}<option value={d}>{DAY_NAMES[d]}</option>{/each}
            <option value="weekday">weekday (Mon–Fri)</option>
          </select>
        </div>
      {:else if preset.kind === 'yearly_date'}
        {@const p = preset}
        <div class="inline small">
          <span>On</span>
          <select aria-label="Month" bind:value={p.month}>
            {#each MONTH_NAMES as m, i (m)}<option value={i + 1}>{m}</option>{/each}
          </select>
          <input class="num" type="number" min="1" max="31" aria-label="Day of month" bind:value={p.monthDay} />
        </div>
      {:else if preset.kind === 'yearly_nth'}
        {@const p = preset}
        <div class="inline wrap small">
          <span>On the</span>
          <select aria-label="Which" bind:value={p.nth}>
            {#each NTHS as n (n)}<option value={n}>{ordinal(n)}</option>{/each}
          </select>
          <select aria-label="Weekday" bind:value={p.day}>
            {#each DAYS as d (d)}<option value={d}>{DAY_NAMES[d]}</option>{/each}
          </select>
          <span>of</span>
          <select aria-label="Month" bind:value={p.month}>
            {#each MONTH_NAMES as m, i (m)}<option value={i + 1}>{m}</option>{/each}
          </select>
        </div>
      {/if}

      <div class="inline wrap small">
        <span>Ends</span>
        <select
          aria-label="Ends"
          value={ends.kind}
          onchange={(e) => {
            const v = e.currentTarget.value;
            ends =
              v === 'count'
                ? { kind: 'count', count: 10 }
                : v === 'until'
                  ? { kind: 'until', date: `${Number(anchor.slice(0, 4)) + 1}${anchor.slice(4)}` }
                  : { kind: 'never' };
          }}
        >
          <option value="never">never</option>
          <option value="count">after a number of times</option>
          <option value="until">on a date</option>
        </select>
        {#if ends.kind === 'count'}
          {@const e = ends}
          <input class="num" type="number" min="1" aria-label="Times" bind:value={e.count} />
          <span>times</span>
        {:else if ends.kind === 'until'}
          {@const e = ends}
          <input type="date" aria-label="Last date" bind:value={e.date} />
        {/if}
      </div>
    {/if}
    {#if customNote}<p class="muted small">{customNote}</p>{/if}

    <div class="rule-options">
      <label class="rule-opt">
        <input type="checkbox" aria-label="Skip weekends" bind:checked={skipWeekends} />
        <span>Skip weekends <span class="muted small">(Saturday and Sunday move to Monday)</span></span>
      </label>
      {#if trigger === 'on_done'}
        <label class="rule-opt">
          <input type="checkbox" aria-label="Reopen this task" bind:checked={reopen} />
          <span>Reopen this task <span class="muted small">(instead of making a new one)</span></span>
        </label>
      {/if}
      <label class="rule-opt">
        <input type="checkbox" aria-label="Copy checklists" bind:checked={copyChecklists} />
        <span>Carry checklists over <span class="muted small">(unticked)</span></span>
      </label>
    </div>

    <div class="rule-preview" aria-live="polite" data-rule-preview>
      {#if previewError}
        <p class="error small">{previewError}</p>
      {:else if preview}
        <p><strong>{preview.description[0].toUpperCase() + preview.description.slice(1)}</strong></p>
        {#if trigger === 'on_done'}
          <p class="muted small">If you finish it today, the next ones are due:</p>
        {:else if !rule && itemDay}
          <p class="muted small">This item stays the current one (due {longDay(itemDay)}). Next:</p>
        {:else if becomes}
          <p class="muted small">This item becomes the first one, due {longDay(becomes)}. Next:</p>
        {:else}
          <p class="muted small">Next:</p>
        {/if}
        {#if upcoming.length}
          <ol class="preview-dates">
            {#each upcoming as d (d)}<li>{longDay(d)}{d.slice(0, 4) !== anchor.slice(0, 4) ? `, ${d.slice(0, 4)}` : ''}</li>{/each}
          </ol>
        {:else}
          <p class="muted small">No more dates: the rule ends before then.</p>
        {/if}
      {:else}
        <p class="muted small">Working out the dates…</p>
      {/if}
    </div>

    <label class="field">
      <span class="muted small">Each new one is called</span>
      <input aria-label="Title for each one" bind:value={title} />
      <span class="muted small"><code>{'{date}'}</code> adds its date: "Bulk trash {'{date}'}" → "Bulk trash Wed Nov 11"</span>
    </label>

    <div class="field" role="group" aria-label="Each new one is assigned to">
      <span class="muted small">Each new one is assigned to</span>
      <AssigneePicker assignees={whoAccounts} onchange={(handles) => (who = handles)} />
      <span class="muted small">Changing who has this one does not change the next ones; set it here.</span>
    </div>

    {#if trigger === 'schedule'}
    <fieldset class="field policy">
      <legend class="muted small">If one is missed</legend>
      {#each POLICIES as p (p.value)}
        <label title={p.hint}>
          <input type="radio" name="missed-{item.id}" value={p.value} bind:group={policy} />
          <span>{p.label}</span>
        </label>
      {/each}
      <span class="muted small">{POLICIES.find((p) => p.value === policy)?.hint}</span>
    </fieldset>

    <label class="inline small">
      <span>Create each one</span>
      <input class="num" type="number" min="0" max="366" aria-label="Lead days" bind:value={lead} />
      <span>day{lead === 1 ? '' : 's'} ahead</span>
    </label>
    {/if}

    <div class="inline">
      <button class="primary" disabled={!preview || !!previewError || saving} onclick={save}
        >{saving ? 'Saving…' : rule ? 'Save rule' : 'Make recurring'}</button
      >
      <button class="link" onclick={() => (editing = false)}>Cancel</button>
    </div>
  </div>
{/if}
