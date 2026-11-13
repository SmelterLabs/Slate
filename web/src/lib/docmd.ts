// The Markdown renderer for doc pages: everything the task renderer (markdown.ts) does plus real
// heading levels with anchors, check lists, images and Slate embeds. Both renderers run on the
// one engine in lib/mdcore.ts (escape first, stash finished markup), so the HTML is safe for
// {@html} and the two cannot drift apart.
//
// The page renders as blocks: runs of HTML, and a `task` block for every `[[task:<id>]]` that
// stands on a line of its own (the page shows a live task card there). `[[task:<id>]]` inside a
// sentence is a chip and `[[doc:<id>]]` a link to the page, resolved through `ctx`. Images that
// point at an uploaded doc file carry `data-file` instead of `src`: the page fetches them with
// the bearer token (lib/docs.svelte.ts `fileUrl`).

import { FILE_URL, HEADING, escape, renderBlocks, renderInline, type Block, type Keep } from './mdcore';

export { escape, type Block };

export interface DocCtx {
  task(id: string): { title: string; done: boolean; color: string } | null;
  doc(id: string): { title: string; icon: string | null } | null;
}

export const EMBED_LINE = /^\s*\[\[task:([0-9A-Za-z]{12})\]\]\s*$/;

/** "Next steps" -> "next-steps"; the page adds -2, -3 for repeats. */
export function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/<[^>]+>/g, '')
      .replace(/&[a-z#0-9]+;/g, '')
      .replace(/[^\p{L}\p{N}]+/gu, '-')
      .replace(/^-+|-+$/g, '') || 'section'
  );
}

function tokens(out: string, keep: Keep, ctx: DocCtx): string {
  return out
    .replace(/\[\[task:([0-9A-Za-z]{12})\]\]/g, (_, id: string) => {
      const t = ctx.task(id);
      if (!t) return keep(`<span class="task-chip missing" data-task="${id}">Task not found</span>`);
      return keep(
        `<a class="task-chip${t.done ? ' done' : ''}" href="#task=${id}" data-task="${id}">` +
          `<span class="dot" style="background:${escape(t.color)}"></span>${escape(t.title)}</a>`,
      );
    })
    .replace(/\[\[doc:([0-9A-Za-z]{12})\]\]/g, (_, id: string) => {
      const d = ctx.doc(id);
      if (!d) return keep(`<span class="doc-link missing" data-doc="${id}">Page not found</span>`);
      return keep(
        `<a class="doc-link" href="#/docs/${id}" data-doc="${id}">${d.icon ? `${escape(d.icon)} ` : ''}${escape(d.title)}</a>`,
      );
    });
}

/** `alt` and `url` arrive escaped, so both are safe inside a quoted attribute. */
function image(alt: string, url: string): string | null {
  const file = FILE_URL.exec(url);
  if (file) return `<img class="doc-img" data-file="${file[1]}" alt="${alt}" loading="lazy">`;
  if (/^https:/i.test(url)) return `<img class="doc-img" src="${url}" alt="${alt}" loading="lazy">`;
  return null;
}

export function inline(src: string, ctx: DocCtx): string {
  return renderInline(src, { tokens: (out, keep) => tokens(out, keep, ctx), image, internalLinks: true });
}

/** Render a page body into blocks. Line numbers are 0-based source lines (for tick boxes).
 *
 * With `split`, every paragraph, heading, list, table, quote, code block and divider is a block
 * of its own and carries `src`: its exact source text, trailing blank lines included, so the
 * blocks' `src` joined is the body again. The editor uses it to keep the text of blocks nobody
 * touched exactly as it was (lib/richdoc.ts). */
export function renderDoc(src: string, ctx: DocCtx, opts: { split?: boolean } = {}): Block[] {
  const seen = new Map<string, number>();
  return renderBlocks(src, {
    inline: (s) => inline(s, ctx),
    heading: (level, text, line) => {
      const base = slug(text);
      const n = (seen.get(base) ?? 0) + 1;
      seen.set(base, n);
      const id = n > 1 ? `${base}-${n}` : base;
      return `<h${level} id="h-${id}" data-line="${line}">${inline(text, ctx)}</h${level}>`;
    },
    check: (ticked, line, html) =>
      `<span class="md-check${ticked ? ' ticked' : ''}"><input type="checkbox" class="md-task" data-line="${line}"${ticked ? ' checked' : ''}><span>${html}</span></span>`,
    checkListClass: 'checks',
    embed: (line) => EMBED_LINE.exec(line)?.[1] ?? null,
    split: opts.split,
  });
}

export interface Heading {
  level: number;
  text: string;
  id: string;
  line: number;
}

/** The headings of a body, with the anchors renderDoc gives them (outside code fences). */
export function headings(src: string): Heading[] {
  const out: Heading[] = [];
  const seen = new Map<string, number>();
  let fenced = false;
  src.split('\n').forEach((line, i) => {
    if (/^\s*```/.test(line)) {
      fenced = !fenced;
      return;
    }
    const m = fenced ? null : HEADING.exec(line);
    if (!m || !m[2]) return;
    const base = slug(m[2]);
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    out.push({
      level: m[1].length,
      text: m[2].replace(/\[\[(task|doc):[0-9A-Za-z]{12}\]\]/g, '…').replace(/[*_`~]/g, ''),
      id: `h-${n > 1 ? `${base}-${n}` : base}`,
      line: i,
    });
  });
  return out;
}
