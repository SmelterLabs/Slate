<script lang="ts">
  // Checklists inside the task (ClickUp's checklists): small ticked to-dos that are not
  // subtasks. Each has a progress bar; entries tick, rename in place, delete, and drag into
  // order (a finger holds an entry a moment to lift it).
  import { draftKey, readDraft } from '../../lib/drafts';
  import { saveDraft } from '../../lib/drafts.svelte';
  import { beforeAt, slotFor } from '../../lib/slots';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Checklist, ChecklistEntry, ItemDetail } from '../../lib/types';
  import Menu from '../Menu.svelte';
  import Icon from './Icon.svelte';

  let { item }: { item: ItemDetail } = $props();

  const lists = $derived(item.checklists ?? []);
  let drafts = $state<Record<string, string>>({});
  let editing = $state<string | null>(null);
  let editText = $state('');
  let renaming = $state<string | null>(null);
  let nameText = $state('');
  let hideDone = $state<Record<string, boolean>>({});

  // The "Add an item" boxes keep what was typed if the card closes before Enter.
  function addText(id: string): string {
    return drafts[id] ?? readDraft(draftKey.checklist(id));
  }
  function setAddText(id: string, v: string) {
    drafts[id] = v;
    saveDraft(draftKey.checklist(id), v);
  }

  function done(cl: Checklist) {
    return cl.items.filter((e) => e.done).length;
  }

  async function add(cl: Checklist, e: SubmitEvent) {
    e.preventDefault();
    const t = addText(cl.id).trim();
    if (!t) return;
    setAddText(cl.id, '');
    // Optimistic row; the refresh replaces it.
    cl.items.push({ id: `tmp-${Date.now()}`, checklist_id: cl.id, title: t, done: false, done_at: null, assignee_id: null, position: cl.items.length });
    await task.addEntry(cl.id, t);
  }

  function tick(entry: ChecklistEntry) {
    entry.done = !entry.done;
    void task.updateEntry(entry.id, { done: entry.done });
  }

  function saveEdit(entry: ChecklistEntry) {
    const t = editText.trim();
    editing = null;
    if (t && t !== entry.title) {
      entry.title = t;
      void task.updateEntry(entry.id, { title: t });
    }
  }

  // Drag to reorder, within one checklist.
  let dragEntry = $state<{ checklist: string; id: string } | null>(null);
  let slot = $state<{ checklist: string; before: string | null } | null>(null);

  function overEntries(e: DragEvent, cl: Checklist) {
    if (dragEntry?.checklist !== cl.id) return;
    e.preventDefault();
    const rows = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('li[data-entry]')]
      .filter((li) => li.dataset.entry !== dragEntry!.id)
      .map((li) => {
        const r = li.getBoundingClientRect();
        return { id: li.dataset.entry!, top: r.top, bottom: r.bottom };
      });
    const before = beforeAt(rows, e.clientY);
    if (slot?.checklist !== cl.id || slot.before !== before) slot = { checklist: cl.id, before };
  }

  async function dropEntry(e: DragEvent, cl: Checklist) {
    e.preventDefault();
    const moving = dragEntry;
    const at = slot;
    dragEntry = null;
    slot = null;
    if (!moving || at?.checklist !== cl.id) return;
    const writes = slotFor(cl.items, moving.id, at.before);
    if (!writes.length) return;
    for (const w of writes) {
      const entry = cl.items.find((x) => x.id === w.id);
      if (entry) entry.position = w.position;
    }
    cl.items.sort((a, b) => a.position - b.position);
    for (const w of writes) await task.updateEntry(w.id, { position: w.position });
  }

  function saveName(cl: Checklist) {
    const t = nameText.trim();
    renaming = null;
    if (t && t !== cl.name) {
      cl.name = t;
      void task.renameChecklist(cl.id, t);
    }
  }
</script>

