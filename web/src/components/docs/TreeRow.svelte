<script lang="ts" module>
  export const DOC_DRAG = 'application/x-slate-doc';
</script>

<script lang="ts">
  // One page in the Docs tree, and its subpages when unfolded. With a mouse, drag a page onto
  // the top or bottom edge of another to put it before or after, or onto its middle to nest it.
  // The ⋯ menu does the same on any device (Move…), plus subpage, duplicate, favorite, archive.
  import { docGlyph, type TreeNode } from '../../lib/docs';
  import { docs } from '../../lib/docs.svelte';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import Menu from '../Menu.svelte';
  import TreeRow from './TreeRow.svelte';

  let {
    node,
    activeId,
    expanded,
    ontoggle,
    onmove,
    dragging = $bindable(null),
  }: {
    node: TreeNode;
    activeId: string | null;
    expanded: Set<string>;
    ontoggle: (id: string) => void;
    onmove: (id: string) => void;
    dragging?: string | null;
  } = $props();

  const d = $derived(node.doc);
  const open = $derived(expanded.has(d.id));
  let zone = $state<'before' | 'inside' | 'after' | null>(null);

  function over(e: DragEvent) {
    if (!dragging || dragging === d.id) return;
    e.preventDefault();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const y = (e.clientY - r.top) / r.height;
    zone = y < 0.28 ? 'before' : y > 0.72 ? 'after' : 'inside';
  }

  function dropHere(e: DragEvent) {
    e.preventDefault();
    const id = dragging ?? e.dataTransfer?.getData(DOC_DRAG);
    const z = zone;
    zone = null;
    dragging = null;
    if (!id || id === d.id || !z) return;
    if (z === 'inside') {
      void docs.move(id, { parent_id: d.id });
      if (!open) ontoggle(d.id);
      return;
    }
    // Before or after this page, among its siblings.
    const siblings = docs.list
      .filter((x) => x.parent_id === d.parent_id && (d.parent_id || (x.list_id === d.list_id && x.section_id === d.section_id)))
      .sort((a, b) => a.position - b.position);
    const at = siblings.findIndex((x) => x.id === d.id);
    const beforeId = z === 'before' ? d.id : (siblings[at + 1]?.id ?? null);
    const place = d.list_id ? { kind: 'list' as const, id: d.list_id } : d.section_id ? { kind: 'section' as const, id: d.section_id } : { kind: 'workspace' as const };
    void docs.move(id, d.parent_id ? { parent_id: d.parent_id, before_id: beforeId } : { place, before_id: beforeId });
  }

  async function addSub() {
    const made = await docs.create({ parent_id: d.id });
    if (!made) return;
    if (!open) ontoggle(d.id);
    location.hash = `#/docs/${made.id}`;
  }
</script>

<li class="tree-li">
  <div
    class="tree-row"
    class:active={activeId === d.id}
    class:drop-before={zone === 'before'}
    class:drop-inside={zone === 'inside'}
    class:drop-after={zone === 'after'}
    class:ghost={dragging === d.id}
    style="--depth:{node.depth}"
    role="treeitem"
    aria-selected={activeId === d.id}
    aria-expanded={node.children.length ? open : undefined}
    tabindex="-1"
    draggable="true"
    ondragstart={(e) => {
      dragging = d.id;
      e.dataTransfer?.setData(DOC_DRAG, d.id);
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    }}
    ondragend={() => {
      dragging = null;
      zone = null;
    }}
    ondragover={over}
    ondragleave={() => (zone = null)}
    ondrop={dropHere}
  >
    <button
      class="tree-fold"
      class:hidden={!node.children.length}
      aria-label={open ? `Fold ${d.title}` : `Unfold ${d.title}`}
      tabindex={node.children.length ? 0 : -1}
      onclick={() => ontoggle(d.id)}><Icon name={open ? 'chevron' : 'chevronRight'} size={12} /></button
    >
    <a class="tree-link" href="#/docs/{d.id}" draggable="false">
      <span class="tree-icon">{d.icon ?? docGlyph(d)}</span>
      <span class="tree-title">{d.title}</span>
      {#if d.kind === 'file' && d.file}<span class="tree-rev muted" title="{d.file.filename} · revision {d.file.revision_no}">r{d.file.revision_no}</span>{/if}
    </a>
    <span class="tree-tools">
      <button class="icon" aria-label="Add a subpage to {d.title}" title="Add subpage" onclick={() => void addSub()}><Icon name="plus" size={13} /></button>
      <Menu
        label="Actions for {d.title}"
        entries={[
          { label: 'Add a subpage', onselect: () => void addSub() },
          { label: 'Move…', onselect: () => onmove(d.id) },
          {
            label: 'Duplicate',
            onselect: () => void docs.duplicate(d.id).then((c) => c && (location.hash = `#/docs/${c.id}`)),
          },
          {
            label: board.favoriteOf('doc', d.id) ? 'Remove from favorites' : 'Add to favorites',
            onselect: () => void board.toggleFavorite('doc', d.id),
          },
          { label: 'Archive', danger: true, onselect: () => void docs.archive(d.id) },
        ]}
      />
    </span>
  </div>
  {#if open && node.children.length}
    <ul class="tree-ul" role="group">
      {#each node.children as child (child.doc.id)}
        <TreeRow node={child} {activeId} {expanded} {ontoggle} {onmove} bind:dragging />
      {/each}
    </ul>
  {/if}
</li>
