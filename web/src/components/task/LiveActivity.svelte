<script lang="ts">
  // What the agent is doing right now, under "is typing…": its run's latest lines in a box of a
  // fixed height that scrolls only itself, so a new line never moves the comments above. It
  // follows the newest line unless the reader scrolled back inside it.
  import { tick } from 'svelte';
  import { lineTime, type LiveLine } from '../../lib/liveactivity.svelte';

  // `tall` + `label`: the same box under an agent's answer, for a run that is over (its whole
  // log, a little taller, starting at the top).
  let { lines, label = 'What the agent is doing now', tall = false }: { lines: LiveLine[]; label?: string; tall?: boolean } =
    $props();

  let box = $state<HTMLElement>();
  let following = true;
  $effect(() => {
    void lines.length;
    void lines.at(-1)?.at;
    if (following && !tall) void tick().then(() => box && (box.scrollTop = box.scrollHeight));
  });
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (a scrolling region must be reachable by keyboard) -->
<div
  class="live-lines"
  class:tall
  data-live-activity
  role="log"
  aria-label={label}
  aria-live="off"
  tabindex="0"
  bind:this={box}
  onscroll={() => box && (following = box.scrollHeight - box.scrollTop - box.clientHeight < 12)}
>
  {#each lines as l, i (i + l.at + l.text)}
    <div class="line" title={l.text}><time datetime={l.at}>{lineTime(l.at)}</time><span>{l.text}</span></div>
  {/each}
</div>

<style>
  .live-lines {
    /* About four times the typing line, whatever the number of lines: the feed never jumps. */
    height: 104px;
    margin-top: 8px;
    padding: 6px 8px;
    overflow-y: auto;
    overscroll-behavior: contain;
    overflow-anchor: none;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--card);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    color: var(--muted);
  }
  .live-lines.tall {
    height: auto;
    max-height: 220px;
  }
  .live-lines.tall .line span {
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .live-lines.tall .line:last-child span {
    color: inherit;
  }
  .line {
    display: flex;
    gap: 8px;
    min-width: 0;
  }
  .line time {
    flex: none;
    opacity: 0.7;
    font-variant-numeric: tabular-nums;
  }
  .line span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .line:last-child span {
    color: var(--text);
  }
</style>
