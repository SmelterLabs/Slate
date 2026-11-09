<script lang="ts">
  // "+ Add Task" at the foot of a column. The new task takes the column's meaning (its due day,
  // status, priority, assignee or list) and understands quick-add syntax (#tag !high @who due:fri)
  // on top. Enter files it and keeps the box open for the next one; Esc closes.
  import { parseQuickAdd } from '../lib/quickadd';
  import { draftKey } from '../lib/drafts';
  import { persisted } from '../lib/drafts.svelte';
  import { board } from '../lib/store.svelte';
  import type { Group, Scope } from '../lib/views';
  import AiToggle from './AiToggle.svelte';
  import Icon from './Icon.svelte';

  let {
    group,
    scope,
    viewKey,
    open,
    onopen,
    onclose,
  }: { group: Group; scope: Scope; viewKey: string; open: boolean; onopen: () => void; onclose: () => void } =
    $props();

  const draft = persisted(() => draftKey.newCard(viewKey, group.key));
  let busy = $state(false);
  let picked = $state<string | null>(null);

  const fixedList = $derived(
    group.drop?.kind === 'list' ? group.drop.listId : scope.kind === 'list' ? scope.id : null,
  );
  const listId = $derived(fixedList ?? picked ?? board.quickAddList(viewKey, scope));
  const choices = $derived(
    fixedList
      ? []
      : board.sections
          .filter((s) => scope.kind === 'everything' || (scope.kind === 'section' && s.id === scope.id))
          .map((s) => ({ section: s, lists: board.listsOf(s.id) })),
  );

  function focus(el: HTMLElement) {
    el.focus();
    el.scrollIntoView({ block: 'nearest' });
  }

  async function submit(e?: Event) {
    e?.preventDefault();
    if (busy || !listId) return;
    const defaults = board.columnDefaults(group.drop, listId);
    if (ai) {
      // AI add (POST /items/ai): the column's meaning is the starting point; the model's
      // reading of the words wins where they say otherwise.
      if (!draft.value.trim()) return;
      busy = true;
      if (await board.aiAdd(listId, draft.value, defaults)) draft.value = '';
      busy = false;
      return;
    }
    const parsed = parseQuickAdd(draft.value);
    if (!parsed.title) return;
    busy = true;
    if (await board.quickAdd(listId, parsed, defaults)) draft.value = '';
    busy = false;
  }

  const ai = $derived(board.aiAddOn && board.aiReady);
</script>

{#if open}
  <form class="col-add" onsubmit={submit}>
    <textarea
      rows={ai ? 3 : 2}
      aria-label="New task in {group.title}"
      placeholder={ai
        ? 'Describe it: what, who, when. AI fills in the details.'
        : 'Task name, or type #tag !high @who'}
      bind:value={draft.value}
      readonly={busy}
      use:focus
      onkeydown={(e) => {
        if (e.key === 'Enter' && !e.shiftKey) void submit(e);
        else if (e.key === 'Escape') {
          e.stopPropagation();
          draft.value = '';
          onclose();
        }
      }}
    ></textarea>
    <div class="col-add-foot">
      <AiToggle />
      {#if choices.length}
        <select
          aria-label="Add to list"
          value={listId ?? ''}
          onchange={(e) => {
            picked = e.currentTarget.value;
            board.rememberQuickAddList(viewKey, picked);
          }}
        >
          {#each choices as c (c.section.id)}
            <optgroup label={c.section.name}>
              {#each c.lists as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
            </optgroup>
          {/each}
        </select>
      {/if}
      <button
        type="button"
        class="link small"
        onclick={() => {
          draft.value = '';
          onclose();
        }}>Cancel</button
      >
      <button type="submit" class="primary small" disabled={!draft.value.trim() || busy}>Save</button>
    </div>
  </form>
{:else}
  <button class="col-add-btn" onclick={onopen}
    ><Icon name="plus" /> Add Task{#if draft.value.trim()}<span class="draft-tag" title="You started typing a task here">Draft</span>{/if}</button
  >
{/if}
