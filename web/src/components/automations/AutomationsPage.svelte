<script lang="ts">
  // Automations (#/automations, or #/automations?list=<id> from a list's ⚡): every rule as a
  // card with an on/off switch, its sentence, scope and last run; a gallery of starting points;
  // and the run log (what fired, on which task, how each action went). Live through the
  // board's stream (lib/automations.svelte.ts).
  import { onDestroy, onMount } from 'svelte';
  import {
    automations,
    blankRule,
    PRESETS,
    toDraft,
    type Preset,
    type Rule,
    type RuleDraft,
  } from '../../lib/automations.svelte';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import RuleBuilder from './RuleBuilder.svelte';

  let { listId = null }: { listId?: string | null } = $props();

  let tab = $state<'rules' | 'log'>('rules');
  let editing = $state<{ id: string | null; draft: RuleDraft } | null>(null);
  let confirmDelete = $state<string | null>(null);
  let runFor = $state<string | null>(null);
  let runQuery = $state('');

  onMount(() => automations.start());
  onDestroy(() => automations.stop());

  const scopeList = $derived(listId && board.lists[listId] ? board.lists[listId] : null);
  const shown = $derived(
    scopeList
      ? automations.rules.filter(
          (r) => r.list_id === scopeList.id || r.section_id === scopeList.section_id || (!r.list_id && !r.section_id),
        )
      : automations.rules,
  );
  const runs = $derived(
    scopeList ? automations.runs.filter((r) => shown.some((x) => x.id === r.automation_id)) : automations.runs,
  );
  const failing = $derived(automations.rules.filter((r) => r.last_status && r.last_status !== 'ok').length);

  function scopeLabel(r: Rule): string {
    if (r.list_id) return board.lists[r.list_id]?.name ?? 'A list';
    if (r.section_id) return `${board.sections.find((s) => s.id === r.section_id)?.name ?? 'A section'} (section)`;
    return 'Every list';
  }

  function startNew(preset?: Preset) {
    const draft = blankRule(scopeList?.id ?? null);
    if (preset) Object.assign(draft, preset.rule(board.me?.account.handle ?? null));
    editing = { id: null, draft };
  }

  function edit(r: Rule) {
    editing = { id: r.id, draft: toDraft(r) };
  }

  async function remove(r: Rule) {
    confirmDelete = null;
    try {
      await automations.remove(r);
      board.flash(`Deleted "${r.name}"`);
    } catch (e) {
      board.toast = (e as Error).message;
    }
  }

  const candidates = $derived.by(() => {
    const q = runQuery.trim().toLowerCase();
    const rule = automations.rules.find((r) => r.id === runFor);
    return board.plainItems
      .filter((i) => !i.archived_at && (!rule?.list_id || i.list_id === rule.list_id))
      .filter((i) => !q || i.title.toLowerCase().includes(q))
      .slice(0, 8);
  });

  async function runOn(r: Rule, itemId: string) {
    runFor = null;
    runQuery = '';
    try {
      const run = await automations.runNow(r, itemId);
      board.flash(run.status === 'ok' ? `Ran "${r.name}"` : `"${r.name}" ran with problems: see the log`);
    } catch (e) {
      board.toast = (e as Error).message;
    }
  }

  function who(id: string | null): string {
    if (!id) return 'Someone';
    return board.accountName(id) ?? 'Someone';
  }
</script>

