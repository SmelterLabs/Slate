// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderDoc, type DocCtx } from './docmd';
import { blocksMd, drawEditor, editorMarkdown } from './richdoc';

const ctx: DocCtx = {
  task: (id) => (id === 'AAAAAAAAAAAA' ? { title: 'Buy paint', done: false, color: 'red' } : null),
  doc: (id) => (id === 'BBBBBBBBBBBB' ? { title: 'Plan', icon: null } : null),
};

function editor(src: string) {
  const root = document.createElement('div');
  const drawn = drawEditor(root, renderDoc(src, ctx, { split: true }));
  return { root, save: () => editorMarkdown(root, drawn, src) };
}

const PAGE = [
  '# Plan',
  'Intro *star* text',
  '',
  '* odd bullet',
  '* [x] done thing',
  '',
  '[[task:AAAAAAAAAAAA]]',
  '',
  '| A | B |',
  '|:--|--:|',
  '| 1 | **2** |',
  '',
  '> quote',
  '',
  '---',
  '```',
  'code  here',
  '```',
  'See [[doc:BBBBBBBBBBBB]] and [x](https://example.com).',
  '',
].join('\n');

describe('split blocks', () => {
  it('carry source slices that join back into the page', () => {
    for (const src of [PAGE, '', 'one', '\n\nlead', 'a\n\n\nb\n', '- a\n\n- b\ntext', '## H\npara\n## H2']) {
      expect(renderDoc(src, ctx, { split: true }).map((b) => b.src).join('')).toBe(src);
    }
  });
});

describe('editorMarkdown', () => {
  it('gives the page back untouched when nothing changed', () => {
    expect(editor(PAGE).save()).toBe(PAGE);
  });

  it('rewrites only the block that changed', () => {
    const { root, save } = editor(PAGE);
    const p = root.querySelector('[data-b="1"] p')!;
    p.textContent = 'New intro';
    const out = save();
    expect(out).toContain('# Plan\n');
    expect(out).toContain('New intro\n\n* odd bullet'); // the list keeps its own markers
    expect(out).toContain('|:--|--:|');
  });

  it('writes formatting typed in the editor as Markdown', () => {
    const { root, save } = editor('Hello');
    root.querySelector('p')!.innerHTML = 'Hi <b>bold </b><i>it</i> <strike>gone</strike> <code>x</code> <a href="https://example.com">link</a>';
    expect(save()).toBe('Hi **bold** _it_ ~~gone~~ `x` [link](https://example.com)');
  });

  it('keeps chips, page links and embeds as their tokens', () => {
    const { root, save } = editor('See [[task:AAAAAAAAAAAA]] and [[doc:BBBBBBBBBBBB]] and [[doc:CCCCCCCCCCCC]]\n\n[[task:AAAAAAAAAAAA]]');
    root.querySelector('p')!.append(' more');
    expect(save()).toBe('See [[task:AAAAAAAAAAAA]] and [[doc:BBBBBBBBBBBB]] and [[doc:CCCCCCCCCCCC]] more\n\n[[task:AAAAAAAAAAAA]]');
  });

  it('writes checklists from the boxes', () => {
    const { root, save } = editor('- [ ] one\n- [x] two');
    const boxes = root.querySelectorAll('input');
    expect(boxes).toHaveLength(2);
    boxes[0].setAttribute('checked', '');
    boxes[1].removeAttribute('checked');
    expect(save()).toBe('- [x] one\n- [ ] two');
  });

  it('writes a new paragraph typed after the last block with a blank line', () => {
    const { root, save } = editor('## Head\nText');
    const p = document.createElement('p');
    p.textContent = 'Added';
    root.appendChild(p);
    expect(save()).toBe('## Head\nText\n\nAdded');
  });

  it('turns loose typed text into a paragraph', () => {
    const { root, save } = editor('Original');
    root.textContent = 'My unsaved text';
    expect(save()).toBe('My unsaved text');
  });

  it('writes tables, quotes, headings and lists made in the editor', () => {
    const root = document.createElement('div');
    root.innerHTML =
      '<h2>Title</h2><ul><li>a<ul><li>b</li></ul></li></ul><ol><li>x</li><li>y</li></ol>' +
      '<blockquote>said</blockquote><div class="table-wrap"><table><tr><th>A</th><th>B|C</th></tr><tr><td>1</td><td></td></tr></table></div><hr>';
    expect(blocksMd(root)).toBe('## Title\n\n- a\n  - b\n\n1. x\n2. y\n\n> said\n\n| A | B\\|C |\n| --- | --- |\n| 1 |  |\n\n---');
  });

  it('drops a deleted block cleanly', () => {
    const { root, save } = editor('A\n\nB\n\nC');
    root.querySelector('[data-b="1"]')!.remove();
    expect(save()).toBe('A\n\nC');
  });
});

describe('browser quirks', () => {
  it('reads a list the browser put inside a paragraph', () => {
    const root = document.createElement('div');
    root.innerHTML = '<p>Before</p><p><ul class="checks"><li class="md-li-check"><input type="checkbox">Send</li></ul></p>';
    expect(blocksMd(root)).toBe('Before\n\n- [ ] Send');
  });
});
