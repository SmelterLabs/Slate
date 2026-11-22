<script lang="ts">
  // List settings: rename, and the statuses editor (add, rename, recolour, reorder, kind,
  // default). Saving is one PUT /lists/{id}/statuses; if removed statuses still hold items, it
  // asks where they go first (the PUT's `fallback`). Opened by `board.openListSettings(id)`.
  import { untrack } from 'svelte';
  import { board } from '../lib/store.svelte';
  import {
    SWATCHES,
    draftProblem,
    draftWarning,
    strandedCount,
    type StatusDraft,
  } from '../lib/structure';
  import type { Assignee, List, StatusKind } from '../lib/types';
  import { trapFocus } from '../lib/focustrap';
  import AssigneePicker from './AssigneePicker.svelte';
  import TagEditor from './TagEditor.svelte';

  let { list }: { list: List } = $props();

  const KINDS: { kind: StatusKind; label: string }[] = [
    { kind: 'open', label: 'Open' },
    { kind: 'active', label: 'Active' },
    { kind: 'done', label: 'Done' },
    { kind: 'cancelled', label: 'Cancelled' },
  ];

  function fromList(l: List): StatusDraft[] {
    return [...l.statuses]
      .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
      .map((s) => ({ id: s.id, name: s.name, kind: s.kind, color: s.color }));
  }

  // Snapshot of the list when the panel opened; a live change elsewhere does not clobber edits.
  const original = untrack(() => fromList(list));
  let name = $state(untrack(() => list.name));
  let rows = $state<StatusDraft[]>(structuredClone(original));
  let defaultIndex = $state(
    Math.max(0, untrack(() => original.findIndex((s) => s.id === list.default_status_id))),
  );
  let swatchFor = $state<number | null>(null);
  let fallback = $state('');
  let saving = $state(false);
  // The list's own settings: what belongs here, who owns a new card, its tags, whether it counts.
  let savedSettings = $state(untrack(() => ({
    note_md: list.note_md?.trim() ? list.note_md : null,
    default_assignee_id: list.default_assignee_id ?? null,
    default_tags: [...(list.default_tags ?? [])],
    reference: list.reference ?? false,
  })));
  let note = $state(untrack(() => savedSettings.note_md ?? ''));
  let defaultOwnerId = $state(untrack(() => savedSettings.default_assignee_id));
  let defaultTags = $state(untrack(() => [...savedSettings.default_tags]));
  let reference = $state(untrack(() => savedSettings.reference));
  const owner = $derived<Assignee[]>(
    defaultOwnerId && board.accounts[defaultOwnerId] ? [board.accounts[defaultOwnerId]] : [],
  );
  function pickOwner(handles: string[]) {
    // One default owner: the last account picked in the (multi) account picker wins.
    defaultOwnerId = handles.length
      ? (board.pickable.find((a) => a.handle === handles[handles.length - 1])?.id ?? defaultOwnerId)
      : null;
  }
  const settingsChanges = $derived.by(() => {
    const changes: Partial<typeof savedSettings> = {};
    const noteMd = note.trim() ? note : null;
    if (noteMd !== savedSettings.note_md) changes.note_md = noteMd;
    if (defaultOwnerId !== savedSettings.default_assignee_id) changes.default_assignee_id = defaultOwnerId;
    if (JSON.stringify(defaultTags) !== JSON.stringify(savedSettings.default_tags)) {
      changes.default_tags = [...defaultTags];
    }
    if (list.kind !== 'drawer' && reference !== savedSettings.reference) changes.reference = reference;
    return changes;
  });
  const settingsChanged = $derived(Object.keys(settingsChanges).length > 0);

  const problem = $derived(draftProblem(rows));
  const warning = $derived(draftWarning(rows));
  const stranded = $derived(
    strandedCount(
      Object.values(board.items),
      list.id,
      original.map((s) => s.id!),
      rows,
    ),
  );
  const changed = $derived(
    name.trim() !== list.name ||
      settingsChanged ||
      JSON.stringify(rows) !== JSON.stringify(original) ||
      original[defaultIndex]?.id !== list.default_status_id,
  );

  function add() {
    rows.push({ name: '', kind: 'open', color: null });
  }

  function remove(i: number) {
    rows.splice(i, 1);
    if (defaultIndex >= rows.length) defaultIndex = rows.length - 1;
    else if (i < defaultIndex) defaultIndex -= 1;
  }

  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= rows.length) return;
    [rows[i], rows[j]] = [rows[j], rows[i]];
    if (defaultIndex === i) defaultIndex = j;
    else if (defaultIndex === j) defaultIndex = i;
  }

  async function save() {
    if (problem || (stranded && !fallback)) return;
    saving = true;
    if (settingsChanged) {
      const changes = settingsChanges;
      if (!(await board.updateListSettings(list.id, changes))) {
        saving = false;
        return;
      }
      savedSettings = { ...savedSettings, ...changes };
    }
    let ok = true;
    if (name.trim() && name.trim() !== list.name) await board.renameList(list.id, name.trim());
    if (
      JSON.stringify(rows) !== JSON.stringify(original) ||
      original[defaultIndex]?.id !== list.default_status_id
    ) {
      ok = await board.saveStatuses(
        list.id,
        rows,
        stranded ? fallback : null,
        rows[defaultIndex]?.name ?? null,
      );
    }
    saving = false;
    if (ok) board.closeListSettings();
  }

  function kindColor(kind: StatusKind) {
    return `var(--kind-${kind})`;
  }
