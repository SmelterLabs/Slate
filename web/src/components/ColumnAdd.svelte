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
  let aiLocked = $state<string>('');
  const ai = $derived(board.aiAddOn && board.aiReady);

  const fixedList = $derived(
    group.drop?.kind === 'list' ? group.drop.listId : scope.kind === 'list' ? scope.id : null,
  );
  const listId = $derived(fixedList ?? picked ?? board.quickAddList(viewKey, scope));
  const defaultInboxId = $derived(board.workspaceInboxList());
  const defaultInboxName = $derived(defaultInboxId ? board.lists[defaultInboxId]?.name ?? 'Todo' : 'Auto');
  const choices = $derived(
    fixedList
      ? []
      : board.sections
          .filter((s) => ai || scope.kind === 'everything' || (scope.kind === 'section' && s.id === scope.id))
          .map((s) => ({ section: s, lists: board.listsOf(s.id).filter((l) => board.levelOn(l.id) === 'edit') }))
          .filter((c) => c.lists.length),
  );

  function focus(el: HTMLElement) {
    el.focus();
    el.scrollIntoView({ block: 'nearest' });
  }

  async function submit(e?: Event) {
    e?.preventDefault();
    if (busy) return;
    if (ai) {
      if (!draft.value.trim()) return;
      const target = fixedList ?? (aiLocked || null);
      const defaultsList = target ?? defaultInboxId ?? listId;
      const defaults = defaultsList ? board.columnDefaults(group.drop, defaultsList) : {};
      busy = true;
      if (await board.aiAdd(target, draft.value, defaults, { route: !target })) draft.value = '';
      busy = false;
      return;
    }
    if (!listId) return;
    const defaults = board.columnDefaults(group.drop, listId);
    const parsed = parseQuickAdd(draft.value);
    if (!parsed.title) return;
    busy = true;
    if (await board.quickAdd(listId, parsed, defaults)) draft.value = '';
    busy = false;
  }
</script>

{#if open}
  <form class="col-add" onsubmit={submit}>
    <textarea
      rows={ai ? 3 : 2}
      aria-label="New task in {group.title}"
      placeholder={ai
        ? 'Describe it: what, who, when, repeats. AI fills in the details.'
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
        {#if ai}
          <select
            aria-label="Add to list"
            value={aiLocked}
            disabled={busy}
            onchange={(e) => {
              aiLocked = e.currentTarget.value;
            }}
          >
            <option value="">Auto ({defaultInboxName})</option>
            {#each choices as c (c.section.id)}
              <optgroup label={c.section.name}>
                {#each c.lists as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
              </optgroup>
            {/each}
          </select>
        {:else}
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
      {/if}
      <button
        type="button"
        class="link small"
        onclick={() => {
          draft.value = '';
          onclose();
        }}>Cancel</button
      >
      <button type="submit" class="primary small" disabled={!draft.value.trim() || busy}>{busy ? 'Saving…' : 'Save'}</button>
    </div>
  </form>
{:else}
  <button class="col-add-btn" onclick={onopen}
    ><Icon name="plus" /> Add Task{#if draft.value.trim()}<span class="draft-tag" title="You started typing a task here">Draft</span>{/if}</button
  >
{/if}
