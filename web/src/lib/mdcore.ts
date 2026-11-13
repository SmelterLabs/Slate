// The one Markdown engine behind both renderers: markdown.ts (item bodies, comments, chat) and
// docmd.ts (doc pages). Each renderer supplies only what differs (Slate tokens, images, heading
// levels, check boxes, embeds); the escaping, the inline pipeline and the block parser live here
// once, so a fix to either always reaches both.
//
// Safety rests on two rules. Everything is HTML-escaped first, so the source can only add markup
// through this file. And every piece of finished markup (code span, link, image, token) is
// stashed behind a `\u0000N\u0000` placeholder the moment it is built, so no later pass can
// match inside it; the placeholders are swapped back at the very end.

export function escape(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const HEX_COLOR = /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const NAMED_COLOR = /^[a-zA-Z]{3,20}$/;
const THEME_COLOR = /^var\(--[\w-]+\)$/;

/** A colour that is safe to put in a `style` value: a hex colour, a plain CSS colour name or one
 * of the board's own theme variables. The server accepts only the first two; anything else (a
 * value stored before colours were checked) gives `fallback`. */
export function safeColor(c: string | null | undefined, fallback = ''): string {
  const v = c ?? '';
  return HEX_COLOR.test(v) || NAMED_COLOR.test(v) || THEME_COLOR.test(v) ? v : fallback;
}

/** Stash finished HTML; returns its placeholder. */
export type Keep = (html: string) => string;

export interface InlineHooks {
  /** Slate's own tokens (`[[doc:…]]`, `[[task:…]]`, `[[list:…]]`): replace them in `s`, which is
   * already escaped, passing each result through `keep`. */
  tokens?: (s: string, keep: Keep) => string;
  /** `![alt](url)`: HTML for an image, or null to show only the (escaped) alt text. Without this
   * hook images are left as text. */
  image?: (alt: string, url: string) => string | null;
  /** Links inside Slate (`#/…`) and to uploaded doc files are allowed too. */
  internalLinks?: boolean;
}

const FILE_URL = /^\/api\/v1\/doc-files\/([0-9A-Za-z]{12})$/;
export { FILE_URL };

function safeUrl(url: string, internal: boolean): { url: string; external: boolean } | null {
  if (/^(https?:|mailto:|obsidian:)/i.test(url)) return { url, external: true };
  if (internal && (/^#\//.test(url) || FILE_URL.test(url))) return { url, external: false };
  return null;
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

const EXTERNAL = ' target="_blank" rel="noopener noreferrer"';

/** One line of Markdown to safe inline HTML. */
export function renderInline(src: string, hooks: InlineHooks = {}): string {
  const stash: string[] = [];
  const keep: Keep = (html) => {
    stash.push(html);
    return `\u0000${stash.length - 1}\u0000`;
  };
  let out = escape(src).replace(/`([^`]+)`/g, (_, c: string) => keep(`<code>${c}</code>`));
  if (hooks.tokens) out = hooks.tokens(out, keep);
  if (hooks.image) {
    const image = hooks.image;
    out = out.replace(/!\[([^\]\u0000]*)\]\(([^)\s\u0000]+)\)/g, (_, alt: string, url: string) => {
      const img = image(alt, url);
      return img === null ? alt : keep(img);
    });
  }
  out = out
    .replace(/\[([^\]]+)\]\(([^)\s\u0000]+)\)/g, (whole, text: string, raw: string) => {
      // `raw` is already escaped, so it is safe inside a quoted attribute as it stands.
      const ok = safeUrl(raw, !!hooks.internalLinks);
      if (!ok) return whole;
      return keep(`<a href="${ok.url}"${ok.external ? EXTERNAL : ''}>${text}</a>`);
    })
    // Anything that looks like a web address is a link, wherever it was typed. It never runs
    // into a placeholder, so it cannot swallow markup built above.
    .replace(/(^|[^\w/])(https?:\/\/[^\s<\u0000]+)/g, (whole, pre: string, raw: string) => {
      const url = trimUrl(raw);
      if (url.length <= 'https://'.length) return whole;
      return pre + keep(`<a href="${url}"${EXTERNAL}>${url}</a>`) + raw.slice(url.length);
    })
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/(^|\W)_([^_\s][^_]*)_(?=\W|$)/g, '$1<em>$2</em>');
  // Stashed pieces can nest (a code span inside a link label); unwrap until none remain.
  for (let i = 0; i < 4 && out.includes('\u0000'); i++) {
    out = out.replace(/\u0000(\d+)\u0000/g, (_, n) => stash[Number(n)]);
  }
  return out.replace(/\u0000/g, '');
}

export type Block =
  | { kind: 'html'; html: string; line: number; src?: string }
  | { kind: 'task'; id: string; line: number; src?: string };

export interface BlockHooks {
  inline: (s: string) => string;
  /** A heading's HTML from its level (1-6), its raw text and its source line. */
  heading: (level: number, text: string, line: number) => string;
  /** A `- [ ]` / `- [x]` entry: the HTML for the box and its (already rendered) text. */
  check: (ticked: boolean, line: number, html: string) => string;
  /** Class put on a list whose first entry is a check box. */
  checkListClass?: string;
  /** A line that is a block of its own (a task embed): the id it carries, or null. */
  embed?: (line: string) => string | null;
  /** Every construct is a block of its own and carries its source (see docmd.ts renderDoc). */
  split?: boolean;
}

export const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function cells(line: string): string[] {
  let t = line.trim();
  if (t.startsWith('|')) t = t.slice(1);
  if (t.endsWith('|') && !t.endsWith('\\|')) t = t.slice(0, -1);
  return t.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

const FENCE = /^\s*```/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
export const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^(\s*)[-*+]\s+(?:\[( |x|X)\](?:\s+|$))?(.*)$/;
const NUMBERED = /^(\s*)(\d+)[.)]\s+(.*)$/;

interface ListFrame {
  type: 'ul' | 'ol';
  indent: number;
}

/** A Markdown body to blocks of safe HTML. Line numbers are 0-based source lines. */
export function renderBlocks(src: string, hooks: BlockHooks): Block[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const split = !!hooks.split;
  const inline = hooks.inline;
  const blocks: Block[] = [];
  let html: string[] = [];
  let start = 0;
  let para: string[] = [];
  const lists: ListFrame[] = [];

  const flushPara = () => {
    if (para.length) html.push(`<p>${para.map(inline).join('<br>')}</p>`);
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
  // A construct that cannot sit inside a paragraph or a list.
  const begin = (i: number) => {
    boundary(i);
    flushPara();
    closeLists();
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const embed = hooks.embed?.(line);
    if (embed) {
      flushHtml(i);
      blocks.push({ kind: 'task', id: embed, line: i });
      start = i + 1;
      continue;
    }
    if (FENCE.test(line)) {
      begin(i);
      const lang = line.trim().slice(3).trim().replace(/[^\w+-]/g, '');
      const body: string[] = [];
      while (++i < lines.length && !FENCE.test(lines[i])) body.push(lines[i]);
      html.push(`<pre><code${lang ? ` class="lang-${lang}"` : ''}>${escape(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (RULE.test(line)) {
      begin(i);
      html.push('<hr>');
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      begin(i);
      html.push(hooks.heading(heading[1].length, heading[2], i));
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      begin(i);
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '',
      );
      const cell = (tag: string, k: number, text: string) =>
        `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ''}>${inline(text)}</${tag}>`;
      const th = head.map((c, k) => cell('th', k, c)).join('');
      const rows: string[] = [];
      i += 2;
      for (; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) {
        const row = cells(lines[i]);
        rows.push(`<tr>${head.map((_, k) => cell('td', k, row[k] ?? '')).join('')}</tr>`);
      }
      i--;
      html.push(`<div class="md-table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      begin(i);
      const quote: string[] = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) quote.push(lines[i].replace(/^\s*>\s?/, ''));
      i--;
      const paras = quote
        .join('\n')
        .split(/\n\s*\n/)
        .map((q) => `<p>${q.split('\n').map(inline).join('<br>')}</p>`);
      html.push(`<blockquote>${paras.join('')}</blockquote>`);
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = NUMBERED.exec(line);
    if (bullet || numbered) {
      if (!lists.length) boundary(i);
      flushPara();
      const indent = (bullet ?? numbered)![1].replace(/\t/g, '    ').length;
      const type = bullet ? 'ul' : 'ol';
      const isCheck = bullet?.[2] !== undefined;
      const startAt = numbered && Number(numbered[2]) !== 1 ? ` start="${Number(numbered[2])}"` : '';
      const cls = isCheck && hooks.checkListClass ? ` class="${hooks.checkListClass}"` : '';
      const open = () => {
        html.push(`<${type}${startAt}${cls}><li>`);
        lists.push({ type, indent });
      };
      const top = lists[lists.length - 1];
      if (!top || indent > top.indent) {
        open();
      } else {
        closeLists(indent);
        const cur = lists[lists.length - 1];
        if (cur && cur.type !== type) {
          html.push(`</li></${lists.pop()!.type}>`);
          open();
        } else if (cur) {
          html.push('</li><li>');
        } else {
          open();
        }
      }
      const text = inline(bullet ? bullet[3] : numbered![3]);
      html.push(isCheck ? hooks.check(bullet![2].toLowerCase() === 'x', i, text) : text);
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
      html.push(`<br>${inline(line.trim())}`);
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
