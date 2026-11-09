<script lang="ts">
  // Docs (#/docs[/<id>]): ClickUp-style pages. On the left, every page as a tree, grouped by
  // where it lives (the workspace, a section, a list), with search over titles and text. On the
  // right, the open page (DocPage), or recent pages and templates. On a phone the tree is the
  // first screen and a page opens over it ("All pages" goes back): the same features, stacked.
  import { onMount, untrack } from 'svelte';
  import {
    buildTree,
    descendants,
    docGlyph,
    filterDocs,
    placeKey,
    placeOf,
    sortTree,
    tagCounts,
    type DocKind,
    type DocSummary,
    type FileGroup,
    type Place,
    type TreeFilter,
    type TreeNode,
    type TreeSort,
  } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { ago } from '../../lib/format';
  import { inbox } from '../../lib/inbox.svelte';
  import { board } from '../../lib/store.svelte';
  import EmptyState from '../EmptyState.svelte';
  import Icon from '../Icon.svelte';
  import DocPage from './DocPage.svelte';
  import Picker, { type PickerOption } from './Picker.svelte';
  import TreeRow from './TreeRow.svelte';
  import UploadRevision from './UploadRevision.svelte';

  let { id = null }: { id?: string | null } = $props();

  onMount(() => {
    docs.start();
    void docs.loadTemplates().catch(() => {});
  });
  $effect(() => {
    void docs.open(id);
    // Opening a page reads its mentions in the inbox, as opening a task or a channel does.
    const open = id;
    if (open) untrack(() => inbox.readDoc(open));
  });

  // --- the tree -------------------------------------------------------------------------------

  let filter = $state('');
  let hits = $state<DocSummary[] | null>(null);
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const q = filter.trim();
    clearTimeout(searchTimer);
    if (q.length < 2) {
      hits = null;
      return;
    }
    searchTimer = setTimeout(() => {
      void docs.search(q, 30).then((r) => (filter.trim() === q ? (hits = r) : null), () => {});
    }, 180);
  });

  // --- filters and sort (kept per device) --------------------------------------------------
  const FILTER_KEY = 'slate.docs.filter';
  function readFilter(): { kind: DocKind | null; group: FileGroup | null; tag: string | null; sort: TreeSort } {
    try {
      const raw = localStorage.getItem(FILTER_KEY);
      if (raw) return { kind: null, group: null, tag: null, sort: 'tree', ...(JSON.parse(raw) as object) };
    } catch {
      /* private mode */
    }
    return { kind: null, group: null, tag: null, sort: 'tree' };
  }
  let flt = $state(readFilter());
  $effect(() => {
    try {
      localStorage.setItem(FILTER_KEY, JSON.stringify(flt));
    } catch {
      /* private mode */
    }
  });
  const filtering = $derived(!!(flt.kind || flt.group || flt.tag));
  const tagChips = $derived(tagCounts(docs.list).slice(0, 12));
  const GROUPS: { key: FileGroup; label: string }[] = [
    { key: 'pdf', label: 'PDFs' },
    { key: 'sheet', label: 'Sheets' },
    { key: 'image', label: 'Images' },
    { key: 'text', label: 'Text' },
    { key: 'other', label: 'Other' },
  ];
  function setKind(k: DocKind | null) {
    flt = { ...flt, kind: k, group: k === 'file' ? flt.group : null };
  }
  const shownDocs = $derived(filterDocs(docs.list, flt as TreeFilter));
  const tree = $derived.by(() => {
    const t = buildTree(shownDocs);
    if (flt.sort === 'tree') return t;
    return new Map([...t.entries()].map(([k, nodes]) => [k, sortTree(nodes, flt.sort)]));
  });
  const expanded = $derived.by(() => {
    const set = new Set(board.prefs.docs_expanded ?? []);
    // The open page's ancestors are always unfolded, so it shows in the tree.
    let cur = id ? docs.byId[id] : undefined;
    while (cur?.parent_id) {
      set.add(cur.parent_id);
      cur = docs.byId[cur.parent_id];
    }
    return set;
  });
  function toggle(docId: string) {
    const now = new Set(board.prefs.docs_expanded ?? []);
    if (now.has(docId)) now.delete(docId);
    else now.add(docId);
    void board.setPrefs({ docs_expanded: [...now].slice(-300) });
  }

  interface Group {
    key: string;
    place: Place;
    label: string;
    sub?: string;
    nodes: TreeNode[];
  }
  const groups = $derived.by((): Group[] => {
    const out: Group[] = [];
    const ws = tree.get('ws');
    if (ws) out.push({ key: 'ws', place: { kind: 'workspace' }, label: 'Workspace', nodes: ws });
    for (const sec of board.sections) {
      const own = tree.get(`s:${sec.id}`);
      if (own) out.push({ key: `s:${sec.id}`, place: { kind: 'section', id: sec.id }, label: sec.name, nodes: own });
      for (const l of board.listsOf(sec.id)) {
        const nodes = tree.get(`l:${l.id}`);
        if (nodes) out.push({ key: `l:${l.id}`, place: { kind: 'list', id: l.id }, label: l.name, sub: sec.name, nodes });
      }
    }
    return out;
  });
  let dragging = $state<string | null>(null);
  let dropGroup = $state<string | null>(null);

  // --- new page -------------------------------------------------------------------------------

  let creating = $state(false);
  let newPlace = $state('ws');
  function placeFromKey(key: string): Place {
    if (key.startsWith('l:')) return { kind: 'list', id: key.slice(2) };
    if (key.startsWith('s:')) return { kind: 'section', id: key.slice(2) };
    return { kind: 'workspace' };
  }
  function openNew(place?: Place) {
    const fromDoc = docs.current ? placeOf(docs.byId[docs.current.path[0]?.id ?? docs.current.id] ?? docs.current) : null;
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '').get('in');
    newPlace = place ? placeKey(place) : (q ?? (fromDoc ? placeKey(fromDoc) : 'ws'));
    creating = true;
  }
  async function create(template: string) {
    creating = false;
    const d = await docs.create({ place: placeFromKey(newPlace), template });
    if (d) location.hash = `#/docs/${d.id}`;
  }

  // --- upload a file (a file doc with revision 1) ---------------------------------------------

  let uploadOpen = $state(false);
  let uploading = $state(false);
  let uploadPlace = $state('ws');
  function openUpload(place?: Place) {
    creating = false;
    uploadPlace = place ? placeKey(place) : newPlace;
    uploadOpen = true;
  }
  async function uploadNew(file: File, note: string) {
    uploading = true;
    try {
      const d = await docs.createFile({ place: placeFromKey(uploadPlace) }, file, note);
      if (d) {
        uploadOpen = false;
        location.hash = `#/docs/${d.id}`;
      }
    } finally {
      uploading = false;
    }
  }
  // `#/docs?new=1[&in=l:<list>]` (from a list's menu in the nav) opens the new-page sheet;
  // `#/docs?upload=1[&in=s:<section>]` (from a project home) the upload sheet.
  function checkNew() {
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '');
    if (q.get('upload')) {
      newPlace = q.get('in') ?? 'ws';
      openUpload();
    } else if (q.get('new')) openNew();
    else return;
    history.replaceState(null, '', location.hash.split('?')[0]);
  }
  onMount(() => {
    checkNew();
    window.addEventListener('hashchange', checkNew);
    return () => window.removeEventListener('hashchange', checkNew);
  });

  // --- move -----------------------------------------------------------------------------------

  let moving = $state<string | null>(null);
  const moveOptions = $derived.by((): PickerOption[] => {
    if (!moving) return [];
    const banned = descendants(moving, docs.list);
    banned.add(moving);
    const out: PickerOption[] = [{ id: 'p:ws', label: 'Workspace (top level)', icon: '🗂️' }];
    for (const sec of board.sections) {
      out.push({ id: `p:s:${sec.id}`, label: `${sec.name} (top level)`, icon: '▤' });
      for (const l of board.listsOf(sec.id)) out.push({ id: `p:l:${l.id}`, label: l.name, hint: sec.name, icon: '☰', depth: 1 });
    }
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        if (banned.has(n.doc.id)) continue;
        out.push({ id: `d:${n.doc.id}`, label: n.doc.title, hint: 'Inside this page', icon: n.doc.icon ?? '📄', depth: n.depth });
        walk(n.children);
      }
    };
    for (const g of groups) walk(g.nodes);
    return out;
  });
  function moveTo(target: string) {
    const docId = moving;
    moving = null;
    if (!docId) return;
    if (target.startsWith('d:')) void docs.move(docId, { parent_id: target.slice(2) });
    else void docs.move(docId, { place: placeFromKey(target.slice(2)) });
  }

  // --- archived ---------------------------------------------------------------------------------

  let showArchived = $state(false);
  let archived = $state<DocSummary[]>([]);
  async function loadArchived() {
    archived = await docs.archived();
  }
  $effect(() => {
    if (showArchived) {
      void docs.list;
      void loadArchived();
    }
  });
  let confirmDelete = $state<string | null>(null);

  const recent = $derived([...docs.list].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 9));
  const placeName = (d: DocSummary): string => {
    const top = docs.byId[d.id] ? (pathTop(d) ?? d) : d;
    const p = placeOf(top);
    if (p.kind === 'list') return board.lists[p.id]?.name ?? 'List';
    if (p.kind === 'section') return board.sections.find((s) => s.id === p.id)?.name ?? 'Section';
    return 'Workspace';
  };
  function pathTop(d: DocSummary): DocSummary | undefined {
    let cur: DocSummary | undefined = d;
    const seen = new Set<string>();
    while (cur?.parent_id && !seen.has(cur.id)) {
      seen.add(cur.id);
      cur = docs.byId[cur.parent_id];
    }
    return cur;
  }
