import { describe, expect, it } from 'vitest';
import { headings, inline, renderDoc, slug, type DocCtx } from './docmd';

const ctx: DocCtx = {
  task: (id) => (id === 'AAAAAAAAAAAA' ? { title: 'Buy <paint>', done: false, color: 'red' } : null),
  doc: (id) => (id === 'BBBBBBBBBBBB' ? { title: 'Plan', icon: '🧭' } : null),
};

const html = (src: string) =>
  renderDoc(src, ctx)
    .map((b) => (b.kind === 'html' ? b.html : `[task ${b.id}]`))
    .join('');

describe('renderDoc', () => {
  it('splits out task embeds that stand on their own line', () => {
    const blocks = renderDoc('Intro\n\n[[task:AAAAAAAAAAAA]]\n\nAfter', ctx);
    expect(blocks.map((b) => b.kind)).toEqual(['html', 'task', 'html']);
    expect(blocks[1]).toEqual({ kind: 'task', id: 'AAAAAAAAAAAA', line: 2 });
  });

  it('renders headings at their real level with unique anchors', () => {
    const out = html('# Plan\n\n## Notes\n\n## Notes');
    expect(out).toContain('<h1 id="h-plan" data-line="0">Plan</h1>');
    expect(out).toContain('<h2 id="h-notes" data-line="2">Notes</h2>');
    expect(out).toContain('<h2 id="h-notes-2" data-line="4">Notes</h2>');
  });

  it('renders tables with alignment', () => {
    const out = html('| A | B |\n| :-- | --: |\n| 1 | **2** |\n| x |');
    expect(out).toContain('<th style="text-align:right">B</th>');
    expect(out).toContain('<td style="text-align:right"><strong>2</strong></td>');
    expect(out).toContain('<tr><td>x</td><td style="text-align:right"></td></tr>');
  });

  it('renders checklists with their source line and nested lists', () => {
    const out = html('- [ ] one\n- [x] two\n  - child\n- three');
    expect(out).toContain('data-line="0"');
    expect(out).toContain('class="md-check ticked"><input type="checkbox" class="md-task" data-line="1" checked>');
    expect(out).toMatch(/<li>.*two.*<ul><li>child<\/li><\/ul><\/li><li>three/);
  });

  it('renders quotes, dividers and code', () => {
    const out = html('> said\n> more\n\n---\n\n```py\n<b>\n```');
    expect(out).toContain('<blockquote><p>said<br>more</p></blockquote>');
    expect(out).toContain('<hr>');
    expect(out).toContain('<pre><code class="lang-py">&lt;b&gt;</code></pre>');
  });

  it('escapes everything and allows only safe links', () => {
    const out = html('<script>x</script> [a](javascript:alert(1)) [b](#/docs/x) [c](https://example.com)');
    expect(out).not.toContain('<script>');
    expect(out).not.toContain('href="javascript');
    expect(out).toContain('<a href="#/docs/x">b</a>');
    expect(out).toContain('<a href="https://example.com" target="_blank" rel="noopener noreferrer">c</a>');
  });
});

describe('inline', () => {
  it('resolves task chips and doc links, escaped', () => {
    expect(inline('see [[task:AAAAAAAAAAAA]]', ctx)).toContain('Buy &lt;paint&gt;</a>');
    expect(inline('[[task:CCCCCCCCCCCC]]', ctx)).toContain('Task not found');
    expect(inline('[[doc:BBBBBBBBBBBB]]', ctx)).toBe('<a class="doc-link" href="#/docs/BBBBBBBBBBBB" data-doc="BBBBBBBBBBBB">🧭 Plan</a>');
  });

  it('turns uploaded images into token-fetched images', () => {
    expect(inline('![shot](/api/v1/doc-files/DDDDDDDDDDDD)', ctx)).toBe(
      '<img class="doc-img" data-file="DDDDDDDDDDDD" alt="shot" loading="lazy">',
    );
    expect(inline('![x](http://insecure.example/a.png)', ctx)).toBe('x');
  });

  it('links bare URLs and strikes through', () => {
    expect(inline('go https://example.com/a.', ctx)).toBe(
      'go <a href="https://example.com/a" target="_blank" rel="noopener noreferrer">https://example.com/a</a>.',
    );
    expect(inline('~~old~~', ctx)).toBe('<del>old</del>');
  });
});

describe('headings', () => {
  it('lists headings outside code with the same anchors', () => {
    expect(headings('# A\n```\n# not\n```\n## A')).toEqual([
      { level: 1, text: 'A', id: 'h-a', line: 0 },
      { level: 2, text: 'A', id: 'h-a-2', line: 4 },
    ]);
    expect(slug('Next steps!')).toBe('next-steps');
  });
});
