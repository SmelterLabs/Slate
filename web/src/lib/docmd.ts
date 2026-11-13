// The Markdown renderer for doc pages: everything the task renderer (markdown.ts) does plus real
// heading levels with anchors, tables, quotes, dividers, nested lists, strikethrough, images, and
// Slate embeds. Escape-first like markdown.ts, so the HTML is safe for {@html}.
//
// The page renders as blocks: runs of HTML, and a `task` block for every `[[task:<id>]]` that
// stands on a line of its own (the page shows a live task card there). `[[task:<id>]]` inside a
// sentence is a chip and `[[doc:<id>]]` a link to the page, resolved through `ctx`. Images that
// point at an uploaded doc file carry `data-file` instead of `src`: the page fetches them with
// the bearer token (lib/docs.svelte.ts `fileUrl`).

export interface DocCtx {
  task(id: string): { title: string; done: boolean; color: string } | null;
  doc(id: string): { title: string; icon: string | null } | null;
}

export type Block =
  | { kind: 'html'; html: string; line: number; src?: string }
  | { kind: 'task'; id: string; line: number; src?: string };

export const EMBED_LINE = /^\s*\[\[task:([0-9A-Za-z]{12})\]\]\s*$/;
const FILE_URL = /^\/api\/v1\/doc-files\/([0-9A-Za-z]{12})$/;

export function escape(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

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

function safeUrl(url: string): string | null {
  if (/^(https?:|mailto:|obsidian:)/i.test(url)) return url;
  if (/^#\//.test(url)) return url; // a link inside Slate
  if (FILE_URL.test(url)) return url;
  return null;
}

export function inline(src: string, ctx: DocCtx): string {
  const stash: string[] = [];
  const keep = (html: string) => {
    stash.push(html);
    return `\u0000${stash.length - 1}\u0000`;
  };
  let out = escape(src).replace(/`([^`]+)`/g, (_, c: string) => keep(`<code>${c}</code>`));
  out = out
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
    })
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt: string, raw: string) => {
      const url = raw.replace(/&amp;/g, '&');
      const file = FILE_URL.exec(url);
      if (file) return keep(`<img class="doc-img" data-file="${file[1]}" alt="${alt}" loading="lazy">`);
      if (/^https:/i.test(url)) return keep(`<img class="doc-img" src="${escape(url)}" alt="${alt}" loading="lazy">`);
      return alt;
    })
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, text: string, raw: string) => {
      const url = safeUrl(raw.replace(/&amp;/g, '&'));
      if (!url) return whole;
      const external = !url.startsWith('#') && !FILE_URL.test(url);
      return keep(
        `<a href="${escape(url)}"${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${text}</a>`,
      );
    })
    .replace(/(^|[\s(])(https?:\/\/[^\s<\u0000]+[^\s<\u0000.,;:!?)\]])/g, (_, pre: string, url: string) =>
      pre + keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`),
    )
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, '$1<em>$2</em>');
  // Stashed pieces can nest (a code span inside a link label); unwrap until none remain.
  for (let i = 0; i < 3 && out.includes('\u0000'); i++) {
    out = out.replace(/\u0000(\d+)\u0000/g, (_, n) => stash[Number(n)]);
  }
  return out;
}

const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

interface ListFrame {
  type: 'ul' | 'ol';
  indent: number;
}

/** Render a page body into blocks. Line numbers are 0-based source lines (for tick boxes).
 *
 * With `split`, every paragraph, heading, list, table, quote, code block and divider is a block
 * of its own and carries `src`: its exact source text, trailing blank lines included, so the
 * blocks' `src` joined is the body again. The editor uses it to keep the text of blocks nobody
 * touched exactly as it was (lib/richdoc.ts). */
