<script lang="ts">
  // Saved views of the current scope as tabs (ClickUp-style). The active tab carries a ⋯ menu;
  // unsaved toolbar changes show a dot with Save / Save as new / Revert.
  import { badge, channelKey } from '../lib/chat';
  import { chat as chatState } from '../lib/chat.svelte';
  import { chatPanel } from '../lib/chatpanel.svelte';
  import { board } from '../lib/store.svelte';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  /** `chat`: the scope's Chat tab is the one showing. */
  let { chat = false }: { chat?: boolean } = $props();

  const scope = $derived(board.selection);
  const views = $derived(board.viewsFor(scope));
  const active = $derived(board.active);
  const key = $derived(scope.kind === 'everything' ? null : channelKey(scope.kind, scope.id));
  const channel = $derived(key ? chatState.channel(key) : null);
  /** The tab reads as on while the chat panel shows this scope's chat beside the board. */
  const inPanel = $derived(!!key && chatPanel.open && chatPanel.channel === key);

  /** On a wide screen the tab opens the chat panel on this scope's channel and the board stays;
   * on a narrow one it goes to the Chat tab page. A click on the tab that is already open closes it. */
  function openChat(e: MouseEvent) {
    if (!key || !chatPanel.fits || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (inPanel) chatPanel.hide();
    else chatPanel.show(key);
  }

  let menuOpen = $state(false);
  let renaming = $state<string | null>(null);
  let naming = $state(false);
  let name = $state('');
  let confirmDelete = $state(false);

  function startRename() {
    renaming = active.saved?.id ?? null;
    name = active.saved?.name ?? '';
    menuOpen = false;
  }
  async function finishRename(e: SubmitEvent) {
    e.preventDefault();
    if (renaming && name.trim()) await board.renameView(renaming, name.trim());
    renaming = null;
  }
  function startSaveAs() {
    naming = true;
    name = active.saved ? `${active.saved.name} copy` : 'New view';
    menuOpen = false;
  }
  async function finishSaveAs(e: SubmitEvent) {
    e.preventDefault();
    if (name.trim() && (await board.saveAsNew(name.trim()))) naming = false;
  }
  function nameInput(el: HTMLInputElement) {
    el.focus();
    el.select();
  }
</script>

<div class="view-tabs" role="tablist" aria-label="Views">
  {#if !views.length}
    {#if chat}
      <span class="view-tab" role="tab" aria-selected="false"><a href={board.href(scope)}>Default</a></span>
    {:else}
      <span class="view-tab on" role="tab" aria-selected="true">
        Default{#if active.dirty}<span class="dirty" title="Unsaved changes"></span>{/if}
      </span>
    {/if}
  {/if}
  {#each views as v (v.id)}
    {@const on = v.id === active.key && !chat}
    {#if renaming === v.id}
      <form class="view-tab on" onsubmit={finishRename}>
        <input aria-label="View name" bind:value={name} use:nameInput onblur={() => (renaming = null)} maxlength="100" />
      </form>
    {:else}
      <span class="view-tab" class:on role="tab" aria-selected={on}>
        <a href={board.href(scope, v.id)} data-view={v.id}
          >{v.name}{#if board.favoriteOf('view', v.id)}<span class="fav-mark" title="In favorites"><Icon name="star" fill size={10} /></span>{/if}</a
        >
        {#if on && active.dirty}<span class="dirty" title="Unsaved changes"></span>{/if}
        {#if on}
          <Dropdown title="View menu" cls="icon small" bind:open={menuOpen}>
            {#snippet label()}<Icon name="more" size={16} />{/snippet}
            <div class="menu">
              <button onclick={startRename}>Rename</button>
              <button
                onclick={() => {
                  menuOpen = false;
                  void board.toggleFavorite('view', v.id);
                }}>{board.favoriteOf('view', v.id) ? 'Remove from favorites' : 'Add to favorites'}</button
              >
              <button
                onclick={() => {
                  menuOpen = false;
                  void board.duplicateView(v.id);
                }}>Duplicate</button
              >
              {#if confirmDelete}
                <button
                  class="danger"
                  onclick={() => {
                    confirmDelete = false;
                    menuOpen = false;
                    void board.deleteView(v.id);
                  }}>Really delete “{v.name}”?</button
                >
              {:else}
                <button class="danger" onclick={() => (confirmDelete = true)}>Delete view</button>
              {/if}
            </div>
          </Dropdown>
        {/if}
      </span>
    {/if}
  {/each}

  {#if scope.kind !== 'everything'}
    <span class="view-tab channel-tab" class:on={chat || inPanel} role="tab" aria-selected={chat || inPanel}>
      <a href="#/{scope.kind}/{scope.id}/chat" data-view="channel" onclick={openChat}
        ><Icon name="hash" size={13} />Chat{#if channel?.mentions}<span class="count hot" title="Mentions you"
            >@{badge(channel.mentions)}</span
          >{:else if channel?.unread}<span class="count" title="Unread">{badge(channel.unread)}</span>{/if}</a
      >
    </span>
  {/if}

  {#if chat}
    <!-- the view controls belong to the views -->
  {:else if naming}
    <form class="view-tab on" onsubmit={finishSaveAs}>
      <input aria-label="New view name" bind:value={name} use:nameInput maxlength="100" />
      <button class="primary small" type="submit">Save</button>
      <button class="link small" type="button" onclick={() => (naming = false)}>Cancel</button>
    </form>
  {:else}
    <button class="view-tab add" title="Save the current setup as a new view" onclick={startSaveAs}>+ View</button>
  {/if}

  {#if active.dirty && !naming && !chat}
    <span class="view-actions">
      <button class="primary small" onclick={() => (active.saved ? void board.saveView() : startSaveAs())}
        >{active.saved ? 'Save' : 'Save view'}</button
      >
      {#if active.saved}<button class="link small" onclick={startSaveAs}>Save as new</button>{/if}
      <button class="link small" onclick={() => board.revert()}>Revert</button>
    </span>
  {/if}
</div>
