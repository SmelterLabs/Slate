<script lang="ts">
  // ClickUp's watchers: an eye with the watchers' faces and count in the task window. Click it
  // for a popover: Watch / Stop watching for yourself, who is watching (× removes), and a
  // searchable list of people and agents to add. Anyone can add or remove anyone; someone
  // added by hand is told (inbox `watching`). Watchers hear about every change to the task.
  // Same popover on phone and PC; it opens under the eye, right-aligned, within the screen.
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Assignee } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from './Icon.svelte';

  let { itemId, watchers }: { itemId: string; watchers: Assignee[] } = $props();

  let open = $state(false);
  let q = $state('');
  let root: HTMLDivElement | undefined = $state();
  let trigger: HTMLButtonElement | undefined = $state();
  let search: HTMLInputElement | undefined = $state();

  const watching = $derived(watchers.some((w) => w.id === task.me));
  const faces = $derived(watchers.slice(0, 3));

  function show() {
    open = true;
    if (matchMedia('(pointer: fine)').matches) queueMicrotask(() => search?.focus());
  }

  function hide() {
    open = false;
    q = '';
  }

  function outside(e: PointerEvent) {
    if (open && root && !root.contains(e.target as Node)) hide();
  }

  function onkey(e: KeyboardEvent) {
    if (!open || e.key !== 'Escape') return;
    e.stopPropagation();
    hide();
    trigger?.focus();
  }

  const candidates = $derived(
    board.pickable
      .filter((a) => !watchers.some((w) => w.id === a.id))
      .filter((a) => {
        const s = q.trim().toLowerCase();
        return !s || a.display_name.toLowerCase().includes(s) || a.handle.includes(s);
      })
      .sort(
        (a, b) =>
          Number(a.kind === 'agent') - Number(b.kind === 'agent') ||
          a.display_name.localeCompare(b.display_name),
      ),
  );

  function add(handle: string) {
    void task.setWatchers(itemId, [...watchers.map((w) => w.handle), handle]);
    q = '';
  }

  function remove(w: Assignee) {
    if (w.id === task.me) void task.watch(itemId, false);
    else void task.setWatchers(itemId, watchers.filter((x) => x.id !== w.id).map((x) => x.handle));
  }
</script>

<svelte:document onpointerdown={outside} onkeydown={onkey} />

<div class="watchers" bind:this={root}>
  <button
    class="watch"
    class:on={watching}
    bind:this={trigger}
    data-watchers
    title={watching ? 'You are watching: every change reaches you' : 'Watchers'}
    aria-label="Watchers: {watchers.length}{watching ? ', including you' : ''}"
    aria-expanded={open}
    onclick={() => (open ? hide() : show())}
  >
    <Icon name="eye" size={15} />
    {#if faces.length}<Avatars people={faces} />{/if}
    <span class="n">{watchers.length || ''}</span>
  </button>
  {#if open}
    <div class="watch-pop" role="group" aria-label="Watchers">
      <div class="wp-head">
        <strong>Watchers</strong>
        <button class={watching ? 'secondary' : 'primary'} data-watch-me onclick={() => void task.watch(itemId, !watching)}>
          <Icon name="eye" size={13} />
          {watching ? 'Stop watching' : 'Watch'}
        </button>
      </div>
      <p class="muted small">Watchers are told about every change: comments, status, fields, checklists, files.</p>
      {#if watchers.length}
        <ul class="wp-list" aria-label="Watching">
          {#each watchers as w (w.id)}
            <li>
              <Avatars people={[w]} />
              <span class="wp-name">{w.id === task.me ? `${w.display_name} (you)` : w.display_name}</span>
              <span class="muted small">{w.kind === 'agent' ? 'agent' : `@${w.handle}`}</span>
              <button class="icon" aria-label="Remove {w.display_name} as a watcher" onclick={() => remove(w)}
                ><Icon name="x" size={13} /></button
              >
            </li>
          {/each}
        </ul>
      {:else}
        <p class="muted small">Nobody is watching yet.</p>
      {/if}
      <input aria-label="Add a watcher" placeholder="Add a person or agent" bind:this={search} bind:value={q} />
      <ul class="wp-list wp-add" aria-label="People to add">
        {#each candidates as a (a.id)}
          <li>
            <button class="wp-pick" onclick={() => add(a.handle)}>
              <Avatars people={[a]} />
              <span class="wp-name">{a.display_name}</span>
              <span class="muted small">@{a.handle}{a.kind === 'agent' ? ' · agent' : ''}</span>
            </button>
          </li>
        {:else}
          <li class="muted small">{q ? 'Nobody matches.' : 'Everyone is watching.'}</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .watchers {
    position: relative;
  }
  .watch {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border: 1px solid var(--line);
    background: none;
    color: var(--muted);
    border-radius: 6px;
    padding: 3px 7px;
    font-size: 12px;
    min-height: 26px;
  }
  .watch.on {
    color: var(--accent);
    border-color: var(--accent);
  }
  .watch :global(.avatars) {
    margin-left: 0;
  }
  .watch-pop {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    z-index: 40;
    width: 320px;
    max-width: calc(100vw - 32px);
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel);
    box-shadow: var(--shadow-pop);
    display: grid;
    gap: 8px;
  }
  .wp-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .wp-head button {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .watch-pop p {
    margin: 0;
  }
  .watch-pop > input {
    width: 100%;
  }
  .wp-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 2px;
    max-height: 180px;
    overflow-y: auto;
  }
  .wp-list li {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto auto;
    align-items: center;
    column-gap: 8px;
    padding: 3px 4px;
  }
  .wp-list :global(.avatars) {
    margin-left: 0;
  }
  .wp-add li {
    display: block;
    padding: 0;
  }
  .wp-pick {
    width: 100%;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    column-gap: 8px;
    padding: 5px 6px;
    border: 0;
    border-radius: 4px;
    background: none;
    color: var(--text);
    text-align: left;
  }
  .wp-pick:hover {
    background: var(--card-hover);
  }
  .wp-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
