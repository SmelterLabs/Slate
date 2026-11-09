<script lang="ts">
  // The message box under a channel or a thread. Enter sends (Shift+Enter for a new line);
  // `@` picks a person or agent to mention; `#` picks a task (the message then shows it as a
  // card), a page or a list to link (`[[doc:<id>]]` / `[[list:<id>]]`, rendered as the page's
  // title or the list's name). Mentioned accounts hear it in their inbox, check-in and phone.
  import { insertMention, mentionAt } from '../../lib/activity';
  import { hashPicks, insertHashPick, taskRefAt, type HashPick } from '../../lib/chat';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { chat } from '../../lib/chat.svelte';
  import { docs } from '../../lib/docs.svelte';
  import { board } from '../../lib/store.svelte';
  import type { Account } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from '../Icon.svelte';

  let {
    channel,
    parentId = null,
    placeholder = 'Message',
    autofocus = false,
  }: { channel: string; parentId?: string | null; placeholder?: string; autofocus?: boolean } = $props();

  const draft = persisted(() => draftKey.chat(channel, parentId));
  let area = $state<HTMLTextAreaElement>();
  let mention = $state<{ start: number; query: string } | null>(null);
  let taskRef = $state<{ start: number; query: string } | null>(null);
  let active = $state(0);

  const people = $derived.by(() => {
    if (!mention) return [] as Account[];
    const q = mention.query;
    return board.pickable
      .filter((a) => (!q || a.handle.startsWith(q) || a.display_name.toLowerCase().includes(q)))
      .sort(
        (a, b) =>
          Number(!a.handle.startsWith(q)) - Number(!b.handle.startsWith(q)) || a.display_name.localeCompare(b.display_name),
      )
      .slice(0, 6);
  });

  // `#`: tasks, pages and lists, grouped (lib/chat.ts hashPicks); `picks` is the flat list the
  // arrow keys walk.
  const groups = $derived.by(() => {
    if (!taskRef) return [];
    const list = channel.startsWith('list:') ? board.lists[channel.slice(5)] : undefined;
    const sectionId = list?.section_id ?? (channel.startsWith('section:') ? channel.slice(8) : null);
    return hashPicks(taskRef.query, {
      tasks: board.plainItems,
      docs: docs.list,
      lists: Object.values(board.lists),
      here: { listId: list?.id ?? null, sectionId },
      listName: (id) => board.lists[id]?.name,
      sectionName: (id) => board.sections.find((s) => s.id === id)?.name,
    });
  });
  const picks = $derived(groups.flatMap((g) => g.picks));

  const picking = $derived(people.length ? 'people' : picks.length ? 'refs' : null);
  const count = $derived(picking === 'people' ? people.length : picks.length);

  $effect(() => {
    if (autofocus) queueMicrotask(() => area?.focus());
  });

  export function focus() {
    area?.focus();
  }

  // A restored draft (or a switch to another card's draft) sizes the box to fit it.
  $effect(() => {
    void draft.value;
    autosize();
  });

  function autosize() {
    if (!area) return;
    area.style.height = 'auto';
    area.style.height = `${Math.min(area.scrollHeight + 2, 200)}px`;
  }

  function track() {
    if (!area) return;
    mention = mentionAt(draft.value, area.selectionStart);
    taskRef = mention ? null : taskRefAt(draft.value, area.selectionStart);
    active = 0;
  }

  function place(out: { text: string; caret: number }) {
    draft.value = out.text;
    mention = null;
    taskRef = null;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(out.caret, out.caret);
    });
  }

  function pickPerson(a: Account) {
    if (mention && area) place(insertMention(draft.value, mention.start, area.selectionStart, a.handle));
  }

  function pickRef(p: HashPick) {
    if (taskRef && area) place(insertHashPick(draft.value, taskRef.start, area.selectionStart, p));
  }

  async function send() {
    const body = draft.value.trim();
    if (!body) return;
    draft.value = '';
    queueMicrotask(autosize);
    if (!(await chat.send(channel, body, parentId))) draft.value = body;
  }

  function keys(e: KeyboardEvent) {
    if (picking) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : count - 1)) % count;
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        if (picking === 'people') pickPerson(people[active]);
        else pickRef(picks[active]);
        return;
      }
      if (e.key === 'Escape') {
        e.stopPropagation();
        mention = null;
        taskRef = null;
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void send();
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      area?.blur();
    }
  }

  function insert(ch: string) {
    draft.value = `${draft.value}${draft.value && !/\s$/.test(draft.value) ? ' ' : ''}${ch}`;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(draft.value.length, draft.value.length);
      track();
    });
  }
</script>

<div class="chat-composer">
  {#if mention}<p class="muted small" role="note">Directory suggestions do not confirm access to this channel. Check sharing before relying on a mention; mentioning someone does not grant access.</p>{/if}
  {#if picking === 'people'}
    <ul class="chat-pick" role="listbox" aria-label="Mention someone">
      {#each people as a, i (a.id)}
        <li role="option" aria-selected={i === active}>
          <button onmousedown={(e) => e.preventDefault()} onclick={() => pickPerson(a)}>
            <Avatars people={[a]} />
            <span>{a.display_name}</span>
            <span class="muted small">@{a.handle}{a.kind === 'agent' ? ' · agent' : ''}</span>
          </button>
        </li>
      {/each}
    </ul>
  {:else if picking === 'refs'}
    <ul class="chat-pick" role="listbox" aria-label="Link a task, page or list">
      {#each groups as g (g.kind)}
        <li class="chat-pick-group" role="presentation" aria-hidden="true">{g.label}</li>
        {#each g.picks as p (p.id)}
          <li role="option" aria-selected={p.index === active} data-kind={p.kind}>
            <button onmousedown={(e) => e.preventDefault()} onclick={() => pickRef(p)}>
              {#if p.kind === 'task'}<Icon name="check" size={14} />{:else if p.kind === 'doc' && p.icon}<span class="chat-pick-emoji" aria-hidden="true">{p.icon}</span>{:else}<Icon name={p.kind === 'doc' ? 'page' : 'list'} size={14} />{/if}
              <span class="grow">{p.title}</span>
              <span class="muted small">{p.sub}</span>
            </button>
          </li>
        {/each}
      {/each}
    </ul>
  {/if}
  <textarea
    bind:this={area}
    bind:value={draft.value}
    rows="1"
    aria-label={placeholder}
    {placeholder}
    oninput={() => {
      track();
      autosize();
    }}
    onclick={track}
    onkeydown={keys}
    onkeyup={(e) => (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && track()}
    onblur={() =>
      setTimeout(() => {
        mention = null;
        taskRef = null;
      }, 150)}
  ></textarea>
  <div class="chat-composer-bar">
    <button class="icon" type="button" title="Mention someone" aria-label="Mention someone" onclick={() => insert('@')}
      ><Icon name="at" size={15} /></button
    >
    <button class="icon" type="button" title="Link a task, page or list" aria-label="Link a task, page or list" onclick={() => insert('#')}
      ><Icon name="hash" size={15} /></button
    >
    <span class="hint muted">Enter to send · Shift+Enter for a new line</span>
    <button class="send" disabled={!draft.value.trim()} onclick={send} aria-label="Send"><Icon name="send" size={14} /></button>
  </div>
</div>
