import { describe, expect, it } from 'vitest';
import { placeSection } from './structure';
import { invitationRecovery, inviteSecretFrom, moreDestination, primaryDestinations, safeWhiteboard } from './collaboration';

describe('pasted invitation links', () => {
  const secret = `invite_Ab12Cd34Ef56_${'x'.repeat(43)}`;
  it('finds the secret in a whole invite link, with or without the origin', () => {
    expect(inviteSecretFrom(`https://slate.example.com:8448/#/invite?secret=${secret}`)).toBe(secret);
    expect(inviteSecretFrom(`  #/invite?secret=${encodeURIComponent(secret)}\n`)).toBe(secret);
  });
  it('accepts the bare secret and reports an incomplete link as empty', () => {
    expect(inviteSecretFrom(secret)).toBe(secret);
    expect(inviteSecretFrom('https://slate.example.com/#/invite?secret=')).toBe('');
    expect(inviteSecretFrom('https://slate.example.com/#/invite?x=1')).toBe('');
  });
  it('leaves ordinary tokens and other links alone', () => {
    expect(inviteSecretFrom(`slate_Ab12Cd34Ef56_${'x'.repeat(43)}`)).toBeNull();
    expect(inviteSecretFrom('https://slate.example.com/#/section/Ab12Cd34Ef56')).toBeNull();
    expect(inviteSecretFrom('invite_short')).toBeNull();
    expect(inviteSecretFrom('')).toBeNull();
  });
});

describe('invitation recovery', () => {
  it('gives a fresh-link route for an invalid, expired, revoked or used invitation', () => {
    for (const status of [400, 403, 404, 409, 410, 422]) {
      expect(invitationRecovery(status)).toContain('fresh invitation link');
      expect(invitationRecovery(status)).toContain('own access');
    }
  });
  it('does not pretend a network, rate-limit or server failure needs a new invitation', () => {
    for (const status of [0, 408, 429, 500, 502, 503]) {
      expect(invitationRecovery(status)).toContain('Try again');
      expect(invitationRecovery(status)).not.toContain('fresh invitation');
    }
    expect(invitationRecovery(null)).toBe('');
  });
});

describe('project navigation', () => {
  it('keeps five primary destinations and makes deep links discoverable', () => {
    expect(primaryDestinations).toEqual(['home', 'inbox', 'docs', 'chat', 'everything']);
    expect(moreDestination('agents')).toBe(true);
    expect(moreDestination('docs')).toBe(false);
    expect(moreDestination(null)).toBe(false);
  });
  it('permits only credential-free HTTPS whiteboards', () => {
    expect(safeWhiteboard('https://example.com/board')).toBe(true);
    for (const url of ['javascript:alert(1)', 'http://example.com', 'https://user:pass@example.com', '//example.com', 'https://x\n.com']) expect(safeWhiteboard(url)).toBe(false);
  });
  it('places sections before or after targets without changing containers', () => {
    const rows = [{ id: 'a', position: 0 }, { id: 'b', position: 1 }, { id: 'c', position: 2 }];
    expect(placeSection(rows, 'c', 'a', false)).toEqual([{ id: 'c', position: 0 }, { id: 'a', position: 1 }, { id: 'b', position: 2 }]);
    expect(placeSection(rows, 'a', 'c', true)).toEqual([{ id: 'b', position: 0 }, { id: 'c', position: 1 }, { id: 'a', position: 2 }]);
    expect(placeSection(rows, 'a', 'a', true)).toEqual([]);
    expect(placeSection(rows, 'unknown', 'a', false)).toEqual([]);
    expect(placeSection(rows, 'a', 'unknown', false)).toEqual([]);
  });
});
