// Reads the board's global stylesheets (the ones main.ts imports) for the class-name guard test.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = join(__dirname, '..');

/** The global stylesheets, in the order main.ts imports them. */
export function globalSheets(): string[] {
  const main = readFileSync(join(SRC, 'main.ts'), 'utf8');
  return [...main.matchAll(/^import '\.\/([\w-]+\.css)';/gm)].map((m) => m[1]);
}

/** Classes a sheet styles on their own: a selector that is one class, maybe with pseudo-classes
 * (`.name`, `.name:hover`), in any rule, @media included. */
export function soloClasses(css: string): Set<string> {
  const out = new Set<string>();
  const body = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of body.matchAll(/(^|[{}])\s*([^{}@]+?)\s*\{/g)) {
    for (const sel of m[2].split(',')) {
      const one = /^\.([\w-]+)(?::{1,2}[\w-]+(?:\([^)]*\))?)*$/.exec(sel.trim());
      if (one) out.add(one[1]);
    }
  }
  return out;
}

/** Each class styled on its own by more than one global sheet, with the sheets. */
export function sharedClasses(): Map<string, string[]> {
  const where = new Map<string, string[]>();
  for (const file of globalSheets()) {
    for (const name of soloClasses(readFileSync(join(SRC, file), 'utf8'))) {
      where.set(name, [...(where.get(name) ?? []), file]);
    }
  }
  return new Map([...where].filter(([, files]) => files.length > 1));
}
