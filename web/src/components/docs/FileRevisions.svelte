<script lang="ts">
  // A file doc's history: every revision, newest first, with who, when, size and the note
  // that says what changed (small, folded; the caret opens it). Pick one to see it in the
  // viewer; Download saves it; Restore makes an older one current again as a new revision,
  // so nothing is ever lost. Compare with current shows what changed (RevisionCompare).
  import { fileSize } from '../../lib/activity';
  import { notIndexed, type DocRevision } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';

  let {
    revisions,
    picked,
    onpick,
    oncompare,
    onclose,
  }: {
    revisions: DocRevision[];
    picked: number | null;
    onpick: (n: number | null) => void;
    oncompare: (from: number) => void;
    onclose: () => void;
  } = $props();

  let busy = $state(false);
  const current = $derived(revisions[0]?.revision_no ?? 0);
  const pinned = $derived(docs.current?.pinned ?? null);
  let pinning = $state<number | null>(null);
  let pinNote = $state('');
  async function pin(n: number) {
    busy = true;
    try {
      await docs.pin(revisions[0].doc_id, n, pinNote.trim());
      pinning = null;
      pinNote = '';
    } finally {
      busy = false;
    }
  }
  const who = (id: string) => board.accounts[id]?.display_name ?? 'Someone';

  async function restore(n: number) {
    busy = true;
    try {
      if (await docs.restoreRevision(revisions[0].doc_id, n)) onpick(null);
    } finally {
      busy = false;
    }
  }
</script>

<aside class="doc-history file-revisions" aria-label="Revisions">
  <header>
    <h2>Revisions</h2>
    <button class="icon" aria-label="Close revisions" onclick={onclose}><Icon name="x" size={16} /></button>
  </header>
  <div class="dh-body">
    <ol class="dh-list rev-list">
      {#each revisions as r (r.id)}
        {@const isCurrent = r.revision_no === current}
        {@const active = picked === r.revision_no || (picked === null && isCurrent)}
        <li class="rev-item" class:active data-revision={r.revision_no}>
          <button class="rev-pick" class:active onclick={() => onpick(isCurrent ? null : r.revision_no)}>
            <span class="dh-top">
              <b>{isCurrent ? 'Current' : `Revision ${r.revision_no}`}{#if r.restored_from}<span class="muted"> · restored from {r.restored_from}</span>{/if}</b>
              <span class="muted">{#if pinned?.revision_no === r.revision_no}<span class="pin-dot" title="In use"></span> in use · {/if}rev {r.revision_no}</span>
            </span>
            <span class="muted">{who(r.uploaded_by)} · {ago(r.created_at, board.now)} · {fileSize(r.size)}{#if notIndexed(r)} · <span title="Too large for the server to read for search, or unreadable; it opens and downloads as usual">not searchable</span>{/if}</span>
            {#if r.filename !== revisions[0].filename}<span class="muted rev-name">{r.filename}</span>{/if}
          </button>
          <details class="rev-note">
            <summary><Icon name="chevronRight" size={11} /><span>{r.note.length > 60 ? `${r.note.slice(0, 60)}…` : r.note}</span></summary>
            <p>{r.note}</p>
          </details>
          {#if pinning === r.revision_no}
            <form class="rev-pin-form" onsubmit={(e) => { e.preventDefault(); void pin(r.revision_no); }}>
              <!-- svelte-ignore a11y_autofocus -->
              <input bind:value={pinNote} placeholder="Why this one is in use (optional)" aria-label="Pin note" maxlength="200" autofocus />
              <button class="primary small" disabled={busy}>Pin</button>
              <button type="button" class="link small" onclick={() => (pinning = null)}>Cancel</button>
            </form>
          {/if}
          <span class="rev-tools">
            <button class="link small" onclick={() => void docs.download(r)}><Icon name="download" size={12} /> Download</button>
            {#if pinned?.revision_no === r.revision_no}
              <button class="link small" disabled={busy} onclick={() => void docs.pin(revisions[0].doc_id, null)}>Unpin</button>
            {:else}
              <button class="link small" onclick={() => { pinning = r.revision_no; pinNote = ''; }}><span class="pin-dot"></span> Pin as in use</button>
            {/if}
            {#if !isCurrent}
              <button class="link small" onclick={() => oncompare(r.revision_no)}><Icon name="history" size={12} /> Compare with current</button>
              <button class="link small" disabled={busy} onclick={() => void restore(r.revision_no)}><Icon name="restore" size={12} /> Make current</button>
            {/if}
          </span>
        </li>
      {:else}
        <li class="muted pad">No file uploaded yet.</li>
      {/each}
    </ol>
    <p class="muted small rev-foot">Older revisions stay for good. Making one current again adds it as a new revision.</p>
  </div>
</aside>
