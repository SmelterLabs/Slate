// A pasted token is a credential. The board may send it as the stream's query parameter (the
// server redacts it from the access log); it must never be written into a URL the board builds,
// a link or a log line.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

function files(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) files(p, out);
    else if (/\.(ts|svelte)$/.test(e.name) && !e.name.endsWith('.test.ts')) out.push(p);
  }
  return out;
}

describe('the token', () => {
  it('is never written into a URL, a link or a log line the board builds', () => {
    const offenders: string[] = [];
    for (const file of files(join(__dirname, '..'))) {
      const src = readFileSync(file, 'utf8');
      for (const m of src.matchAll(/(?:console\.\w+|location\.(?:href|assign|replace)|window\.open)\([^)]*token/g)) {
        offenders.push(`${file}: ${m[0].slice(0, 80)}`);
      }
      for (const m of src.matchAll(/`[^`]*\?[^`]*token=[^`]*`/g)) {
        if (!/events\/stream/.test(m[0])) offenders.push(`${file}: ${m[0].slice(0, 80)}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
