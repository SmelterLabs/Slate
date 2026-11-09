<script lang="ts">
  // One revision of a file doc, shown the way its type allows: the browser's own PDF viewer,
  // an image, a Word or Excel file drawn by the board (files/DocumentPreview), text with line
  // numbers, a CSV/TSV as a table, or a download card for anything else (DWG, ZIP, PPTX…). Bytes are fetched once with the token (docs.fileUrl) because
  // <iframe> and <img> cannot send the Authorization header themselves.
  import { fileSize } from '../../lib/activity';
  import { parseTable, threadsOf, viewerKind, type DocRevision } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import DocumentPreview from '../files/DocumentPreview.svelte';
  import Icon from '../Icon.svelte';

  let { revision }: { revision: DocRevision } = $props();

  // Review pins: open threads anchored to a spot on this image (their revision shown too, so a
  // pin from an older revision reads as such). Click the image to start one.
  const pins = $derived(
    threadsOf(docs.comments, 'review')
      .filter((t) => !t.root.resolved_at && t.root.anchor_x !== null && t.root.anchor_y !== null)
      .map((t, i) => ({ n: i + 1, t })),
  );
  function pinHere(e: MouseEvent) {
    const el = e.currentTarget as HTMLElement;
    const r = el.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    docs.startReview({ x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 }, revision.revision_no);
  }

  const kind = $derived(viewerKind(revision.content_type, revision.filename));
  let url = $state<string | null>(null);
  let blob = $state<Blob | null>(null);
  let text = $state<string | null>(null);
  let failed = $state(false);
  const TEXT_LIMIT = 2 * 1024 * 1024;

  $effect(() => {
    const rev = revision;
    const k = kind;
    url = null;
    blob = null;
    text = null;
    failed = false;
    let live = true;
    if (k === 'docx' || k === 'xlsx') {
      docs.fileBlob(rev.id).then(
        (b) => live && (blob = b),
        () => live && (failed = true),
      );
    } else if (k === 'pdf' || k === 'image') {
      docs.fileUrl(rev.id).then(
        (u) => live && (url = u),
        () => live && (failed = true),
      );
    } else if ((k === 'text' || k === 'table') && rev.size <= TEXT_LIMIT) {
      docs.fileText(rev.id).then(
        (t) => live && (text = t),
        () => live && (failed = true),
      );
    }
    return () => {
      live = false;
    };
  });

  const rows = $derived(kind === 'table' && text !== null ? parseTable(text) : []);
  const lines = $derived(kind === 'text' && text !== null ? text.split('\n') : []);
</script>

<div class="file-view" data-kind={kind} data-revision={revision.revision_no}>
  {#if failed}
    <div class="file-card">
      <Icon name="alert" size={18} />
      <span>Could not load this file.</span>
      <button class="secondary" onclick={() => void docs.download(revision)}><Icon name="download" size={13} /> Download</button>
    </div>
  {:else if kind === 'pdf'}
    {#if url}
      <iframe class="file-pdf" src={url} title={revision.filename}></iframe>
    {:else}
      <div class="file-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div></div>
    {/if}
  {:else if kind === 'docx' || kind === 'xlsx'}
    {#if blob}
      <div class="file-document">
        <DocumentPreview {blob} {kind} filename={revision.filename} size={revision.size} ondownload={() => void docs.download(revision)} />
      </div>
    {:else}
      <div class="file-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div></div>
    {/if}
  {:else if kind === 'image'}
    {#if url}
      <div class="file-image-wrap">
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <img class="file-image" src={url} alt={revision.filename} title="Click to start a review thread here" onclick={pinHere} />
        {#each pins as p (p.t.root.id)}
          <button
            class="file-pin"
            class:stale={p.t.root.outdated}
            style="left:{(p.t.root.anchor_x ?? 0) * 100}%;top:{(p.t.root.anchor_y ?? 0) * 100}%"
            title={p.t.root.body_md}
            aria-label="Review thread {p.n}"
            data-pin={p.t.root.id}
            onclick={(e) => {
              e.stopPropagation();
              docs.focusComment = p.t.root.id;
            }}>{p.n}</button
          >
        {/each}
      </div>
    {:else}
      <div class="file-loading" aria-busy="true"><div class="sk-line w60"></div></div>
    {/if}
  {:else if kind === 'table'}
    {#if text === null}
      {#if revision.size > TEXT_LIMIT}
        <div class="file-card"><Icon name="info" size={18} /><span>Too large to show as a table.</span><button class="secondary" onclick={() => void docs.download(revision)}><Icon name="download" size={13} /> Download</button></div>
      {:else}
        <div class="file-loading" aria-busy="true"><div class="sk-line"></div></div>
      {/if}
    {:else}
      <div class="file-table-wrap">
        <table class="file-table">
          <tbody>
            {#each rows as row, r (r)}
              <tr>
                <th scope="row" class="file-rownum">{r + 1}</th>
                {#each row as cell, c (c)}
                  {#if r === 0}<th scope="col">{cell}</th>{:else}<td>{cell}</td>{/if}
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
        {#if rows.length >= 500}<p class="muted small">Showing the first 500 rows.</p>{/if}
      </div>
    {/if}
  {:else if kind === 'text'}
    {#if text === null}
      {#if revision.size > TEXT_LIMIT}
        <div class="file-card"><Icon name="info" size={18} /><span>Too large to show here.</span><button class="secondary" onclick={() => void docs.download(revision)}><Icon name="download" size={13} /> Download</button></div>
      {:else}
        <div class="file-loading" aria-busy="true"><div class="sk-line"></div></div>
      {/if}
    {:else}
      <pre class="file-text">{#each lines as line, n (n)}<span class="file-line"><span class="file-ln">{n + 1}</span>{line}
</span>{/each}</pre>
    {/if}
  {:else}
    <div class="file-card">
      <span class="file-ext">{(/\.([a-z0-9]{1,6})$/i.exec(revision.filename)?.[1] ?? 'file').toUpperCase()}</span>
      <span><b>{revision.filename}</b><br /><span class="muted small">{fileSize(revision.size)} · no preview for this type; download to open it</span></span>
      <button class="primary" onclick={() => void docs.download(revision)}><Icon name="download" size={13} /> Download</button>
    </div>
  {/if}
</div>
