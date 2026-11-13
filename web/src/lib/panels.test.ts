// @vitest-environment happy-dom
// The per-device stores: the task window's dock and size (taskwindow.svelte.ts), the chat
// panel (chatpanel.svelte.ts) and the live set of drafts (drafts.svelte.ts). Each remembers its
// state in localStorage, so a fresh module after a write is the next page load.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { STORAGE_KEY as WINDOW_KEY, DEFAULT_WIDTH, DEFAULT_HEIGHT, NARROWEST_WIDTH } from './taskwindow';
import { STORAGE_KEY as PANEL_KEY, PANEL_DEFAULT_WIDTH } from './chatpanel';
import { DRAFT_PREFIX, draftKey } from './drafts';

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  window.innerWidth = 1600;
  window.innerHeight = 1000;
});
afterEach(() => localStorage.clear());

const taskWindow = async () => (await import('./taskwindow.svelte')).taskWindow;
const chatPanel = async () => (await import('./chatpanel.svelte')).chatPanel;
const drafts = async () => import('./drafts.svelte');

describe('task window', () => {
  it('opens centered at the default size on a device that never chose one', async () => {
    const w = await taskWindow();
    expect([w.dock, w.width, w.height, w.docked]).toEqual(['center', DEFAULT_WIDTH, DEFAULT_HEIGHT, false]);
  });

  it('a dock chosen once is where the next page opens it', async () => {
    (await taskWindow()).setDock('third');
    vi.resetModules();
    const again = await taskWindow();
    expect(again.dock).toBe('third');
    expect(again.docked).toBe(true);
    expect(again.shown).toBe(Math.round(1600 / 3));
  });

  it('a resize stays within the screen and the smallest window, and is remembered', async () => {
    const w = await taskWindow();
    w.resize(10_000, 10_000);
    expect(w.width).toBeLessThan(1600);
    expect(w.height).toBeLessThan(1000);
    w.resize(10, 10);
    expect(w.width).toBe(NARROWEST_WIDTH);
    expect(JSON.parse(localStorage.getItem(WINDOW_KEY)!).width).toBe(NARROWEST_WIDTH);
    w.resetSize();
    expect([w.width, w.height]).toEqual([DEFAULT_WIDTH, DEFAULT_HEIGHT]);
  });

  it('a narrow dock stacks the activity under the task', async () => {
    const w = await taskWindow();
    w.setDock('quarter');
    expect(w.stacked).toBe(true);
    w.setDock('center');
    expect(w.stacked).toBe(false);
  });

  it('with no storage it still works for the page', async () => {
    const w = await taskWindow();
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(() => w.setDock('third')).not.toThrow();
    expect(w.dock).toBe('third');
    set.mockRestore();
  });
});

describe('chat panel', () => {
  it('stays open on its channel and thread across a reload', async () => {
    const p = await chatPanel();
    expect(p.open).toBe(false);
    p.show('general', 'M1');
    vi.resetModules();
    const again = await chatPanel();
    expect([again.open, again.channel, again.thread]).toEqual([true, 'general', 'M1']);
  });

  it('a different channel drops the old thread; the same one keeps it', async () => {
    const p = await chatPanel();
    p.show('general', 'M1');
    p.show('general');
    expect(p.thread).toBe('M1');
    p.show('dm:alex');
    expect([p.channel, p.thread]).toEqual(['dm:alex', null]);
    p.setThread('M2');
    p.pick('dm:alex');
    expect(p.thread).toBe('M2');
    p.pick('general');
    expect(p.thread).toBeNull();
  });

  it('the sidebar entry toggles it, and closing leaves full screen', async () => {
    const p = await chatPanel();
    p.toggle();
    expect(p.open).toBe(true);
    p.toggleFullscreen();
    expect(p.expanded).toBe(true);
    p.toggle();
    expect([p.open, p.fullscreen]).toEqual([false, false]);
  });

  it('wide and compact: the width toggles between the default and 720', async () => {
    const p = await chatPanel();
    expect(p.width).toBe(PANEL_DEFAULT_WIDTH);
    p.toggleWidth();
    expect(p.width).toBe(720);
    expect(p.expanded).toBe(true);
    p.toggleWidth();
    expect(p.width).toBe(PANEL_DEFAULT_WIDTH);
    expect(JSON.parse(localStorage.getItem(PANEL_KEY)!).width).toBe(PANEL_DEFAULT_WIDTH);
  });

  it('expanding to the Chat page and docking back returns to the page it came from', async () => {
    const p = await chatPanel();
    p.show('general');
    p.expand('#/list/L1');
    expect(p.open).toBe(false);
    expect(p.dock('general')).toBe('#/list/L1');
    expect(p.open).toBe(true);
    // Docking from a chat page, or with no page remembered, goes home.
    p.expand('#/chat/general');
    expect(p.dock('general')).toBe('#/home');
    expect(p.dock('general')).toBe('#/home');
  });
});

describe('drafts', () => {
  it('typing marks the box and its card; sending (blank) clears both', async () => {
    const d = await drafts();
    const key = draftKey.comment('I1');
    d.saveDraft(key, 'half a thought');
    expect(d.hasDraft(key)).toBe(true);
    expect(d.itemHasDraft('I1')).toBe(true);
    expect(d.itemHasDraft('I2')).toBe(false);
    d.saveDraft(key, '');
    expect(d.hasDraft(key)).toBe(false);
    expect(d.itemHasDraft('I1')).toBe(false);
  });

  it('a draft typed in another tab shows here, and its removal too', async () => {
    const d = await drafts();
    const key = draftKey.comment('I9');
    localStorage.setItem(DRAFT_PREFIX + key, JSON.stringify({ t: 'from the other tab', at: Date.now() }));
    window.dispatchEvent(new StorageEvent('storage', { key: DRAFT_PREFIX + key }));
    expect(d.hasDraft(key)).toBe(true);
    localStorage.removeItem(DRAFT_PREFIX + key);
    window.dispatchEvent(new StorageEvent('storage', { key: DRAFT_PREFIX + key }));
    expect(d.hasDraft(key)).toBe(false);
  });

  it('nothing is saved while the page is leaving, until the person acts again', async () => {
    const d = await drafts();
    const key = draftKey.comment('I1');
    window.dispatchEvent(new Event('beforeunload'));
    d.saveDraft(key, 'the message that was just sent');
    expect(d.hasDraft(key)).toBe(false);
    expect(localStorage.getItem(DRAFT_PREFIX + key)).toBeNull();
    window.dispatchEvent(new Event('keydown'));
    d.saveDraft(key, 'typed after all');
    expect(d.hasDraft(key)).toBe(true);
  });

  it('loads the drafts already stored when the page starts', async () => {
    const key = draftKey.comment('I3');
    localStorage.setItem(DRAFT_PREFIX + key, JSON.stringify({ t: 'kept', at: Date.now() }));
    const d = await drafts();
    expect(d.itemHasDraft('I3')).toBe(true);
  });
});