</script>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={() => board.closeListSettings()}
></button>
<div class="settings" role="dialog" aria-label="List settings" aria-modal="true" use:trapFocus={{ onescape: () => board.closeListSettings() }}>
  <div class="drawer-top">
    <h2>List settings</h2>
    <button class="icon" aria-label="Close" onclick={() => board.closeListSettings()}>✕</button>
  </div>

  <label class="field">
    <span class="muted small">Name</span>
    <input aria-label="List name" bind:value={name} />
  </label>

  <label class="field">
    <span class="muted small">What belongs here</span>
    <textarea
      aria-label="What belongs here"
      bind:value={note}
      rows="4"
      maxlength="2000"
      placeholder="What belongs in this list, and how a card here is finished"
    ></textarea>
  </label>

  <div class="field">
    <span class="muted small">Default owner</span>
    <AssigneePicker assignees={owner} onchange={pickOwner} />
  </div>

  <div class="field">
    <span class="muted small">Default tags</span>
    <TagEditor tags={defaultTags} onchange={(t) => (defaultTags = t.slice(0, 5))} />
  </div>

  {#if list.kind !== 'drawer'}
    <label class="field ref-check">
      <input type="checkbox" aria-label="Reference list: not counted as work" bind:checked={reference} />
      <span>Reference list: not counted as work</span>
    </label>
  {/if}

  <div class="field" role="radiogroup" aria-label="Kind">
    <span class="muted small">Kind</span>
    <div class="kind-row">
      <button role="radio" aria-checked={(list.kind ?? 'tasks') === 'tasks'} class:on={(list.kind ?? 'tasks') === 'tasks'} onclick={() => void board.setListKind(list.id, 'tasks')}>☰ List of tasks</button>
      <button role="radio" aria-checked={list.kind === 'drawer'} class:on={list.kind === 'drawer'} onclick={() => void board.setListKind(list.id, 'drawer')}>▣ Drawer of documents</button>
    </div>
    <span class="muted small">A list becomes a drawer only while it holds no tasks; a drawer's documents stay where they are either way.</span>
  </div>

  {#if list.kind !== 'drawer'}
  <div class="field">
    <span class="muted small">Statuses: the board's columns, left to right</span>
    <ol class="status-rows">
      {#each rows as row, i (row)}
        <li class="status-row" data-status-row={i}>
          <span class="updown">
            <button aria-label="Move {row.name || 'status'} up" disabled={i === 0} onclick={() => move(i, -1)}
              >▲</button
            >
            <button
              aria-label="Move {row.name || 'status'} down"
              disabled={i === rows.length - 1}
              onclick={() => move(i, 1)}>▼</button
            >
          </span>
          <button
            class="swatch"
            aria-label="Colour of {row.name || 'status'}"
            style:background={row.color ?? kindColor(row.kind)}
            onclick={() => (swatchFor = swatchFor === i ? null : i)}
          ></button>
          <input class="status-name" aria-label="Status name" placeholder="Name" bind:value={row.name} />
          <select aria-label="Kind of {row.name || 'status'}" bind:value={row.kind}>
            {#each KINDS as k (k.kind)}<option value={k.kind}>{k.label}</option>{/each}
          </select>
          <label class="default" title="New items start here">
            <input
              type="radio"
              name="default-status"
              aria-label="Default status {row.name}"
              checked={defaultIndex === i}
              onchange={() => (defaultIndex = i)}
            />
            <span class="small muted">default</span>
          </label>
          <button
            class="x"
            aria-label="Remove {row.name || 'status'}"
            disabled={rows.length === 1}
            onclick={() => remove(i)}>✕</button
          >
          {#if swatchFor === i}
            <span class="swatches">
              <button
                class="swatch auto"
                aria-label="Kind colour"
                style:background={kindColor(row.kind)}
                onclick={() => {
                  row.color = null;
                  swatchFor = null;
                }}>A</button
              >
              {#each SWATCHES as c (c)}
                <button
                  class="swatch"
                  class:on={row.color === c}
                  aria-label="Colour {c}"
                  style:background={c}
                  onclick={() => {
                    row.color = c;
                    swatchFor = null;
                  }}
                ></button>
              {/each}
            </span>
          {/if}
        </li>
      {/each}
    </ol>
    <div class="inline">
      <button class="link plain" onclick={add}>+ Add status</button>
    </div>
    <p class="muted small">
      Kinds tell Slate what a column means: Open (not started), Active (in progress), Done
      (complete; stamps the completion time) and Cancelled.
    </p>
  </div>
  {/if}

  {#if stranded}
    <label class="field stranded">
      <span
        >{stranded} item{stranded === 1 ? ' is' : 's are'} on a status you removed. Move {stranded === 1
          ? 'it'
          : 'them'} to:</span
      >
      <select aria-label="Move items to" bind:value={fallback}>
        <option value="" disabled>Pick a status</option>
        {#each rows.filter((r) => r.name.trim()) as r (r)}<option value={r.name.trim()}>{r.name}</option
          >{/each}
      </select>
    </label>
  {/if}
  {#if problem}<p class="error small">{problem}</p>{:else if warning}<p class="muted small">
      {warning}
    </p>{/if}

  <div class="inline">
    <button
      class="primary"
      disabled={!changed || !!problem || (stranded > 0 && !fallback) || saving}
      onclick={save}>{saving ? 'Saving…' : 'Save'}</button
    >
    <button class="link" onclick={() => board.closeListSettings()}>Cancel</button>
  </div>
</div>
