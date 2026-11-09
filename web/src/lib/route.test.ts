import { describe, expect, it } from 'vitest';
import { AGENDA_VIEW_ID, docOf, isKiosk, pageOf, parseRoute } from './route';

describe('routes', () => {
  it('parses every scope with and without a view', () => {
    expect(parseRoute('')).toEqual({ scope: { kind: 'everything' }, viewId: null });
    expect(parseRoute('#/everything')).toEqual({ scope: { kind: 'everything' }, viewId: null });
    expect(parseRoute('#/everything/v/vw1')).toEqual({ scope: { kind: 'everything' }, viewId: 'vw1' });
    expect(parseRoute('#/section/S1')).toEqual({ scope: { kind: 'section', id: 'S1' }, viewId: null });
    expect(parseRoute('#/list/L1/v/vw2')).toEqual({ scope: { kind: 'list', id: 'L1' }, viewId: 'vw2' });
    expect(parseRoute('#/list/L1?kiosk=1')).toEqual({ scope: { kind: 'list', id: 'L1' }, viewId: null });
  });

  it('opens a list or section on its Channel tab', () => {
    expect(parseRoute('#/list/L1/chat')).toEqual({ scope: { kind: 'list', id: 'L1' }, viewId: null, chat: true });
    expect(parseRoute('#/section/S1/chat?thread=m1').chat).toBe(true);
    expect(parseRoute('#/list/L1/v/vw2').chat).toBeUndefined();
  });

  it('maps the old Today page to the Agenda view', () => {
    expect(parseRoute('#/agents').page).toBe('agents');
    expect(parseRoute('#/everything').page).toBeUndefined();
    expect(parseRoute('#/today')).toEqual({ scope: { kind: 'everything' }, viewId: AGENDA_VIEW_ID });
  });

  it('kiosk from the query or the hash', () => {
    expect(isKiosk('?kiosk=1', '#/everything')).toBe(true);
    expect(isKiosk('', '#/everything?kiosk=1')).toBe(true);
    expect(isKiosk('', '#/everything')).toBe(false);
  });

  it('names the board pages', () => {
    expect(pageOf('#/home')).toBe('home');
    expect(pageOf('#/archive?tab=trash')).toBe('archive');
    expect(pageOf('#/inbox?task=x')).toBe('inbox');
    expect(pageOf('#/docs')).toBe('docs');
    expect(pageOf('#/docs/AbCdEfGhIjKl?task=x')).toBe('docs');
    expect(docOf('#/docs/AbCdEfGhIjKl?task=x')).toBe('AbCdEfGhIjKl');
    expect(docOf('#/docs')).toBeNull();
    expect(pageOf('#/chat')).toBe('chat');
    expect(pageOf('#/chat/list:L1?thread=m1')).toBe('chat');
    expect(pageOf('#/everything')).toBeNull();
    expect(pageOf('')).toBeNull();
  });
});
