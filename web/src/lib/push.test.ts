import { describe, expect, it } from 'vitest';
import { DEFAULT_PUSH_REASONS, deviceLabel, missedHint, NOTIFY_GROUPS, PUSH_REASONS } from './push';

const MAC_CHROME = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36';
const WIN_EDGE = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 Edg/130.0';
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';

describe('missedHint', () => {
  it('sends a Mac to System Settings for the browser, or for Slate when installed', () => {
    expect(missedHint(MAC_CHROME, false)).toContain('System Settings → Notifications → Google Chrome');
    expect(missedHint(MAC_CHROME, true)).toContain('System Settings → Notifications → Slate');
  });
  it('names the Windows and iPhone switches', () => {
    expect(missedHint(WIN_EDGE, false)).toContain('Windows Settings → System → Notifications → Microsoft Edge');
    expect(missedHint(IPHONE, true)).toContain('Settings → Notifications → Slate');
  });
});

describe('failed and stalled reasons', () => {
  it('lists failed and stalled right after it for push, fallback defaults, and the notifications grid', () => {
    const keys = PUSH_REASONS.map((r) => r.key);
    expect(keys).toContain('failed');
    expect(keys[keys.indexOf('failed') + 1]).toBe('stalled');
    expect(DEFAULT_PUSH_REASONS).toContain('failed');
    expect(DEFAULT_PUSH_REASONS[DEFAULT_PUSH_REASONS.indexOf('failed') + 1]).toBe('stalled');
    const groups = NOTIFY_GROUPS.map((g) => g.key);
    expect(groups).toContain('failed');
    expect(groups[groups.indexOf('failed') + 1]).toBe('stalled');
    expect(NOTIFY_GROUPS.flatMap((g) => g.reasons)).toContain('stalled');
  });
});

describe('deviceLabel', () => {
  it('names the OS and browser, or the installed app', () => {
    expect(deviceLabel(MAC_CHROME, false)).toBe('Mac (Chrome)');
    expect(deviceLabel(IPHONE, true)).toBe('iPhone app');
  });
});
