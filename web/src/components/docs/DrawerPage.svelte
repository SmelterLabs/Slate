<script lang="ts">
  // A drawer: a list whose entries are documents. Two states on one
  // page. With nothing picked, the Files table: folders and documents with their revision,
  // pin, last change, tags and open threads; chips narrow it, Upload / New page / New folder
  // add to it, a folder row opens in place with a breadcrumb, and a file dropped anywhere
  // uploads here. Click a row and the Reading state takes over: the file rail on the left,
  // the document in the middle (DocPage, with its viewer, revisions and discussion), Back to
  // the table. `?folder=<id>` and `?doc=<id>` on the list's hash carry both states.
  import { onDestroy, onMount } from 'svelte';
  import { fileSize } from '../../lib/activity';
  import { docGlyph, fileGroup, filterEntries, tagCounts, type Drawer, type DrawerEntry, type FileGroup } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { ago, shortDate } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import DocPage from './DocPage.svelte';
  import Picker, { type PickerOption } from './Picker.svelte';
  import UploadRevision from './UploadRevision.svelte';

  let { listId }: { listId: string } = $props();

  const list = $derived(board.lists[listId]);
  let drawer = $state<Drawer | null>(null);
  let folderId = $state<string | null>(null);
  let docId = $state<string | null>(null);
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function readHash() {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
    folderId = q.get('folder');
    docId = q.get('doc');
  }
  function setHash(next: { folder?: string | null; doc?: string | null }) {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
    const folder = next.folder === undefined ? q.get('folder') : next.folder;
    const d = next.doc === undefined ? q.get('doc') : next.doc;
    q.delete('folder');
    q.delete('doc');
    if (folder) q.set('folder', folder);
    if (d) q.set('doc', d);
    const base = location.hash.split('?')[0];
    const str = q.toString();
    history.replaceState(null, '', `${base}${str ? `?${str}` : ''}`);
    readHash();
  }

  async function load() {
    const gen = ++generation;
    try {
      const out = await docs.drawer(listId, folderId);
      if (gen === generation) drawer = out;
    } catch (e) {
      if (gen === generation) {
        drawer = null;
        board.flash((e as Error).message);
      }
    }
  }
  onMount(() => {
    readHash();
    docs.start();
    window.addEventListener('hashchange', readHash);
  });
  $effect(() => {
    void listId;
    void folderId;
    void load();
  });
  $effect(() => {
    void docs.open(docId);
  });
  const off = board.onEvent((ev) => {
    if (ev.type.startsWith('doc.') || ev.type.startsWith('list.')) {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 250);
    }
  });
  onDestroy(() => {
    generation++;
    clearTimeout(timer);
    window.removeEventListener('hashchange', readHash);
    off();
    void docs.open(null);
  });

  // --- chips --------------------------------------------------------------------------------
  let group = $state<FileGroup | 'page' | null>(null);
  let tag = $state<string | null>(null);
  let q = $state('');
  const GROUPS: { key: FileGroup | 'page'; label: string }[] = [
    { key: 'pdf', label: 'PDFs' },
    { key: 'image', label: 'Images' },
    { key: 'sheet', label: 'Sheets' },
    { key: 'text', label: 'Text' },
    { key: 'page', label: 'Pages' },
  ];
  const present = $derived(new Set((drawer?.entries ?? []).map((e) => (e.kind === 'page' ? 'page' : fileGroup(e)))));
  const tags = $derived(tagCounts(drawer?.entries ?? []));
  const shown = $derived(filterEntries(drawer?.entries ?? [], { group, tag, q }));
  const needs = $derived((drawer?.entries ?? []).filter((e) => (e.open_threads ?? 0) > 0).length);

  // --- adding ---------------------------------------------------------------------------------
  let uploadOpen = $state(false);
  let dropped = $state<File | null>(null);
  let uploading = $state(false);
  let dragOver = $state(false);
  let naming = $state<'folder' | 'page' | null>(null);
  let newName = $state('');
  const where = $derived(folderId ? { parent_id: folderId } : { place: { kind: 'list' as const, id: listId } });

  async function upload(file: File, note: string) {
    uploading = true;
    try {
      const d = await docs.createFile(where, file, note);
      if (d) {
        uploadOpen = false;
        dropped = null;
        await load();
        board.flash(`${d.title} added`);
      }
    } finally {
      uploading = false;
    }
  }
  async function create() {
    const kind = naming;
    const title = newName.trim();
    naming = null;
    newName = '';
    if (!kind || !title) return;
    const d = await docs.create({ ...where, kind: kind === 'folder' ? 'folder' : 'page', title });
    if (!d) return;
    await load();
    if (kind === 'page') setHash({ doc: d.id });
  }
  function drop(e: DragEvent) {
    dragOver = false;
    const f = e.dataTransfer?.files?.[0];
    if (!f) return;
    e.preventDefault();
    dropped = f;
    uploadOpen = true;
  }

  // --- reading state: the rail ------------------------------------------------------------------
  const rail = $derived.by(() => {
    // Every document in the drawer, grouped under its folder, in tree order.
    const inDrawer = docs.list.filter((d) => {
      let cur = d;
      const seen = new Set<string>();
      while (cur.parent_id && !seen.has(cur.id)) {
        seen.add(cur.id);
        const p = docs.byId[cur.parent_id];
        if (!p) break;
        cur = p;
      }
      return cur.list_id === listId;
    });
    const byParent = new Map<string | null, typeof inDrawer>();
    for (const d of inDrawer) byParent.set(d.parent_id, [...(byParent.get(d.parent_id) ?? []), d]);
    const rows: { doc: (typeof inDrawer)[number]; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const d of [...(byParent.get(parent) ?? [])].sort((a, b) => a.position - b.position)) {
        rows.push({ doc: d, depth });
        if (d.kind === 'folder') walk(d.id, depth + 1);
      }
    };
    walk(null, 0);
    return rows;
  });

  let moving = $state<string | null>(null);
  const moveOptions = $derived.by((): PickerOption[] => {
    const out: PickerOption[] = [{ id: 'top', label: `${list?.name ?? 'Drawer'} (top)`, icon: '▣' }];
    for (const r of rail) if (r.doc.kind === 'folder' && r.doc.id !== moving) out.push({ id: r.doc.id, label: r.doc.title, icon: '📁', depth: r.depth + 1 });
    return out;
  });
  function moveTo(target: string) {
    const id = moving;
    moving = null;
    if (!id) return;
    void docs.move(id, target === 'top' ? { place: { kind: 'list', id: listId } } : { parent_id: target }).then(load);
  }
  const who = (id: string) => board.accountName(id) ?? 'someone';
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<section
  class="drawer-page"
  class:reading={!!docId}
  class:drag-over={dragOver}
  data-testid="drawer"
  ondragover={(e) => {
    if (e.dataTransfer?.types.includes('Files')) {
      e.preventDefault();
      dragOver = true;
    }
  }}
  ondragleave={() => (dragOver = false)}
  ondrop={drop}
