<script lang="ts">
  // A markdown box that saves itself: no Edit button. Click to type; it saves a moment after
  // you stop (and when you leave the box or close the task), and says so. When you are not in
  // it, it shows the rendered markdown; `- [ ]` boxes there tick straight away.
  import { onDestroy, untrack } from 'svelte';
  import { highlightMentions, toggleTaskLine } from '../../lib/activity';
  import { renderMarkdown } from '../../lib/markdown';
  import { board } from '../../lib/store.svelte';

  let {
    value,
    onsave,
    label,
    placeholder = 'Add a description',
    big = false,
    onpaste = undefined,
  }: {
    value: string;
    onsave: (text: string) => Promise<boolean>;
    label: string;
    placeholder?: string;
    big?: boolean;
    onpaste?: (files: File[]) => void;
  } = $props();

  const DELAY = 1200;

  let draft = $state(untrack(() => value));
  let editing = $state(false);
  let saved = $state(untrack(() => value)); // what the server last accepted from us
  let phase = $state<'idle' | 'pending' | 'saving' | 'saved' | 'error'>('idle');
  let area = $state<HTMLTextAreaElement>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let savedTimer: ReturnType<typeof setTimeout> | undefined;

  // Someone else (or another tab) changed it: take theirs unless we are mid-edit.
  $effect(() => {
    const v = value;
    if (!editing && phase !== 'pending' && phase !== 'saving' && v !== draft) {
      draft = v;
      saved = v;
    }
  });

  const html = $derived(
    highlightMentions(
      renderMarkdown(draft, { taskBoxes: true }),
      (h) => Object.values(board.accounts).some((a) => a.handle === h),
      board.me?.account.handle,
    ),
  );

  function autosize() {
    if (!area) return;
    area.style.height = 'auto';
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  async function flush() {
    clearTimeout(timer);
    if (draft === saved) {
      if (phase === 'pending') phase = 'idle';
      return;
    }
    const text = draft;
    phase = 'saving';
    const ok = await onsave(text);
    if (ok) {
      saved = text;
      phase = draft === text ? 'saved' : 'pending';
      clearTimeout(savedTimer);
      savedTimer = setTimeout(() => phase === 'saved' && (phase = 'idle'), 2500);
      if (phase === 'pending') schedule();
    } else {
      phase = 'error';
    }
  }

  function schedule() {
    clearTimeout(timer);
    phase = 'pending';
    timer = setTimeout(() => void flush(), DELAY);
  }

  function start(e?: MouseEvent) {
    const t = e?.target as HTMLElement | undefined;
    if (t?.closest('a')) return; // links open
    if (t instanceof HTMLInputElement && t.dataset.line !== undefined) {
      // A task box in the rendered view: tick it and save at once.
      draft = toggleTaskLine(draft, Number(t.dataset.line));
      void flush();
      return;
    }
    editing = true;
    queueMicrotask(() => {
      area?.focus();
      autosize();
    });
  }

  function wrap(before: string, after = before) {
    if (!area) return;
    const { selectionStart: a, selectionEnd: b } = area;
    const inner = draft.slice(a, b) || (after === ')' ? 'link' : 'text');
    draft = draft.slice(0, a) + before + inner + after + draft.slice(b);
    schedule();
    queueMicrotask(() => {
      area?.setSelectionRange(a + before.length, a + before.length + inner.length);
      autosize();
    });
  }

  function keys(e: KeyboardEvent) {
    const mod = e.metaKey || e.ctrlKey;
    if (e.key === 'Escape' || (mod && e.key === 'Enter')) {
      e.preventDefault();
      e.stopPropagation();
      area?.blur();
    } else if (mod && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      wrap('**');
    } else if (mod && e.key.toLowerCase() === 'i') {
      e.preventDefault();
      wrap('_');
    } else if (mod && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      wrap('[', '](https://)');
    } else if (e.key === 'Enter' && !e.shiftKey && area) {
      // Carry a list on: "- ", "1. " and "- [ ] " continue onto the next line.
      const pos = area.selectionStart;
      const line = draft.slice(draft.lastIndexOf('\n', pos - 1) + 1, pos);
      const m = /^(\s*)([-*+] \[[ x]\] |[-*+] |(\d+)[.)] )(.*)$/i.exec(line);
      if (!m) return;
      e.preventDefault();
      if (!m[4].trim()) {
        // An empty bullet ends the list.
        draft = draft.slice(0, pos - line.length) + draft.slice(pos);
        queueMicrotask(() => area?.setSelectionRange(pos - line.length, pos - line.length));
      } else {
        const bullet = m[3] ? `${Number(m[3]) + 1}. ` : m[2].replace(/\[x\]/i, '[ ]');
        const insert = `\n${m[1]}${bullet}`;
        draft = draft.slice(0, pos) + insert + draft.slice(area.selectionEnd);
        queueMicrotask(() => area?.setSelectionRange(pos + insert.length, pos + insert.length));
      }
      schedule();
      queueMicrotask(autosize);
    }
  }

  function paste(e: ClipboardEvent) {
    const files = [...(e.clipboardData?.files ?? [])];
    if (files.length && onpaste) {
      e.preventDefault();
      onpaste(files);
    }
  }

  // Closing the task or switching to another one must not lose the last keystrokes.
  onDestroy(() => {
    if (draft !== saved) void onsave(draft);
    clearTimeout(timer);
    clearTimeout(savedTimer);
  });