export function renderDoc(src: string, ctx: DocCtx, opts: { split?: boolean } = {}): Block[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const split = !!opts.split;
  const blocks: Block[] = [];
  let html: string[] = [];
  let start = 0;
  let para: string[] = [];
  const lists: ListFrame[] = [];
  const seen = new Map<string, number>();

  const flushPara = () => {
    if (para.length) html.push(`<p>${para.map((l) => inline(l, ctx)).join('<br>')}</p>`);
    para = [];
  };
  const closeLists = (toIndent = -1) => {
    while (lists.length && lists[lists.length - 1].indent > toIndent) {
      html.push(`</li></${lists.pop()!.type}>`);
    }
  };
  const flushHtml = (line: number) => {
    flushPara();
    closeLists();
    if (html.length) blocks.push({ kind: 'html', html: html.join(''), line: start });
    html = [];
    start = line;
  };
  // In split mode, a new construct starting at line `i` ends the one before it.
  const boundary = (i: number) => {
    if (split && (html.length || para.length)) flushHtml(i);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const embed = EMBED_LINE.exec(line);
    if (embed) {
      flushHtml(i);
      blocks.push({ kind: 'task', id: embed[1], line: i });
      start = i + 1;
      continue;
    }
    if (/^\s*```/.test(line)) {
      boundary(i);
      flushPara();
      closeLists();
      const lang = line.trim().slice(3).trim().replace(/[^\w+-]/g, '');
      const body: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      html.push(`<pre><code${lang ? ` class="lang-${lang}"` : ''}>${escape(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      boundary(i);
      flushPara();
      closeLists();
      html.push('<hr>');
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      boundary(i);
      flushPara();
      closeLists();
      const level = heading[1].length;
      const text = inline(heading[2], ctx);
      const base = slug(heading[2]);
      const n = (seen.get(base) ?? 0) + 1;
      seen.set(base, n);
      const id = n > 1 ? `${base}-${n}` : base;
      html.push(`<h${level} id="h-${id}" data-line="${i}">${text}</h${level}>`);
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      boundary(i);
      flushPara();
      closeLists();
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '',
      );
      const th = head
        .map((c, k) => `<th${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(c, ctx)}</th>`)
        .join('');
      const rows: string[] = [];
      i += 2;
      for (; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) {
        const row = cells(lines[i]);
        rows.push(
          `<tr>${head
            .map((_, k) => `<td${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(row[k] ?? '', ctx)}</td>`)
            .join('')}</tr>`,
        );
      }
      i--;
      html.push(`<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      boundary(i);
      flushPara();
      closeLists();
      const quote: string[] = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*>\s?/, ''));
      i--;
      const paras = quote.join('\n').split(/\n\s*\n/).map((p) => `<p>${p.split('\n').map((l) => inline(l, ctx)).join('<br>')}</p>`);
      html.push(`<blockquote>${paras.join('')}</blockquote>`);
      continue;
    }
    const bullet = /^(\s*)[-*+]\s+(?:\[( |x|X)\]\s*)?(.*)$/.exec(line);
    const numbered = /^(\s*)(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      if (!lists.length) boundary(i);
      flushPara();
      const indent = (bullet ?? numbered)![1].replace(/\t/g, '    ').length;
      const type = bullet ? 'ul' : 'ol';
      const top = lists[lists.length - 1];
      if (!top || indent > top.indent) {
        const startAt = numbered && Number(numbered[2]) !== 1 ? ` start="${Number(numbered[2])}"` : '';
        html.push(`<${type}${startAt}${bullet?.[2] !== undefined ? ' class="checks"' : ''}><li>`);
        lists.push({ type, indent });
      } else {
        closeLists(indent);
        const cur = lists[lists.length - 1];
        if (cur && cur.type !== type) {
          html.push(`</li></${lists.pop()!.type}><${type}><li>`);
          lists.push({ type, indent });
        } else if (cur) {
          html.push('</li><li>');
        } else {
          html.push(`<${type}><li>`);
          lists.push({ type, indent });
        }
      }
      if (bullet?.[2] !== undefined) {
        const ticked = bullet[2].toLowerCase() === 'x';
        html.push(
          `<span class="md-check${ticked ? ' ticked' : ''}"><input type="checkbox" class="md-task" data-line="${i}"${ticked ? ' checked' : ''}><span>${inline(bullet[3], ctx)}</span></span>`,
        );
      } else {
        html.push(inline(bullet ? bullet[3] : numbered![3], ctx));
      }
      continue;
    }
    if (!line.trim()) {
      flushPara();
      // A blank line ends a list unless the next line carries it on.
      const next = lines[i + 1] ?? '';
      if (!/^\s*([-*+]|\d+[.)])\s+/.test(next)) closeLists();
      continue;
    }
    if (lists.length && /^\s+\S/.test(line)) {
      // A wrapped continuation of a list entry.
      html.push(`<br>${inline(line.trim(), ctx)}`);
      continue;
    }
    if (!para.length) boundary(i);
    closeLists();
    para.push(line);
  }
  flushHtml(lines.length);
  if (split) {
    // Each block's source runs from its first line to the next block's first line.
    blocks.forEach((b, k) => {
      const from = k === 0 ? 0 : b.line;
      const to = k + 1 < blocks.length ? blocks[k + 1].line : lines.length;
      b.src = lines.slice(from, to).join('\n') + (to < lines.length ? '\n' : '');
    });
  }
  return blocks;
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
    const m = fenced ? null : /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
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