>
  {#if docId}
    <div class="drawer-reading">
      <aside class="drawer-rail" aria-label="In this drawer">
        <button class="link drawer-back" onclick={() => setHash({ doc: null })}><Icon name="chevronLeft" size={13} /> All files</button>
        <ul>
          {#each rail as r (r.doc.id)}
            <li style="--depth:{r.depth}" class:folder={r.doc.kind === 'folder'} class:on={r.doc.id === docId}>
              {#if r.doc.kind === 'folder'}
                <span class="rail-folder">{#if r.doc.pinned_to_top}<span class="pin-top" aria-label="Pinned to the top">📌</span> {/if}📁 {r.doc.title}</span>
              {:else}
                <button class="rail-row" onclick={() => setHash({ doc: r.doc.id })}>
                  <span class="rail-glyph">{r.doc.icon ?? docGlyph(r.doc)}</span>
                  <span class="rail-main">
                    <span class="rail-title">{#if r.doc.pinned_to_top}<span class="pin-top" aria-label="Pinned to the top">📌</span> {/if}{r.doc.title}</span>
                    <span class="muted small">
                      {#if r.doc.kind === 'file' && r.doc.file}r{r.doc.file.revision_no}{#if r.doc.pinned} · <span class="pin-dot" title="Rev {r.doc.pinned.revision_no} in use"></span> r{r.doc.pinned.revision_no} in use{/if}{:else}v{r.doc.version_no}{/if}
                      {#if r.doc.open_threads} · {r.doc.open_threads} open{/if}
                    </span>
                  </span>
                </button>
              {/if}
            </li>
          {/each}
        </ul>
        <p class="muted small pad">Drop a file anywhere to add it here.</p>
      </aside>
      <div class="drawer-doc">
        {#if docs.current && docs.current.id === docId}
          <DocPage doc={docs.current} onmove={(x) => (moving = x)} openDiscussion={window.matchMedia('(min-width: 761px)').matches} />
        {:else if docs.missing}
          <p class="muted pad">That document is gone. <button class="link" onclick={() => setHash({ doc: null })}>Back to the drawer</button></p>
        {:else}
          <div class="doc-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div></div>
        {/if}
      </div>
    </div>
  {:else}
    <div class="drawer-bar">
      <nav class="drawer-crumbs" aria-label="Folder">
        <button class="link" class:current={!folderId} onclick={() => setHash({ folder: null })}>{list?.name ?? 'Drawer'}</button>
        {#each drawer?.path ?? [] as p (p.id)}
          <Icon name="chevronRight" size={12} />
          <button class="link" class:current={p.id === folderId} onclick={() => setHash({ folder: p.id })}>{p.title}</button>
        {/each}
      </nav>
      <span class="drawer-counts muted small" data-testid="drawer-counts">
        {#if drawer}{[`${drawer.counts.docs} doc${drawer.counts.docs === 1 ? '' : 's'}`, drawer.counts.folders ? `${drawer.counts.folders} folder${drawer.counts.folders === 1 ? '' : 's'}` : '', drawer.counts.open_threads ? `${drawer.counts.open_threads} open thread${drawer.counts.open_threads === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')}{/if}
      </span>
      <span class="sp"></span>
      <button class="secondary" onclick={() => { naming = 'folder'; newName = ''; }}><Icon name="folder" size={13} /> New folder</button>
      <button class="secondary" onclick={() => { naming = 'page'; newName = ''; }}><Icon name="page" size={13} /> New page</button>
      <button class="primary" onclick={() => (uploadOpen = true)}><Icon name="clip" size={13} /> Upload</button>
    </div>
    <div class="drawer-chips" role="group" aria-label="Filter">
      <button class="chip" class:on={!group} onclick={() => (group = null)}>All</button>
      {#each GROUPS as g (g.key)}
        {#if present.has(g.key)}<button class="chip" class:on={group === g.key} onclick={() => (group = group === g.key ? null : g.key)}>{g.label}</button>{/if}
      {/each}
      {#each tags.slice(0, 8) as t (t.name)}
        <button class="chip tag" class:on={tag === t.name} onclick={() => (tag = tag === t.name ? null : t.name)}>#{t.name}</button>
      {/each}
      {#if needs}<span class="chip needs" title="Documents with open review threads"><Icon name="comment" size={11} /> {needs} with open threads</span>{/if}
      <label class="drawer-search"><Icon name="search" size={13} /><input bind:value={q} placeholder="Filter this folder by name" aria-label="Filter this folder by name" /></label>
    </div>
    {#if naming}
      <form class="drawer-new" onsubmit={(e) => { e.preventDefault(); void create(); }}>
        <span>{naming === 'folder' ? '📁 New folder' : '📄 New page'}{folderId ? ` in ${drawer?.folder?.title ?? 'this folder'}` : ''}</span>
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={newName} placeholder="Name" aria-label="{naming === 'folder' ? 'Folder' : 'Page'} name" autofocus maxlength="200" />
        <button class="primary" disabled={!newName.trim()}>Create</button>
        <button type="button" class="secondary" onclick={() => (naming = null)}>Cancel</button>
      </form>
    {/if}
    {#if !drawer}
      <div class="doc-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div></div>
    {:else if !shown.length}
      <div class="drawer-empty">
        {#if drawer.entries.length}<p class="muted">Nothing matches. <button class="link" onclick={() => { group = null; tag = null; q = ''; }}>Show everything</button></p>
        {:else}<p class="muted">Nothing in here yet. Drop a file on this page, or use Upload, New page or New folder.</p>{/if}
      </div>
    {:else}
      <table class="drawer-table" data-testid="drawer-table">
        <thead><tr><th class="c-name">Name</th><th>Created</th><th>Revision</th><th>Last change</th><th>Tags</th><th>Threads</th><th></th></tr></thead>
        <tbody>
          {#each shown as e (e.id)}
            <tr class:folder={e.kind === 'folder'} class:pinned={!!e.pinned_to_top} data-entry={e.id} data-kind={e.kind} data-pinned={e.pinned_to_top ? 'true' : undefined}>
              <td class="c-name">
                {#if e.pinned_to_top}<span class="pin-top" title="Pinned to the top by {who(e.pinned_to_top.by ?? '')}" aria-label="Pinned to the top">📌</span>{/if}
                {#if e.kind === 'folder'}
                  <button class="row-link" onclick={() => setHash({ folder: e.id })}><span class="glyph">📁</span><b>{e.title}</b><span class="muted small"> {e.children_count} {e.children_count === 1 ? 'entry' : 'entries'}</span></button>
                {:else}
                  <button class="row-link" onclick={() => setHash({ doc: e.id })}><span class="glyph">{e.icon ?? docGlyph(e)}</span>{e.title}</button>
                {/if}
              </td>
              <td class="c-created"><span class="muted" title={new Date(e.created_at).toLocaleString()} data-created>{e.kind === 'folder' ? '' : shortDate(e.created_at, board.now)}</span></td>
              <td>
                {#if e.kind === 'file' && e.file}
                  <span class="rev-strip" title="{e.file.filename} · {fileSize(e.file.size)}">
                    {#each Array.from({ length: Math.min(e.file.revision_no, 6) }) as _, i (i)}
                      {@const n = e.file.revision_no - Math.min(e.file.revision_no, 6) + i + 1}
                      <i class:cur={n === e.file.revision_no} class:pin={e.pinned?.revision_no === n}></i>
                    {/each}
                    <span class="muted">r{e.file.revision_no}</span>
                    {#if e.pinned}<span class="muted" title={e.pinned.note ?? 'In use'}>· r{e.pinned.revision_no} in use</span>{/if}
                  </span>
                {:else if e.kind === 'page'}<span class="muted">v{e.version_no}</span>{/if}
              </td>
              <td class="c-change">
                {#if e.kind === 'file' && e.file}<i class="note">{e.file.note}</i> <span class="muted">· {ago(e.file.created_at, board.now)} · {who(e.file.uploaded_by)}</span>
                {:else}<span class="muted">{ago(e.updated_at, board.now)} · {who(e.updated_by)}</span>{/if}
              </td>
              <td>{#each e.tags ?? [] as t (t)}<span class="tag">{t}</span>{/each}</td>
              <td>{#if e.open_threads}<span class="thr"><Icon name="comment" size={11} /> {e.open_threads}</span>{/if}</td>
              <td class="c-tools">
                <button class="icon" class:lit={!!e.pinned_to_top} aria-label="{e.pinned_to_top ? 'Unpin' : 'Pin to top'} {e.title}" title={e.pinned_to_top ? 'Unpin from the top' : 'Pin to the top of this list'} onclick={() => void docs.pinToTop(e.id, !e.pinned_to_top)}><Icon name="pin" size={13} /></button>
                <button class="icon" aria-label="Move {e.title}" title="Move to a folder" onclick={() => (moving = e.id)}><Icon name="folder" size={13} /></button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</section>

{#if uploadOpen}
  <UploadRevision title="Upload into {drawer?.folder?.title ?? list?.name ?? 'the drawer'}" file={dropped} defaultNote="First upload" busy={uploading} onsubmit={(f, n) => void upload(f, n)} onclose={() => { uploadOpen = false; dropped = null; }} />
{/if}
{#if moving}
  <Picker title="Move “{docs.byId[moving]?.title ?? 'entry'}” to…" placeholder="Pick a folder" options={moveOptions} onclose={() => (moving = null)} onpick={moveTo} />
{/if}
