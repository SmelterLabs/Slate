<script lang="ts">
  // One scope (Everything, a section or a list) through its active view: tabs, toolbar, quick
  // add, then the board or list layout. The engine runs once here per change, never per card.
  import { board } from '../lib/store.svelte';
  import { runView, type Scope, type ViewConfig } from '../lib/views';
  import BoardView from './BoardView.svelte';
  import BulkBar from './BulkBar.svelte';
  import ListLayout from './ListLayout.svelte';
  import QuickAddBar from './QuickAddBar.svelte';
  import TableView from './TableView.svelte';
  import Toolbar from './Toolbar.svelte';
  import ViewTabs from './ViewTabs.svelte';
  import EmptyState from './EmptyState.svelte';
  import Icon from './Icon.svelte';
  import ProjectHome from './ProjectHome.svelte';
  import DrawerPage from './docs/DrawerPage.svelte';
  import ChannelView from './chat/ChannelView.svelte';
  import { channelKey } from '../lib/chat';
  import { LEVEL_LABEL, atLeast, sectionLevel } from '../lib/permissions';

  let {
    scope,
    kiosk = false,
    chat = false,
    threadId = null,
  }: { scope: Scope; kiosk?: boolean; chat?: boolean; threadId?: string | null } = $props();
  /** The Chat tab: this list's or section's chat in place of a view. */
  const inChat = $derived(chat && scope.kind !== 'everything');

  const active = $derived(board.active);
  const config = $derived(active.config);
  // Plain copies, not the $state proxies: see board.plainItems.
  const groups = $derived(
    runView(board.plainItems, $state.snapshot(scope), $state.snapshot(config) as ViewConfig, board.ctx),
  );
  const wide = $derived(scope.kind !== 'list');
  const title = $derived(
    scope.kind === 'everything'
      ? 'Everything'
      : scope.kind === 'section'
        ? (board.sections.find((s) => s.id === scope.id)?.name ?? '')
        : (board.lists[scope.id]?.name ?? ''),
  );
  const crumb = $derived(
    scope.kind === 'list' ? board.sections.find((s) => s.id === board.lists[scope.id]?.section_id)?.name : null,
  );
  const shown = $derived(new Set(groups.flatMap((g) => g.items.map((i) => i.id))).size);

  // Screen order for shift-click ranges.
  $effect(() => {
    board.order = groups.flatMap((g) => g.items.map((i) => i.id));
  });

  // What this token may do here (the server enforces it; this only explains a refusal early).
  const level = $derived(
    scope.kind === 'list'
      ? board.levelOn(scope.id)
      : scope.kind === 'section'
        ? sectionLevel(board.me?.access, scope.id)
        : null,
  );
  const isPrivate = $derived(
    scope.kind === 'list'
      ? !!board.lists[scope.id]?.private
      : scope.kind === 'section'
        ? !!board.sections.find((s) => s.id === scope.id)?.private
        : false,
  );

  // Quick add only where this token may file tasks (a guest with a view share gets none).
  const canAdd = $derived(
    Object.values(board.lists).some(
      (l) =>
        (scope.kind === 'everything' || (scope.kind === 'list' ? l.id === scope.id : l.section_id === scope.id)) &&
        atLeast(board.levelOn(l.id), 'edit'),
    ),
  );

  /** A drawer (a list of documents) shows its files instead of task views. */
  const isDrawer = $derived(scope.kind === 'list' && board.lists[scope.id]?.kind === 'drawer');

  // The gear appears when the store provides board.openListSettings(listId).
  const openSettings = $derived(
    (board as unknown as { openListSettings?: (id: string) => void }).openListSettings,
  );
</script>

<section
  class="list-page"
  class:kiosk-page={kiosk}
  class:board-mode={config.layout === 'board' && !inChat}
  class:chat-mode={inChat}
  data-scope={scope.kind}
>
  <div class="list-head">
    <h2>
      {#if crumb}<span class="muted crumb">{crumb} /</span>{/if}
      {title}
      {#if kiosk}<span class="muted view-name">· {active.name}</span>{/if}
    </h2>
    {#if isPrivate}<span class="private-chip" title="Private: only owners, admins and people it is shared with"
        ><Icon name="lock" size={12} /> Private</span
      >{/if}
    {#if !inChat}<span class="muted small">{shown} shown</span>{/if}
    {#if scope.kind !== 'everything' && board.canManage && !kiosk}
      {@const target = scope}
      <button class="share-btn" onclick={() => board.openShare(target.kind, target.id)} title="Who can see and change this {target.kind}"
        ><Icon name="share" size={14} /> Share</button
      >
    {/if}
    {#if scope.kind === 'list' && openSettings && !kiosk}
      {@const id = scope.id}
      <button class="icon" aria-label="List settings" title="List settings" onclick={() => openSettings.call(board, id)}
        ><Icon name="gear" size={16} /></button
      >
    {/if}
  </div>
  <!-- A private section is a project with its own membership: it gets the project home. Open
       sections keep their board at the top. -->
  {#if scope.kind === 'section' && isPrivate && !inChat && !kiosk}{#key scope.id}<ProjectHome id={scope.id} />{/key}{/if}
  {#if inChat && scope.kind !== 'everything'}
    <ViewTabs chat />
    {#key scope.id}
      <ChannelView channelKey={channelKey(scope.kind, scope.id)} {threadId} embedded />
    {/key}
  {:else if isDrawer && scope.kind === 'list'}
    {@const drawerId = scope.id}
    <nav class="drawer-tabs" aria-label="Drawer tabs">
      <span class="on">Files</span>
      <a href="#/list/{drawerId}/chat"><Icon name="chat" size={13} /> Chat</a>
    </nav>
    {#key drawerId}<DrawerPage listId={drawerId} />{/key}
  {:else}
  {#if scope.kind === 'list' && level && !atLeast(level, 'edit') && !kiosk}
    <div class="level-banner" role="note">
      <Icon name={level === 'view' ? 'eye' : 'comment'} size={14} />
      {LEVEL_LABEL[level]}: {level === 'view'
        ? 'you can read this list and its tasks, but not change them.'
        : 'you can read and comment here, but not change tasks.'}
    </div>
  {/if}
  {#if !kiosk}
    <ViewTabs />
    <Toolbar />
    {#if canAdd}<QuickAddBar {scope} viewKey={active.key} />{/if}
    <BulkBar />
  {/if}

  {#if !shown && !kiosk && (board.q.trim() || config.filter.rules.length)}
    <EmptyState
      icon="search"
      title="No tasks match"
      hint={board.q.trim()
        ? `Nothing in this view matches “${board.q.trim()}”.`
        : 'The filters on this view hide every task.'}
    >
      {#if board.q.trim()}<button class="primary" onclick={() => (board.q = '')}>Clear search</button>{/if}
      {#if config.filter.rules.length}
        <button class="chip-btn" onclick={() => board.setConfig({ filter: { ...config.filter, rules: [] } })}
          >Clear filters</button
        >
      {/if}
    </EmptyState>
  {:else if !shown && !kiosk && config.layout !== 'board'}
    <EmptyState
      icon="sparkle"
      title={scope.kind === 'everything' ? 'All clear' : 'Nothing here yet'}
      hint="Add a task above (press c), or ask an agent to file one. New tasks appear here live."
    />
  {/if}

  {#if config.layout === 'board'}
    <BoardView {groups} {config} {wide} {scope} viewKey={active.key} {kiosk} />
  {:else if config.layout === 'table'}
    <TableView {groups} {config} {scope} />
  {:else}
    <ListLayout {groups} {config} {wide} />
  {/if}
  {/if}
</section>
