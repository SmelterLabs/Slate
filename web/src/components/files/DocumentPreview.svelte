<script lang="ts">
  // A document drawn by the board itself, from its bytes: a Word file as a page (lib/office
  // renderDocx), a workbook as sheets with tabs (readWorkbook), a CSV as a table, text with
  // line numbers. Used full-screen by the task viewer (MediaViewer) and in place by Docs &
  // Files (FileView). Light on any background, like paper; scrolls inside itself.
  import { fileSize } from '../../lib/activity';
  import { parseTable } from '../../lib/docs';
  import { columnLetter, OFFICE_LIMIT, readWorkbook, renderDocx, type Sheet } from '../../lib/office';
  import Icon from '../Icon.svelte';

  let {
    blob,
    kind,
    filename,
    size,
    ondownload,
  }: {
    blob: Blob;
    kind: 'docx' | 'xlsx' | 'table' | 'text';
    filename: string;
    size: number;
    /** Offered when the file cannot be shown. */
    ondownload?: () => void;
  } = $props();

  const TEXT_LIMIT = 2 * 1024 * 1024;
  let html = $state<string | null>(null);
  let sheets = $state<Sheet[] | null>(null);
  let sheetAt = $state(0);
  let text = $state<string | null>(null);
  let failed = $state<string | null>(null);
  const tooBig = $derived(kind === 'docx' || kind === 'xlsx' ? size > OFFICE_LIMIT : size > TEXT_LIMIT);
  const loading = $derived(!failed && !tooBig && html === null && sheets === null && text === null);

  $effect(() => {
    const b = blob;
    const k = kind;
    html = sheets = text = failed = null;
    sheetAt = 0;
    if (tooBig) return;
    let live = true;
    const fail = (e: unknown) => {
      if (!live) return;
      console.warn(`preview of ${filename} failed`, e);
      failed = 'This file could not be read.';
    };
    if (k === 'docx') {
      renderDocx(b).then((r) => {
        if (!live) return;
        if (r.warnings.length) console.info(`${filename}: ${r.warnings.length} parts not shown (${r.warnings[0]})`);
        html = r.html;
      }, fail);
    } else if (k === 'xlsx') {
      readWorkbook(b).then((s) => live && (sheets = s), fail);
    } else {
      b.text().then((t) => live && (text = t), fail);
    }
    return () => {
      live = false;
    };
  });

  const rows = $derived(kind === 'table' && text !== null ? parseTable(text) : []);
  const lines = $derived(kind === 'text' && text !== null ? text.split('\n') : []);
  const sheet = $derived(sheets?.[sheetAt] ?? null);
</script>

