<script lang="ts">
  // The message box under a channel or a thread. Enter sends (Shift+Enter for a new line);
  // `@` picks a person or agent to mention; `#` picks a task (the message then shows it as a
  // card), a page or a list to link (`[[doc:<id>]]` / `[[list:<id>]]`, rendered as the page's
  // title or the list's name). Mentioned accounts hear it in their inbox, check-in and phone.
  // Files: paste (a screenshot from the clipboard), drop, or pick with the paperclip. Each
  // uploads at once and waits above the box as a chip; Send posts them with the message, and a
  // message can be files alone.
  import { fileSize, insertMention, mentionAt } from '../../lib/activity';
  import { hashPicks, insertHashPick, taskRefAt, type ChatFile, type HashPick } from '../../lib/chat';
  import { untrack } from 'svelte';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { isSendKey, mentionCandidates, pickerKey } from '../../lib/mentions.svelte';
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

  // Files waiting to go with the next message, and how many are still uploading. They belong
  // to this box's channel: moving to another channel takes back what was not sent.
  let files = $state<ChatFile[]>([]);
  let uploading = $state(0);
  let dragging = $state(false);
  let picker = $state<HTMLInputElement>();
  const MAX_FILES = 10;
  let boxKey = $derived(`${channel}|${parentId ?? ''}`);
  $effect(() => {
    void boxKey;
    return () => {
      for (const f of untrack(() => files)) void chat.discard(f);
      files = [];
    };
  });

  async function attach(list: Iterable<File>) {
    for (const file of list) {
      if (files.length + uploading >= MAX_FILES) {
        board.flash(`A message carries at most ${MAX_FILES} files.`);
        break;
      }
      const at = boxKey;
      uploading += 1;
      const f = await chat.upload(channel, file);
      uploading -= 1;
      if (!f) continue;
      // The box moved to another channel while this uploaded: it is not that channel's file.
      if (at !== boxKey) void chat.discard(f);
      else files = [...files, f];
    }
    area?.focus();
  }

  function unattach(f: ChatFile) {
    files = files.filter((x) => x.id !== f.id);
    void chat.discard(f);
  }

  // A screenshot on the clipboard arrives as a file with no text; text pastes as ever.
  function paste(e: ClipboardEvent) {
    const pasted = [...(e.clipboardData?.files ?? [])];
    if (!pasted.length) return;
    e.preventDefault();
    void attach(pasted);
  }

  function hasFiles(e: DragEvent): boolean {
    return [...(e.dataTransfer?.types ?? [])].includes('Files');
  }

  function drop(e: DragEvent) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragging = false;
    void attach([...(e.dataTransfer?.files ?? [])]);
  }

  const people = $derived(mention ? mentionCandidates(board.pickable, mention.query) : []);

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

  const ready = $derived(!uploading && (!!draft.value.trim() || files.length > 0));

  async function send() {
    const body = draft.value.trim();
    if (uploading || (!body && !files.length)) return;
    const sending = files;
    draft.value = '';
    files = [];
    queueMicrotask(autosize);
    if (!(await chat.send(channel, body, parentId, sending))) {
      draft.value = body;
      files = sending;
    }
  }

  function keys(e: KeyboardEvent) {
    const k = picking ? pickerKey(e, active, count) : null;
    if (k === 'close') {
      e.stopPropagation();
      mention = null;
      taskRef = null;
      return;
    }
    if (k) {
      e.preventDefault();
      if (k !== 'pick') active = k.active;
      else if (picking === 'people') pickPerson(people[active]);
      else pickRef(picks[active]);
      return;
    }
    if (isSendKey(e)) {
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

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="chat-composer"
  class:dragging
  ondragover={(e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragging = true;
  }}
  ondragleave={(e) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) dragging = false;
  }}
  ondrop={drop}
>
  <input
    bind:this={picker}
    type="file"
    multiple
    hidden
    aria-label="Attach files"
    onchange={(e) => {
      const input = e.currentTarget;
      void attach([...(input.files ?? [])]).then(() => (input.value = ''));
    }}
  />
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
  {#if files.length || uploading}
    <ul class="chat-attach" aria-label="Files to send">
      {#each files as f (f.id)}
        <li class:image={!!chat.thumbs[f.id]}>
          {#if chat.thumbs[f.id]}
            <img src={chat.thumbs[f.id]} alt={f.filename} title={f.filename} />
          {:else}
            <Icon name="clip" size={14} />
            <span class="chat-attach-name" title={f.filename}>{f.filename}</span>
            <span class="muted small">{fileSize(f.size)}</span>
          {/if}
          <button class="chat-attach-x" type="button" aria-label="Remove {f.filename}" title="Remove" onclick={() => unattach(f)}
            ><Icon name="x" size={12} /></button
          >
        </li>
      {/each}
      {#if uploading}<li class="chat-attach-busy muted small" role="status">Uploading…</li>{/if}
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
    onpaste={paste}
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
    <button class="icon" type="button" title="Attach a file (or paste a screenshot)" aria-label="Attach a file" onclick={() => picker?.click()}
      ><Icon name="clip" size={15} /></button
    >
    <span class="hint muted">Enter to send · Shift+Enter for a new line</span>
    <button class="send" disabled={!ready} onclick={send} aria-label="Send"><Icon name="send" size={14} /></button>
  </div>
</div>
