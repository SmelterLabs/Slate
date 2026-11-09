<script lang="ts">
  // One parameter of a trigger or action, with the right control for its kind: statuses of the
  // rule's list (or every status name), status kinds, agent report states, people and agents, tags, priorities,
  // lists, a number of days, text with placeholders, or a URL.
  import type { CatalogParam } from '../../lib/automations.svelte';
  import { board } from '../../lib/store.svelte';

  let {
    param,
    value = $bindable(),
    listId = null,
    id,
  }: { param: CatalogParam; value: string | number | undefined; listId?: string | null; id: string } = $props();

  const statusNames = $derived.by(() => {
    const lists = listId && board.lists[listId] ? [board.lists[listId]] : Object.values(board.lists);
    return [...new Set(lists.flatMap((l) => l.statuses.map((s) => s.name)))];
  });
  const accounts = $derived(
    Object.values(board.accounts)
      .filter((a) => a.handle !== 'slate')
      .sort((a, b) => a.display_name.localeCompare(b.display_name)),
  );
  const optional = $derived(!param.required);
</script>

{#if param.kind === 'status'}
  <select {id} bind:value aria-label={param.help}>
    {#if optional}<option value="">any status</option>{/if}
    {#if !optional && !value}<option value="" disabled>choose a status</option>{/if}
    {#each statusNames as s (s)}<option value={s}>{s}</option>{/each}
  </select>
{:else if param.kind === 'status_kind'}
  <select {id} bind:value aria-label={param.help}>
    <option value="">any kind</option>
    <option value="open">open</option>
    <option value="active">active</option>
    <option value="done">done</option>
    <option value="cancelled">cancelled</option>
  </select>
{:else if param.kind === 'agent_state'}
  <select {id} bind:value aria-label={param.help}>
    <option value="">any new state</option>
    <option value="working">working</option>
    <option value="blocked">blocked</option>
    <option value="review">review (ready for review)</option>
    <option value="done">done</option>
    <option value="idle">idle (stopped)</option>
  </select>
{:else if param.kind === 'account'}
  <select {id} bind:value aria-label={param.help}>
    <option value="" disabled={!optional}>{optional ? 'anyone' : 'choose someone'}</option>
    {#each accounts as a (a.id)}
      <option value={a.handle}>{a.kind === 'agent' ? '🤖 ' : ''}{a.display_name} (@{a.handle})</option>
    {/each}
  </select>
{:else if param.kind === 'priority'}
  <select {id} bind:value aria-label={param.help}>
    {#if optional}<option value="">any priority</option>{/if}
    {#if !optional && !value}<option value="" disabled>choose</option>{/if}
    <option value="urgent">Urgent</option>
    <option value="high">High</option>
    <option value="normal">Normal</option>
    <option value="low">Low</option>
    <option value="none">None</option>
  </select>
{:else if param.kind === 'list'}
  <select {id} bind:value aria-label={param.help}>
    <option value="" disabled>choose a list</option>
    {#each board.sections as sec (sec.id)}
      <optgroup label={sec.name}>
        {#each board.listsOf(sec.id) as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
      </optgroup>
    {/each}
  </select>
{:else if param.kind === 'tag'}
  <input {id} list="automation-tags" bind:value placeholder={optional ? 'any tag' : 'tag'} aria-label={param.help} />
  <datalist id="automation-tags">
    {#each board.tags as t (t.id)}<option value={t.name}></option>{/each}
  </datalist>
{:else if param.kind === 'number'}
  <span class="days">
    in <input {id} type="number" min="-1" max="3650" bind:value aria-label={param.help} /> days
  </span>
{:else if param.kind === 'text'}
  <textarea {id} rows="2" bind:value placeholder={param.help} aria-label={param.help}></textarea>
{:else}
  <input {id} type="url" bind:value placeholder="https://…" aria-label={param.help} />
{/if}

<style>
  select,
  input,
  textarea {
    min-width: 0;
    max-width: 100%;
  }
  textarea {
    width: 100%;
    resize: vertical;
    font: inherit;
  }
  .days {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
  }
  .days input {
    width: 72px;
  }
</style>
