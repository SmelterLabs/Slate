<script lang="ts">
  // The favorites bar (ClickUp-style): one-click chips for your pinned lists, sections, views and
  // tasks, across the top of the app. Drag a chip to reorder; drag a list in from the nav to pin
  // it; hover (or right-click, or long-press on a phone) for the × that unpins.
  import { chipFor, type Chip } from '../lib/favorites';
  import { docs } from '../lib/docs.svelte';
  import { board } from '../lib/store.svelte';
  import AgentChatButton from './AgentChatButton.svelte';
  import WaitingBell from './WaitingBell.svelte';
  import Icon from './Icon.svelte';

  const LIST_DRAG = 'application/x-slate-list';
  const FAV_DRAG = 'application/x-slate-favorite';

  const chips = $derived.by(() => {
    const ctx = {
      lists: board.lists,
      sections: board.sections,
      views: board.views,
      items: board.items,
      selection: board.selection,
      // The tab showing, also when it came from the remembered last view rather than the URL.
      viewId: board.active.saved?.id ?? null,
      openId: board.openId,
      docs: docs.byId,
      docId: docs.current?.id ?? null,
    };
    return board.favorites.map((f) => chipFor(f, ctx)).filter((c): c is Chip => c !== null);
  });

  let dragging = $state<string | null>(null);
  /** Where a drop lands: before this favorite id, or at the end (`null`). */
  let dropBefore = $state<string | null | undefined>(undefined);
  /** The chip whose × is showing after a right-click or long-press. */
  let armed = $state<string | null>(null);

  function accepts(e: DragEvent): boolean {
    const types = e.dataTransfer?.types ?? [];
    return !!dragging || types.includes(LIST_DRAG);
  }

  function over(e: DragEvent, beforeId: string | null) {
    if (!accepts(e)) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = dragging ? 'move' : 'link';
    dropBefore = beforeId;
  }

  function dropHere(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    const before = dropBefore ?? null;
    const moving = dragging;
    const listId = moving ? null : e.dataTransfer?.getData(LIST_DRAG);
    dragging = null;
    dropBefore = undefined;
    if (moving) {
      void board.moveFavorite(moving, before);
    } else if (listId) {
      const at = before ? board.favorites.findIndex((f) => f.id === before) : -1;
      void board.addFavorite('list', listId, at < 0 ? undefined : at);
    }
  }

  function openChip(e: MouseEvent, chip: Chip) {
    armed = null;
    if (chip.href) return; // the link navigates
    e.preventDefault();
    void board.open(chip.fav.target_id);
  }
</script>

<svelte:window onclick={() => (armed = null)} />

<div
  class="favbar"
  class:empty={!chips.length}
  role="toolbar"
  aria-label="Favorites"
  tabindex="-1"
  ondragover={(e) => over(e, null)}
  ondragleave={(e) => {
    if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) dropBefore = undefined;
  }}
  ondrop={dropHere}
>
  <span class="favbar-lead" aria-hidden="true" title="Favorites"><Icon name="star" fill size={13} /></span>
  {#each chips as chip (chip.fav.id)}
    <span
      class="fav-chip"
      class:active={chip.active}
      class:dragging={dragging === chip.fav.id}
      class:drop-before={dropBefore === chip.fav.id && dragging !== chip.fav.id}
      class:armed={armed === chip.fav.id}
      data-fav={chip.fav.kind}
      role="listitem"
      draggable="true"
      ondragstart={(e) => {
        dragging = chip.fav.id;
        e.dataTransfer?.setData(FAV_DRAG, chip.fav.id);
        if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
      }}
      ondragend={() => {
        dragging = null;
        dropBefore = undefined;
      }}
      ondragover={(e) => {
        // Drop on a chip's right half to land after it.
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const i = chips.findIndex((c) => c.fav.id === chip.fav.id);
        const after = e.clientX > r.left + r.width / 2;
        over(e, after ? (chips[i + 1]?.fav.id ?? null) : chip.fav.id);
      }}
      ondrop={dropHere}
      oncontextmenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        armed = armed === chip.fav.id ? null : chip.fav.id;
      }}
    >
      <a
        class="fav-link"
        href={chip.href ?? '#'}
        title={chip.hint}
        draggable="false"
        aria-current={chip.active ? 'page' : undefined}
        onclick={(e) => openChip(e, chip)}
      >
        <span class="fav-icon" aria-hidden="true">{chip.icon}</span>
        <span class="fav-label">{chip.label}</span>
      </a>
      <button
        class="fav-remove"
        aria-label="Remove {chip.label} from favorites"
        title="Remove from favorites"
        onclick={(e) => {
          e.stopPropagation();
          armed = null;
          void board.removeFavorite(chip.fav.id);
        }}><Icon name="x" size={12} /></button
      >
    </span>
  {:else}
    <span class="favbar-empty">Star a list, view or task to pin it here, or drag a list in from the left.</span>
  {/each}
  {#if chips.length && dropBefore === null}<span class="fav-drop-end" aria-hidden="true"></span>{/if}
  <!-- Desktop: the agents' chat button at the bar's right end (a phone has it in the top bar). -->
  <span class="favbar-end"><WaitingBell /><AgentChatButton /></span>
</div>