<section class="work-page automations-page" aria-label="Automations">
  <header class="work-head">
    <div>
      <h1>Automations{#if scopeList}<span class="muted">&nbsp;· {scopeList.name}</span>{/if}</h1>
      <p class="muted">
        When something happens to a task, Slate does the routine part for you. Changes show as made by
        Slate, so history always says why.
      </p>
    </div>
    <div class="inline">
      {#if scopeList}<a class="secondary" href="#/automations">All automations</a>{/if}
      <button class="primary" onclick={() => startNew()}><Icon name="plus" size={14} /> New automation</button>
    </div>
  </header>

  <div class="inbox-tabs" role="tablist">
    <button role="tab" aria-selected={tab === 'rules'} class:active={tab === 'rules'} onclick={() => (tab = 'rules')}>
      Rules <span class="count">{shown.length}</span>
    </button>
    <button role="tab" aria-selected={tab === 'log'} class:active={tab === 'log'} onclick={() => (tab = 'log')}>
      Run log {#if failing}<span class="count hot" title="Rules whose last run had a problem">{failing}</span>{/if}
    </button>
  </div>

  {#if automations.revealed}
    {@const r = automations.revealed}
    <div class="secret" role="status">
      <p>
        <b>Signing secret for “{r.name}”.</b> Its webhook calls carry
        <code>X-Slate-Signature: sha256=&lt;HMAC of the body&gt;</code>; give this secret to the receiver so it can
        check them. It is shown only now.
      </p>
      <code class="value">{r.secret}</code>
      <span class="inline">
        <button class="secondary" onclick={() => void navigator.clipboard?.writeText(r.secret).then(() => board.flash('Copied'))}
          >Copy</button
        >
        <button class="ghost" onclick={() => (automations.revealed = null)}>Done</button>
      </span>
    </div>
  {/if}

  {#if !automations.loaded}
    <p class="muted pad-row">Loading…</p>
  {:else if automations.error}
    <p class="muted pad-row">Could not load automations: {automations.error}</p>
  {:else if tab === 'rules'}
    {#if shown.length}
      <div class="rules">
        {#each shown as r (r.id)}
          <article class="rule" class:off={!r.enabled} data-rule={r.id}>
            <label class="switch" title={r.enabled ? 'On: click to pause' : 'Off: click to turn on'}>
              <input
                type="checkbox"
                checked={r.enabled}
                onchange={() => void automations.toggle(r)}
                aria-label="{r.name} on"
              />
              <span class="knob" aria-hidden="true"></span>
            </label>
            <div class="what">
              <button class="rule-name" onclick={() => edit(r)}>{r.name}</button>
              <p class="sentence">{r.description}</p>
              <div class="meta">
                <span class="chip">{scopeLabel(r)}</span>
                {#if r.run_count}
                  <span class="ran status-{r.last_status}">
                    <span class="dot" aria-hidden="true"></span>
                    Ran {r.run_count} {r.run_count === 1 ? 'time' : 'times'}{#if r.last_run_at}, last {ago(r.last_run_at, board.now)}{/if}
                    {#if r.last_status === 'failed'}· failed{:else if r.last_status === 'partial'}· partly failed{/if}
                  </span>
                {:else}
                  <span class="muted small">Not run yet</span>
                {/if}
              </div>
              {#if runFor === r.id}
                <div class="run-pick">
                  <input
                    placeholder="Run on which task? Type to search"
                    bind:value={runQuery}
                    aria-label="Find a task to run on"
                  />
                  <ul>
                    {#each candidates as c (c.id)}
                      <li><button class="link" onclick={() => void runOn(r, c.id)}>{c.title}</button></li>
                    {:else}
                      <li class="muted small">No matching tasks.</li>
                    {/each}
                  </ul>
                </div>
              {/if}
            </div>
            <div class="acts">
              {#if confirmDelete === r.id}
                <span class="small">Delete?</span>
                <button class="link danger" onclick={() => void remove(r)}>Yes, delete</button>
                <button class="link" onclick={() => (confirmDelete = null)}>Keep</button>
              {:else}
                <button class="ghost" onclick={() => (runFor = runFor === r.id ? null : r.id)} title="Run its actions on a task now"
                  >Run now</button
                >
                <button class="ghost" onclick={() => edit(r)}>Edit</button>
                {#if r.signed}
                  <button class="ghost" title="Make a new webhook signing secret" onclick={() => void automations.rotateSecret(r)}
                    >New secret</button
                  >
                {/if}
                <button class="ghost" aria-label="Delete {r.name}" onclick={() => (confirmDelete = r.id)}
                  ><Icon name="trash" size={14} /></button
                >
              {/if}
            </div>
          </article>
        {/each}
      </div>
    {/if}

    <section class="gallery" aria-label="Start from a template">
      <h2>{shown.length ? 'Add another' : 'Start with one of these'}</h2>
      <div class="presets">
        {#each PRESETS as p (p.key)}
          <button class="preset" onclick={() => startNew(p)}>
            <span class="p-icon" aria-hidden="true"><Icon name="sparkle" size={16} /></span>
            <b>{p.title}</b>
            <span class="muted small">{p.hint}</span>
          </button>
        {/each}
      </div>
    </section>
  {:else}
    {#if !runs.length}
      <div class="inbox-empty">
        <div class="big">⚡</div>
        <p>No runs yet. Each time a rule fires, it shows here with what it did.</p>
      </div>
    {:else}
      <ol class="log">
        {#each runs as run (run.id)}
          <li class="run status-{run.status}">
            <span class="dot" aria-hidden="true"></span>
            <span class="when muted small" title={new Date(run.at).toLocaleString()}>{ago(run.at, board.now)}</span>
            <span class="body">
              <b>{run.automation_name ?? 'A deleted automation'}</b>
              on
              {#if run.item_id && run.item_title}
                <button class="link" onclick={() => board.open(run.item_id!)}>{run.item_title}</button>
              {:else}<span class="muted">a deleted task</span>{/if}
              <span class="muted small">· {run.event_id ? `after ${who(run.triggered_by)}'s change` : `run by ${who(run.triggered_by)}`}</span>
              <span class="results">
                {#each run.results as res, i (i)}
                  <span class="res" class:bad={!res.ok} title={res.detail}>{res.ok ? '✓' : '✕'} {res.detail}</span>
                {/each}
              </span>
            </span>
          </li>
        {/each}
      </ol>
    {/if}
  {/if}
</section>

{#if editing}
  <RuleBuilder bind:draft={editing.draft} id={editing.id} onclose={() => (editing = null)} />
{/if}

<style>
  .secret {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 14px;
    border: 1px solid var(--warning);
    border-radius: var(--radius-lg);
    background: color-mix(in srgb, var(--warning) 10%, transparent);
  }
  .secret p {
    margin: 0;
  }
  .secret .value {
    padding: 6px 8px;
    border-radius: var(--radius-sm);
    background: var(--panel);
    overflow-wrap: anywhere;
    user-select: all;
  }
  .secret .inline {
    display: flex;
    gap: 8px;
  }
  .rules {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .rule {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: 14px;
    align-items: start;
    padding: 14px 16px;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    background: var(--card);
    transition: opacity var(--dur-2) var(--ease);
  }
  .rule.off {
    opacity: 0.6;
  }
  .rule-name {
    background: none;
    border: 0;
    padding: 0;
    font: inherit;
    font-weight: 600;
    font-size: var(--fs-md);
    color: var(--text);
    cursor: pointer;
    text-align: left;
  }
  .sentence {
    margin: 4px 0 8px;
    color: var(--muted);
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .ran {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: var(--fs-sm);
    color: var(--muted);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--success);
    flex: none;
  }
  .status-partial .dot {
    background: var(--warning);
  }
  .status-failed .dot {
    background: var(--danger);
  }
  .acts {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .switch {
    position: relative;
    display: inline-block;
    width: 36px;
    height: 20px;
    margin-top: 2px;
    cursor: pointer;
  }
  .switch input {
    position: absolute;
    opacity: 0;
    inset: 0;
    margin: 0;
    cursor: pointer;
  }
  .knob {
    position: absolute;
    inset: 0;
    border-radius: var(--radius-pill);
    background: var(--line-strong);
    transition: background var(--dur-2) var(--ease);
  }
  .knob::after {
    content: '';
    position: absolute;
    width: 16px;
    height: 16px;
    top: 2px;
    left: 2px;
    border-radius: 50%;
    background: #fff;
    box-shadow: var(--shadow-sm);
    transition: translate var(--dur-2) var(--ease-spring);
  }
  .switch input:checked + .knob {
    background: var(--accent);
  }
  .switch input:checked + .knob::after {
    translate: 16px 0;
  }
  .switch input:focus-visible + .knob {
    box-shadow: var(--focus-ring);
  }
  .run-pick {
    margin-top: 10px;
    padding: 8px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--panel);
  }
  .run-pick input {
    width: 100%;
  }
  .run-pick ul {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
  }
  .run-pick li {
    padding: 3px 0;
  }
  .gallery h2 {
    font-size: var(--fs-md);
    margin: 8px 0 10px;
  }
  .presets {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 10px;
  }
  .preset {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    text-align: left;
    padding: 12px 14px;
    border: 1px dashed var(--line-strong);
    border-radius: var(--radius-lg);
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }
  .preset:hover {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .p-icon {
    color: var(--accent);
  }
  .log {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    background: var(--card);
  }
  .run {
    display: grid;
    grid-template-columns: 10px 80px minmax(0, 1fr);
    gap: 10px;
    align-items: baseline;
    padding: 10px 14px;
    border-top: 1px solid var(--line);
  }
  .run:first-child {
    border-top: 0;
  }
  .results {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
  }
  .res {
    font-size: var(--fs-xs);
    padding: 1px 8px;
    border-radius: var(--radius-pill);
    background: color-mix(in srgb, var(--success) 14%, transparent);
    color: var(--text);
    overflow-wrap: anywhere;
  }
  .res.bad {
    background: color-mix(in srgb, var(--danger) 16%, transparent);
  }
  @media (max-width: 760px) {
    .rule {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .acts {
      grid-column: 1 / -1;
      justify-content: flex-start;
    }
    .run {
      grid-template-columns: 10px minmax(0, 1fr);
    }
    .run .when {
      display: none;
    }
  }
</style>
