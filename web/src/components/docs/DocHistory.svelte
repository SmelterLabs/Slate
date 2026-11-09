<script lang="ts">
  // Page history: one version per sitting of edits (autosaves fold together), newest first.
  // Pick one to see what changed since (a diff against the page now) or how it read; restore
  // brings it back as a new version, so nothing is ever lost. "Name this version" closes the
  // current sitting with a label, a checkpoint to come back to.
  import { onMount } from 'svelte';
  import { renderDoc } from '../../lib/docmd';
  import { diffLines, sideBySide, type DocDiff, type DocVersion } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';

  let { docId, onclose }: { docId: string; onclose: () => void } = $props();

  let versions = $state<DocVersion[]>([]);
  let picked = $state<number | null>(null);
  let diff = $state<DocDiff | null>(null);
  let body = $state<DocVersion | null>(null);
  let mode = $state<'changes' | 'side' | 'page'>('changes');
  const sideRows = $derived(diff ? sideBySide(diff.diff) : []);
  let naming = $state(false);
  let name = $state('');
  let busy = $state(false);

  async function load() {
    versions = await docs.versions(docId);
    if (picked === null && versions.length > 1) void pick(versions[1].version_no);
    else if (picked === null && versions[0]) void pick(versions[0].version_no);
  }
  onMount(() => void load());

  // A save or restore elsewhere adds or grows a version: list them again.
  $effect(() => {
    void docs.current?.version_no;
    void docs.current?.revision;
    void load();
  });

  async function pick(n: number) {
    picked = n;
    [diff, body] = await Promise.all([docs.diff(docId, n), docs.version(docId, n)]);
  }

  async function restore() {
    if (picked === null) return;
    busy = true;
    try {
      if (await docs.restore(docId, picked)) await load();
    } finally {
      busy = false;
    }
  }

  async function saveName() {
    busy = true;
    try {
      await docs.checkpoint(docId, name.trim());
      naming = false;
      name = '';
      await load();
    } finally {
      busy = false;
    }
  }

  const who = (id: string) => board.accounts[id]?.display_name ?? 'Someone';
  const current = $derived(docs.current?.version_no ?? 0);
  const preview = $derived(
    body?.body_md != null
      ? renderDoc(body.body_md, {
          task: (id) => {
            const t = board.items[id];
            return t ? { title: t.title, done: t.status?.kind === 'done', color: 'var(--accent)' } : null;
          },
          doc: (id) => docs.byId[id] ?? null,
        })
      : [],
  );
</script>

<aside class="doc-history" aria-label="Page history">
  <header>
    <h2>Page history</h2>
    <button class="icon" aria-label="Close history" onclick={onclose}><Icon name="x" size={16} /></button>
  </header>
  <div class="dh-name">
    {#if naming}
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveName();
        }}
      >
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={name} maxlength="200" placeholder="Version name, e.g. Sent for review" autofocus />
        <button class="primary" disabled={busy}>Save</button>
      </form>
    {:else}
      <button class="secondary" onclick={() => (naming = true)}><Icon name="flag" size={13} /> Name this version</button>
    {/if}
  </div>
  <div class="dh-body">
    <ol class="dh-list">
      {#each versions as v (v.id)}
        <li>
          <button class:active={picked === v.version_no} onclick={() => void pick(v.version_no)}>
            <span class="dh-top">
              <b>{v.note ?? (v.version_no === current ? 'Current version' : `Version ${v.version_no}`)}</b>
              <span class="muted">v{v.version_no}</span>
            </span>
            <span class="muted">{who(v.author_id)} · {ago(v.updated_at, board.now)}</span>
          </button>
        </li>
      {/each}
    </ol>
    <section class="dh-view">
      {#if picked !== null && diff}
        <div class="dh-tabs" role="tablist">
          <button role="tab" aria-selected={mode === 'changes'} class:active={mode === 'changes'} onclick={() => (mode = 'changes')}>Changes since</button>
          <button role="tab" aria-selected={mode === 'side'} class:active={mode === 'side'} onclick={() => (mode = 'side')}>Side by side</button>
          <button role="tab" aria-selected={mode === 'page'} class:active={mode === 'page'} onclick={() => (mode = 'page')}>How it read</button>
          <span class="dh-count"><span class="add">+{diff.added}</span> <span class="del">−{diff.removed}</span></span>
        </div>
        {#if mode === 'changes'}
          {#if diff.title_before !== diff.title_after}
            <p class="dh-title">Title: <del>{diff.title_before}</del> → <ins>{diff.title_after}</ins></p>
          {/if}
          {#if diff.diff}
            <pre class="dh-diff">{#each diffLines(diff.diff) as l, k (k)}<span class="dl {l.kind}">{l.kind === 'add' ? '+ ' : l.kind === 'del' ? '− ' : l.kind === 'hunk' ? '' : '  '}{l.text}
</span>{/each}</pre>
          {:else}
            <p class="muted">The text is the same as the page now.</p>
          {/if}
        {:else if mode === 'side'}
          {#if diff.diff}
            <div class="rc-side dh-side" role="table" data-testid="side-by-side">
              {#each sideRows as r, k (k)}
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
            <p class="muted">The text is the same as the page now.</p>
          {/if}
        {:else}
          <h3>{body?.title}</h3>
          <div class="md dh-preview">
            {#each preview as b, k (k)}
              {#if b.kind === 'html'}{@html b.html}{:else}<p class="task-chip">Task card</p>{/if}
            {/each}
          </div>
        {/if}
        {#if picked !== current}
          <button class="primary dh-restore" disabled={busy} onclick={() => void restore()}><Icon name="restore" size={14} /> Restore version {picked}</button>
        {/if}
      {:else}
        <p class="muted">Pick a version.</p>
      {/if}
    </section>
  </div>
</aside>
