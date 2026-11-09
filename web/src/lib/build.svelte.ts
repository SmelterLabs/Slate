// Which build this board talks to: the revision number in the top-left corner (`r123`) and
// the short commit in its tooltip. The server knows (src/slate/build.py); the board reads it
// from /healthz on start and whenever freshness.ts checks for a new deploy.

export interface BuildInfo {
  rev: string;
  commit: string | null;
}

class Build {
  rev = $state('');
  commit = $state<string | null>(null);

  /** Take the fields from a /healthz body; anything unreadable leaves it as it was. */
  take(body: unknown) {
    if (!body || typeof body !== 'object') return;
    const b = body as Partial<BuildInfo>;
    if (typeof b.rev === 'string' && b.rev) this.rev = b.rev;
    this.commit = typeof b.commit === 'string' && b.commit ? b.commit : null;
  }

  /** `r123`, `dev` for a checkout, empty until known. */
  get label(): string {
    return !this.rev ? '' : /^\d+$/.test(this.rev) ? `r${this.rev}` : this.rev;
  }
}

export const build = new Build();