</script>

<section class="docs-shell" class:has-doc={!!id} aria-label="Docs & Files">
  <aside class="docs-side" aria-label="All pages and files">
    <header class="docs-side-head">
      <h2>Pages & files</h2>
      <span class="docs-side-btns">
        <button class="secondary" onclick={() => openUpload()} title="Upload a file (PDF, spreadsheet, photo) as a document with revisions"><Icon name="clip" size={14} /> Upload</button>
        <button class="primary" onclick={() => openNew()}><Icon name="plus" size={14} /> New page</button>
      </span>
    </header>
    <label class="docs-search">
      <Icon name="search" size={14} />
      <input bind:value={filter} placeholder="Search pages, files and text" aria-label="Search docs" />
      {#if filter}<button class="icon" aria-label="Clear search" onclick={() => (filter = '')}><Icon name="x" size={12} /></button>{/if}
    </label>

    <div class="docs-filters" role="group" aria-label="Filter and sort">
      <span class="docs-chips">
        <button class="chip" class:on={!flt.kind} onclick={() => setKind(null)}>All</button>
        <button class="chip" class:on={flt.kind === 'page'} onclick={() => setKind('page')}>Pages</button>
        <button class="chip" class:on={flt.kind === 'file'} onclick={() => setKind('file')}>Files</button>
        {#if flt.kind === 'file'}
          {#each GROUPS as g (g.key)}
            <button class="chip sub" class:on={flt.group === g.key} onclick={() => (flt = { ...flt, group: flt.group === g.key ? null : g.key })}>{g.label}</button>
          {/each}
        {/if}
      </span>
      <select class="docs-sort" bind:value={flt.sort} aria-label="Sort">
        <option value="tree">Tree order</option>
        <option value="updated">Recently changed</option>
        <option value="name">Name</option>
      </select>
    </div>
    {#if tagChips.length}
      <div class="docs-tags" role="group" aria-label="Filter by tag">
        {#each tagChips as t (t.name)}
          <button class="chip tag" class:on={flt.tag === t.name} onclick={() => (flt = { ...flt, tag: flt.tag === t.name ? null : t.name })}>#{t.name} <span class="muted">{t.count}</span></button>
        {/each}
      </div>
    {/if}

    {#if filter.trim()}
      <ul class="docs-hits">
        {#each hits ?? docs.list.filter((d) => d.title.toLowerCase().includes(filter.trim().toLowerCase())) as h (h.id)}
          <li>
            <a href="#/docs/{h.id}" class:active={h.id === id}>
              <span class="tree-icon">{h.icon ?? docGlyph(h)}</span>
              <span class="hit-text"><b>{h.title}</b>{#if h.snippet}<span class="muted">{h.snippet}</span>{:else if h.kind === 'file' && h.file}<span class="muted">{h.file.filename} · rev {h.file.revision_no}</span>{/if}</span>
            </a>
          </li>
        {:else}
          <li class="muted pad">No page or file matches.</li>
        {/each}
      </ul>
    {:else}
      <div class="docs-tree" role="tree" aria-label="Pages and files">
        {#each groups as g (g.key)}
          <div
            class="tree-group"
            class:drop={dropGroup === g.key}
            role="group"
            aria-label={g.label}
            ondragover={(e) => {
              if (!dragging || (e.target as HTMLElement).closest('.tree-row')) return;
              e.preventDefault();
              dropGroup = g.key;
            }}
            ondragleave={() => (dropGroup = null)}
            ondrop={(e) => {
              if ((e.target as HTMLElement).closest('.tree-row')) return;
              e.preventDefault();
              const docId = dragging;
              dragging = null;
              dropGroup = null;
              if (docId) void docs.move(docId, { place: g.place });
            }}
          >
            <div class="tree-group-head">
              <span class="tg-label"><Icon name={g.place.kind === 'list' ? 'list' : g.place.kind === 'section' ? 'folder' : 'layers'} size={12} />{g.label}{#if g.sub}<span class="muted"> · {g.sub}</span>{/if}</span>
              <button class="icon" aria-label="New page in {g.label}" title="New page here" onclick={() => openNew(g.place)}><Icon name="plus" size={12} /></button>
            </div>
            <ul class="tree-ul">
              {#each g.nodes as n (n.doc.id)}
                <TreeRow node={n} activeId={id} {expanded} ontoggle={toggle} onmove={(x) => (moving = x)} bind:dragging />
              {/each}
            </ul>
          </div>
        {:else}
          {#if docs.loaded && filtering}
            <p class="muted pad">Nothing matches this filter. <button class="link" onclick={() => (flt = { ...flt, kind: null, group: null, tag: null })}>Show everything</button></p>
          {:else if docs.loaded}
            <p class="muted pad">Nothing here yet. Start a page for a plan or meeting notes, or upload a PDF, drawing or spreadsheet.</p>
          {/if}
        {/each}
      </div>
    {/if}

    <footer class="docs-side-foot">
      <button class="link" onclick={() => (showArchived = !showArchived)}>
        <Icon name="archive" size={13} /> {showArchived ? 'Hide archived pages' : 'Archived pages'}
      </button>
      {#if showArchived}
        <ul class="docs-archived">
          {#each archived as a (a.id)}
            <li>
              <span class="tree-icon">{a.icon ?? docGlyph(a)}</span>
              <span class="tree-title">{a.title}</span>
              {#if confirmDelete === a.id}
                <button class="danger-btn" onclick={() => void docs.remove(a.id).then(() => { confirmDelete = null; void loadArchived(); })}>Delete forever</button>
                <button class="link" onclick={() => (confirmDelete = null)}>Cancel</button>
              {:else}
                <button class="link" onclick={() => void docs.unarchive(a.id).then(loadArchived)}>Restore</button>
                <button class="link danger" onclick={() => (confirmDelete = a.id)}>Delete</button>
              {/if}
            </li>
          {:else}
            <li class="muted">Nothing archived.</li>
          {/each}
        </ul>
      {/if}
    </footer>
  </aside>

  <div class="docs-main">
    {#if id}
      <a class="docs-back" href="#/docs"><Icon name="chevronLeft" size={14} /> All pages and files</a>
      {#if docs.current && docs.current.id === id}
        <DocPage doc={docs.current} onmove={(x) => (moving = x)} />
      {:else if docs.missing}
        <EmptyState icon="archive" title="That page is gone" hint="It was archived or deleted. Archived pages can be restored from the list on the left.">
          <a class="primary" href="#/docs">All pages</a>
        </EmptyState>
      {:else}
        <div class="doc-loading" aria-busy="true"><div class="sk-line w60"></div><div class="sk-line"></div><div class="sk-line w80"></div></div>
      {/if}
    {:else}
      <div class="docs-home">
        <header class="work-head">
          <div>
            <h1>Docs & Files</h1>
            <p class="muted">Pages, drawings, quotes and spreadsheets next to the work, every file with its revisions. Agents read and write them too.</p>
          </div>
        </header>
        {#if recent.length}
          <h2 class="docs-h">Recent</h2>
          <div class="docs-cards">
            {#each recent as d (d.id)}
              <a class="doc-card" href="#/docs/{d.id}">
                <span class="dc-icon">{d.icon ?? docGlyph(d)}</span>
                <span class="dc-title">{d.title}</span>
                <span class="dc-meta muted">{#if d.kind === 'file' && d.file}rev {d.file.revision_no} · {/if}{placeName(d)} · {ago(d.updated_at, board.now)} by {board.accounts[d.updated_by]?.display_name ?? 'someone'}</span>
              </a>
            {/each}
          </div>
        {/if}
        <h2 class="docs-h">Start from a template</h2>
        <div class="docs-cards templates">
          <button class="doc-card" onclick={() => openUpload({ kind: 'workspace' })}>
            <span class="dc-icon">📎</span>
            <span class="dc-title">Upload a file</span>
            <span class="dc-meta muted">A PDF, spreadsheet, drawing or photo, with revisions</span>
          </button>
          {#each docs.templates as t (t.key)}
            <button class="doc-card" onclick={() => { newPlace = 'ws'; void create(t.key); }}>
              <span class="dc-icon">{t.icon || '📄'}</span>
              <span class="dc-title">{t.label}</span>
              <span class="dc-meta muted">{t.body_md ? t.body_md.split('\n').find((l) => l.startsWith('## '))?.slice(3) ?? 'A ready outline' : 'An empty page'}</span>
            </button>
          {/each}
        </div>
      </div>
    {/if}
  </div>
</section>

{#if creating}
  <div class="picker-scrim" role="presentation" onclick={() => (creating = false)}></div>
  <div class="picker new-doc" role="dialog" aria-label="New page">
    <header>New page</header>
    <label class="nd-where">
      <span>Where</span>
      <select bind:value={newPlace}>
        <option value="ws">Workspace</option>
        {#each board.sections as sec (sec.id)}
          <optgroup label={sec.name}>
            <option value="s:{sec.id}">{sec.name} (whole section)</option>
            {#each board.listsOf(sec.id) as l (l.id)}<option value="l:{l.id}">{l.name}</option>{/each}
          </optgroup>
        {/each}
      </select>
    </label>
    <div class="nd-templates">
      {#each docs.templates as t (t.key)}
        <button onclick={() => void create(t.key)}>
          <span class="dc-icon">{t.icon || '📄'}</span>
          <span>{t.label}</span>
        </button>
      {/each}
      <button class="nd-upload" onclick={() => openUpload()}>
        <span class="dc-icon">📎</span>
        <span>Upload a file</span>
      </button>
    </div>
  </div>
{/if}

{#if uploadOpen}
  <UploadRevision title="Upload a file" defaultNote="First upload" busy={uploading} onsubmit={(f, n) => void uploadNew(f, n)} onclose={() => (uploadOpen = false)} />
{/if}

{#if moving}
  <Picker title="Move “{docs.byId[moving]?.title ?? 'page'}” to…" placeholder="Find a place or a page" options={moveOptions} onclose={() => (moving = null)} onpick={moveTo} />
{/if}
