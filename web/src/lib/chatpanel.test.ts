import { describe, expect, it } from 'vitest';
import {
  chatPanelOf,
  clampPanelWidth,
  DEFAULT_PANEL,
  PANEL_DEFAULT_WIDTH,
  PANEL_NARROWEST,
  panelChannelFor,
  panelFits,
  flashTitle,
  readPanelState,
  windowBadge,
  writePanelState,
} from './chatpanel';

describe('chatPanelOf', () => {
  it('reads ?chat= and ?thread= from any route', () => {
    expect(chatPanelOf('#/docs/abc?chat=list:L1')).toEqual({ channel: 'list:L1', thread: null });
    expect(chatPanelOf('#/list/L1?task=T1&chat=general&thread=M9')).toEqual({ channel: 'general', thread: 'M9' });
  });
  it('is null without ?chat=', () => {
    expect(chatPanelOf('#/docs/abc')).toBeNull();
    expect(chatPanelOf('#/chat/general?thread=M1')).toBeNull();
  });
});

describe('panelFits', () => {
  it('wide screens get the panel, phones and narrow windows the page', () => {
    expect(panelFits(1920)).toBe(true);
    expect(panelFits(900)).toBe(true);
    expect(panelFits(899)).toBe(false);
    expect(panelFits(390)).toBe(false);
  });
});

describe('clampPanelWidth', () => {
  it('keeps the width between the narrowest and what the window leaves for the page', () => {
    expect(clampPanelWidth(420, 1920)).toBe(420);
    expect(clampPanelWidth(100, 1920)).toBe(PANEL_NARROWEST);
    expect(clampPanelWidth(5000, 1920)).toBe(1920 - 248 - 160);
    expect(clampPanelWidth(633.6, 1920)).toBe(634);
  });
  it('never goes under the narrowest, even in a window too small for both', () => {
    expect(clampPanelWidth(900, 500)).toBe(PANEL_NARROWEST);
  });
});

describe('panel state storage', () => {
  it('round-trips', () => {
    const s = { open: true, channel: 'list:L1', thread: 'M2', width: 560 };
    expect(readPanelState(writePanelState(s))).toEqual(s);
  });
  it('gives a stored state from before widths the default width', () => {
    expect(readPanelState('{"open":true,"channel":"general","thread":null}').width).toBe(PANEL_DEFAULT_WIDTH);
    expect(readPanelState('{"open":true,"channel":"general","thread":null,"width":"wide"}').width).toBe(PANEL_DEFAULT_WIDTH);
    expect(readPanelState('{"open":true,"channel":"general","thread":null,"width":-5}').width).toBe(PANEL_DEFAULT_WIDTH);
  });
  it('falls back to the default on nothing, junk or a missing channel', () => {
    expect(readPanelState(null)).toEqual(DEFAULT_PANEL);
    expect(readPanelState('{not json')).toEqual(DEFAULT_PANEL);
    expect(readPanelState('{"open":true}')).toEqual(DEFAULT_PANEL);
    expect(readPanelState('{"open":"yes","channel":"general","thread":3}')).toEqual({ open: false, channel: 'general', thread: null, width: PANEL_DEFAULT_WIDTH });
  });
});

describe('panelChannelFor', () => {
  const ch = (key: string, unread = 0, mentions = 0) => ({ key, unread, mentions });
  it('keeps the remembered channel when the account can see it', () => {
    expect(panelChannelFor('list:L1', [ch('general'), ch('list:L1')])).toBe('list:L1');
  });
  it('falls back for a guest whose browser remembers General', () => {
    expect(panelChannelFor('general', [ch('section:S1'), ch('list:L1')])).toBe('section:S1');
  });
  it('prefers a channel with a mention, then one with unread', () => {
    expect(panelChannelFor('general', [ch('section:S1'), ch('list:L1', 3), ch('list:L2', 1, 1)])).toBe('list:L2');
    expect(panelChannelFor('general', [ch('section:S1'), ch('list:L1', 3)])).toBe('list:L1');
  });
  it('is null until the channels are known', () => {
    expect(panelChannelFor('general', [])).toBeNull();
  });
});

describe('windowBadge', () => {
  it('counts unread chat only', () => {
    expect(windowBadge(3)).toEqual({ title: '(3) Slate', count: 3 });
    expect(windowBadge(0)).toEqual({ title: 'Slate', count: 0 });
  });
});

describe('flashTitle', () => {
  const ch = (name: string, unread: number, at: string | null) => ({ name, unread, last_message_at: at });
  it('names the channel with the newest unread message, and counts the others', () => {
    expect(flashTitle([ch('General', 0, '2026-11-02T10:00:00Z'), ch('Todo', 2, '2026-11-02T09:00:00Z')])).toBe('New in #Todo');
    expect(
      flashTitle([ch('Todo', 2, '2026-11-02T09:00:00Z'), ch('Example Project', 1, '2026-11-02T11:00:00Z'), ch('Ideas', 1, null)]),
    ).toBe('New in #Example Project +2');
  });
  it('is null with nothing unread', () => {
    expect(flashTitle([ch('General', 0, null)])).toBeNull();
  });
});
