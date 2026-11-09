import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
it('plain document stylesheet contains no Svelte-only global pseudos', () => {
  expect(readFileSync(new URL('../docs.css', import.meta.url), 'utf8')).not.toMatch(/:global\(/);
});
