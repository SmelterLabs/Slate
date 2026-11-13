<script lang="ts">
  // Tag manager (ClickUp's "Tags" settings): every tag with how many items carry it, its colour,
  // rename in place, merge into another tag, delete, and + New tag. Each action is one REST call
  // (PATCH /tags/{tag}, POST /tags/{tag}/merge, DELETE /tags/{tag}); every board hears it live.
  import { board } from '../lib/store.svelte';
  import { SWATCHES } from '../lib/structure';
  import type { Tag } from '../lib/types';
  import Icon from './Icon.svelte';
  import TagPill from './TagPill.svelte';
  import { trapFocus } from '../lib/focustrap';

  let q = $state('');
  let renaming = $state<string | null>(null);
  let renameText = $state('');
  let swatchFor = $state<string | null>(null);
  let merging = $state<string | null>(null);
  let mergeInto = $state('');
  let deleting = $state<string | null>(null);
  let newName = $state('');
  let newColor = $state<string | null>(null);

  const shown = $derived(
    board.tags.filter((t) => !q.trim() || t.name.includes(q.trim().toLowerCase().replace(/^#/, ''))),
  );

  async function rename(t: Tag) {
    const name = renameText.trim().replace(/^#/, '');
    renaming = null;
    if (name && name.toLowerCase() !== t.name) await board.updateTag(t.name, { name });
  }

  async function merge(t: Tag) {
    if (!mergeInto) return;
    const into = mergeInto;
    merging = null;
    mergeInto = '';
    if (await board.mergeTag(t.name, into)) board.flash(`Merged #${t.name} into #${into}`);
  }

  async function create(e: SubmitEvent) {
    e.preventDefault();
    const name = newName.trim().replace(/^#/, '');
    if (name && (await board.createTag(name, newColor))) {
      newName = '';
      newColor = null;
    }
  }
</script>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={() => board.closeTagManager()}></button>
<div class="settings tag-manager" role="dialog" aria-label="Tags" aria-modal="true" use:trapFocus={{ onescape: () => board.closeTagManager() }}>
  <div class="drawer-top">
    <h2>Tags</h2>
    <button class="icon" aria-label="Close" onclick={() => board.closeTagManager()}><Icon name="x" size={16} /></button>
  </div>
  <p class="muted small">
    Tags cut across every list.
    {#if board.canManage}
      Rename one and every item keeps it; merge folds one tag into another; delete takes it off every
      item. Recurring rules follow along.
    {:else}
      Only an owner or admin recolours, renames, merges or deletes one; you can add a tag.
    {/if}
  </p>

  <input type="search" aria-label="Find tag" placeholder="Find a tag…" bind:value={q} />

  <ul class="tag-rows">
    {#each shown as t (t.id)}
      <li class="tag-row" data-tag={t.name}>
        <button
          class="swatch"
          aria-label="Colour of {t.name}"
          style:background={t.color ?? 'var(--chip)'}
          disabled={!board.canManage}
          onclick={() => (swatchFor = swatchFor === t.id ? null : t.id)}
        ></button>
        {#if renaming === t.id}
          <!-- svelte-ignore a11y_autofocus -->
          <input
            aria-label="New name for {t.name}"
            autofocus
            bind:value={renameText}
            onblur={() => rename(t)}
            onkeydown={(e) => {
              if (e.key === 'Enter') void rename(t);
              if (e.key === 'Escape') {
                e.stopPropagation();
                renaming = null;
              }
            }}
          />
        {:else}
          <TagPill name={t.name} />
          <span class="muted small">{t.item_count ?? 0} item{t.item_count === 1 ? '' : 's'}</span>
        {/if}
        {#if board.canManage}
          <span class="tag-actions">
            <button
              class="link small"
              onclick={() => {
                renaming = t.id;
                renameText = t.name;
              }}>Rename</button
            >
            <button class="link small" onclick={() => ((merging = merging === t.id ? null : t.id), (mergeInto = ''))}
              >Merge</button
            >
            <button class="link small danger" onclick={() => (deleting = deleting === t.id ? null : t.id)}>Delete</button>
          </span>
        {/if}
        {#if swatchFor === t.id}
          <span class="swatches">
            <button
              class="swatch auto"
              aria-label="No colour"
              onclick={() => {
                swatchFor = null;
                void board.updateTag(t.name, { color: '' });
              }}>–</button
            >
            {#each SWATCHES as c (c)}
              <button
                class="swatch"
                class:on={t.color === c}
                aria-label="Colour {c}"
                style:background={c}
                onclick={() => {
                  swatchFor = null;
                  void board.updateTag(t.name, { color: c });
                }}
              ></button>
            {/each}
          </span>
        {/if}
        {#if merging === t.id}
          <span class="inline tag-confirm">
            Merge #{t.name} into
            <select aria-label="Merge into" bind:value={mergeInto}>
              <option value="" disabled>pick a tag</option>
              {#each board.tags.filter((x) => x.id !== t.id) as x (x.id)}<option value={x.name}>#{x.name}</option>{/each}
            </select>
            <button class="primary small" disabled={!mergeInto} onclick={() => merge(t)}>Merge</button>
          </span>
        {/if}
        {#if deleting === t.id}
          <span class="inline tag-confirm">
            Remove #{t.name} from {t.item_count ?? 0} item{t.item_count === 1 ? '' : 's'} and delete it?
            <button
              class="primary small danger"
              onclick={() => {
                deleting = null;
                void board.deleteTag(t.name);
              }}>Delete</button
            >
            <button class="link small" onclick={() => (deleting = null)}>Keep</button>
          </span>
        {/if}
      </li>
    {:else}
      <li class="muted small">{board.tags.length ? 'No tag matches.' : 'No tags yet.'}</li>
    {/each}
  </ul>

  <form class="inline" onsubmit={create}>
    <input aria-label="New tag" placeholder="New tag" bind:value={newName} />
    <span class="swatches inline-swatches">
      {#each SWATCHES as c (c)}
        <button
          type="button"
          class="swatch"
          class:on={newColor === c}
          aria-label="New tag colour {c}"
          style:background={c}
          onclick={() => (newColor = newColor === c ? null : c)}
        ></button>
      {/each}
    </span>
    <button class="primary" type="submit" disabled={!newName.trim()}>Add tag</button>
  </form>
</div>
