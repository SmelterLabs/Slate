// The Markdown renderer for item bodies, comments and chat: headings, paragraphs, bullet and
// numbered lists (nested by indent), task boxes, tables, quotes, dividers, fenced code, inline
// code, bold, italic, strikethrough, links. The engine is lib/mdcore.ts, shared with the doc page
// renderer (docmd.ts), so the two agree on syntax and on escaping; the output is safe for
// {@html}. Links are http(s)/mailto/obsidian only.
//
// Slate's own tokens: `[[doc:<id>]]` is a link to a page and `[[list:<id>]]` to a list (chat's
// `#` picker inserts them; a task filed from a doc says "From doc [[doc:<id>]]"). With `refs`
// they render as the page's icon and title or the list's name; a token the resolvers do not know
// (a page or list the reader cannot see, or one that is gone) renders as "a page" / "a list", so
// the link still opens (to the usual gone / not-shared screen) and nothing about it leaks.

import { escape, renderBlocks, renderInline, type Keep } from './mdcore';

/** Live names for Slate tokens; `undefined` when the reader does not know the page or list. */
export interface RefResolvers {
  doc?: (id: string) => { title: string; icon: string | null } | null | undefined;
  list?: (id: string) => string | null | undefined;
}

const ID = '([0-9A-Za-z]{12})';
const DOC_TOKEN = new RegExp(`\\[\\[doc:${ID}\\]\\]`, 'g');
const LIST_TOKEN = new RegExp(`\\[\\[list:${ID}\\]\\]`, 'g');

function docRef(id: string, refs?: RefResolvers): string {
  const d = refs?.doc?.(id);
  const text = d ? `${escape(d.icon || '📄')} ${escape(d.title)}` : '📄 a page';
  return `<a class="doc-ref" href="#/docs/${id}">${text}</a>`;
}

function listRef(id: string, refs?: RefResolvers): string {
  const name = refs?.list?.(id);
  return `<a class="list-ref" href="#/list/${id}">${name ? escape(name) : 'a list'}</a>`;
}

function inline(s: string, refs?: RefResolvers): string {
  return renderInline(s, {
    tokens: (out: string, keep: Keep) =>
      out
        .replace(DOC_TOKEN, (_, id: string) => keep(docRef(id, refs)))
        .replace(LIST_TOKEN, (_, id: string) => keep(listRef(id, refs))),
  });
}

/** `taskBoxes`: render `- [ ]` as real checkboxes carrying `data-line` (the source line);
 * `refs`: live names for `[[doc:<id>]]` and `[[list:<id>]]` tokens (RefResolvers). */
export function renderMarkdown(src: string, opts: { taskBoxes?: boolean; refs?: RefResolvers } = {}): string {
  const refs = opts.refs;
  return renderBlocks(src, {
    inline: (s) => inline(s, refs),
    // Inside a drawer or a card, headings start at h3.
    heading: (level, text) => {
      const h = Math.min(level + 2, 6);
      return `<h${h}>${inline(text, refs)}</h${h}>`;
    },
    check: (ticked, line, html) =>
      opts.taskBoxes
        ? `<input type="checkbox" class="md-task" data-line="${line}"${ticked ? ' checked' : ''}> ${html}`
        : `${ticked ? '☑' : '☐'} ${html}`,
  })
    .map((b) => (b.kind === 'html' ? b.html : ''))
    .join('');
}
