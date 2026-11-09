// A deliberately small markdown renderer for item bodies, comments and chat: headings,
// paragraphs, bullet and numbered lists (nested by indent), task boxes, tables, quotes, dividers,
// fenced code, inline code, bold, italic, strikethrough, links. Everything is HTML-escaped first,
// so the output is safe for {@html}. Links are http(s)/mailto/obsidian only. The doc page has
// its own, larger renderer (docmd.ts: anchors, images, embeds); the two agree on syntax.
//
// Slate's own tokens: `[[doc:<id>]]` is a link to a page and `[[list:<id>]]` to a list (chat's
// `#` picker inserts them; a task filed from a doc says "From doc [[doc:<id>]]"). With `refs`
// they render as the page's icon and title or the list's name; a token the resolvers do not know
// (a page or list the reader cannot see, or one that is gone) renders as "a page" / "a list", so
// the link still opens (to the usual gone / not-shared screen) and nothing about it leaks.

/** Live names for Slate tokens; `undefined` when the reader does not know the page or list. */
export interface RefResolvers {
  doc?: (id: string) => { title: string; icon: string | null } | null | undefined;
  list?: (id: string) => string | null | undefined;
}

function escape(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const ID = '([0-9A-Za-z]{12})';

function docRef(id: string, refs?: RefResolvers): string {
  const d = refs?.doc?.(id);
  const text = d ? `${escape(d.icon || '📄')} ${escape(d.title)}` : '📄 a page';
  return `<a class="doc-ref" href="#/docs/${id}">${text}</a>`;
}

function listRef(id: string, refs?: RefResolvers): string {
  const name = refs?.list?.(id);
  return `<a class="list-ref" href="#/list/${id}">${name ? escape(name) : 'a list'}</a>`;
}

/** A bare `http(s)://` URL pasted into text (already HTML-escaped) ends before trailing
 * punctuation, a closing bracket that the URL itself did not open, or an escaped quote/angle. */
function trimUrl(url: string): string {
  for (;;) {
    const m = /(&quot;|&#39;|&lt;|&gt;)$/.exec(url);
    if (m) {
      url = url.slice(0, -m[0].length);
      continue;
    }
    const last = url[url.length - 1];
    if ('.,;:!?'.includes(last)) {
      url = url.slice(0, -1);
    } else if ((last === ')' || last === ']') && count(url, last) > count(url, last === ')' ? '(' : '[')) {
      url = url.slice(0, -1);
    } else {
      return url;
    }
  }
}

function count(s: string, ch: string): number {
  return s.split(ch).length - 1;
}

function inline(s: string, refs?: RefResolvers): string {
  // Code spans and links are stashed so emphasis never touches a URL (`_` and `*` are common
  // in them) and a code span can sit inside a link label.
  const stash: string[] = [];
  const keep = (html: string) => {
    stash.push(html);
    return `\u0000${stash.length - 1}\u0000`;
  };
  const link = (url: string, text: string) =>
    keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`);
  let out = escape(s).replace(/`([^`]+)`/g, (_, c: string) => keep(`<code>${c}</code>`));
  out = out
    .replace(/\[([^\]]+)\]\(((?:https?:|mailto:|obsidian:)[^)\s]+)\)/g, (_, text, url) => link(url, text))
    .replace(new RegExp(`\\[\\[doc:${ID}\\]\\]`, 'g'), (_, id: string) => docRef(id, refs))
    .replace(new RegExp(`\\[\\[list:${ID}\\]\\]`, 'g'), (_, id: string) => listRef(id, refs))
    // Anything that looks like a web address is a link, wherever it was typed.
    .replace(/(^|[^\w/])(https?:\/\/[^\s\u0000]+)/g, (whole, pre: string, raw: string) => {
      const url = trimUrl(raw);
      if (url.length <= 'https://'.length) return whole;
      return pre + link(url, url) + raw.slice(url.length);
    })
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
  let t = line.trim();
  if (t.startsWith('|')) t = t.slice(1);
  if (t.endsWith('|') && !t.endsWith('\\|')) t = t.slice(0, -1);
  return t.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

interface ListFrame {
  type: 'ul' | 'ol';
  indent: number;
}

/** `taskBoxes`: render `- [ ]` as real checkboxes carrying `data-line` (the source line);
 * `refs`: live names for `[[doc:<id>]]` and `[[list:<id>]]` tokens (RefResolvers). */
export function renderMarkdown(src: string, opts: { taskBoxes?: boolean; refs?: RefResolvers } = {}): string {
  const refs = opts.refs;
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const html: string[] = [];
  let para: string[] = [];
  const lists: ListFrame[] = [];

  const flushPara = () => {
    if (para.length) html.push(`<p>${para.map((l) => inline(l, refs)).join('<br>')}</p>`);
    para = [];
  };
  const closeLists = (toIndent = -1) => {
    while (lists.length && lists[lists.length - 1].indent > toIndent) {
      html.push(`</li></${lists.pop()!.type}>`);
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      closeLists();
      const body: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      html.push(`<pre><code>${escape(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flushPara();
      closeLists();
      html.push('<hr>');
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) {
      flushPara();
      closeLists();
      const level = Math.min(heading[1].length + 2, 6); // inside a drawer, start at h3
      html.push(`<h${level}>${inline(heading[2], refs)}</h${level}>`);
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      flushPara();
      closeLists();
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '',
      );
      const th = head
        .map((c, k) => `<th${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(c, refs)}</th>`)
        .join('');
      const rows: string[] = [];
      i += 2;
      for (; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) {
        const row = cells(lines[i]);
        rows.push(
          `<tr>${head
            .map((_, k) => `<td${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(row[k] ?? '', refs)}</td>`)
            .join('')}</tr>`,
        );
      }
      i--;
      html.push(`<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      flushPara();
      closeLists();
      const quote: string[] = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*>\s?/, ''));
      i--;
      const paras = quote
        .join('\n')
        .split(/\n\s*\n/)
        .map((q) => `<p>${q.split('\n').map((l) => inline(l, refs)).join('<br>')}</p>`);
      html.push(`<blockquote>${paras.join('')}</blockquote>`);
      continue;
    }
    const bullet = /^(\s*)[-*+]\s+(?:\[( |x)\]\s+)?(.*)$/i.exec(line);
    const numbered = /^(\s*)(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushPara();
      const indent = (bullet ?? numbered)![1].replace(/\t/g, '    ').length;
      const type = bullet ? 'ul' : 'ol';
      const top = lists[lists.length - 1];
      const startAt = numbered && Number(numbered[2]) !== 1 ? ` start="${Number(numbered[2])}"` : '';
      if (!top || indent > top.indent) {
        html.push(`<${type}${startAt}><li>`);
        lists.push({ type, indent });
      } else {
        closeLists(indent);
        const cur = lists[lists.length - 1];
        if (cur && cur.type !== type) {
          html.push(`</li></${lists.pop()!.type}><${type}${startAt}><li>`);
          lists.push({ type, indent });
        } else if (cur) {
          html.push('</li><li>');
        } else {
          html.push(`<${type}${startAt}><li>`);
          lists.push({ type, indent });
        }
      }
      const ticked = bullet?.[2]?.toLowerCase() === 'x';
      let box = bullet?.[2] ? (ticked ? '☑ ' : '☐ ') : '';
      if (bullet?.[2] && opts.taskBoxes) {
        box = `<input type="checkbox" class="md-task" data-line="${i}"${ticked ? ' checked' : ''}> `;
      }
      html.push(`${box}${inline(bullet ? bullet[3] : numbered![3], refs)}`);
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
      html.push(`<br>${inline(line.trim(), refs)}`);
      continue;
    }
    closeLists();
    para.push(line);
  }
  flushPara();
  closeLists();
  return html.join('');
}
