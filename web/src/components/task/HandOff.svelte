<script lang="ts">
  // "Hand to an agent": assign an agent and tell it what to do, in one atomic POST /batch
  // (assign + an @mention comment). The agent's check-in (GET /checkin) then shows "assigned" and
  // "mentioned"; its webhook, if any, fires too. Its reports come back as the badge above.
  import { untrack } from 'svelte';
  import { ApiError, post } from '../../lib/api';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { board } from '../../lib/store.svelte';
  import type { Item } from '../../lib/types';
  import Icon from '../Icon.svelte';

  let { item }: { item: Item } = $props();

  let open = $state(false);
  let who = $state('');
  const draft = persisted(() => draftKey.handoff(item.id));
  // A hand-off typed but not sent comes back open on this card.
  $effect(() => {
    void item.id;
    untrack(() => {
      open = !!draft.value;
      if (open) who ||= agentsList[0]?.handle ?? '';
    });
  });

  function cancel() {
    open = false;
    draft.value = '';
  }
  let busy = $state(false);

  const agentsList = $derived(
    board.pickable
      .filter((a) => a.kind === 'agent')
      .sort((a, b) => a.display_name.localeCompare(b.display_name)),
  );

  function start() {
    open = true;
    who ||= agentsList[0]?.handle ?? '';
  }

  async function send() {
    if (!who || busy) return;
    busy = true;
    const handles = item.assignees.map((a) => a.handle);
    const ops: unknown[] = [];
    if (!handles.includes(who)) {
      ops.push({ tool: 'item', action: 'assign', params: { id: item.id, assignees: [...handles, who] } });
    }
    const text = draft.value.trim();
    if (text) ops.push({ tool: 'item', action: 'comment', params: { id: item.id, body_md: `@${who} ${text}` } });
    try {
      if (ops.length) await post('/batch', { ops });
      const name = agentsList.find((a) => a.handle === who)?.display_name ?? who;
      board.flash(`Handed to ${name}`);
      open = false;
      draft.value = '';
    } catch (e) {
      board.flash(e instanceof ApiError ? e.detail : String(e));
    } finally {
      busy = false;
    }
  }
</script>

{#if agentsList.length}
  {#if !open}
    <button class="handoff-open" onclick={start}><Icon name="robot" size={13} /> Hand to an agent</button>
  {:else}
    <form
      class="handoff"
      aria-label="Hand to an agent"
      onsubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <div class="ho-row">
        <label class="muted small" for="handoff-who">Agent</label>
        <select id="handoff-who" bind:value={who}>
          {#each agentsList as a (a.id)}<option value={a.handle}>{a.display_name}</option>{/each}
        </select>
      </div>
      <textarea
        aria-label="What should it do?"
        placeholder="What should it do? (posted as a comment that mentions it)"
        rows="2"
        bind:value={draft.value}
        onkeydown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            void send();
          }
          if (e.key === 'Escape') {
            e.stopPropagation();
            cancel();
          }
        }}
      ></textarea>
      <div class="ho-row end">
        <button type="button" class="link" onclick={cancel}>Cancel</button>
        <button type="submit" class="primary" disabled={busy || !who}>Hand off</button>
      </div>
    </form>
  {/if}
{/if}

<style>
  .handoff-open {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
    border: 1px dashed var(--line);
    border-radius: 999px;
    padding: 3px 10px;
  }
  .handoff-open:hover {
    color: var(--text);
    border-color: var(--accent);
  }
  .handoff {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 10px 12px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    border-radius: var(--radius);
    margin-bottom: 10px;
  }
  /* not .row: that global class is the List layout's row (app.css) */
  .ho-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .ho-row.end {
    justify-content: flex-end;
    gap: 14px;
  }
</style>
