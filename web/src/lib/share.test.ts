import { describe, expect, it } from 'vitest';
import { linkTitle, shareDraft, shareParamsFrom } from './share';

describe('share params in the address', () => {
  it('reads the share sheet query and the in-app route, else nothing', () => {
    expect(shareParamsFrom('?title=A&text=B&url=https%3A%2F%2Fexample.com', '')).toEqual({ title: 'A', text: 'B', url: 'https://example.com' });
    expect(shareParamsFrom('', '#/share?url=https%3A%2F%2Fexample.com%2Fp')).toEqual({ title: null, text: null, url: 'https://example.com/p' });
    expect(shareParamsFrom('?kiosk=1', '#/everything')).toBeNull();
    expect(shareParamsFrom('', '#/list/abc?task=def')).toBeNull();
  });
});

describe('a shared page or link becomes a quick-add draft', () => {
  it('uses the title and puts the link in the body', () => {
    expect(shareDraft({ title: 'How to file expenses', text: null, url: 'https://example.com/expenses' })).toEqual({
      line: 'How to file expenses',
      body: 'https://example.com/expenses',
    });
  });
  it('takes the first line of the text as the title and keeps the rest', () => {
    expect(shareDraft({ title: '', text: 'Quote from Chris\nBring it Tuesday\nhttps://example.com/q/1', url: '' })).toEqual({
      line: 'Quote from Chris',
      body: 'https://example.com/q/1\n\nBring it Tuesday',
    });
  });
  it('finds a link inside the text when the url param is empty', () => {
    expect(shareDraft({ title: null, text: 'Check out https://example.com/a?b=1 later', url: null })).toEqual({
      line: 'Check out later',
      body: 'https://example.com/a?b=1',
    });
  });
  it('turns a bare link into a Read item, and a video into Watch', () => {
    expect(shareDraft({ url: 'https://www.example.com/2026/11/04/some-story.html' })).toEqual({
      line: 'Read example.com/2026/11/04/some-story.html',
      body: 'https://www.example.com/2026/11/04/some-story.html',
    });
    expect(shareDraft({ text: 'https://youtu.be/dQw4w9WgXcQ' })?.line).toBe('Watch youtu.be/dQw4w9WgXcQ');
    expect(linkTitle('https://example.com/')).toBe('Read example.com');
    expect(linkTitle(`https://example.com/${'x'.repeat(80)}`)).toMatch(/^Read example\.com\/x+…$/);
  });
  it('keeps quick-add words in a shared title literal', () => {
    expect(shareDraft({ title: 'New #1 release @home ~soon !now', url: 'https://example.com' })?.line).toBe('New \\#1 release \\@home \\~soon \\!now');
  });
  it('ignores a url that is not http(s) and returns null for an empty share', () => {
    expect(shareDraft({ title: 'Note', url: 'javascript:alert(1)' })).toEqual({ line: 'Note', body: '' });
    expect(shareDraft({ title: '  ', text: '\n', url: '' })).toBeNull();
    expect(shareDraft({})).toBeNull();
  });
});
