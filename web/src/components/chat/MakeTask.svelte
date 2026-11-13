<script lang="ts">
  // "Turn into task": file one task from a chat message. The title starts as the message's
  // first line, the list as the channel's own list (or the first list for General and section
  // channels); the task quotes the message, and the message shows the task as a card.
  import { untrack } from 'svelte';
  import type { ChatMessage } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import { board } from '../../lib/store.svelte';
  import { trapFocus } from '../../lib/focustrap';

  let { m, onclose }: { m: ChatMessage; onclose: () => void } = $props();

  function firstLine(body: string): string {
    for (const line of body.split('\n')) {
      const t = line
        .replace(/^\s*(?:#+|[-*+]|\d+[.)])\s+/, '')
        .replace(/[*_`]/g, '')
        .trim();
      if (t) return t.slice(0, 200);
    }
    return 'Task from chat';
  }

  // The form starts from the message it was opened on.
  const first = untrack(() => m);
  const ownList = first.channel.startsWith('list:') ? first.channel.slice(5) : null;
  const sectionId = first.channel.startsWith('section:') ? first.channel.slice(8) : null;
  const choices = $derived(board.sections.flatMap((s) => board.listsOf(s.id).map((l) => ({ l, s }))));
  let title = $state(firstLine(first.body_md));
  let listId = $state(
    (ownList && board.lists[ownList] ? ownList : null) ??
      (sectionId ? board.listsOf(sectionId)[0]?.id : null) ??
      board.sections.flatMap((s) => board.listsOf(s.id))[0]?.id ??
      '',
  );
  let due = $state('');
  let assignMe = $state(false);
  let saving = $state(false);

  async function save(e: SubmitEvent) {
    e.preventDefault();
    if (!title.trim() || !listId || saving) return;
    saving = true;
    const item = await chat.makeTask(m, {
      list_id: listId,
      title: title.trim(),
      ...(due ? { due_at: due } : {}),
      ...(assignMe && board.me ? { assignees: [board.me.account.handle] } : {}),
    });
    saving = false;
    if (item) onclose();
  }

  function focus(el: HTMLInputElement) {
    el.focus();
    el.select();
  }
</script>

<!-- Escape closes it through trapFocus; a click on the scrim around the form closes it too. -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div
  class="chat-dialog-scrim"
  role="dialog"
  aria-modal="true"
  aria-label="Turn into task"
  tabindex="-1"
  onclick={(e) => e.target === e.currentTarget && onclose()}
  use:trapFocus={{ onescape: onclose }}
>
  <form class="chat-dialog" aria-label="Turn into task" onsubmit={save}>
    <h3>Turn into task</h3>
    <blockquote class="chat-quote">{m.body_md.length > 280 ? `${m.body_md.slice(0, 279)}…` : m.body_md}</blockquote>
    <label>
      <span>Title</span>
      <input bind:value={title} maxlength="500" use:focus aria-label="Task title" />
    </label>
    <label>
      <span>List</span>
      <select bind:value={listId} aria-label="List">
        {#each choices as c (c.l.id)}<option value={c.l.id}>{c.s.name} / {c.l.name}</option>{/each}
      </select>
    </label>
    <div class="chat-dialog-row">
      <label>
        <span>Due</span>
        <input type="date" bind:value={due} aria-label="Due date" />
      </label>
      <label class="chat-check">
        <input type="checkbox" bind:checked={assignMe} />
        <span>Assign to me</span>
      </label>
    </div>
    <div class="inline chat-dialog-actions">
      <button class="link" type="button" onclick={onclose}>Cancel</button>
      <button class="primary" type="submit" disabled={!title.trim() || !listId || saving}>Create task</button>
    </div>
  </form>
</div>
