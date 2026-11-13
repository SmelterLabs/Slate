<script lang="ts">
  // The upload sheet: pick a file and say what changed. The note is required for everyone
  // (a deliberate rule): on a PDF or a drawing it is the only diff there is. Used for a new file
  // doc (the note defaults to "First upload") and for the next revision of one.
  import { fileSize } from '../../lib/activity';
  import Icon from '../Icon.svelte';
  import { trapFocus } from '../../lib/focustrap';

  let {
    title,
    file = null,
    defaultNote = '',
    busy = false,
    onsubmit,
    onclose,
  }: {
    title: string;
    /** A file already chosen (dropped or picked elsewhere); otherwise the sheet asks. */
    file?: File | null;
    defaultNote?: string;
    busy?: boolean;
    onsubmit: (file: File, note: string) => void;
    onclose: () => void;
  } = $props();

  let chosen = $state<File | null>(null);
  let note = $state('');
  let input = $state<HTMLInputElement>();
  $effect(() => {
    chosen = file;
    note = defaultNote;
  });
  const ready = $derived(!!chosen && note.trim().length > 0 && !busy);
</script>

<div class="picker-scrim" role="presentation" onclick={onclose}></div>
<div class="picker upload-sheet" role="dialog" aria-modal="true" aria-label={title} use:trapFocus={{ onescape: onclose }}>
  <header>{title}</header>
  <form
    class="upload-form"
    onsubmit={(e) => {
      e.preventDefault();
      if (chosen && note.trim()) onsubmit(chosen, note.trim());
    }}
  >
  <input
    bind:this={input}
    type="file"
    hidden
    aria-label="Choose a file"
    onchange={(e) => {
      const f = (e.currentTarget as HTMLInputElement).files?.[0];
      if (f) chosen = f;
    }}
  />
  <button type="button" class="upload-pick" class:has-file={!!chosen} onclick={() => input?.click()}>
    <Icon name="clip" size={16} />
    {#if chosen}
      <span class="upload-name">{chosen.name}</span><span class="muted small">{fileSize(chosen.size)}</span>
      <span class="link small">Change</span>
    {:else}
      <span>Choose a file…</span><span class="muted small">PDF, spreadsheet, photo, drawing · up to 25 MB</span>
    {/if}
  </button>
  <label class="upload-note">
    <span>What changed <span class="muted">(required)</span></span>
    <!-- svelte-ignore a11y_autofocus -->
    <input bind:value={note} maxlength="200" placeholder="e.g. Corrected the totals on page 2; new price on line 4" autofocus={!!file} required />
  </label>
  <footer>
    <button type="button" class="secondary" onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!ready}>{busy ? 'Uploading…' : 'Upload'}</button>
  </footer>
  </form>
</div>
