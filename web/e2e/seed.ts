import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { workspaceDay } from '../src/lib/quickadd';
import { setWorkspaceTimezone } from '../src/lib/timezone';

export const WORKSPACE_TIMEZONE = 'America/Chicago';
setWorkspaceTimezone(WORKSPACE_TIMEZONE);

// Playwright shares one instant across setup and workers so a run can straddle midnight.
export const calendarNow = () => new Date(process.env.SLATE_E2E_NOW!);
export const today = () => workspaceDay(calendarNow());

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
