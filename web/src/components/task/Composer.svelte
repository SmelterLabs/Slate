<script lang="ts">
  // The comment box at the foot of the activity column. Enter sends (Shift+Enter for a new
  // line); `@` opens a picker of people and agents; files can be attached from here too:
  // the paperclip, or paste a screenshot (or drop a file) on the box. It becomes a file on the
  // task and waits above the box as a thumbnail; Send adds a `📎 <filename>` line per file to
  // the comment, which the activity column shows as the picture (lib/activity `commentFiles`)
  // and an agent reads as the name of the file meant.
  import { untrack } from 'svelte';
  import { fileLine, fileSize } from '../../lib/activity';
  import { askFor } from '../../lib/conversation';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { isSendKey, MentionPicker } from '../../lib/mentions.svelte';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Account, Attachment } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from './Icon.svelte';

  let {
    itemId,
    onattach,
    askDefault = '',
  }: { itemId: string; onattach: () => void; askDefault?: string } = $props();

  const draft = persisted(() => draftKey.comment(itemId));
  let area = $state<HTMLTextAreaElement>();
  let sending = $state(false);
  const picker = new MentionPicker(() => board.pickable);
  /** Ask: the handle this comment is addressed to ('' = a plain comment). */
  let ask = $state('');
  /** The viewer picked someone (or "no one") by hand: the default stops following the card. */
  let picked = $state(false);
  let shownItem = '';
  $effect(() => {
    if (itemId !== shownItem) {
      shownItem = itemId;
      picked = false;
    }
    // An agent @mentioned in the text is who the question is for ("@my-agent can you…"); a
    // comment that @mentions only people is a plain comment, not an Ask to the default agent.
    if (!picked) ask = askFor(draft.value, askable, askDefault);
  });
  // Asking yourself is never what you mean: the picker lists everyone else.
  const askable = $derived(board.pickable.filter((a) => a.id !== board.me?.account.id));
  const askName = $derived(board.pickable.find((a) => a.handle === ask)?.display_name ?? ask);

  /** Put "@handle " in the box (Reply on a comment uses this). */
  export function mentionPerson(handle: string) {
    const prefix = draft.value && !/\s$/.test(draft.value) ? `${draft.value} ` : draft.value;
    draft.value = `${prefix}@${handle} `;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(draft.value.length, draft.value.length);
      autosize();
    });
  }

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
    area.style.height = `${Math.min(area.scrollHeight + 2, 240)}px`;
  }

  function track() {
    if (area) picker.track(draft.value, area.selectionStart);
  }

  function pick(a: Account) {
    const out = area && picker.insert(draft.value, area.selectionStart, a);
    if (!out) return;
    draft.value = out.text;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(out.caret, out.caret);
    });
  }

  let attaching = $state(0);
  /** Files pasted or dropped here, already on the task, that go with the next comment. */
  let pending = $state<Attachment[]>([]);
  // Another card in the same window: its box starts with nothing waiting. (Compared by value:
  // the card's detail is replaced on every refresh, which is not another card.)
  let pendingFor = untrack(() => itemId);
  $effect.pre(() => {
    if (itemId === pendingFor) return;
    pendingFor = itemId;
    pending = [];
  });
  $effect(() => {
    for (const a of pending) void task.thumb(a);
  });

  /** Attach pasted or dropped files to the task; they wait above the box until Send. */
  async function attach(files: File[]) {
    const at = itemId;
    attaching += 1;
    const done = await task.upload(itemId, files);
    attaching -= 1;
    if (at === itemId) pending = [...pending, ...done];
    area?.focus();
  }

  /** Take a waiting file back: off the comment and off the task. */
  function unattach(a: Attachment) {
    pending = pending.filter((x) => x.id !== a.id);
    void task.removeAttachment(a.id);
  }

  // A screenshot on the clipboard arrives as a file with no text; text pastes as ever.
  function paste(e: ClipboardEvent) {
    const files = [...(e.clipboardData?.files ?? [])];
    if (!files.length) return;
    e.preventDefault();
    void attach(files);
  }

  function drop(e: DragEvent) {
    const files = [...(e.dataTransfer?.files ?? [])];
    if (!files.length) return;
    e.preventDefault();
    e.stopPropagation(); // the task window's own drop would attach them a second time
    void attach(files);
  }

  const ready = $derived(!attaching && (!!draft.value.trim() || pending.length > 0));

  async function send() {
    const text = draft.value.trim();
    if (!ready || sending) return;
    const body = [text, ...pending.map((a) => fileLine(a.filename))].filter(Boolean).join('\n');
    sending = true;
    if (await task.comment(itemId, body, ask || null)) {
      pending = [];
      if (ask && board.pickable.some((a) => a.handle === ask && a.kind === 'agent')) {
        try {
          localStorage.setItem('slate.lastAsk', ask);
        } catch {
          // private mode: the default just falls back
        }
      }
      draft.value = '';
      picked = false;
      ask = askDefault;
      queueMicrotask(autosize);
    }
    sending = false;
  }

  function keys(e: KeyboardEvent) {
    if (picker.keydown(e, pick)) return;
    if (isSendKey(e)) {
      e.preventDefault();
      void send();
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      area?.blur();
    }
  }
