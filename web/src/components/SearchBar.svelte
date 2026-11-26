<script lang="ts">
  import Icon from './Icon.svelte';

  let { onsearch = () => {} }: { onsearch?: () => void } = $props();
  const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
</script>

<button
  class="universal-search"
  id="palette-open"
  type="button"
  aria-label="Search everything"
  onclick={onsearch}
>
  <span class="universal-search-icon" aria-hidden="true"><Icon name="search" size={16} /></span>
  <span class="universal-search-text">Search tasks, comments, docs, chat, or #ID…</span>
  <kbd>{mod} K</kbd>
</button>
