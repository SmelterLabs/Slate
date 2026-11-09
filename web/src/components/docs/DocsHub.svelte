<script lang="ts">
  // The project home's documents panel: the section's drawers with
  // counts, its pages outside any drawer, and what needs the viewer (asks to them, others'
  // open review threads on documents they touched), each one click from its thread. No feed.
  import { onDestroy } from 'svelte';
  import { docGlyph, type SectionDocuments } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';

  let { sectionId, canEdit = false }: { sectionId: string; canEdit?: boolean } = $props();

  let hub = $state<SectionDocuments | null>(null);
  let failed = $state(false);
  let adding = $state(false);
  let newName = $state('');
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function load() {
    const gen = ++generation;
    try {
      const out = await docs.hub(sectionId);
      if (gen === generation) {
        hub = out;
        failed = false;
      }
    } catch {
      if (gen === generation) failed = true;
    }
  }
  $effect(() => {
    void sectionId;
    void load();
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
    off();
  });

  async function addDrawer() {
    const name = newName.trim();
    if (!name) return;
    const l = await board.createList(sectionId, name, 'drawer');
    adding = false;
    newName = '';
    if (l) location.hash = `#/list/${l.id}`;
  }
  const name = (id: string) => board.accountName(id);
</script>

<section class="docs-hub" aria-label="Project docs" data-testid="docs-hub">
  <h3>Drawers</h3>
  {#if failed}
    <p class="muted small">Could not load the documents.</p>
  {:else if !hub}
    <div class="sk-line w60"></div>
  {:else}
    {#each hub.drawers as d (d.list.id)}
      <a class="project-link hub-drawer" href="#/list/{d.list.id}" data-testid="hub-drawer">
        <span><Icon name="folder" size={13} /> {d.list.name}</span>
        <span class="muted">{d.counts.docs}{#if d.needs_you} · <span class="hub-need-count">{d.needs_you} need{d.needs_you === 1 ? 's' : ''} you</span>{/if}</span>
      </a>
    {/each}
    {#each hub.pages as p (p.id)}
      <a class="project-link" href="#/docs/{p.id}"><span>{p.icon ?? docGlyph(p)} {p.title}</span><span class="muted">page</span></a>
    {/each}
    {#if !hub.drawers.length && !hub.pages.length}<p class="muted small">No drawers or pages in this project yet.</p>{/if}
    {#if canEdit}
      {#if adding}
        <form class="hub-add" onsubmit={(e) => { e.preventDefault(); void addDrawer(); }}>
          <!-- svelte-ignore a11y_autofocus -->
          <input bind:value={newName} placeholder="Drawer name, e.g. Contracts" aria-label="New drawer name" maxlength="100" autofocus />
          <button class="chip-btn" disabled={!newName.trim()}>Create drawer</button>
          <button type="button" class="link" onclick={() => (adding = false)}>Cancel</button>
        </form>
      {:else}
        <span class="hub-actions"><button class="chip-btn" onclick={() => (adding = true)}>New drawer</button><a class="chip-btn" href="#/docs?new=1&in=s:{sectionId}">New page</a></span>
      {/if}
    {/if}
    {#if hub.needs_you.length}
      <div class="hub-needs" data-testid="hub-needs">
        <h4><Icon name="at" size={12} /> Needs you</h4>
        <ul>
          {#each hub.needs_you as n (n.comment.id)}
            <li>
              <a href={n.url.replace(/^\//, '')}>
                <span class="hub-need-kind" class:ask={n.kind === 'ask'}>{n.kind === 'ask' ? 'Asked' : 'Review'}</span>
                <span class="hub-need-doc">{n.doc.icon ?? docGlyph({ kind: n.doc.kind, file: null })} {n.doc.title}</span>
                <span class="hub-need-text muted">{name(n.comment.author_id) ?? 'Someone'}: {n.comment.body_md.split('\n')[0].slice(0, 100)}</span>
              </a>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/if}
</section>

<style>
  h3 {
    margin: 0 0 6px;
  }
  .hub-drawer {
    align-items: center;
  }
  .hub-drawer > span:first-child {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .hub-need-count {
    color: var(--accent);
    font-weight: 600;
  }
  .hub-add {
    display: flex;
    gap: 6px;
    align-items: center;
    margin-top: 6px;
  }
  .hub-add input {
    flex: 1;
    min-width: 0;
    padding: 5px 8px;
    border-radius: 6px;
  }
  .hub-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 6px;
  }
  .hub-needs {
    margin: 10px 0 0;
    padding: 8px 10px;
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    background: var(--accent-soft);
  }
  .hub-needs h4 {
    margin: 0 0 4px;
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: var(--fs-xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .hub-needs ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .hub-needs a {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 2px 8px;
    padding: 4px 0;
    font-size: var(--fs-sm);
  }
  .hub-need-kind {
    padding: 0 6px;
    border-radius: 8px;
    background: var(--chip);
    font-size: var(--fs-xs);
    align-self: center;
  }
  .hub-need-kind.ask {
    background: var(--accent);
    color: var(--accent-ink, #fff);
  }
  .hub-need-text {
    grid-column: 2;
    font-size: var(--fs-xs);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
