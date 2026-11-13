import { describe, expect, it } from 'vitest';
import { MentionPicker, isSendKey, mentionCandidates, pickerKey } from './mentions.svelte';
import type { Account } from './types';

const acct = (handle: string, display_name: string, kind: 'person' | 'agent' = 'person'): Account => ({
  id: `A_${handle}`,
  handle,
  display_name,
  kind,
});
const POOL = [acct('alex', 'Alex'), acct('my-agent', 'My Agent', 'agent'), acct('other-agent', 'Alexandra Bot', 'agent')];

const key = (key: string, more: Partial<KeyboardEvent> & { keyCode?: number } = {}) =>
  ({ key, shiftKey: false, isComposing: false, preventDefault() {}, stopPropagation() {}, ...more }) as unknown as KeyboardEvent;

describe('mentions', () => {
  it('lists handle matches first, then name matches', () => {
    expect(mentionCandidates(POOL, 'al').map((a) => a.handle)).toEqual(['alex', 'other-agent']);
    expect(mentionCandidates(POOL, '').map((a) => a.handle)).toEqual(['alex', 'other-agent', 'my-agent']);
  });

  it('never sends, picks or moves on a key that belongs to an input-method composition', () => {
    expect(isSendKey(key('Enter'))).toBe(true);
    expect(isSendKey(key('Enter', { shiftKey: true }))).toBe(false);
    expect(isSendKey(key('Enter', { isComposing: true }))).toBe(false);
    expect(isSendKey(key('Enter', { keyCode: 229 }))).toBe(false);
    expect(pickerKey(key('Enter', { isComposing: true }), 0, 3)).toBeNull();
    expect(pickerKey(key('ArrowDown'), 2, 3)).toEqual({ active: 0 });
    expect(pickerKey(key('ArrowUp'), 0, 3)).toEqual({ active: 2 });
    expect(pickerKey(key('Tab'), 0, 3)).toBe('pick');
    expect(pickerKey(key('Escape'), 0, 3)).toBe('close');
    expect(pickerKey(key('Enter'), 0, 0)).toBeNull();
  });

  it('tracks the @query, walks the list and inserts the picked handle', () => {
    const p = new MentionPicker(() => POOL);
    p.track('hi @al', 6);
    expect(p.open).toBe(true);
    expect(p.people.map((a) => a.handle)).toEqual(['alex', 'other-agent']);
    expect(p.keydown(key('ArrowDown'), () => {})).toBe(true);
    let picked = '';
    expect(p.keydown(key('Enter'), (a) => (picked = a.handle))).toBe(true);
    expect(picked).toBe('other-agent');
    expect(p.insert('hi @al', 6, POOL[2])).toEqual({ text: 'hi @other-agent ', caret: 16 });
    expect(p.open).toBe(false);
    p.track('hi @al', 6);
    let stopped = false;
    expect(p.keydown(key('Escape', { stopPropagation: () => (stopped = true) }), () => {})).toBe(true);
    expect(stopped).toBe(true);
    expect(p.open).toBe(false);
    expect(p.keydown(key('Enter'), () => {})).toBe(false);
  });
});
