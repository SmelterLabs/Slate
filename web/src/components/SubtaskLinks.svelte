<script lang="ts">
  // Thin curves from each subtask card to its parent card, drawn over the board while
  // "Subtasks" is on. Positions come from the DOM (cards scroll inside their own columns and
  // the board scrolls sideways) and are redrawn on the next frame after any scroll, resize or
  // render. Same column: a tree elbow down the subtask's indent. Another column: an S-curve across the
  // gap. An end scrolled out of its column is pinned to the column's edge and drawn dashed. A
  // parent that is not on the board (filtered out, folded column, past "Show more") gets no
  // line; the card's "Subtask of …" label says whose it is instead.
  import type { Group } from '../lib/views';

  let { groups, host, hidden = false }: { groups: Group[]; host: HTMLElement | null; hidden?: boolean } = $props();

  type Link = { key: string; d: string; child: string; parent: string; dashed: boolean; x: number; y: number };
  type Box = { left: number; right: number; top: number; bottom: number; y: number; out: boolean; col: Element | null };

  let links = $state<Link[]>([]);
  let hot = $state<string | null>(null);
  let svg = $state<SVGSVGElement | null>(null);

  const parentOf = $derived.by(() => {
    const m = new Map<string, string>();
    for (const g of groups) for (const i of g.items) if (i.parent_id) m.set(i.id, i.parent_id);
    return m;
  });

  // How far below a card's top the line meets it: the first line of the title.
  const ANCHOR = 16;
  let raf = 0;
  let watch: (els: Element[]) => void = () => {};

  function schedule() {
    if (!raf) raf = requestAnimationFrame(measure);
  }

  function measure() {
    raf = 0;
    const el = host;
    if (!el || hidden || !parentOf.size) {
      if (links.length) links = [];
      return;
    }
    const origin = el.getBoundingClientRect();
    const ox = origin.left - el.scrollLeft;
    const oy = origin.top - el.scrollTop;
    const byId = new Map<string, HTMLElement[]>();
    for (const c of el.querySelectorAll<HTMLElement>('.cards > .card[data-item]')) {
      const id = c.dataset.item!;
      byId.set(id, [...(byId.get(id) ?? []), c]);
    }
    const box = (c: HTMLElement): Box => {
      const r = c.getBoundingClientRect();
      const view = c.parentElement!.getBoundingClientRect();
      let y = r.top + Math.min(ANCHOR, r.height / 2);
      const out = y < view.top || y > view.bottom;
      y = Math.min(Math.max(y, view.top), view.bottom);
      const clamp = (v: number) => Math.min(Math.max(v, view.top), view.bottom) - oy;
      return {
        left: r.left - ox,
        right: r.right - ox,
        top: clamp(r.top),
        bottom: clamp(r.bottom),
        y: y - oy,
        out,
        col: c.closest('.column'),
      };
    };
    const next: Link[] = [];
    const used: Element[] = [];
    for (const [childId, parentId] of parentOf) {
      const parents = byId.get(parentId);
      const kids = byId.get(childId);
      if (!parents || !kids) continue;
      const pb = parents.map(box);
      kids.forEach((k, n) => {
        const c = box(k);
        // An item can sit in more than one column (grouped by tag or assignee): use the nearest parent.
        const p = pb.reduce((a, b) => (Math.abs(b.left - c.left) < Math.abs(a.left - c.left) ? b : a));
        if (p.out && c.out && p.col === c.col) return;
        let d: string;
        let x: number;
        if (p.col === c.col) {
          // A tree elbow down the subtask's indent: from the parent's edge to the subtask's side.
          const x0 = Math.min(p.left, c.left) + 6;
          const y0 = c.y >= p.y ? p.bottom : p.top;
          x = c.left;
          d = `M${x0},${y0} C${x0},${c.y} ${x0},${c.y} ${x},${c.y}`;
        } else {
          const rightward = p.right <= c.left;
          const x0 = rightward ? p.right : p.left;
          x = rightward ? c.left : c.right;
          const dx = Math.max(24, Math.abs(x - x0) / 2) * (rightward ? 1 : -1);
          d = `M${x0},${p.y} C${x0 + dx},${p.y} ${x - dx},${c.y} ${x},${c.y}`;
        }
        next.push({ key: `${childId}:${n}`, d, child: childId, parent: parentId, dashed: p.out || c.out, x, y: c.y });
      });
      used.push(...parents, ...kids);
    }
    watch(used);
    links = next;
  }

  $effect(() => {
    void parentOf;
    void hidden;
    schedule();
  });

  $effect(() => {
    const el = host;
    if (!el) return;
    // Cards change size (a pill added, a title edited) without the board resizing.
    const seen = new WeakSet<Element>();
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    watch = (els) => {
      for (const c of els)
        if (!seen.has(c)) {
          seen.add(c);
          ro.observe(c);
        }
    };
    // Cards added, removed or moved; changes inside the drawing itself do not count.
    const mo = new MutationObserver((recs) => {
      if (recs.some((r) => !svg?.contains(r.target))) schedule();
    });
    mo.observe(el, { childList: true, subtree: true });
    const over = (e: Event) => {
      hot = (e.target as Element | null)?.closest?.<HTMLElement>('[data-item]')?.dataset.item ?? null;
    };
    const leave = () => (hot = null);
    el.addEventListener('scroll', schedule, { capture: true, passive: true });
    el.addEventListener('mouseover', over);
    el.addEventListener('focusin', over);
    el.addEventListener('mouseleave', leave);
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      ro.disconnect();
      mo.disconnect();
      watch = () => {};
      el.removeEventListener('scroll', schedule, { capture: true });
      el.removeEventListener('mouseover', over);
      el.removeEventListener('focusin', over);
      el.removeEventListener('mouseleave', leave);
      window.removeEventListener('resize', schedule);
      cancelAnimationFrame(raf);
      raf = 0;
    };
  });
</script>

<!-- Always in the page (an svg added or removed would itself count as a board change). It is
     1×1 and draws outside its box, so it never makes the board scroll further. -->
<svg class="subtask-links" class:off={hidden || !links.length} bind:this={svg} width="1" height="1" aria-hidden="true">
  {#each links as l (l.key)}
    <g class:hot={hot === l.child || hot === l.parent} class:dashed={l.dashed} data-child={l.child} data-parent={l.parent}>
      <path d={l.d} />
      <circle cx={l.x} cy={l.y} r="2.25" />
    </g>
  {/each}
</svg>

<style>
  .subtask-links {
    position: absolute;
    left: 0;
    top: 0;
    overflow: visible;
    pointer-events: none;
    z-index: 1;
  }
  .subtask-links.off {
    display: none;
  }
  path {
    fill: none;
    stroke: var(--accent);
    stroke-width: 1.4;
    stroke-linecap: round;
    opacity: 0.4;
    transition:
      opacity 0.12s,
      stroke-width 0.12s;
  }
  circle {
    fill: var(--accent);
    opacity: 0.55;
    transition: opacity 0.12s;
  }
  .dashed path {
    stroke-dasharray: 2 4;
  }
  .hot path {
    opacity: 0.95;
    stroke-width: 2;
  }
  .hot circle {
    opacity: 1;
  }
</style>
