<script lang="ts">
  import { trapFocus } from '../lib/focustrap';
  let { onclose }: { onclose: () => void } = $props();
  const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
  const keys: [string, string][] = [
    [`${mod} + K`, 'Search everything and run commands'],
    [`${mod} + Z`, 'Undo the last change'],
    ['j / k', 'Next / previous task on the page'],
    ['Enter', 'Open the task under the cursor'],
    ['e', 'Complete the open task (or the one under the cursor)'],
    ['g h', 'Go to My Work'],
    ['g i', 'Go to Inbox'],
    ['g e', 'Go to Everything'],
    ['g p', 'Go to Docs'],
    ['g m', 'Go to Chat'],
    ['/', 'Search this view'],
    ['c', 'New item (quick add)'],
    ['b', 'Board ↔ List'],
    ['f', 'Filter'],
    ['x', 'Select the card under the pointer'],
    ['s', 'Star the open task (or the card under the pointer) to the favorites bar'],
    ['Shift + click', 'Select a range'],
    ['Esc', 'Close the task window, menu or selection'],
    ['?', 'This sheet'],
  ];
</script>

<button class="drawer-scrim" aria-label="Close shortcuts" onclick={onclose}></button>
<div class="sheet" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts" use:trapFocus={{ onescape: onclose }}>
  <h3>Keyboard shortcuts</h3>
  <dl class="keys">
    {#each keys as [k, what] (k)}<dt><kbd>{k}</kbd></dt><dd>{what}</dd>{/each}
  </dl>
  <p class="muted small">Quick add understands <code>tomorrow 4pm</code>, <code>next fri</code>, <code>in 3 days</code>, <code>!1</code>…<code>!4</code>, <code>~list</code> and <code>every monday</code>; put <code>\</code> before a word to keep it as text.</p>
  <p class="muted small">Monitor mode: add <code>?kiosk=1</code> before the <code>#</code> in the address.</p>
</div>
