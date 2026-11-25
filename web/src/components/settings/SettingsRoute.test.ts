import { describe, expect, it } from 'vitest';
import { pageOf } from '../../lib/route';
import { moreDestination } from '../../lib/collaboration';

describe('settings route', () => {
  it('recognizes #/settings as the settings page', () => {
    expect(pageOf('#/settings')).toBe('settings');
    expect(pageOf('#/settings/')).toBe('settings');
    expect(pageOf('#/settings?tab=ai')).toBe('settings');
  });

  it('keeps settings under the More nav drawer', () => {
    expect(moreDestination('settings')).toBe(true);
  });
});
