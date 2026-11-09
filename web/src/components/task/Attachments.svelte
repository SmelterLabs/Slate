<script lang="ts">
  // Files on the task: drop or paste anywhere in the window, or pick. Images show a
  // thumbnail; click opens images, videos and PDFs in the full-screen viewer and downloads
  // anything else.
  import { ago } from '../../lib/format';
  import { fileSize } from '../../lib/activity';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { ItemDetail } from '../../lib/types';
  import Icon from './Icon.svelte';

  let { item, picker = $bindable() }: { item: ItemDetail; picker?: HTMLInputElement } = $props();

  const files = $derived(item.attachments ?? []);

  $effect(() => {
    for (const a of files) void task.thumb(a);
  });

  function ext(name: string): string {
    const m = /\.([a-z0-9]{1,5})$/i.exec(name);
    return m ? m[1].toUpperCase() : 'FILE';
  }
</script>

<input
  bind:this={picker}
  type="file"
  multiple
  hidden
  aria-label="Attach files"
  onchange={(e) => {
    const input = e.currentTarget;
    void task.upload(item.id, [...(input.files ?? [])]).then(() => (input.value = ''));
  }}
/>

{#if files.length || task.uploading}
  <section class="block" aria-label="Attachments">
    <header>
      <h3><Icon name="clip" /> Attachments <span class="count">{files.length}</span></h3>
      <button class="link small" onclick={() => picker?.click()}>+ Add</button>
    </header>
    <ul class="grid">
      {#each files as a (a.id)}
        <li>
          <button class="thumb" title="Open {a.filename}" aria-label="Open {a.filename}" onclick={() => task.open(a)}>
            {#if task.thumbs[a.id]}
              <img src={task.thumbs[a.id]} alt={a.filename} />
            {:else}
              <span class="ext">{ext(a.filename)}</span>
            {/if}
          </button>
          <div class="info">
            <span class="name" title={a.filename}>{a.filename}</span>
            <span class="muted">{fileSize(a.size)} · {ago(a.created_at, board.now)}</span>
          </div>
          <button
            class="icon del"
            aria-label="Remove {a.filename}"
            title="Remove"
            onclick={() => confirm(`Remove ${a.filename}?`) && void task.removeAttachment(a.id)}
            ><Icon name="trash" size={13} /></button
          >
          <button
            class="icon promote"
            aria-label="Move {a.filename} to Docs & Files"
            title="Move to Docs & Files (a document with revisions)"
            onclick={() => void task.promoteAttachment(a, item.list_id)}
            ><Icon name="page" size={13} /></button
          >
        </li>
      {/each}
      {#if task.uploading}
        <li class="uploading muted small">Uploading {task.uploading}…</li>
      {/if}
    </ul>
  </section>
{/if}

<style>
  .grid {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 10px;
  }
  li {
    position: relative;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
    background: var(--panel);
  }
  .thumb {
    width: 100%;
    height: 96px;
    border: 0;
    padding: 0;
    background: var(--chip);
    display: grid;
    place-items: center;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .ext {
    font-weight: 700;
    font-size: 13px;
    color: var(--muted);
    letter-spacing: 0.06em;
  }
  .info {
    display: flex;
    flex-direction: column;
    padding: 6px 8px;
    font-size: 12px;
    gap: 2px;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12.5px;
  }
  .del {
    position: absolute;
    top: 4px;
    right: 4px;
    opacity: 0;
    border: 0;
    border-radius: 6px;
    background: var(--panel);
    color: var(--muted);
    padding: 4px;
  }
  .promote {
    position: absolute;
    top: 4px;
    right: 28px;
    opacity: 0;
    border: 0;
    border-radius: 6px;
    background: var(--panel);
    color: var(--muted);
    padding: 4px;
  }
  li:hover .del,
  .del:focus,
  li:hover .promote,
  .promote:focus {
    opacity: 1;
  }
  .uploading {
    display: grid;
    place-items: center;
    min-height: 140px;
  }
</style>
