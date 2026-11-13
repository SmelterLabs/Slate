// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderDoc, type DocCtx } from './docmd';
import { renderMarkdown } from './markdown';

// Every renderer output goes through the same checks, so a fix can never reach one renderer only.
const ctx: DocCtx = {
  task: (id) => (id === 'AAAAAAAAAAAA' ? { title: 'https://a.example.com/t "x"', done: false, color: '#c00' } : null),
  doc: (id) => (id === 'BBBBBBBBBBBB' ? { title: 'https://evil.example.com/x', icon: '"' } : null),
};
const refs = {
  doc: (id: string) => (id === 'BBBBBBBBBBBB' ? { title: 'https://evil.example.com/x', icon: null } : undefined),
  list: (id: string) => (id === 'LLLLLLLLLLLL' ? 'https://evil.example.com/list' : undefined),
};

const renderers: Record<string, (src: string) => string> = {
  markdown: (src) => renderMarkdown(src, { refs, taskBoxes: true }),
  doc: (src) =>
    renderDoc(src, ctx)
      .map((b) => (b.kind === 'html' ? b.html : ''))
      .join(''),
};

const HOSTILE = [
  'See https://a.example.com/[x](https://b.example.com/x/onmouseover=document.title=/pwned/.source//)',
  'See https://a.example.com/[x](https://b.example.com/x/style=position:fixed;inset:0)',
  'See https://a.example.com/![p](https://b.example.com/p.png/onerror=alert(1))',
  'See https://a.example.com/[[doc:BBBBBBBBBBBB]] and https://a.example.com/[[list:LLLLLLLLLLLL]]',
  'https://a.example.com/[[task:AAAAAAAAAAAA]]',
  '[x](javascript:alert(1)) [y](data:text/html,<b>) [z](vbscript:x) ![i](javascript:x)',
  '[a "b"](https://example.com/"onmouseover="x) <img src=x onerror=alert(1)>',
  '[`https://x.example.com/[y](https://z.example.com/)`](https://example.com/)',
  '| h |\n|---|\n| https://a.example.com/[x](https://b.example.com/onclick=x) |',
  '- [ ] https://a.example.com/[x](https://b.example.com/onfocus=x)',
  '> https://a.example.com/![p](https://b.example.com/onload=x)',
  '# https://a.example.com/[x](https://b.example.com/onclick=x)',
  '**https://a.example.com/_[x](https://b.example.com/x_y)_**',
  '\u00000\u0000 [x](https://example.com/) \u00001\u0000',
];

const ATTRS = new Set([
  'href', 'target', 'rel', 'class', 'id', 'alt', 'src', 'loading', 'type', 'checked', 'start', 'style',
  'data-task', 'data-doc', 'data-file', 'data-line',
]);
const STYLE = /^(text-align:(left|center|right)|background:(#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,20})?)$/;
const HREF = /^(https?:|mailto:|obsidian:|#)/;

function problems(html: string): string[] {
  const out: string[] = [];
  for (const tag of html.match(/<a\b[^>]*>/g) ?? []) {
    if ((tag.match(/\shref=/g) ?? []).length !== 1) out.push(`not one href: ${tag}`);
  }
  if (/href="[^"]*</.test(html)) out.push('markup inside an href');
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  for (const el of Array.from(doc.body.querySelectorAll('*'))) {
    if (['SCRIPT', 'IFRAME', 'OBJECT', 'STYLE'].includes(el.tagName)) out.push(`element ${el.tagName}`);
    for (const a of Array.from(el.attributes)) {
      if (!ATTRS.has(a.name)) out.push(`attribute ${a.name} on ${el.tagName}`);
      if (a.name === 'style' && !STYLE.test(a.value)) out.push(`style ${a.value}`);
      if (a.name === 'href' && !HREF.test(a.value)) out.push(`href ${a.value}`);
      if (a.name === 'src' && !/^https:/.test(a.value)) out.push(`src ${a.value}`);
    }
  }
  return out;
}

describe('both Markdown renderers', () => {
  for (const [name, render] of Object.entries(renderers)) {
    it(`${name}: hostile input yields only allowed elements, attributes and URLs`, () => {
      for (const src of HOSTILE) expect(problems(render(src)), src).toEqual([]);
    });
  }

  it('markdown: a page or list title that is a URL stays inside its chip', () => {
    const out = renderMarkdown('[[list:LLLLLLLLLLLL]] and https://a.example.com/[[list:LLLLLLLLLLLL]]', { refs });
    const doc = new DOMParser().parseFromString(`<body>${out}</body>`, 'text/html');
    const links = Array.from(doc.body.querySelectorAll('a')).map((a) => [a.className, a.getAttribute('href')]);
    expect(links).toEqual([
      ['list-ref', '#/list/LLLLLLLLLLLL'],
      ['', 'https://a.example.com/'],
      ['list-ref', '#/list/LLLLLLLLLLLL'],
    ]);
    expect(renderMarkdown('[[doc:BBBBBBBBBBBB]]', { refs })).toBe(
      '<p><a class="doc-ref" href="#/docs/BBBBBBBBBBBB">📄 https://evil.example.com/x</a></p>',
    );
  });

  it('agree on the shared syntax', () => {
    const src = '- a\n  - b\n    1. c\n- d\n\n| x | y |\n|---|--:|\n| **1** | ~~2~~ |\n\n> q\n\n---\n\n`c` [l](https://example.com/) https://example.com/b.';
    const strip = (h: string) => h.replace(/ (class|id|data-line)="[^"]*"/g, '');
    expect(strip(renderers.doc(src))).toBe(strip(renderers.markdown(src)));
  });
});
