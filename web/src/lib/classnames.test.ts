// Svelte scopes a component's <style>, but app.css still styles any element with a global class.
// `.row` (the List layout's row grid) and `.card` (a board card) break a component that
// reuses the name for an inner wrapper (docs/design-system.md, "Scoped class names").
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const RESERVED = ['row', 'card'];
const root = join(__dirname, '..', 'components');

function svelteFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? svelteFiles(join(dir, e.name)) : e.name.endsWith('.svelte') ? [join(dir, e.name)] : [],
  );
}

describe('component styles', () => {
  it('do not redefine the global layout classes', () => {
    const offenders: string[] = [];
    for (const file of svelteFiles(root)) {
      const raw = /<style[^>]*>([\s\S]*?)<\/style>/.exec(readFileSync(file, 'utf8'))?.[1] ?? '';
      const style = raw.replace(/\/\*[\s\S]*?\*\//g, '');
      for (const name of RESERVED) {
        if (new RegExp(`(^|[\\s,}])\\.${name}(?![\\w-])`, 'm').test(style)) offenders.push(`${file}: .${name}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
