<script lang="ts">
  // One doc page: where it lives (breadcrumb), its icon and title, who changed it last, the body
  // editor, its subpages, the pages that link here, and an outline to jump through the
  // headings. Title, icon and body all autosave through lib/docs.svelte.ts.
  // A file doc (kind `file`) shows its current revision in FileView instead of the editor,
  // with Upload new revision, a Revisions panel in place of page history, and the body as
  // Notes under the file.
  import { fileSize } from '../../lib/activity';
  import { headings } from '../../lib/docmd';
  import { asMarkdownFile, docGlyph, filenameFor, ICON_CHOICES, placeOf, type Doc } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import Menu from '../Menu.svelte';
  import TagEditor from '../TagEditor.svelte';
  import DocBody from './DocBody.svelte';
  import DocDiscussion from './DocDiscussion.svelte';
  import DocHistory from './DocHistory.svelte';
  import FileRevisions from './FileRevisions.svelte';
  import FileView from './FileView.svelte';
  import RevisionCompare from './RevisionCompare.svelte';
  import Picker, { type PickerOption } from './Picker.svelte';
  import UploadRevision from './UploadRevision.svelte';

  let { doc, onmove, openDiscussion = false }: { doc: Doc; onmove: (id: string) => void; openDiscussion?: boolean } = $props();

  const draft = $derived(docs.draft);
  let iconOpen = $state(false);
  let history = $state(false);
  /** The discussion panel; history and discussion share the right-hand column. A drawer's
   * reading state opens it from the start. */
  // svelte-ignore state_referenced_locally
  let discussion = $state(openDiscussion);
  /** A top-level page in a section (not already in a drawer) can become a drawer. */
  const canBecomeDrawer = $derived(doc.kind === 'page' && !doc.parent_id && !doc.list_id && !!doc.section_id);
  async function turnIntoDrawer() {
    const l = await docs.toDrawer(doc.id);
    if (l) location.hash = `#/list/${l.id}`;
  }
  const openThreads = $derived(doc.open_threads ?? 0);
  const commentCount = $derived(doc.comment_count ?? 0);
  // A selection or a pin starts a review thread: show the panel.
  $effect(() => {
    if (docs.pendingAnchor || docs.focusComment) {
      discussion = true;
      history = false;
    }
  });
  // `?comment=<id>` (a push, the inbox, a copied link) opens the panel on that thread.
  $effect(() => {
    void doc.id;
    const q = new URLSearchParams(location.hash.split('?')[1] ?? '').get('comment');
    if (q) {
      docs.focusComment = q;
      window.history.replaceState(null, '', location.hash.split('?')[0]);
    }
  });
  let outlineOpen = $state(false);
  let confirmArchive = $state(false);

  // --- file docs ------------------------------------------------------------------------------
  const isFile = $derived(doc.kind === 'file');
  const current = $derived(doc.revisions[0] ?? null);
  /** An older revision being looked at (null = the current one). */
  let pickedRev = $state<number | null>(null);
  /** Two revisions being compared in place of the viewer. */
  let compare = $state<{ from: number; to: number } | null>(null);
  const shown = $derived(pickedRev === null ? current : (doc.revisions.find((r) => r.revision_no === pickedRev) ?? current));
  let uploadOpen = $state(false);
  let dropped = $state<File | null>(null);
  let uploading = $state(false);
  let dragOver = $state(false);
  $effect(() => {
    void doc.id;
    pickedRev = null;
    compare = null;
  });

  async function upload(file: File, note: string) {
    uploading = true;
    try {
      const rev = await docs.uploadRevision(doc.id, file, note);
      if (rev) {
        uploadOpen = false;
        dropped = null;
        pickedRev = null;
        board.flash(`Revision ${rev.revision_no} uploaded`);
      }
    } finally {
      uploading = false;
    }
  }

  function dropFile(e: DragEvent) {
    dragOver = false;
    if (!isFile) return;
    const f = e.dataTransfer?.files?.[0];
    if (!f) return;
    e.preventDefault();
    dropped = f;
    uploadOpen = true;
  }

  const place = $derived.by(() => {
    const top = doc.path[0] ? docs.byId[doc.path[0].id] : doc;
    const p = placeOf(top ?? doc);
    if (p.kind === 'list') {
      const l = board.lists[p.id];
      const s = board.sections.find((x) => x.id === l?.section_id);
      return [s?.name, l?.name].filter(Boolean).join(' / ') || 'A list';
    }
    if (p.kind === 'section') return board.sections.find((x) => x.id === p.id)?.name ?? 'A section';
    return 'Workspace';
  });
  const toc = $derived(headings(draft?.body_md ?? ''));
  const editor = $derived(board.accounts[doc.updated_by]?.display_name ?? 'Someone');
  const uploader = $derived(current ? (board.accounts[current.uploaded_by]?.display_name ?? 'Someone') : '');
  const fav = $derived(!!board.favoriteOf('doc', doc.id));

  function jump(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    outlineOpen = false;
  }

  function download() {
    const blob = new Blob([asMarkdownFile({ title: draft?.title ?? doc.title, icon: draft?.icon ?? doc.icon, body_md: draft?.body_md ?? doc.body_md })], {
      type: 'text/markdown',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filenameFor(doc.title);
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function copyLink() {
    await navigator.clipboard?.writeText(`${location.origin}${location.pathname}#/docs/${doc.id}`);
    board.flash('Link copied');
  }

  async function copyMarkdown() {
    await navigator.clipboard?.writeText(draft?.body_md ?? doc.body_md);
    board.flash('Markdown copied');
  }

  async function addSubpage() {
    const d = await docs.create({ parent_id: doc.id });
    if (d) location.hash = `#/docs/${d.id}`;
  }

  async function duplicate() {
    const d = await docs.duplicate(doc.id);
    if (d) location.hash = `#/docs/${d.id}`;
  }

  const outlineOptions = $derived(toc.map((h): PickerOption => ({ id: h.id, label: h.text, depth: h.level - 1 })));
</script>

<article
  class="doc-page"
  class:with-history={history || discussion}
  class:is-file={isFile}
  class:drag-over={dragOver}
  data-kind={doc.kind}
  ondragover={(e) => {
    if (isFile && e.dataTransfer?.types.includes('Files')) {
      e.preventDefault();
      dragOver = true;
    }
  }}
  ondragleave={() => (dragOver = false)}
  ondrop={dropFile}
>
  <div class="doc-main">
    <nav class="doc-crumbs" aria-label="Where this page is">
      <span class="crumb-place"><Icon name={place === 'Workspace' ? 'layers' : 'list'} size={13} />{place}</span>
      {#each doc.path as p (p.id)}
        <Icon name="chevronRight" size={12} />
        <a href="#/docs/{p.id}">{p.icon ? `${p.icon} ` : ''}{p.title}</a>
      {/each}
    </nav>

    {#if docs.phase === 'conflict'}
      <div class="doc-conflict" role="alert">
        <Icon name="alert" size={16} />
        <span><b>{board.accounts[docs.remote?.updated_by ?? '']?.display_name ?? 'Someone'}</b> changed this page while you were typing.</span>
        <button class="secondary" onclick={() => docs.downloadDraft()}>Download my draft</button>
        <button class="secondary" onclick={() => void docs.reload()}>Take theirs</button>
        <button class="primary" onclick={() => void docs.keepMine()}>Keep mine</button>
      </div>
    {:else if docs.saveFailed && docs.dirty}
      <div class="doc-conflict" role="alert">
        <span>Could not save. Your draft is still here.</span>
        <button class="secondary" onclick={() => docs.downloadDraft()}>Download my draft</button>
        <button class="primary" onclick={() => void docs.flush()}>Retry save</button>
      </div>
    {/if}

    <header class="doc-head">
      <div class="doc-icon-row">
        <span class="doc-icon-wrap">
          <button class="doc-icon" class:empty={!draft?.icon} aria-label={isFile ? 'File icon' : 'Page icon'} onclick={() => (iconOpen = !iconOpen)}>
            {draft?.icon || docGlyph(doc)}
          </button>
          {#if iconOpen}
            <div class="picker-scrim clear" role="presentation" onclick={() => (iconOpen = false)}></div>
            <div class="icon-grid" role="listbox" aria-label="Choose an icon">
              {#each ICON_CHOICES as ic (ic)}
                <button
                  role="option"
                  aria-selected={draft?.icon === ic}
                  onclick={() => {
                    docs.edit({ icon: ic });
                    iconOpen = false;
                    void docs.flush();
                  }}>{ic}</button
                >
              {/each}
              <button
                class="icon-none"
                onclick={() => {
                  docs.edit({ icon: null });
                  iconOpen = false;
                  void docs.flush();
                }}>No icon</button
              >
            </div>
          {/if}
        </span>
        <div class="doc-actions">
          <span class="doc-save" aria-live="polite">
            {#if docs.phase === 'saving' || docs.phase === 'pending'}<span class="muted">Saving…</span>
            {:else if docs.phase === 'saved'}<span class="ok">✓ Saved</span>
            {:else if docs.phase === 'error'}<button class="retry" onclick={() => void docs.flush()}>Not saved · Retry</button>{/if}
          </span>
          {#if isFile}
            <button class="secondary doc-upload-btn" onclick={() => (uploadOpen = true)}><Icon name="clip" size={13} /> {current ? 'Upload new revision' : 'Upload file'}</button>
          {/if}
          {#if toc.length && !isFile}
            <button class="icon doc-outline-btn" aria-label="Outline" title="Outline" onclick={() => (outlineOpen = true)}><Icon name="lines" size={16} /></button>
          {/if}
          <button class="icon" class:on={fav} aria-label={fav ? 'Remove from favorites' : 'Add to favorites'} title="Favorite" onclick={() => void board.toggleFavorite('doc', doc.id)}>
            <Icon name="star" size={16} />
          </button>
          <button
            class="icon dd-toggle"
            class:on={discussion}
            aria-label="Discussion"
            title="Discussion and review threads"
            onclick={() => {
              discussion = !discussion;
              if (discussion) history = false;
            }}
          >
            <Icon name="comment" size={16} />
            {#if openThreads}<span class="dd-badge open" title="{openThreads} open review thread{openThreads > 1 ? 's' : ''}">{openThreads}</span>
            {:else if commentCount}<span class="dd-badge">{commentCount}</span>{/if}
          </button>
          <button
            class="icon"
            class:on={history}
            aria-label={isFile ? 'Revisions' : 'Page history'}
            title={isFile ? 'Revisions' : 'Page history'}
            onclick={() => {
              history = !history;
              if (history) discussion = false;
            }}><Icon name="clock" size={16} /></button
          >
          <Menu
            label={isFile ? 'File actions' : 'Page actions'}
            entries={[
              ...(isFile
                ? [
                    { label: current ? 'Upload new revision' : 'Upload file', onselect: () => (uploadOpen = true) },
                    ...(current ? [{ label: `Download ${current.filename}`, onselect: () => void docs.download(current) }] : []),
                  ]
                : []),
              { label: 'Add a subpage', onselect: () => void addSubpage() },
              { label: 'Move…', onselect: () => onmove(doc.id) },
              ...(canBecomeDrawer ? [{ label: 'Turn into a drawer', onselect: () => void turnIntoDrawer() }] : []),
              { label: 'Duplicate', onselect: () => void duplicate() },
              { label: 'Copy link', onselect: () => void copyLink() },
              ...(isFile
                ? [{ label: 'Revisions', onselect: () => (history = true) }]
                : [
                    { label: 'Copy as Markdown', onselect: () => void copyMarkdown() },
                    { label: 'Download as Markdown', onselect: download },
                    { label: 'Page history', onselect: () => (history = true) },
                  ]),
              { label: 'Archive…', danger: true, onselect: () => (confirmArchive = true) },
            ]}
          />
        </div>
      </div>
      <textarea
        class="doc-title"
        rows="1"
        aria-label="Page title"
        placeholder="Untitled"
        maxlength="200"
        value={draft?.title ?? ''}
        oninput={(e) => {
          const el = e.target as HTMLTextAreaElement;
          docs.edit({ title: el.value.replace(/\n/g, ' ') });
          el.style.height = 'auto';
          el.style.height = `${el.scrollHeight}px`;
        }}
        onkeydown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            (document.querySelector('.doc-rendered') as HTMLElement | null)?.click();
          }
        }}
        onblur={(e) => {
          const el = e.target as HTMLTextAreaElement;
          if (!el.value.trim()) docs.edit({ title: 'Untitled' });
          void docs.flush();
        }}
      ></textarea>
      <div class="doc-tags" data-testid="doc-tags">
        <TagEditor tags={doc.tags ?? []} onchange={(tags) => void docs.setTags(doc.id, tags)} />
      </div>
      {#if isFile}
        <p class="doc-meta muted" data-testid="file-meta">
          {#if current}
            Revision {current.revision_no} · {current.filename} · {fileSize(current.size)} · uploaded {ago(current.created_at, board.now)} by {uploader}
            {#if doc.pinned}<span class="pinned-line" data-testid="pinned"><span class="pin-dot"></span> rev {doc.pinned.revision_no} in use{#if doc.pinned.note}: <i>{doc.pinned.note}</i>{/if}</span>{/if}
          {:else}
            No file yet · upload the first revision
          {/if}
        </p>
      {:else}
        <p class="doc-meta muted">
          Edited {ago(doc.updated_at, board.now)} by {editor} · {doc.words.toLocaleString()} words · version {doc.version_no}
        </p>
      {/if}
    </header>

    {#if confirmArchive}
      <div class="doc-confirm" role="alertdialog" aria-label="Archive this page">
        <span>Archive “{doc.title}”{doc.children.length ? ` and its ${doc.children.length} subpage${doc.children.length > 1 ? 's' : ''}` : ''}? You can restore it from Archived pages.</span>
        <button class="secondary" onclick={() => (confirmArchive = false)}>Cancel</button>
        <button
          class="danger-btn"
          onclick={() => {
            confirmArchive = false;
            const parent = doc.parent_id;
            void docs.archive(doc.id).then(() => (location.hash = parent ? `#/docs/${parent}` : '#/docs'));
          }}>Archive</button
        >
      </div>
    {/if}

    {#if isFile}
      {#if compare && current}
        <RevisionCompare docId={doc.id} revisions={doc.revisions} from={compare.from} to={compare.to} onchange={(f, t) => (compare = { from: f, to: t })} onclose={() => (compare = null)} />
      {:else if shown}
        {#if pickedRev !== null && shown.revision_no !== current?.revision_no}
          <div class="rev-banner" role="status">
            <Icon name="history" size={14} />
            <span>Viewing <b>revision {shown.revision_no}</b> of {current?.revision_no}, superseded {shown.superseded_at ? ago(shown.superseded_at, board.now) : ''} · <i>{shown.note}</i></span>
            <button class="link" onclick={() => (compare = { from: shown.revision_no, to: current?.revision_no ?? shown.revision_no })}>Compare with current</button>
            <button class="link" onclick={() => (pickedRev = null)}>Back to current</button>
          </div>
        {:else if current}
          <p class="rev-note-line muted small" data-testid="current-note"><Icon name="info" size={12} /> <span>Rev {current.revision_no}: {current.note}</span></p>
        {/if}
        <FileView revision={shown} />
      {:else}
        <div class="file-card file-empty">
          <Icon name="clip" size={20} />
          <span>Nothing uploaded yet. Drop a file here, or</span>
          <button class="primary" onclick={() => (uploadOpen = true)}>Upload the first revision</button>
        </div>
      {/if}
      <details class="file-notes" open={!!doc.body_md.trim()}>
        <summary><Icon name="chevronRight" size={12} /> Notes about this file</summary>
        {#key doc.id}
          <DocBody docId={doc.id} />
        {/key}
      </details>
    {:else}
      {#if doc.facts?.length}
        <!-- A record page: the leading ```facts block, read as key/value. The editor below
             keeps the block itself editable as text; there is no facts editor. -->
        <table class="doc-facts" aria-label="Facts">
          <tbody>
            {#each doc.facts as fact, n (n)}
              <tr><th scope="row">{fact.key}</th><td>{fact.value}</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#key doc.id}
        <DocBody docId={doc.id} />
      {/key}
    {/if}

    <section class="doc-sub" aria-label="Subpages">
      <header>
        <h3>Subpages</h3>
        <button class="secondary" onclick={() => void addSubpage()}><Icon name="plus" size={13} /> Add subpage</button>
      </header>
      {#if doc.children.length}
        <ul>
          {#each doc.children as c (c.id)}
            <li><a href="#/docs/{c.id}"><span class="doc-sub-icon">{c.icon ?? docGlyph(c)}</span>{c.title}{#if c.kind === 'file' && c.file}<span class="muted small"> rev {c.file.revision_no}</span>{/if}<span class="muted">{ago(c.updated_at, board.now)}</span></a></li>
          {/each}
        </ul>
      {/if}
    </section>

    {#if doc.backlinks.length}
      <section class="doc-sub" aria-label="Linked from">
        <header><h3>Linked from</h3></header>
        <ul>
          {#each doc.backlinks as b (b.id)}
            <li><a href="#/docs/{b.id}"><span class="doc-sub-icon">{b.icon ?? '📄'}</span>{b.title}</a></li>
          {/each}
        </ul>
      </section>
    {/if}
  </div>

  {#if toc.length > 1 && !history && !discussion}
    <aside class="doc-toc" aria-label="On this page">
      <h4>On this page</h4>
      {#each toc as h (h.id)}
        <button style="padding-left:{(h.level - 1) * 12}px" onclick={() => jump(h.id)}>{h.text}</button>
      {/each}
    </aside>
  {/if}

  {#if discussion}
    <DocDiscussion docId={doc.id} {isFile} viewedRevision={isFile ? shown?.revision_no : doc.revision} onclose={() => (discussion = false)} />
  {:else if history && isFile}
    <FileRevisions
      revisions={doc.revisions}
      picked={pickedRev}
      onpick={(n) => {
        pickedRev = n;
        compare = null;
      }}
      oncompare={(from) => {
        compare = { from, to: current?.revision_no ?? from };
        pickedRev = null;
      }}
      onclose={() => (history = false)}
    />
  {:else if history}
    <DocHistory docId={doc.id} onclose={() => (history = false)} />
  {/if}
</article>

{#if uploadOpen}
  <UploadRevision
    title={current ? `New revision of ${doc.title}` : `Upload ${doc.title}`}
    file={dropped}
    defaultNote={current ? '' : 'First upload'}
    busy={uploading}
    onsubmit={(f, n) => void upload(f, n)}
    onclose={() => {
      uploadOpen = false;
      dropped = null;
    }}
  />
{/if}

{#if outlineOpen}
  <Picker title="Jump to a heading" placeholder="Find a heading" options={outlineOptions} onclose={() => (outlineOpen = false)} onpick={jump} />
{/if}