<div class="docprev" data-kind={kind} data-state={failed ? 'failed' : tooBig ? 'too-big' : loading ? 'loading' : 'ready'}>
  {#if failed || tooBig}
    <div class="docprev-card">
      <Icon name={failed ? 'alert' : 'info'} size={18} />
      <span><b>{filename}</b><br /><span class="muted small">{fileSize(size)} · {failed ?? 'too large to show here'}</span></span>
      {#if ondownload}<button class="primary" onclick={ondownload}><Icon name="download" size={13} /> Download</button>{/if}
    </div>
  {:else if loading}
    <div class="docprev-loading" aria-busy="true" aria-label="Loading {filename}">
      <div class="sk-line w60"></div>
      <div class="sk-line"></div>
      <div class="sk-line w80"></div>
    </div>
  {:else if kind === 'docx'}
    <article class="docprev-page" aria-label="Document">
      <!-- eslint-disable-next-line svelte/no-at-html-tags -- sanitized by lib/office sanitizeDocHtml -->
      {@html html}
    </article>
  {:else if kind === 'xlsx' && sheets}
    {#if sheets.length > 1}
      <div class="docprev-tabs" role="tablist" aria-label="Sheets">
        {#each sheets as s, i (s.name)}
          <button role="tab" aria-selected={i === sheetAt} class:active={i === sheetAt} onclick={() => (sheetAt = i)}>{s.name}</button>
        {/each}
      </div>
    {/if}
    {#if sheet}
      <div class="docprev-sheet" role="tabpanel" aria-label="Sheet {sheet.name}">
        <table class="docprev-grid">
          <colgroup>
            <col style="width:44px" />
            {#each sheet.widths as w, i (i)}<col style="width:{w}px" />{/each}
          </colgroup>
          <thead>
            <tr>
              <th class="corner"></th>
              {#each sheet.widths as _, i (i)}<th scope="col">{columnLetter(i + 1)}</th>{/each}
            </tr>
          </thead>
          <tbody>
            {#each sheet.rows as row, r (r)}
              <tr>
                <th scope="row">{r + 1}</th>
                {#each row as cell, c (c)}
                  <td colspan={cell.colspan} rowspan={cell.rowspan} class:right={cell.right} class:bold={cell.bold}>{cell.text}</td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
        {#if sheet.truncated}<p class="muted small docprev-more">{sheet.truncated.toLocaleString()} more rows; download the file to see them all.</p>{/if}
      </div>
    {/if}
  {:else if kind === 'table'}
    <div class="docprev-sheet">
      <table class="docprev-grid">
        <tbody>
          {#each rows as row, r (r)}
            <tr>
              <th scope="row">{r + 1}</th>
              {#each row as cell, c (c)}
                {#if r === 0}<th scope="col">{cell}</th>{:else}<td>{cell}</td>{/if}
              {/each}
            </tr>
          {/each}
        </tbody>
      </table>
      {#if rows.length >= 500}<p class="muted small docprev-more">Showing the first 500 rows.</p>{/if}
    </div>
  {:else}
    <pre class="docprev-text">{#each lines as line, n (n)}<span class="docprev-line"><span class="docprev-ln">{n + 1}</span>{line}
</span>{/each}</pre>
  {/if}
</div>

<style>
  .docprev {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: auto;
    background: #e9e9ec;
    color: #1a1a1f;
    border-radius: 6px;
  }
  .docprev-card,
  .docprev-loading {
    margin: auto;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 20px 24px;
    background: #fff;
    border-radius: 10px;
    max-width: 520px;
  }
  .docprev-loading {
    flex-direction: column;
    align-items: stretch;
    width: min(100%, 720px);
  }
  /* A Word page: US Letter width at 96 dpi, an inch of margin, like the printed thing.
     flex-shrink: 0 lets the page grow with its text; as a shrinkable flex item it stopped at the
     viewer's height and every line after the first screen spilled onto the grey. */
  .docprev-page {
    flex-shrink: 0;
    width: min(100%, 816px);
    min-height: 100%;
    margin: 24px auto;
    padding: 72px 72px 96px;
    background: #fff;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.18);
    font: 11pt/1.5 Calibri, Carlito, 'Segoe UI', system-ui, sans-serif;
    box-sizing: border-box;
  }
  .docprev-page :global(h1) {
    font-size: 20pt;
    margin: 0.6em 0 0.3em;
  }
  .docprev-page :global(h2) {
    font-size: 16pt;
    margin: 0.6em 0 0.3em;
  }
  .docprev-page :global(h3) {
    font-size: 13pt;
    margin: 0.6em 0 0.3em;
  }
  .docprev-page :global(p) {
    margin: 0 0 0.7em;
  }
  .docprev-page :global(table) {
    border-collapse: collapse;
    margin: 0.5em 0 1em;
    max-width: 100%;
  }
  .docprev-page :global(td),
  .docprev-page :global(th) {
    border: 1px solid #bbb;
    padding: 4px 8px;
    vertical-align: top;
  }
  .docprev-page :global(img) {
    max-width: 100%;
    height: auto;
  }
  .docprev-page :global(a) {
    color: #1a5fb4;
  }
  @media (max-width: 900px) {
    .docprev-page {
      padding: 32px 24px 48px;
      margin: 0;
    }
  }
  /* A workbook: Excel's grid, column letters and row numbers, tabs along the bottom. */
  .docprev-tabs {
    order: 2;
    display: flex;
    gap: 2px;
    padding: 6px 8px;
    background: #f3f3f5;
    border-top: 1px solid #cfcfd4;
    overflow-x: auto;
    position: sticky;
    bottom: 0;
  }
  .docprev-tabs button {
    border: 1px solid #cfcfd4;
    border-bottom: 0;
    background: #e3e3e7;
    color: inherit;
    padding: 4px 14px;
    border-radius: 4px 4px 0 0;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
  }
  .docprev-tabs button.active {
    background: #fff;
    font-weight: 600;
    border-bottom: 2px solid #217346;
  }
  .docprev-sheet {
    flex: 1;
    min-height: 0;
    overflow: auto;
    background: #fff;
  }
  .docprev-grid {
    border-collapse: collapse;
    table-layout: fixed;
    font: 13px/1.35 Calibri, Carlito, 'Segoe UI', system-ui, sans-serif;
    min-width: 100%;
  }
  .docprev-grid th,
  .docprev-grid td {
    border: 1px solid #d8d8dc;
    padding: 2px 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: left;
    font-weight: 400;
  }
  .docprev-grid thead th {
    position: sticky;
    top: 0;
    background: #f3f3f5;
    text-align: center;
    color: #555;
    z-index: 1;
  }
  .docprev-grid tbody th {
    position: sticky;
    left: 0;
    background: #f3f3f5;
    text-align: center;
    color: #555;
  }
  .docprev-grid .corner {
    left: 0;
    z-index: 2;
  }
  .docprev-grid td.right {
    text-align: right;
  }
  .docprev-grid td.bold {
    font-weight: 700;
  }
  .docprev-more {
    padding: 8px 12px;
    margin: 0;
  }
  .docprev-text {
    flex-shrink: 0;
    margin: 0;
    padding: 16px 20px;
    background: #fff;
    min-height: 100%;
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .docprev-line {
    display: block;
  }
  .docprev-ln {
    display: inline-block;
    width: 3.5em;
    color: #999;
    user-select: none;
  }
</style>