</script>

<div class="autotext" class:big class:editing>
  {#if editing || !draft.trim()}
    <textarea
      bind:this={area}
      aria-label={label}
      {placeholder}
      rows={big ? 4 : 2}
      bind:value={draft}
      oninput={() => {
        schedule();
        autosize();
      }}
      onfocus={() => {
        editing = true;
        autosize();
      }}
      onblur={() => {
        editing = false;
        void flush();
      }}
      onkeydown={keys}
      onpaste={paste}
    ></textarea>
  {:else}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      class="md rendered"
      role="textbox"
      tabindex="0"
      aria-label={label}
      aria-multiline="true"
      onclick={start}
      onfocus={() => start()}
    >
      {@html html}
    </div>
  {/if}
  <div class="state" aria-live="polite">
    {#if phase === 'saving' || phase === 'pending'}
      <span class="muted">Saving…</span>
    {:else if phase === 'saved'}
      <span class="ok">✓ Saved</span>
    {:else if phase === 'error'}
      <button class="retry" onclick={() => void flush()}>Not saved · Retry</button>
    {:else if editing}
      <span class="muted">Markdown · Ctrl+B bold · Ctrl+K link · Esc to finish</span>
    {/if}
  </div>
</div>

<style>
  .autotext {
    position: relative;
  }
  textarea,
  .rendered {
    width: 100%;
    min-height: 44px;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 10px 12px;
    background: transparent;
    line-height: 1.55;
    font-size: 14px;
    resize: none;
    overflow: hidden;
  }
  .big textarea,
  .big .rendered {
    min-height: 96px;
  }
  textarea {
    border-color: var(--line);
    background: var(--panel);
  }
  textarea:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
  }
  .rendered {
    cursor: text;
    word-wrap: break-word;
  }
  .rendered:hover {
    border-color: var(--line);
  }
  .rendered :global(p:first-child) {
    margin-top: 0;
  }
  .rendered :global(p:last-child) {
    margin-bottom: 0;
  }
  .rendered :global(.md-task) {
    margin: 0 4px 0 0;
    vertical-align: -1px;
    cursor: pointer;
  }
  .rendered :global(ul) {
    padding-left: 22px;
  }
  .state {
    min-height: 18px;
    font-size: 11.5px;
    padding: 2px 4px 0;
    text-align: right;
  }
  .ok {
    color: var(--kind-done);
  }
  .retry {
    border: 0;
    background: none;
    color: var(--danger);
    font-size: 11.5px;
    padding: 0;
  }
</style>
