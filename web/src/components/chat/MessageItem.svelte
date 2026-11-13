<script lang="ts">
  // One chat message: who and when (folded under the previous one by the same author), the body
  // with @mentions and task links, the files sent with it (images shown, the rest as chips; a
  // click opens the full-screen viewer), task cards for every task it names or became, reactions, the
  // thread summary, and a hover toolbar (react, reply in thread, make a task, edit, delete, copy
  // link). On touch screens the toolbar is always shown, so the phone has every action too.
  import { untrack } from 'svelte';
  import { QUICK_REACTIONS, fileSize, stamp } from '../../lib/activity';
  import { chatHref, renderBody, type ChatMessage, type LinkedItem } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import { exposure } from '../../lib/exposure';
  import { draftKey, readDraft } from '../../lib/drafts';
  import { saveDraft } from '../../lib/drafts.svelte';
  import { ago, dueLabel, isOverdue } from '../../lib/format';
  import { liveRefs } from '../../lib/refs.svelte';
  import { board } from '../../lib/store.svelte';
  import Avatars from '../Avatars.svelte';
  import Icon from '../Icon.svelte';
  import Menu, { type MenuEntry } from '../Menu.svelte';

  let {
    m,
    head = true,
    inThread = false,
    onthread = () => {},
    ontask = () => {},
  }: {
    m: ChatMessage;
    head?: boolean;
    inThread?: boolean;
    onthread?: (id: string) => void;
    ontask?: (m: ChatMessage) => void;
  } = $props();

  const me = $derived(chat.me);
  const author = $derived(board.accounts[m.author_id]);
  const name = $derived(author?.display_name ?? 'Someone');
  const mine = $derived(m.author_id === me);
  // An owner or admin may take anyone's message down (the server decides the same way:
  // service/chat.py delete); only the author edits.
  const admin = $derived(board.me?.account.role === 'owner' || board.me?.account.role === 'admin');
  const pending = $derived(m.id.startsWith('~'));
  const handle = $derived(board.me?.account.handle);
  const html = $derived(
    renderBody(
      m.body_md,
      (h) => Object.values(board.accounts).some((a) => a.handle === h),
      handle,
      (id) => board.items[id]?.title ?? m.linked_items.find((i) => i.id === id)?.title,
      liveRefs(),
    ),
  );
  // Older pages cached before files existed carry none.
  const files = $derived(m.files ?? []);
  $effect(() => {
    if (!m.deleted_at) for (const f of files) void chat.thumb(f);
  });
  const mentionsMe = $derived(!!handle && m.mentions.includes(handle) && !mine);

  // An edit left open (card closed, page reloaded) comes back open with what was typed.
  const editKey = draftKey.editMessage(untrack(() => m.id));
  let draft = $state(untrack(() => readDraft(editKey)));
  let editing = $state(untrack(() => !!draft));
  function setDraft(v: string) {
    draft = v;
    saveDraft(editKey, v === m.body_md ? '' : v);
  }
  function stopEdit() {
    editing = false;
    saveDraft(editKey, '');
  }
  let picking = $state(false);
  let menuOpen = $state(false);

  function card(l: LinkedItem) {
    const live = board.items[l.id];
    return {
      id: l.id,
      title: live?.title ?? l.title,
      status: live?.status?.name ?? l.status,
      done: live ? live.status?.kind === 'done' || live.status?.kind === 'cancelled' : l.done,
      due: live ? live.due_at : l.due_at,
      overdue: live ? isOverdue(live, board.now) : false,
      list: board.lists[live?.list_id ?? l.list_id]?.name,
      assignees: live?.assignees ?? [],
    };
  }

  function reactors(ids: string[]): string {
    return ids.map((id) => (id === me ? 'You' : (board.accounts[id]?.display_name ?? 'Someone'))).join(', ');
  }

  function bodyClick(e: MouseEvent) {
    const a = (e.target as HTMLElement).closest<HTMLElement>('[data-task]');
    if (a?.dataset.task) {
      e.preventDefault();
      void board.open(a.dataset.task);
    }
  }

  function startEdit() {
    draft = readDraft(editKey) || m.body_md;
    editing = true;
  }

  async function saveEdit() {
    const body = draft.trim();
    // A message with files may lose all its text; one without cannot be saved empty.
    if ((!body && !files.length) || body === m.body_md) {
      stopEdit();
      return;
    }
    if (await chat.edit(m, body)) stopEdit();
  }

  function editKeys(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void saveEdit();
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      stopEdit();
    }
  }

  async function copyLink() {
    const url = `${location.origin}${location.pathname}${chatHref(m.channel, m.parent_id ?? m.id)}`;
    try {
      await navigator.clipboard.writeText(url);
      board.flash('Link copied');
    } catch {
      board.flash(url);
    }
  }

  const entries = $derived.by((): MenuEntry[] => {
    const out: MenuEntry[] = [];
    if (!inThread || m.parent_id === null) out.push({ label: 'Reply in thread', onselect: () => onthread(m.parent_id ?? m.id) });
    if (!m.item_id) out.push({ label: 'Turn into task…', onselect: () => ontask(m) });
    out.push({ label: 'Copy link', onselect: () => void copyLink() });
    if (!mine) out.push({ label: 'Mark this message read', onselect: () => void chat.markMessageRead(m) });
    if (mine) {
      out.push({ label: 'Edit', onselect: startEdit });
      out.push({ label: 'Delete', danger: true, onselect: () => void chat.remove(m) });
    } else if (admin && !m.deleted_at) {
      out.push({ label: `Delete ${name}’s message`, danger: true, onselect: removeOthers });
    }
    return out;
  });

  function removeOthers() {
    if (confirm(`Delete ${name}’s message? It goes for good; the history keeps a record that it was deleted.`)) {
      void chat.remove(m);
    }
  }

  function focusInput(el: HTMLTextAreaElement) {
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }
</script>

