<script lang="ts">
  // Two revisions of a file, compared the way the type allows: text and PDFs (extracted text,
  // labelled approximate) as an inline or side-by-side diff; CSV, TSV and XLSX as the newer
  // table with changed cells painted (old → new) and added rows marked, removed rows listed
  // under it; images side by side with a swipe slider; anything else, the two download cards.
  import { fileSize } from '../../lib/activity';
  import { cellChanges, diffLines, sideBySide, viewerKind, type DocRevision, type RevisionCompare } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import Icon from '../Icon.svelte';

  let {
    docId,
    revisions,
    from,
    to,
    onchange,
    onclose,
  }: {
    docId: string;
    revisions: DocRevision[];
    from: number;
    to: number;
    onchange: (from: number, to: number) => void;
    onclose: () => void;
  } = $props();

  let result = $state<RevisionCompare | null>(null);
  let failed = $state(false);
  let mode = $state<'inline' | 'side'>('side');
  let sheet = $state(0);
  let swipe = $state(50);
  let leftUrl = $state<string | null>(null);
  let rightUrl = $state<string | null>(null);

  $effect(() => {
    const f = from;
    const t = to;
    result = null;
    failed = false;
    let live = true;
    docs.compare(docId, f, t).then(
      (r) => live && (result = r),
      () => live && (failed = true),
    );
    return () => {
      live = false;
    };
  });
  const images = $derived(!!result && result.kind === 'none' && viewerKind(result.from.content_type, result.from.filename) === 'image' && viewerKind(result.to.content_type, result.to.filename) === 'image');
  $effect(() => {
    if (!result || !images) return;
    const r = result;
    leftUrl = rightUrl = null;
    void docs.fileUrl(r.from.id).then((u) => (leftUrl = u), () => {});
    void docs.fileUrl(r.to.id).then((u) => (rightUrl = u), () => {});
  });
  const rows = $derived(result?.kind === 'text' ? sideBySide(result.diff) : []);
  const lines = $derived(result?.kind === 'text' ? diffLines(result.diff) : []);
  const current = $derived(result?.sheets[sheet] ?? null);
  const changes = $derived(current ? cellChanges(current) : new Map());
  const added = $derived(new Set(current?.added_rows ?? []));
  const cols = $derived(current ? Math.max(0, ...current.rows.map((r) => r.length), ...current.before_rows.map((r) => r.length)) : 0);
</script>

