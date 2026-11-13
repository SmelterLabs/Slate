// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chat } from './chat.svelte';
import type { ChatFile } from './chat';

const file = (id: string): ChatFile => ({
  id,
  filename: `${id}.png`,
  content_type: 'image/png',
  size: 10,
  channel: 'general',
  message_id: null,
  created_at: '2026-11-01T00:00:00Z',
  created_by: null,
});

describe('chat thumbnails', () => {
  beforeEach(() => {
    chat.forgetThumbs();
  });

  it('keeps about 200, the oldest forgotten, and forgets all of them on demand', () => {
    const revoked: string[] = [];
    vi.stubGlobal('URL', { createObjectURL: (b: Blob) => `blob:${(b as unknown as { n: string }).n}`, revokeObjectURL: (u: string) => revoked.push(u) });
    for (let i = 0; i < 205; i++) chat.rememberThumb(file(`f${i}`), { n: String(i) } as unknown as Blob);
    expect(Object.keys(chat.thumbs)).toHaveLength(200);
    expect(chat.thumbs.f0).toBeUndefined();
    expect(revoked).toContain('blob:0');
    expect(chat.thumbs.f204).toBe('blob:204');
    chat.forgetThumbs();
    expect(chat.thumbs).toEqual({});
    expect(revoked).toHaveLength(205);
    vi.unstubAllGlobals();
  });
});
