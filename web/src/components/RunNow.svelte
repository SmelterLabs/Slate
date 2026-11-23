<script lang="ts">
  // Run now: ask a task's agents to start on it now instead of at its run time (06:00 workspace
  // time on the due day, or the due time). Offered only when an agent is assigned and the task is
  // open. Waiting uses visible reports and the roster, also on a fresh load of due tasks;
  // it never claims confirmed runner receipt or capacity. The words follow the
  // agents' `wake`: Nudge for one that polls from its own product (its owner is told; the pill
  // says where to go), Copy brief for one a person carries (the task as a prompt to paste).
  // While an agent works on it the button stays: the server answers 409 "My Agent already has it
  // (working 4 min)" and the board says so, instead of a press that seems to do nothing.
  import { board } from '../lib/store.svelte';
  import type { Item } from '../lib/types';
  import Icon from './Icon.svelte';
  import { RUN_TITLE } from '../lib/runmode';

  let { item, compact = false }: { item: Item; compact?: boolean } = $props();

  const asked = $derived(board.runRequested[item.id]);
  const working = $derived(item.agent?.state === 'working');
  const pickup = $derived(board.pickup(item));
  const mode = $derived(board.runMode(item));
</script>

{#if board.canRun(item)}
  {#if asked != null && !working && mode === 'nudge'}
    <span class="run-pill" data-run-state="nudged"
      >Nudged. Open {board.pulledAgents(item)} and say “work Slate”.</span
    >
  {:else if pickup}
    <a class="run-pill" class:late={pickup.warning} data-run-state={pickup.state}
      href="#/agents" title={pickup.title} onclick={(e) => e.stopPropagation()}>{pickup.text}</a>
  {:else}
    <button
      class="run-now"
      class:compact
      title={RUN_TITLE[mode ?? 'run']}
      onclick={(e) => {
        e.stopPropagation();
        void board.runAction(item);
      }}><Icon name="robot" size={12} /> {board.runLabel(item)}</button
    >
  {/if}
{/if}

<style>
  .run-now,
  .run-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;
    line-height: 1.4;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--text);
    white-space: normal;
    overflow-wrap: anywhere;
    min-width: 0;
    max-width: 100%;
    text-decoration: none;
  }
  .run-now {
    cursor: pointer;
  }
  .run-now:hover {
    background: color-mix(in srgb, var(--accent) 20%, transparent);
  }
  .run-pill.late {
    border-color: color-mix(in srgb, var(--danger) 45%, transparent);
    background: color-mix(in srgb, var(--danger) 10%, transparent);
    text-decoration: none;
    white-space: normal;
  }
</style>
