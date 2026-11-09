<script lang="ts">
  // "Create task from selection": a small form under the selection. The title starts as the
  // selected words; the list starts as the page's own list. Creating swaps the selected text for
  // the task's live card (one call: POST /docs/{id}/tasks).
  import { untrack } from 'svelte';
  import { board } from '../../lib/store.svelte';

  let {
    title: initial,
    listId: initialList,
    x,
    y,
    oncreate,
    onclose,
  }: {
    title: string;
    listId: string | null;
    x: number;
    y: number;
    oncreate: (title: string, listId: string) => Promise<void>;
    onclose: () => void;
  } = $props();

  let title = $state(untrack(() => initial));
  const firstList = board.sections.flatMap((s) => board.listsOf(s.id).filter((l) => l.kind !== 'drawer'))[0]?.id ?? '';
  let listId = $state(untrack(() => (initialList && board.lists[initialList]?.kind !== 'drawer' && board.lists[initialList] ? initialList : firstList)));
  let busy = $state(false);
  let input = $state<HTMLInputElement>();
  $effect(() => {
    input?.focus();
    input?.select();
  });

  async function submit(e: Event) {
    e.preventDefault();
    if (!title.trim() || !listId || busy) return;
    busy = true;
    try {
      await oncreate(title.trim(), listId);
    } finally {
      busy = false;
    }
  }
</script>

<div class="picker-scrim clear" role="presentation" onclick={onclose}></div>
<form class="doc-newtask" style="left:{x}px;top:{y}px" onsubmit={submit}>
  <label>
    <span>New task</span>
    <input
      bind:this={input}
      bind:value={title}
      maxlength="500"
      placeholder="Task name"
      onkeydown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onclose();
        }
      }}
    />
  </label>
  <label>
    <span>In list</span>
    <select bind:value={listId}>
      {#each board.sections as sec (sec.id)}
        <optgroup label={sec.name}>
          {#each board.listsOf(sec.id).filter((l) => l.kind !== 'drawer') as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
        </optgroup>
      {/each}
    </select>
  </label>
  <div class="row">
    <button type="button" class="secondary" onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!title.trim() || !listId || busy}>{busy ? 'Creating…' : 'Create task'}</button>
  </div>
</form>
