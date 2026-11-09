import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('renders Slate page and list tokens through the resolvers, and a plain word without them', () => {
    const refs = {
      doc: (id: string) => (id === 'Dd12Cd34Ef56' ? { title: 'Oven manual', icon: null } : undefined),
      list: (id: string) => (id === 'Ll12Cd34Ef56' ? 'Kitchen' : undefined),
    };
    expect(renderMarkdown('see [[doc:Dd12Cd34Ef56]] and [[list:Ll12Cd34Ef56]]', { refs })).toBe(
      '<p>see <a class="doc-ref" href="#/docs/Dd12Cd34Ef56">📄 Oven manual</a> and <a class="list-ref" href="#/list/Ll12Cd34Ef56">Kitchen</a></p>',
    );
    // A task filed from a doc page says "From doc [[doc:ID]]": the link still works with no resolver.
    expect(renderMarkdown('From doc [[doc:Dd12Cd34Ef56]]')).toBe(
      '<p>From doc <a class="doc-ref" href="#/docs/Dd12Cd34Ef56">📄 a page</a></p>',
    );
    expect(renderMarkdown('[[list:Ll12Cd34Ef56]]')).toBe('<p><a class="list-ref" href="#/list/Ll12Cd34Ef56">a list</a></p>');
  });

  it('escapes a resolved title', () => {
    const refs = { doc: () => ({ title: '<b>x</b> & y', icon: '"' }), list: () => undefined };
    expect(renderMarkdown('[[doc:Dd12Cd34Ef56]]', { refs })).toContain('>&quot; &lt;b&gt;x&lt;/b&gt; &amp; y</a>');
  });
});

describe('renderMarkdown: bare URLs', () => {
  it('links a pasted URL anywhere, keeping trailing punctuation out of it', () => {
    expect(renderMarkdown('Open the report: https://tools.example.com:8443/#/reports?section=summary')).toBe(
      '<p>Open the report: <a href="https://tools.example.com:8443/#/reports?section=summary" target="_blank" rel="noopener noreferrer">https://tools.example.com:8443/#/reports?section=summary</a></p>',
    );
    expect(renderMarkdown('see http://x.y/a_b_c, then (https://x.y/z).')).toBe(
      '<p>see <a href="http://x.y/a_b_c" target="_blank" rel="noopener noreferrer">http://x.y/a_b_c</a>, then (<a href="https://x.y/z" target="_blank" rel="noopener noreferrer">https://x.y/z</a>).</p>',
    );
    // A URL whose own path has parentheses keeps them; a quote after it is not part of it.
    expect(renderMarkdown('https://example.com/wiki/Slate_(rock) "x"')).toContain(
      'href="https://example.com/wiki/Slate_(rock)" target="_blank" rel="noopener noreferrer">https://example.com/wiki/Slate_(rock)</a> &quot;x&quot;',
    );
    expect(renderMarkdown('- https://x.y/z\n\n| a |\n|---|\n| https://x.y/t |')).toBe(
      '<ul><li><a href="https://x.y/z" target="_blank" rel="noopener noreferrer">https://x.y/z</a></li></ul>' +
        '<div class="table-wrap"><table><thead><tr><th>a</th></tr></thead><tbody><tr><td><a href="https://x.y/t" target="_blank" rel="noopener noreferrer">https://x.y/t</a></td></tr></tbody></table></div>',
    );
  });

  it('leaves markdown links, code and unsafe schemes alone', () => {
    expect(renderMarkdown('[https://x.y/z](https://x.y/z)')).toBe(
      '<p><a href="https://x.y/z" target="_blank" rel="noopener noreferrer">https://x.y/z</a></p>',
    );
    expect(renderMarkdown('`https://x.y/z` and\n\n```\nhttps://x.y/z\n```')).not.toContain('href');
    expect(renderMarkdown('javascript://x and ftp://x')).not.toContain('href');
    // An underscore-heavy URL is not italics, in a bare URL or a markdown link.
    expect(renderMarkdown('[a](https://x.y/_a_) https://x.y/_b_')).not.toContain('<em>');
  });
});

describe('renderMarkdown: richer blocks', () => {
  it('renders a pipe table with alignment, escaped pipes and short rows', () => {
    const src = '| Order | Work | Done |\n|---|:---:|---:|\n| M1 | Draft a plan \\| contract | yes |\n| M2 | Final pass |';
    expect(renderMarkdown(src)).toBe(
      '<div class="table-wrap"><table><thead><tr><th>Order</th><th style="text-align:center">Work</th><th style="text-align:right">Done</th></tr></thead>' +
        '<tbody><tr><td>M1</td><td style="text-align:center">Draft a plan | contract</td><td style="text-align:right">yes</td></tr>' +
        '<tr><td>M2</td><td style="text-align:center">Final pass</td><td style="text-align:right"></td></tr></tbody></table></div>',
    );
  });

  it('leaves a lone pipe line as text', () => {
    expect(renderMarkdown('a | b')).toBe('<p>a | b</p>');
  });

  it('renders quotes, dividers and strikethrough', () => {
    expect(renderMarkdown('> said\n> twice\n\n---\n\n~~gone~~ kept')).toBe(
      '<blockquote><p>said<br>twice</p></blockquote><hr><p><del>gone</del> kept</p>',
    );
  });

  it('nests lists by indent, switches type, keeps a numbered start and survives a blank line', () => {
    expect(renderMarkdown('- a\n  - b\n    1. c\n- d\n\n- e\n\n3. f')).toBe(
      '<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li><li>d</li><li>e</li></ul><ol start="3"><li>f</li></ol>',
    );
  });

  it('keeps task boxes on their source line inside a nested list', () => {
    expect(renderMarkdown('- [ ] a\n  - [x] b', { taskBoxes: true })).toBe(
      '<ul><li><input type="checkbox" class="md-task" data-line="0"> a<ul><li><input type="checkbox" class="md-task" data-line="1" checked> b</li></ul></li></ul>',
    );
    expect(renderMarkdown('- [x] b')).toBe('<ul><li>☑ b</li></ul>');
  });

  it('wraps a continuation line into the list entry and ends the list at plain text', () => {
    expect(renderMarkdown('- a\n  more\ntext')).toBe('<ul><li>a<br>more</li></ul><p>text</p>');
  });
});
