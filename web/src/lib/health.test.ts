import { describe, expect, it } from 'vitest';
import { byUrgency, bytes, caller, isHealthEvent, ms, percent, uptime } from './health';

describe('health formatting', () => {
  it('puts problems first and keeps the server order otherwise', () => {
    const rows = [
      { key: 'a', status: 'ok' as const },
      { key: 'b', status: 'off' as const },
      { key: 'c', status: 'fail' as const },
      { key: 'd', status: 'warn' as const },
      { key: 'e', status: 'ok' as const },
    ];
    expect(byUrgency(rows).map((r) => r.key)).toEqual(['c', 'd', 'a', 'e', 'b']);
  });

  it('formats sizes, times and rates', () => {
    expect(bytes(null)).toBe('—');
    expect(bytes(512)).toBe('512 B');
    expect(bytes(1536)).toBe('1.5 KB');
    expect(bytes(250 * 1024 * 1024)).toBe('250 MB');
    expect(ms(3.25)).toBe('3.3 ms');
    expect(ms(412.4)).toBe('412 ms');
    expect(ms(2500)).toBe('2.5 s');
    expect(percent(0)).toBe('0%');
    expect(percent(0.004)).toBe('0.4%');
    expect(percent(0.5)).toBe('50%');
    expect(uptime(42)).toBe('42 s');
    expect(uptime(3 * 3600 + 120)).toBe('3 h 2 min');
    expect(uptime(2 * 86400 + 3600)).toBe('2 d 1 h');
  });

  it('names the caller and its token', () => {
    expect(caller({ display_name: 'Agent', handle: 'agent', token_label: 'server', token_id: 't' })).toBe('Agent · server');
    expect(caller({ display_name: null, handle: 'agent', token_label: null, token_id: 't' })).toBe('@agent');
  });

  it('refreshes at once only for health events', () => {
    expect(isHealthEvent('health.alert')).toBe(true);
    expect(isHealthEvent('item.updated')).toBe(false);
  });
});