{#each lists as cl (cl.id)}
  {@const n = done(cl)}
  <section class="block checklist" aria-label="Checklist {cl.name}">
    <header>
      {#if renaming === cl.id}
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="rename"
          aria-label="Checklist name"
          bind:value={nameText}
          autofocus
          onblur={() => saveName(cl)}
          onkeydown={(e) => {
            if (e.key === 'Enter') saveName(cl);
            if (e.key === 'Escape') {
              e.stopPropagation();
              renaming = null;
            }
          }}
        />
      {:else}
        <h3>
          <Icon name="checklist" />
          <button
            class="plain"
            title="Rename"
            onclick={() => {
              renaming = cl.id;
              nameText = cl.name;
            }}>{cl.name}</button
          >
        </h3>
      {/if}
      <span class="progress" title="{n} of {cl.items.length} done">
        <span class="bar"><span style="width: {cl.items.length ? (100 * n) / cl.items.length : 0}%"></span></span>
        {n}/{cl.items.length}
      </span>
      <Menu
        label="Checklist actions"
        entries={[
          { label: hideDone[cl.id] ? 'Show checked items' : 'Hide checked items', onselect: () => (hideDone[cl.id] = !hideDone[cl.id]) },
          {
            label: 'Check everything',
            disabled: n === cl.items.length,
            onselect: () => cl.items.filter((e) => !e.done).forEach(tick),
          },
          {
            label: 'Delete checklist',
            danger: true,
            onselect: () => {
              if (cl.items.length < 3 || confirm(`Delete the checklist “${cl.name}” and its ${cl.items.length} items?`)) void task.deleteChecklist(cl.id);
            },
          },
        ]}
      />
    </header>
    <ul class="entries" ondragover={(e) => overEntries(e, cl)} ondrop={(e) => dropEntry(e, cl)}>
      {#each cl.items.filter((e) => !(hideDone[cl.id] && e.done)) as entry (entry.id)}
        <li
          class:done={entry.done}
          class:lifted={dragEntry?.id === entry.id}
          class:slot-before={slot?.checklist === cl.id && slot.before === entry.id}
          data-entry={entry.id}
          draggable={editing !== entry.id && !entry.id.startsWith('tmp-')}
          ondragstart={(e) => {
            e.dataTransfer?.setData('text/x-slate-entry', entry.id);
            if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
            dragEntry = { checklist: cl.id, id: entry.id };
          }}
          ondragend={() => {
            dragEntry = null;
            slot = null;
          }}
        >
          <span class="grip" aria-hidden="true" title="Drag to reorder"><Icon name="grip" size={12} /></span>
          <input type="checkbox" aria-label="Done: {entry.title}" checked={entry.done} onchange={() => tick(entry)} />
          {#if editing === entry.id}
            <!-- svelte-ignore a11y_autofocus -->
            <input
              class="edit"
              aria-label="Checklist item"
              bind:value={editText}
              autofocus
              onblur={() => saveEdit(entry)}
              onkeydown={(e) => {
                if (e.key === 'Enter') saveEdit(entry);
                if (e.key === 'Escape') {
                  e.stopPropagation();
                  editing = null;
                }
              }}
            />
          {:else}
            <button
              class="text"
              onclick={() => {
                editing = entry.id;
                editText = entry.title;
              }}>{entry.title}</button
            >
          {/if}
          {#if entry.assignee_id}
            <span class="who muted small">{board.accountName(entry.assignee_id)}</span>
          {/if}
          <button class="icon del" aria-label="Delete {entry.title}" title="Delete" onclick={() => void task.removeEntry(entry.id)}
            ><Icon name="x" size={13} /></button
          >
        </li>
      {/each}
      {#if slot?.checklist === cl.id && slot.before === null}<li class="slot-end" aria-hidden="true"></li>{/if}
    </ul>
    <form class="add" onsubmit={(e) => add(cl, e)}>
      <Icon name="plus" size={14} />
      <input aria-label="Add to {cl.name}" placeholder="Add an item" bind:value={() => addText(cl.id), (v) => setAddText(cl.id, v)} />
    </form>
  </section>
{/each}

<style>
  .entries {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  li {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 6px;
    border-radius: 6px;
    font-size: 13.5px;
  }
  li:hover {
    background: var(--card-hover);
  }
  li.lifted {
    opacity: 0.4;
  }
  li.slot-before {
    box-shadow: inset 0 2px 0 var(--accent);
  }
  li.slot-end {
    height: 2px;
    padding: 0;
    background: var(--accent);
  }
  .grip {
    display: inline-flex;
    width: 12px;
    margin-left: -4px;
    color: var(--muted);
    cursor: grab;
    opacity: 0;
  }
  li:hover .grip {
    opacity: 0.8;
  }
  @media (hover: none) {
    .grip {
      opacity: 0.5;
    }
  }
  li input[type='checkbox'] {
    width: 15px;
    height: 15px;
    margin: 0;
    accent-color: var(--kind-done);
  }
  li.done .text {
    color: var(--muted);
    text-decoration: line-through;
  }
  .text {
    flex: 1;
    text-align: left;
    border: 0;
    background: none;
    padding: 2px 0;
    cursor: text;
  }
  .edit,
  .rename {
    flex: 1;
    padding: 2px 6px;
  }
  .del {
    opacity: 0;
    border: 0;
    background: none;
    color: var(--muted);
    padding: 2px;
  }
  li:hover .del,
  .del:focus {
    opacity: 1;
  }
  .add {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--muted);
    padding: 2px 6px;
  }
  .add input {
    flex: 1;
    border: 0;
    background: none;
    padding: 5px 0;
  }
  .add input:focus {
    outline: none;
  }
  .plain {
    border: 0;
    background: none;
    padding: 0;
    font: inherit;
    color: inherit;
  }
</style>
