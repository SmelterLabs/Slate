<script lang="ts">
  // The full-screen viewer for images, videos, PDFs and documents (Word, Excel, CSV, text:
  // DocumentPreview): open from a task's attachment thumbnail or a chat message's file, step
  // through that task's or message's files with the arrows or ← →, close with Escape, the × or
  // a click outside. Lives above the task window.
  import { fileSize } from '../../lib/activity';
  import { viewerKind } from '../../lib/docs';
  import { isDocumentKind } from '../../lib/office';
  import { task } from '../../lib/task.svelte';
  import DocumentPreview from '../files/DocumentPreview.svelte';
  import Icon from './Icon.svelte';

  const view = $derived(task.viewing);
  const all = $derived(task.media());
  const at = $derived(all.findIndex((x) => x.id === view?.a.id));
  const many = $derived(all.length > 1);
  const docKind = $derived.by(() => {
    if (!view) return null;
    const k = viewerKind(view.a.content_type, view.a.filename);
    return isDocumentKind(k) ? k : null;
  });

  let frame = $state<HTMLElement | undefined>();

  $effect(() => {
    if (view) frame?.focus();
  });

  function save() {
    if (!view) return;
    const link = document.createElement('a');
    link.href = view.url;
    link.download = view.a.filename;
    link.click();
  }

  function onKey(e: KeyboardEvent) {
    if (!view) return;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      e.stopPropagation();
      task.step(e.key === 'ArrowLeft' ? -1 : 1);
    }
  }
</script>

{#if view}
  <div
    bind:this={frame}
    class="viewer"
    role="dialog"
    aria-modal="true"
    aria-label="Viewing {view.a.filename}"
    tabindex="-1"
    onkeydown={onKey}
  >
    <header>
      <span class="name" title={view.a.filename}>{view.a.filename}</span>
      <span class="muted small">
        {fileSize(view.a.size)}{#if many} · {at + 1} of {all.length}{/if}
      </span>
      <span class="grow"></span>
      <button class="icon" aria-label="Download {view.a.filename}" title="Download" onclick={save}>
        <Icon name="download" size={18} />
      </button>
      <button class="icon" aria-label="Close viewer" title="Close (Esc)" onclick={() => task.closeViewer()}>
        <Icon name="x" size={20} />
      </button>
    </header>

    <button class="scrim" aria-label="Close viewer" tabindex="-1" onclick={() => task.closeViewer()}></button>

    {#if many}
      <button class="arrow prev" aria-label="Previous file" title="Previous (←)" onclick={() => task.step(-1)}>
        <Icon name="chevronLeft" size={28} />
      </button>
      <button class="arrow next" aria-label="Next file" title="Next (→)" onclick={() => task.step(1)}>
        <Icon name="chevronRight" size={28} />
      </button>
    {/if}

    <div class="stage" class:document={!!docKind}>
      {#if docKind}
        <div class="docbox">
          {#key view.a.id}
            <DocumentPreview blob={view.blob} kind={docKind} filename={view.a.filename} size={view.a.size} ondownload={save} />
          {/key}
        </div>
      {:else if view.a.content_type === 'application/pdf'}
        <iframe src={view.url} title={view.a.filename}></iframe>
      {:else if view.a.content_type.startsWith('video/')}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video src={view.url} controls autoplay playsinline></video>
      {:else}
        <img src={view.url} alt={view.a.filename} />
      {/if}
    </div>
  </div>
{/if}

<style>
  .viewer {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: grid;
    grid-template-rows: auto 1fr;
    background: rgba(8, 8, 12, 0.92);
    color: #f4f4f6;
    outline: none;
  }
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 16px;
    position: relative;
    z-index: 2;
    font-size: 14px;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
  }
  .viewer .muted {
    color: rgba(244, 244, 246, 0.6);
    white-space: nowrap;
  }
  .grow {
    flex: 1;
  }
  .viewer .icon,
  .arrow {
    border: 0;
    background: rgba(255, 255, 255, 0.08);
    color: inherit;
    border-radius: 8px;
    padding: 6px;
    display: grid;
    place-items: center;
    cursor: pointer;
  }
  .viewer .icon:hover,
  .arrow:hover {
    background: rgba(255, 255, 255, 0.18);
  }
  .scrim {
    position: absolute;
    inset: 0;
    border: 0;
    background: transparent;
    cursor: zoom-out;
  }
  .stage {
    position: relative;
    display: grid;
    place-items: center;
    min-height: 0;
    padding: 0 72px 24px;
    pointer-events: none;
  }
  .stage img,
  .stage video {
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    object-fit: contain;
    border-radius: 6px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
    pointer-events: auto;
  }
  .stage video {
    width: 100%;
    max-width: 1600px;
  }
  .stage iframe {
    width: 100%;
    height: 100%;
    max-width: 1400px;
    border: 0;
    border-radius: 6px;
    background: #fff;
    pointer-events: auto;
  }
  /* A document fills the stage like the PDF viewer does: full height, as wide as a sheet needs. */
  .docbox {
    width: 100%;
    height: 100%;
    max-width: 1600px;
    min-height: 0;
    pointer-events: auto;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
    border-radius: 6px;
    overflow: hidden;
  }
  .arrow {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    z-index: 2;
    padding: 12px 8px;
    border-radius: 999px;
  }
  .prev {
    left: 16px;
  }
  .next {
    right: 16px;
  }
  @media (max-width: 700px) {
    .stage {
      padding: 0 8px 8px;
    }
    .arrow {
      top: auto;
      bottom: 16px;
      transform: none;
    }
  }
</style>
