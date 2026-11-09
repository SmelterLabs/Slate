import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setWorkspaceTimezone } from '../src/lib/timezone';

// The e2e server runs in one fixed sample zone (playwright.config.ts). Date helpers the
// specs borrow from the board must agree with it, so every spec gets it by importing this.
setWorkspaceTimezone('America/Chicago');

export const SEED_FILE = join(dirname(fileURLToPath(import.meta.url)), '..', '.e2e', 'seed.json');

export interface Seed {
  alex: string;
  agent: string;
  sectionId: string;
  listId: string;
  dragId: string;
  liveId: string;
  tasksId: string;
  workSectionId: string;
  todayId: string;
  bulkIds: string[];
}

export function seed(): Seed {
  return JSON.parse(readFileSync(SEED_FILE, 'utf8')) as Seed;
}

export const BASE_URL = `http://127.0.0.1:${Number(process.env.SLATE_E2E_PORT ?? 8765)}`;
