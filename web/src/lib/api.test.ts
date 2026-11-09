import { describe, expect, it } from 'vitest';
import { authHeaders } from './api';
import { COOKIE_SESSION } from './collaboration';

describe('authHeaders', () => {
  it('marks every board request as the browser and as source ui', () => {
    for (const token of ['slate_x_y', COOKIE_SESSION, null]) {
      const h = authHeaders(token);
      expect(h['X-Slate-Client']).toBe('browser');
      expect(h['X-Slate-Source']).toBe('ui');
    }
    expect(authHeaders('slate_x_y').Authorization).toBe('Bearer slate_x_y');
    expect(authHeaders(COOKIE_SESSION).Authorization).toBeUndefined();
  });
});