<section class="rev-compare" aria-label="Compare revisions" data-testid="compare">
  <header class="rc-head">
    <span class="rc-pick">
      <label>From <select value={from} onchange={(e) => onchange(Number((e.currentTarget as HTMLSelectElement).value), to)}>
          {#each revisions as r (r.id)}<option value={r.revision_no}>rev {r.revision_no}</option>{/each}
        </select></label>
      <Icon name="chevronRight" size={12} />
      <label>to <select value={to} onchange={(e) => onchange(from, Number((e.currentTarget as HTMLSelectElement).value))}>
          {#each revisions as r (r.id)}<option value={r.revision_no}>rev {r.revision_no}</option>{/each}
        </select></label>
    </span>
    {#if result?.kind === 'text'}
      <span class="dh-tabs" role="tablist">
        <button role="tab" aria-selected={mode === 'side'} class:active={mode === 'side'} onclick={() => (mode = 'side')}>Side by side</button>
        <button role="tab" aria-selected={mode === 'inline'} class:active={mode === 'inline'} onclick={() => (mode = 'inline')}>Inline</button>
        <span class="dh-count"><span class="add">+{result.added}</span> <span class="del">−{result.removed}</span></span>
      </span>
    {/if}
    <button class="secondary" onclick={onclose}><Icon name="x" size={13} /> Close compare</button>
  </header>

  {#if failed}
    <p class="muted pad">Could not compare these revisions.</p>
  {:else if !result}
    <div class="file-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div></div>
  {:else if result.kind === 'text'}
    {#if result.approximate}
      <p class="rc-note muted small"><Icon name="info" size={12} /> Text extracted from the files; layout and drawings are not compared, only the words and numbers.</p>
    {/if}
    {#if !result.diff}
      <p class="muted pad">The text is the same in both revisions.</p>
    {:else if mode === 'side'}
      <div class="rc-side" role="table">
        {#each rows as r, k (k)}
          {#if r.kind === 'hunk'}
            <div class="rc-hunk" role="row"><span></span><span></span></div>
          {:else}
            <div class="rc-row {r.kind}" role="row">
              <span class="rc-cell left" class:empty={!r.left}>{#if r.left}<i>{r.left.no}</i>{r.left.text}{/if}</span>
              <span class="rc-cell right" class:empty={!r.right}>{#if r.right}<i>{r.right.no}</i>{r.right.text}{/if}</span>
            </div>
          {/if}
        {/each}
      </div>
    {:else}
      <pre class="dh-diff rc-inline">{#each lines as l, k (k)}<span class="dl {l.kind}">{l.kind === 'add' ? '+ ' : l.kind === 'del' ? '− ' : l.kind === 'hunk' ? '' : '  '}{l.text}
</span>{/each}</pre>
    {/if}
  {:else if result.kind === 'table'}
    {#if result.sheets.length > 1}
      <div class="dh-tabs rc-sheets" role="tablist">
        {#each result.sheets as s, i (s.name)}
          <button role="tab" aria-selected={sheet === i} class:active={sheet === i} onclick={() => (sheet = i)}>{s.name}{#if s.changed.length || s.added_rows.length || s.removed_rows.length}<span class="dd-count open">{s.changed.length + s.added_rows.length + s.removed_rows.length}</span>{/if}</button>
        {/each}
      </div>
    {/if}
    {#if current}
      {#if !current.changed.length && !current.added_rows.length && !current.removed_rows.length}
        <p class="muted pad">No cell changed in {current.name}.</p>
      {:else}
        <p class="rc-note muted small">{current.changed.length} cell{current.changed.length === 1 ? '' : 's'} changed{#if current.added_rows.length}, {current.added_rows.length} row{current.added_rows.length === 1 ? '' : 's'} added{/if}{#if current.removed_rows.length}, {current.removed_rows.length} removed{/if}. Old value struck through, new value after it.</p>
      {/if}
      <div class="file-table-wrap">
        <table class="file-table rc-table">
          <tbody>
            {#each current.rows as row, r (r)}
              <tr class:added={added.has(r)}>
                <th scope="row" class="file-rownum">{r + 1}</th>
                {#each Array.from({ length: cols }) as _, c (c)}
                  {@const ch = changes.get(`${r}:${c}`)}
                  {#if r === 0}
                    <th scope="col" class:changed={!!ch}>{#if ch}<del>{ch.before}</del> {/if}{row[c] ?? ''}</th>
                  {:else}
                    <td class:changed={!!ch} data-changed={ch ? 'true' : undefined}>{#if ch}<del>{ch.before}</del> <ins>{ch.after}</ins>{:else}{row[c] ?? ''}{/if}</td>
                  {/if}
                {/each}
              </tr>
            {/each}
            {#each current.removed_rows as r (r)}
              <tr class="removed">
                <th scope="row" class="file-rownum">−</th>
                {#each Array.from({ length: cols }) as _, c (c)}<td><del>{current.before_rows[r]?.[c] ?? ''}</del></td>{/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {:else if images}
    <div class="rc-images">
      <div class="rc-swipe" style="--swipe:{swipe}%">
        {#if rightUrl}<img src={rightUrl} alt="Revision {result.to_revision}" />{/if}
        {#if leftUrl}<img class="rc-old" src={leftUrl} alt="Revision {result.from_revision}" />{/if}
        <span class="rc-handle" aria-hidden="true"></span>
        <span class="rc-tag left">rev {result.from_revision}</span>
        <span class="rc-tag right">rev {result.to_revision}</span>
      </div>
      <input type="range" min="0" max="100" bind:value={swipe} aria-label="Swipe between revisions" />
    </div>
  {:else}
    <div class="rc-cards">
      {#each [result.from, result.to] as r (r.id)}
        <div class="file-card">
          <span class="file-ext">{(/\.([a-z0-9]{1,6})$/i.exec(r.filename)?.[1] ?? 'file').toUpperCase()}</span>
          <span><b>rev {r.revision_no} · {r.filename}</b><br /><span class="muted small">{fileSize(r.size)} · {r.note}</span></span>
          <button class="secondary" onclick={() => void docs.download(r)}><Icon name="download" size={13} /> Download</button>
        </div>
      {/each}
      {#if result.message}
        <p class="muted small" data-testid="compare-message">{result.message}</p>
      {:else}
        <p class="muted small">No comparison for this type; the notes say what changed.</p>
      {/if}
    </div>
  {/if}
</section>
