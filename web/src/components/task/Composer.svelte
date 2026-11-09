<script lang="ts">
  // The comment box at the foot of the activity column. Enter sends (Shift+Enter for a new
  // line); `@` opens a picker of people and agents; files can be attached from here too.
  import { insertMention, mentionAt } from '../../lib/activity';
  import { askFor } from '../../lib/conversation';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Account } from '../../lib/types';
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
  let mention = $state<{ start: number; query: string } | null>(null);
  let active = $state(0);
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

  const people = $derived.by(() => {
    if (!mention) return [] as Account[];
    const q = mention.query;
    return board.pickable
      .filter((a) => (!q || a.handle.startsWith(q) || a.display_name.toLowerCase().includes(q)))
      .sort((a, b) => Number(!a.handle.startsWith(q)) - Number(!b.handle.startsWith(q)) || a.display_name.localeCompare(b.display_name))
      .slice(0, 6);
  });

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
    if (!area) return;
    mention = mentionAt(draft.value, area.selectionStart);
    active = 0;
  }

  function pick(a: Account) {
    if (!mention || !area) return;
    const out = insertMention(draft.value, mention.start, area.selectionStart, a.handle);
    draft.value = out.text;
    mention = null;
    queueMicrotask(() => {
      area?.focus();
      area?.setSelectionRange(out.caret, out.caret);
    });
  }

  async function send() {
    const body = draft.value.trim();
    if (!body || sending) return;
    sending = true;
    if (await task.comment(itemId, body, ask || null)) {
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
    if (mention && people.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        active = (active + (e.key === 'ArrowDown' ? 1 : people.length - 1)) % people.length;
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        pick(people[active]);
        return;
      }
      if (e.key === 'Escape') {
        e.stopPropagation();
        mention = null;
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void send();
    } else if (e.key === 'Escape') {
      e.stopPropagation();
      area?.blur();
    }
  }
</script>

<div class="composer">
  {#if mention && people.length}
    <ul class="mentions" role="listbox" aria-label="Mention someone">
      {#each people as a, i (a.id)}
        <li role="option" aria-selected={i === active}>
          <button onmousedown={(e) => e.preventDefault()} onclick={() => pick(a)}>
            <Avatars people={[a]} />
            <span>{a.display_name}</span>
            <span class="muted small">@{a.handle}{a.kind === 'agent' ? ' · agent' : ''}</span>
          </button>
        </li>
      {/each}
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
    onkeyup={(e) => (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && track()}
    onblur={() => setTimeout(() => (mention = null), 150)}
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
    <button class="send" disabled={!draft.value.trim() || sending} onclick={send}
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