</script>

<div class="composer">
  {#if picker.open}
    <ul class="mentions" role="listbox" aria-label="Mention someone">
      {#each picker.people as a, i (a.id)}
        <li role="option" aria-selected={i === picker.active}>
          <!-- Arrow keys or the mouse, not Tab: the list closes a moment after the box loses focus. -->
          <button tabindex="-1" onmousedown={(e) => e.preventDefault()} onclick={() => pick(a)}>
            <Avatars people={[a]} />
            <span>{a.display_name}</span>
            <span class="muted small">@{a.handle}{a.kind === 'agent' ? ' · agent' : ''}</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
  {#if pending.length || attaching}
    <ul class="chat-attach" aria-label="Files to send">
      {#each pending as a (a.id)}
        <li class:image={!!task.thumbs[a.id]}>
          {#if task.thumbs[a.id]}
            <img src={task.thumbs[a.id]} alt={a.filename} title={a.filename} />
          {:else}
            <Icon name="clip" size={14} />
            <span class="chat-attach-name" title={a.filename}>{a.filename}</span>
            <span class="muted small">{fileSize(a.size)}</span>
          {/if}
          <button class="chat-attach-x" type="button" aria-label="Remove {a.filename}" title="Remove" onclick={() => unattach(a)}
            ><Icon name="x" size={12} /></button
          >
        </li>
      {/each}
      {#if attaching}<li class="chat-attach-busy muted small" role="status">Uploading…</li>{/if}
    </ul>
  {/if}
  <textarea
    bind:this={area}
    bind:value={draft.value}
    rows="2"
    aria-label="Add comment"
    placeholder="Comment, or @mention someone. Enter to send"
    oninput={() => {
      track();
      autosize();
    }}
    onclick={track}
    onkeydown={keys}
    onpaste={paste}
    ondrop={drop}
    onkeyup={(e) => (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && track()}
    onblur={() => setTimeout(() => picker.close(), 150)}
  ></textarea>
  <div class="bar">
    <button class="icon" type="button" title="Mention someone" aria-label="Mention someone" onclick={() => {
      draft.value = `${draft.value}${draft.value && !/\s$/.test(draft.value) ? ' ' : ''}@`;
      queueMicrotask(() => {
        area?.focus();
        area?.setSelectionRange(draft.value.length, draft.value.length);
        track();
      });
    }}>@</button>
    <button class="icon" type="button" title="Attach a file" aria-label="Attach a file" onclick={onattach}><Icon name="clip" size={15} /></button>
    <span class="hint muted">Shift+Enter for a new line</span>
    <select
      class="ask"
      aria-label="Ask"
      title="Ask someone: the comment stays open in their queue until they answer"
      bind:value={ask}
      onchange={() => (picked = true)}
    >
      <option value="">No one (comment)</option>
      {#each askable as a (a.id)}
        <option value={a.handle}>{a.display_name}{a.kind === 'agent' ? ' (agent)' : ''}</option>
      {/each}
    </select>
    <button class="send" disabled={!ready || sending} onclick={send}
      ><Icon name="send" size={14} /> {ask ? `Ask ${askName}` : 'Comment'}</button
    >
  </div>
</div>

<style>
  .composer {
    position: relative;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--panel);
    padding: 6px;
  }
  .composer:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent);
  }
  textarea {
    border: 0;
    background: none;
    resize: none;
    padding: 6px;
    min-height: 44px;
    font-size: 14px;
    line-height: 1.45;
  }
  textarea:focus {
    outline: none;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .bar .icon {
    border: 0;
    background: none;
    color: var(--muted);
    width: 28px;
    height: 28px;
    border-radius: 6px;
    display: inline-grid;
    place-items: center;
    padding: 0;
    font-weight: 600;
  }
  .bar .icon:hover {
    background: var(--card-hover);
    color: var(--text);
  }
  .hint {
    font-size: 11px;
    margin-left: 6px;
  }
  .ask {
    margin-left: auto;
    max-width: 140px;
    font-size: 12px;
    padding: 3px 6px;
  }
  .ask + .send {
    margin-left: 6px;
  }
  .send {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 0;
    border-radius: 6px;
    padding: 6px 12px;
    background: var(--accent);
    color: #fff;
    font-weight: 600;
    font-size: 13px;
  }
  .send:disabled {
    opacity: 0.45;
    cursor: default;
  }
  .mentions {
    position: absolute;
    bottom: calc(100% + 6px);
    left: 0;
    right: 0;
    margin: 0;
    padding: 4px;
    list-style: none;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgb(0 0 0 / 0.25);
    z-index: 30;
  }
  .mentions button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    border: 0;
    background: none;
    padding: 6px 8px;
    border-radius: 6px;
    text-align: left;
  }
  .mentions li[aria-selected='true'] button,
  .mentions button:hover {
    background: var(--card-hover);
  }
  @media (max-width: 760px) {
    .hint {
      display: none;
    }
  }
</style>
