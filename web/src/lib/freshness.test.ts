import { describe, expect, it } from 'vitest';
import { buildOf, isNewBuild } from './freshness';

const html = (name: string) =>
  `<!doctype html><head><script type="module" crossorigin src="/assets/${name}.js"></script>` +
  `<link rel="stylesheet" crossorigin href="/assets/index-CSS1.css"></head>`;

describe('freshness', () => {
  it('reads the build from the entry script in index.html', () => {
    expect(buildOf(html('index-AbC_12'))).toBe('/assets/index-AbC_12.js');
    expect(buildOf('<html>no bundle</html>')).toBeNull();
  });

  it('a different bundle on the server (/healthz `board`) is a new build', () => {
    expect(isNewBuild('/assets/index-A.js', '/assets/index-B.js')).toBe(true);
    expect(isNewBuild('/assets/index-A.js', '/assets/index-A.js')).toBe(false);
  });

  it('never reloads on what it cannot read (dev server, no board served, an old server)', () => {
    expect(isNewBuild(null, '/assets/index-B.js')).toBe(false);
    expect(isNewBuild('/assets/index-A.js', null)).toBe(false);
    expect(isNewBuild('/assets/index-A.js', undefined)).toBe(false);
    expect(isNewBuild('/assets/index-A.js', '<html>502</html>')).toBe(false);
  });
});