<svelte:document onmousedown={(e) => picking && !(e.target as HTMLElement).closest('.chat-react-pop, .chat-react-add') && (picking = false)} />

<article
  class="chat-msg"
  use:exposure={() => void chat.expose(m)}
  class:head
  class:pending
  class:mentions-me={mentionsMe}
  class:menu-open={menuOpen || picking}
  data-message={m.id}
  aria-label="{name}: {m.deleted_at ? 'deleted message' : m.body_md.slice(0, 80) || files.map((f) => f.filename).join(', ')}"
>
  <div class="chat-gutter">
    {#if head}
      {#if author}<Avatars people={[author]} />{:else}<span class="avatar">?</span>{/if}
    {:else}
      <span class="chat-time-hover muted" title={new Date(m.created_at).toLocaleString()}
        >{stamp(m.created_at, board.now).replace(/^.* at /, '')}</span
      >
    {/if}
  </div>
  <div class="chat-content">
    {#if head}
      <div class="chat-meta">
        <span class="chat-author">{name}</span>
        {#if author?.kind === 'agent'}<span class="chat-agent"><Icon name="robot" size={11} />Agent</span>{/if}
        <span class="muted small" title={new Date(m.created_at).toLocaleString()}>{stamp(m.created_at, board.now)}</span>
      </div>
    {/if}
    {#if m.deleted_at}
      <p class="chat-deleted muted">This message was deleted.</p>
    {:else if editing}
      <div class="chat-edit">
        <textarea bind:value={() => draft, setDraft} rows="2" aria-label="Edit message" onkeydown={editKeys} use:focusInput></textarea>
        <span class="inline small">
          <button class="primary small" onclick={saveEdit}>Save</button>
          <button class="link small" onclick={stopEdit}>Cancel</button>
          <span class="muted">Enter to save · Esc to cancel</span>
        </span>
      </div>
    {:else}
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div class="chat-body md" onclick={bodyClick}>
        {@html html}{#if m.edited_at}<span class="muted small chat-edited"> (edited)</span>{/if}{#if m.members_only}<span class="muted small chat-edited" title="Guests on this project do not see this message"> · hidden from guests</span>{/if}
      </div>
    {/if}

    {#if !m.deleted_at && files.length}
      <ul class="chat-files" aria-label="Files">
        {#each files as f (f.id)}
          <li>
            {#if chat.thumbs[f.id]}
              <button class="chat-file-image" title="Open {f.filename}" aria-label="Open {f.filename}" onclick={() => chat.openFile(f, files)}>
                <img src={chat.thumbs[f.id]} alt={f.filename} loading="lazy" />
              </button>
            {:else}
              <button class="chat-file" title="Open {f.filename}" aria-label="Open {f.filename}" onclick={() => chat.openFile(f, files)}>
                <Icon name="clip" size={14} />
                <span class="chat-file-name">{f.filename}</span>
                <span class="muted small">{fileSize(f.size)}</span>
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}

    {#if !m.deleted_at && m.linked_items.length}
      <div class="chat-cards">
        {#each m.linked_items as l (l.id)}
          {@const c = card(l)}
          <button class="chat-task-card" class:done={c.done} onclick={() => void board.open(c.id)} data-task-card={c.id}>
            <span class="chat-task-check" aria-hidden="true"><Icon name={c.done ? 'check' : 'status'} size={14} /></span>
            <span class="chat-task-main">
              <span class="chat-task-title">{c.title}</span>
              <span class="chat-task-sub muted small">
                {#if m.item_id === c.id}<span class="chat-made">Made from this message</span>{' · '}{/if}{c.list ??
                  ''}{#if c.status}{' · '}{c.status}{/if}{#if c.due}{' · '}<span class:overdue={c.overdue}
                    >{dueLabel(c.due, board.now)}</span
                  >{/if}
              </span>
            </span>
            {#if c.assignees.length}<Avatars people={c.assignees} />{/if}
          </button>
        {/each}
      </div>
    {/if}

    {#if m.reactions.length}
      <div class="chat-reactions">
        {#each m.reactions as rx (rx.emoji)}
          <button
            class="chat-reaction"
            class:on={rx.account_ids.includes(me)}
            title={reactors(rx.account_ids)}
            aria-label="{rx.emoji} {rx.account_ids.length}, {reactors(rx.account_ids)}"
            onclick={() => chat.react(m, rx.emoji)}>{rx.emoji} {rx.account_ids.length}</button
          >
        {/each}
      </div>
    {/if}

    {#if !inThread && m.reply_count}
      <button class="chat-replies" onclick={() => onthread(m.id)}>
        <Avatars people={m.reply_author_ids.flatMap((id) => (board.accounts[id] ? [board.accounts[id]] : [])).slice(0, 3)} />
        <span class="chat-replies-n">{m.reply_count} {m.reply_count === 1 ? 'reply' : 'replies'}</span>
        {#if chat.channel(m.channel)?.unread_threads?.[m.id]}
          <strong>{chat.channel(m.channel)?.unread_threads?.[m.id]} unread replies</strong>
        {/if}
        {#if m.last_reply_at}<span class="muted small">Last reply {ago(m.last_reply_at, board.now)}</span>{/if}
      </button>
    {/if}
  </div>

  {#if !pending && !m.deleted_at && !editing}
    <div class="chat-tools" role="toolbar" aria-label="Message actions">
      {#each QUICK_REACTIONS.slice(0, 3) as e (e)}
        <button class="chat-tool" title="React {e}" aria-label="React {e}" onclick={() => chat.react(m, e)}>{e}</button>
      {/each}
      <span class="chat-react-wrap">
        <button class="chat-tool chat-react-add" title="Add reaction" aria-label="Add reaction" onclick={() => (picking = !picking)}
          ><Icon name="smile" size={15} /></button
        >
        {#if picking}
          <span class="chat-react-pop" role="menu">
            {#each QUICK_REACTIONS as e (e)}
              <button
                role="menuitem"
                onclick={() => {
                  picking = false;
                  void chat.react(m, e);
                }}>{e}</button
              >
            {/each}
          </span>
        {/if}
      </span>
      {#if !inThread}
        <button class="chat-tool" title="Reply in thread" aria-label="Reply in thread" onclick={() => onthread(m.id)}
          ><Icon name="reply" size={15} /></button
        >
      {/if}
      {#if !m.item_id}
        <button class="chat-tool" title="Turn into task" aria-label="Turn into task" onclick={() => ontask(m)}
          ><Icon name="checklist" size={15} /></button
        >
      {/if}
      <Menu label="More actions" {entries} bind:open={menuOpen} />
    </div>
  {/if}
</article>
