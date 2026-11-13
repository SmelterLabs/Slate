<script lang="ts">
  import { download, exportPath } from '../lib/archive';
  import { notifySettings } from '../lib/notify.svelte';
  import { board } from '../lib/store.svelte';
  import { docGlyph, topOf } from '../lib/docs';
  import { docs } from '../lib/docs.svelte';
  import { inbox } from '../lib/inbox.svelte';
  import { badge } from '../lib/chat';
  import { chat } from '../lib/chat.svelte';
  import { build } from '../lib/build.svelte';
  import { chatPanel } from '../lib/chatpanel.svelte';
  import type { Page } from '../lib/route';
  import type { List, Section } from '../lib/types';
  import { atLeast, canCreateSections, sectionLevel } from '../lib/permissions';
  import ListSettings from './ListSettings.svelte';
  import Menu from './Menu.svelte';
  import Icon from './Icon.svelte';

  /** A chat link on a wide screen opens the chat panel on `key` and stays on this page; a
   * narrow one follows the link to the Chat page. The Chat entry with no key toggles the panel. */
  function openChat(e: MouseEvent, key?: string) {
    if (!chatPanel.fits || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (key) chatPanel.show(key);
    else chatPanel.toggle();
    onpick();
  }

  /** Called when a link is picked: the phone nav closes even if the list did not change. */
  let {
    onpick = () => {},
    onsearch = () => {},
    page = null,
    docId = null,
  }: { onpick?: () => void; onsearch?: () => void; page?: Page | null; docId?: string | null } = $props();
  const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';

  import { COOKIE_SESSION, moreDestination } from '../lib/collaboration';
  import { shortDate } from '../lib/format';
  const moreKey = $derived(`slate.nav.more.${board.me?.account.id ?? ''}`);
  let moreOpen = $state(false);
  $effect(() => {
    const key = moreKey;
    try { moreOpen = localStorage.getItem(key) === 'true' || moreDestination(page); } catch { /* optional preference */ }
  });
  const secondary = $derived(moreDestination(page));
  $effect(() => { if (secondary) moreOpen = true; });
  const todayCount = $derived(board.todayCount);
  const canReorder = $derived(canCreateSections(board.me));
  let draggingSection = $state<string | null>(null);
  let sectionDrop = $state<{ id: string; after: boolean } | null>(null);
  let savingOrder = $state(false);
  function sectionOver(e: DragEvent, id: string) {
    if (!draggingSection || !canReorder || savingOrder || draggingSection === id) return;
    e.preventDefault(); e.stopPropagation();
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    sectionDrop = { id, after: e.clientY > box.top + box.height / 2 };
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
  }
  async function sectionDropped(e: DragEvent) {
    if (!draggingSection || !sectionDrop) return;
    e.preventDefault(); e.stopPropagation();
    const id = draggingSection, target = sectionDrop;
    draggingSection = null; sectionDrop = null; savingOrder = true;
    try { await board.placeSection(id, target.id, target.after); }
    finally { savingOrder = false; }
  }

  const AUDIENCES: Section['audience'][] = ['personal', 'work', 'ai', 'family'];

  // One inline editor at a time: renaming a section or list, adding a list, a new section, or
  // confirming an archive. `key` is "section:<id>" / "list:<id>" / "add:<sectionId>" / "new".
  let editing = $state<string | null>(null);
  let draft = $state('');
  let audience = $state<Section['audience']>('personal');
  let confirming = $state<string | null>(null);
  let showArchived = $state(false);
  /** Which ⋯ menu is open ("section:<id>" / "list:<id>"); right-click opens it too. */
  let menuFor = $state<string | null>(null);

  // Drag a list onto another list (lands before it) or onto a section heading (lands last).
  const LIST_DRAG = 'application/x-slate-list';
  let dragging = $state<string | null>(null);
  let drop = $state<{ sectionId: string; beforeId: string | null } | null>(null);

  function over(e: DragEvent, sectionId: string, beforeId: string | null) {
    if (!dragging || beforeId === dragging) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
    drop = { sectionId, beforeId };
  }

  function dropHere(e: DragEvent) {
    e.preventDefault();
    const id = dragging ?? e.dataTransfer?.getData(LIST_DRAG);
    const target = drop;
    dragging = null;
    drop = null;
    if (id && target) void board.placeList(id, target.sectionId, target.beforeId);
  }

  function newDoc(place: string) {
    location.hash = `#/docs?new=1&in=${place}`;
    onpick();
  }

  function start(key: string, value = '') {
    editing = key;
    draft = value;
    confirming = null;
  }

  function cancel() {
    editing = null;
    draft = '';
  }

  /** Focus and select an inline input as it appears. */
  function focus(el: HTMLInputElement) {
    el.focus();
    el.select();
  }

  async function commit() {
    const key = editing;
    const name = draft.trim();
    editing = null;
    if (!key || !name) return;
    const [kind, id] = key.split(':');
    if (kind === 'section') {
      if (name !== board.sections.find((s) => s.id === id)?.name) {
        await board.updateSection(id, { name });
      }
    } else if (kind === 'list') {
      if (name !== board.lists[id]?.name) await board.renameList(id, name);
    } else if (kind === 'add') {
      const list = await board.createList(id, name, addKind);
      if (list) {
        location.hash = `#/list/${list.id}`;
        onpick();
      }
    } else if (kind === 'new') {
      await board.createSection(name, audience);
    }
  }

  function inputKeys(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      void commit();
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      cancel();
    }
  }

  async function toggleArchived() {
    showArchived = !showArchived;
    if (showArchived) await board.loadArchived();
  }

  /** A section's own top-level pages, in tree order: a doc filed "here" is reachable from here,
   * not only from the Docs page. Subpages stay inside the page tree. */
  /** What the next "Add" makes: a task list or a drawer (a list of documents). */
  let addKind = $state<'tasks' | 'drawer'>('tasks');
  /** Documents in a drawer (its own pages, files and folders, subpages included). */
  const drawerCount = (listId: string) => docs.list.filter((d) => d.kind !== 'folder' && topOf(d.id, docs.byId)?.list_id === listId).length;
  const sectionDocs = (sectionId: string) =>
    docs.list
      .filter((d) => d.section_id === sectionId && !d.list_id && !d.parent_id && !d.archived_at)
      .sort((a, b) => a.position - b.position);

  const canAddSection = $derived(canCreateSections(board.me));
  const canEditSection = (id: string) => atLeast(sectionLevel(board.me?.access, id), 'edit');
  const shareEntry = (kind: 'section' | 'list', id: string) =>
    board.canManage ? [{ label: 'Share…', onselect: () => board.openShare(kind, id) }] : [];

  function sectionMenu(section: Section, index: number) {
    return [
      ...shareEntry('section', section.id),
      { label: 'Add list', disabled: !canEditSection(section.id), onselect: () => { addKind = 'tasks'; start(`add:${section.id}`); } },
      { label: 'Add drawer', disabled: !canEditSection(section.id), onselect: () => { addKind = 'drawer'; start(`add:${section.id}`); } },
      { label: 'New doc here', disabled: !canEditSection(section.id), onselect: () => newDoc(`s:${section.id}`) },
      favEntry('section', section.id),
      { label: 'Rename', onselect: () => start(`section:${section.id}`, section.name) },
      {
        label: 'Move up',
        disabled: !canReorder || savingOrder || index === 0,
        onselect: () => void board.moveSection(section.id, -1),
      },
      {
        label: 'Move down',
        disabled: !canReorder || savingOrder || index === board.sections.length - 1,
        onselect: () => void board.moveSection(section.id, 1),
      },
      { label: 'Export as JSON', onselect: () => save('json', { section_id: section.id }) },
      { label: 'Export as CSV', onselect: () => save('csv', { section_id: section.id }) },
      { label: 'Archive…', danger: true, onselect: () => (confirming = `section:${section.id}`) },
    ];
  }

  function listMenu(list: List, index: number, count: number) {
    return [
      ...shareEntry('list', list.id),
      { label: 'Rename', onselect: () => start(`list:${list.id}`, list.name) },
      { label: 'List settings…', onselect: () => board.openListSettings(list.id) },
      { label: 'New doc in this list', disabled: !atLeast(board.levelOn(list.id), 'edit'), onselect: () => newDoc(`l:${list.id}`) },
      favEntry('list', list.id),
      { label: 'Move up', disabled: index === 0, onselect: () => void board.moveList(list.id, -1) },
      {
        label: 'Move down',
        disabled: index === count - 1,
        onselect: () => void board.moveList(list.id, 1),
      },
      ...board.sections
        .filter((sec) => sec.id !== list.section_id)
        .map((sec) => ({
          label: `Move to ${sec.name}`,
          onselect: () => void board.placeList(list.id, sec.id, null),
        })),
      { label: 'Export as JSON', onselect: () => save('json', { list_id: list.id }) },
      { label: 'Export as CSV', onselect: () => save('csv', { list_id: list.id }) },
      { label: 'Archive…', danger: true, onselect: () => (confirming = `list:${list.id}`) },
    ];
  }

  async function save(format: 'json' | 'csv', scope: { list_id?: string; section_id?: string }) {
    try {
      board.flash(`Downloaded ${await download(exportPath(format, scope), board.token, `slate.${format}`)}`);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  function favEntry(kind: 'list' | 'section', id: string) {
    return {
      label: board.favoriteOf(kind, id) ? 'Remove from favorites' : 'Add to favorites',
      onselect: () => void board.toggleFavorite(kind, id),
    };
  }

  async function archive(key: string) {
    confirming = null;
    const [kind, id] = key.split(':');
    if (kind === 'section') await board.setSectionArchived(id, true);
    else await board.setListArchived(id, true);
  }

  function initials(name: string): string {
    return name
      .split(/\s+/)
      .filter(Boolean)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
</script>

{#snippet star(kind: 'list' | 'section', id: string, name: string)}
  {@const on = !!board.favoriteOf(kind, id)}
  <button
    class="nav-star"
    class:on
    aria-pressed={on}
    aria-label={on ? `Remove ${name} from favorites` : `Add ${name} to favorites`}
    title={on ? 'Remove from favorites' : 'Add to favorites'}
    onclick={(e) => {
      e.preventDefault();
      e.stopPropagation();
      void board.toggleFavorite(kind, id);
    }}><Icon name="star" fill={on} size={13} /></button
  >
{/snippet}

{#snippet chatBadge(kind: 'list', id: string, name: string)}
  {@const c = chat.channel(`${kind}:${id}`)}
  {#if c && (c.unread || c.mentions)}
    <a
      class="nav-chat"
      class:hot={c.mentions > 0}
      href="#/{kind}/{id}/chat"
      onclick={(e) => openChat(e, `${kind}:${id}`)}
      draggable="false"
      title={c.mentions ? `${c.mentions} unread mentions in #${name}` : `${c.unread} unread in #${name}`}
      aria-label={c.mentions ? `${c.mentions} unread mentions in #${name} chat` : `${c.unread} unread in #${name} chat`}
      ><Icon name="chat" size={11} />{c.mentions ? `@${badge(c.mentions)}` : badge(c.unread)}</a
    >
  {/if}
{/snippet}

{#snippet sectionChat(id: string, name: string)}
  <!-- Always there, so a section's chat is one click away; the unread count rides on it. -->
  {@const c = chat.channel(`section:${id}`)}
  {@const n = c?.mentions || c?.unread || 0}
  <a
    class="nav-chat"
    class:quiet={!n}
    class:hot={!!c?.mentions}
    href="#/section/{id}/chat"
    onclick={(e) => openChat(e, `section:${id}`)}
    draggable="false"
    data-section-chat={id}
    title={c?.mentions ? `${c.mentions} unread mentions in #${name}` : c?.unread ? `${c.unread} unread in #${name}` : `Open #${name} chat`}
    aria-label={c?.mentions
      ? `${c.mentions} unread mentions in #${name} chat`
      : c?.unread
        ? `${c.unread} unread in #${name} chat`
        : `Open #${name} chat`}
    ><Icon name="chat" size={11} />{c?.mentions ? `@${badge(c.mentions)}` : badge(c?.unread ?? 0)}</a
  >
{/snippet}

{#snippet confirmRow(key: string, what: string)}
  <div class="nav-confirm" role="alertdialog" aria-label="Confirm archive">
    <span>Archive {what}? Its items hide with it; restore any time from Archived.</span>
    <span class="inline">
      <button class="primary danger-fill" onclick={() => archive(key)}>Archive</button>
      <button class="link" onclick={() => (confirming = null)}>Cancel</button>
    </span>
  </div>
{/snippet}

{#snippet needsMark(listId: string)}
  {@const need = board.needsIn(listId)}
  {#if need}
    <span
      class="needs-mark need-{need.top}"
      data-needs-list={need.n}
      title="{need.n} card{need.n === 1 ? '' : 's'} here need{need.n === 1 ? 's' : ''} you"
      aria-label="{need.n} need you"><i></i>{need.n}</span
    >
  {/if}
{/snippet}

<nav class="nav" aria-label="Lists">
  <div class="nav-brand">
    <span class="logo" aria-hidden="true">S</span>
    <span class="brand-name">Slate</span>
    {#if build.label}
      <span class="brand-rev" title={build.commit ? `Build ${build.commit}` : 'Build'}>{build.label}</span>
    {/if}
    <span class="conn-dot conn-{board.conn}" title={board.conn === 'live' ? 'Live' : 'Reconnecting'}></span>
  </div>
  <button
    class="nav-search"
    id="palette-open"
    onclick={() => {
      onpick();
      onsearch();
    }}
  >
    <Icon name="search" size={15} /><span class="grow">Search</span><kbd>{mod} K</kbd>
  </button>
  <a class="nav-item" href="#/home" onclick={onpick} class:active={page === 'home'}>
    <span class="nav-label"><Icon name="target" size={15} /><span>My Work</span></span>
    <!-- Needs you: the same number as the pill, the window title and the app icon. -->
    {#if board.needsMe.length}<span
        class="count hot"
        data-needs-nav={board.needsMe.length}
        title="{board.needsMe.length} need{board.needsMe.length === 1 ? 's' : ''} you"
        aria-label="{board.needsMe.length} need you">{board.needsMe.length}</span
      >{/if}
  </a>
  <a class="nav-item" href="#/inbox" onclick={onpick} class:active={page === 'inbox'}>
    <span class="nav-label"><Icon name="inbox" size={15} /><span>Inbox</span></span>
    {#if inbox.unread}<span class="count hot" aria-label="{inbox.unread} unread">{inbox.unread}</span>{/if}
  </a>
  <a class="nav-item" href="#/chat" onclick={(e) => openChat(e)} class:active={page === 'chat' || chatPanel.open}>
    <span class="nav-label"><Icon name="chat" size={15} /><span>Chat</span></span>
    {#if chat.mentions}<span class="count hot" aria-label="{chat.mentions} mentions">@{badge(chat.mentions)}</span
      >{:else if chat.unread}<span class="count" aria-label="{chat.unread} unread messages">{badge(chat.unread)}</span>{/if}
  </a>
  <a
    class="nav-item today"
    href="#/everything"
    onclick={onpick}
    class:active={!page && board.selection.kind === 'everything'}
  >
    <span class="nav-label"><Icon name="home" size={15} /><span>Everything</span></span>
    {#if todayCount}<span
        class="count"
        class:hot={board.overdueCount > 0}
        title="Overdue and due today">{todayCount}</span
      >{/if}
  </a>
  <button class="nav-item" aria-expanded={moreOpen} aria-controls="nav-more" onclick={() => { moreOpen = !moreOpen; try { localStorage.setItem(moreKey, String(moreOpen)); } catch { /* optional preference */ } }}>
    <span class="nav-label"><Icon name="chevron" size={15} />{secondary ? `More · ${page}` : 'More'}</span>
  </button>
  <div id="nav-more" hidden={!moreOpen}>
  <a class="nav-item" href="#/docs" onclick={onpick} class:active={page === 'docs'}>
    <span class="nav-label"><Icon name="page" size={15} /><span>Docs & Files</span></span>
  </a>
  <a class="nav-item" href="#/agents" onclick={onpick} class:active={page === 'agents'}>
    <span class="nav-label"><Icon name="robot" size={15} /><span>Agents</span></span>
  </a>
  <a class="nav-item" href="#/archive" onclick={onpick} class:active={page === 'archive'}>
    <span class="nav-label"><Icon name="archive" size={15} /><span>Archive</span></span>
  </a>
  {#if board.canManage}
    <a class="nav-item" href="#/members" onclick={onpick} class:active={page === 'members'}>
      <span class="nav-label"><Icon name="users" size={15} /><span>Members</span></span>
    </a>
  {/if}
  <a class="nav-item" href="#/audit" onclick={onpick} class:active={page === 'audit'}>
    <span class="nav-label"><Icon name="history" size={15} /><span>Audit log</span></span>
  </a>
  {#if board.canManage}
    <a class="nav-item" href="#/health" onclick={onpick} class:active={page === 'health'}>
      <span class="nav-label"><Icon name="pulse" size={15} /><span>Health</span></span>
    </a>
  {/if}
  </div>
  {#each board.sections as section, si (section.id)}
    {@const lists = board.listsOf(section.id)}
    <div class="nav-section" data-section={section.name}>
      {#if editing === `section:${section.id}`}
        <input
          class="nav-input"
          aria-label="Section name"
          bind:value={draft}
          use:focus
          onkeydown={inputKeys}
          onblur={commit}
        />
      {:else}
        <div
          class="nav-heading nav-row"
          class:drop-before={sectionDrop?.id === section.id && !sectionDrop.after}
          class:section-drop-after={sectionDrop?.id === section.id && sectionDrop.after}
          draggable={canReorder && !savingOrder}
          ondragstart={(e) => {
            if (!canReorder || savingOrder) { e.preventDefault(); return; }
            draggingSection = section.id;
            e.dataTransfer?.setData('application/x-slate-section', section.id);
            if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
          }}
          ondragend={() => { draggingSection = null; sectionDrop = null; }}
          class:drop-into={drop?.sectionId === section.id && drop.beforeId === null}
          role="group"
          aria-label="Section {section.name}"
          oncontextmenu={(e) => {
            e.preventDefault();
            menuFor = `section:${section.id}`;
          }}
          ondragover={(e) => draggingSection ? sectionOver(e, section.id) : over(e, section.id, null)}
          ondrop={(e) => draggingSection ? sectionDropped(e) : dropHere(e)}
        >
          <a
            href="#/section/{section.id}"
            draggable="false"
            onclick={onpick}
            class:active={!page && board.selection.kind === 'section' && board.selection.id === section.id}
            ><span class="space-mark aud-{section.audience}" aria-hidden="true"
              >{section.name.trim().charAt(0).toUpperCase()}</span
            ><span class="space-name">{section.name}</span
            >{#if section.private}<span class="nav-lock" title="Private: only people it is shared with"
                ><Icon name="lock" size={11} /></span
              >{/if}</a
          >
          {@render sectionChat(section.id, section.name)}
          <span class="nav-actions">
            {@render star('section', section.id, section.name)}
            {#if canEditSection(section.id)}
              <button
                class="nav-add"
                aria-label="Add list to {section.name}"
                title="Add list"
                onclick={() => start(`add:${section.id}`)}><Icon name="plus" size={13} /></button
              >
            {/if}
            <Menu
              label="{section.name} options"
              entries={sectionMenu(section, si)}
              bind:open={
                () => menuFor === `section:${section.id}`,
                (v) => (menuFor = v ? `section:${section.id}` : null)
              }
            />
          </span>
        </div>
      {/if}
      {#if confirming === `section:${section.id}`}
        {@render confirmRow(`section:${section.id}`, `section ${section.name}`)}
      {/if}
      {#each lists as list, li (list.id)}
        {#if editing === `list:${list.id}`}
          <input
            class="nav-input"
            aria-label="List name"
            bind:value={draft}
            use:focus
            onkeydown={inputKeys}
            onblur={commit}
          />
        {:else}
          <div
            class="nav-row nav-list"
            class:active={!page && board.selection.kind === 'list' && board.selection.id === list.id}
            class:dragging={dragging === list.id}
            class:drop-before={drop?.beforeId === list.id}
            data-list={list.name}
            role="listitem"
            draggable="true"
            ondragstart={(e) => {
              dragging = list.id;
              e.dataTransfer?.setData(LIST_DRAG, list.id);
              if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
            }}
            ondragend={() => {
              dragging = null;
              drop = null;
            }}
            ondragover={(e) => over(e, section.id, list.id)}
            ondrop={(e) => dropHere(e)}
            oncontextmenu={(e) => {
              e.preventDefault();
              menuFor = `list:${list.id}`;
            }}
          >
            <a class="nav-item" href="#/list/{list.id}" onclick={onpick} draggable="false">
              <span class="nav-label"
                ><Icon name={list.private ? 'lock' : list.kind === 'drawer' ? 'folder' : 'list'} size={14} /><span>{list.name}</span></span
              >
              {@render needsMark(list.id)}
              <span class="count">{list.kind === 'drawer' ? drawerCount(list.id) : board.openCount(list.id)}</span>
            </a>
            {@render chatBadge('list', list.id, list.name)}
            <span class="nav-actions">
              {@render star('list', list.id, list.name)}
              <Menu
                label="{list.name} options"
                entries={listMenu(list, li, lists.length)}
                bind:open={
                  () => menuFor === `list:${list.id}`,
                  (v) => (menuFor = v ? `list:${list.id}` : null)
                }
              />
            </span>
          </div>
        {/if}
        {#if confirming === `list:${list.id}`}
          {@render confirmRow(`list:${list.id}`, list.name)}
        {/if}
      {/each}
      {#each sectionDocs(section.id) as doc (doc.id)}
        <div class="nav-row nav-doc" class:active={page === 'docs' && docId === doc.id} role="listitem">
          <a class="nav-item" href="#/docs/{doc.id}" onclick={onpick} draggable="false" title="{doc.kind === 'file' ? 'File' : 'Page'}: {doc.title}">
            <span class="nav-label"
              >{#if doc.icon}<span class="nav-doc-icon" aria-hidden="true">{doc.icon}</span>{:else if doc.kind === 'file'}<span class="nav-doc-icon" aria-hidden="true">{docGlyph(doc)}</span>{:else}<Icon name="page" size={14} />{/if}<span
                >{doc.title || 'Untitled'}</span
              ></span
            >
          </a>
        </div>
      {/each}
      {#if editing === `add:${section.id}`}
        <div class="nav-add-kind" role="radiogroup" aria-label="What to add">
          <button role="radio" aria-checked={addKind === 'tasks'} class:on={addKind === 'tasks'} onmousedown={(e) => e.preventDefault()} onclick={() => (addKind = 'tasks')}><Icon name="list" size={12} /> List</button>
          <button role="radio" aria-checked={addKind === 'drawer'} class:on={addKind === 'drawer'} onmousedown={(e) => e.preventDefault()} onclick={() => (addKind = 'drawer')}><Icon name="folder" size={12} /> Drawer</button>
        </div>
        <input
          class="nav-input"
          aria-label="New {addKind === 'drawer' ? 'drawer' : 'list'} name"
          placeholder={addKind === 'drawer' ? 'Drawer name, Enter to add' : 'List name, Enter to add'}
          bind:value={draft}
          use:focus
          onkeydown={inputKeys}
          onblur={() => (draft.trim() ? commit() : cancel())}
        />
      {/if}
    </div>
  {/each}

  {#if editing === 'new'}
    <form
      class="nav-new"
      onsubmit={(e) => {
        e.preventDefault();
        void commit();
      }}
    >
      <input
        aria-label="New section name"
        placeholder="Section name"
        bind:value={draft}
        use:focus
        onkeydown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            cancel();
          }
        }}
      />
      <select aria-label="Audience" bind:value={audience}>
        {#each AUDIENCES as a (a)}<option value={a}>{a === 'ai' ? 'AI' : a[0].toUpperCase() + a.slice(1)}</option>{/each}
      </select>
      <span class="inline">
        <button class="primary" type="submit" disabled={!draft.trim()}>Add section</button>
        <button class="link" type="button" onclick={cancel}>Cancel</button>
      </span>
    </form>
  {:else if canAddSection}
    <button class="nav-new-button" onclick={() => start('new')}><Icon name="plus" size={13} />New section</button>
  {/if}

  {#if showArchived}
    <div class="nav-archived" aria-label="Archived">
      {#if !board.archivedSections.length && !board.archivedLists.length}
        <p class="muted small">Nothing archived.</p>
      {/if}
      {#each board.archivedSections as s (s.id)}
        <div class="nav-row">
          <span class="muted">{s.name} <span class="small">(section)</span></span>
          <button class="link small" onclick={() => board.setSectionArchived(s.id, false)}>Restore</button>
        </div>
      {/each}
      {#each board.archivedLists as l (l.id)}
        <div class="nav-row">
          <span class="muted">{l.name}</span>
          <button class="link small" onclick={() => board.setListArchived(l.id, false)}>Restore</button>
        </div>
      {/each}
    </div>
  {/if}

  <div class="nav-foot">
    <span class="me" title={board.me?.account.display_name ?? ''}>
      <span class="avatar me-avatar" aria-hidden="true">{initials(board.me?.account.display_name ?? '')}</span>
      <span class="me-text">
        <span class="me-name">{board.me?.account.display_name ?? ''}</span>
        {#if board.token === COOKIE_SESSION && board.me?.session_expires_at}
          <!-- A guest's browser session: the date it ends, so day 31 is not a surprise. -->
          <span class="me-ends" title="Ask the person who invited you for a new link before then, or they can extend it">Access ends {shortDate(board.me.session_expires_at)}</span>
        {/if}
      </span>
    </span>
    <span class="foot-tools">
      <button
        class="foot-btn"
        aria-label="Notifications"
        title="Notifications"
        onclick={() => (notifySettings.open = true)}><Icon name="bell" size={15} /></button
      >
      <button class="foot-btn" aria-label="Tags" title="Tags" onclick={() => board.openTagManager()}
        ><Icon name="tag" size={15} /></button
      >
      <button
        class="foot-btn"
        class:on={showArchived}
        aria-label="Archived"
        title="Archived"
        aria-expanded={showArchived}
        onclick={toggleArchived}><Icon name="archive" size={15} /></button
      >
      <button class="foot-btn" aria-label="Sign out" title="Sign out" onclick={() => board.logout()}
        ><Icon name="logout" size={15} /></button
      >
    </span>
  </div>
</nav>

{#if board.listSettingsId && board.lists[board.listSettingsId]}
  <ListSettings list={board.lists[board.listSettingsId]} />
{/if}
