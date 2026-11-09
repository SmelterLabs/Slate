// Unit tests run in one fixed sample zone with daylight saving. The product default is UTC; a
// board takes its zone from the server (`SLATE_TIMEZONE`).
import { setWorkspaceTimezone } from './lib/timezone';

setWorkspaceTimezone('America/Chicago');
