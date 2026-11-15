# Repository checks

The private-terms check and its Git hooks, the CI workflows and the rules a test holds them to,
the security scans, how rule 5 is tested, and the Office test fixtures.

## Repository checks

These checks guard the repository itself, not the running product. They back two product rules
in `AGENTS.md`: rule 9 (the repository is public and holds nothing personal) and rule 5 (Slate
knows no agent runtime). Each check runs locally and in CI; a test fails when a workflow drifts
from the rules below.

## The private-terms check

`scripts/check_private_terms.py` fails when a file or a commit message holds a word from a
maintainer's denylist of SHA-256 hashes.

- **Where the list lives.** The file named by `SLATE_PRIVATE_TERMS_FILE`, otherwise
  `private_terms.sha256` in the clone's common Git directory (`git rev-parse --git-common-dir`), so
  every worktree of a clone shares one list. The list is never tracked. Without a list every mode
  exits 0; the default mode prints "nothing to check", `--staged` and `--message` stay silent. A
  list that exists but is empty, or holds a line that is not 64 hex characters, is an error.
- **What counts as a term.** Text is lowercased and, when not ASCII, NFKD-folded with its accents
  dropped; a word is then a run of `[a-z0-9]`. A term matches in any case, with or without
  accents. Each word is hashed and looked up in the list.
- **What is scanned.** With no arguments, every tracked file (`git ls-files`); with paths, just
  those files. Every mode scans the file name as well as the content. For `.docx`, `.xlsx` and
  `.pptx` it scans the archive's member names and the text of every `.xml` and `.rels` member, and
  refuses an archive over 64 MB uncompressed. Other files are read as UTF-8, binaries with
  undecodable bytes replaced. `--staged` scans the index bytes (`git show :<path>`) of every added,
  copied, modified or renamed file, not the working copy. `--message FILE` scans the message and
  the Git author and committer email addresses; masked `users.noreply` addresses are skipped, and
  author and committer names are not scanned.
- **Output and exit codes.** A hit prints `path:line: word` (line 0 for a name, `commit message:N`
  for a message). With the `CI` variable set, the word is replaced by a note to run the check
  locally. Exit 0 is clean or no list; 1 is a hit (or `--add` outside a Git checkout with no
  `SLATE_PRIVATE_TERMS_FILE`); 2 is a refused `--add`.
- **Adding a word.** `python scripts/check_private_terms.py --add WORD ...` appends the hashes not
  already on the list. A term that is not one word of letters and digits is refused, nothing is
  added, and the message names the parts to add instead (`my-host` as `my` and `host`). The CI copy
  is the repository secret `PRIVATE_TERMS_SHA256`, set to the contents of the same file.
- **Hooks.** `sh scripts/install-hooks.sh` writes `pre-commit` and `commit-msg` into the clone's
  hooks directory and reports whether a list exists. The pre-commit hook refuses any commit on
  `main` or `master` (and on a detached HEAD), then runs `--staged`; the commit-msg hook runs
  `--message`. Both do nothing on a commit that has no `scripts/check_private_terms.py`. Installed
  hooks are copies, so rerun the script when it changes.
- **In CI** (`.github/workflows/ci.yml`, job `private-terms`). The change is checked out as data
  into `pr/` and the base commit's `scripts/` into `trusted/`. When the secret is empty, as on
  forks, the job prints a skip line and passes. Otherwise it writes the secret to a file named by
  `SLATE_PRIVATE_TERMS_FILE`, copies the trusted checker into `pr/.trusted/` (`mkdir` fails if the
  change already has that folder) and runs it with `python3 -I` over every tracked file. It then
  runs `--message` over each new commit's message, author address and committer address
  (`git log BASE..HEAD`, masked addresses removed), unless there is no base commit. No code from
  the change runs in the job, so an edit to the checker takes effect once it is on the base branch.

## CI

`.github/workflows/ci.yml` runs on pull requests, pushes to `main` and `v*` tags, and by hand.

- `test` (30 min): `uv sync --frozen`, `ruff check`, `ruff format --check`, then `pytest` with
  `integrations/` moved away, `pytest integrations` when the folder exists, and
  `pytest clients/python/tests`.
- `private-terms` (10 min): above.
- `bench` (30 min): `scripts/load_test.py --ci`, which fails when a budget in `bench/budgets.json`
  is broken; the result is kept as an artifact for 30 days (`docs/PERFORMANCE.md`).
- `web` (45 min): `npm ci`, `npm run check`, `npm run build`, then `npm test` (Playwright, against
  a real Slate server). Chromium is cached per Playwright version; its install is tried up to three
  times with apt retrying each download. Traces are kept for 7 days when the job fails.

`tests/test_ci.py` reads the workflow files and fails unless: `ci.yml` and `security.yml` exist;
every workflow sets `permissions: {contents: read}` and no job widens it; every `uses` is pinned to
a 40-character commit; every checkout sets `persist-credentials: false`; every job has
`timeout-minutes` above 0 and at most 60; `ci.yml` mentions neither `packages:` nor `docker/`; the
`PRIVATE_TERMS_SHA256` secret reaches only the `private-terms` job, which checks out the base
commit as `trusted`, runs no `uv`, `pip`, `npm` or `npx`, runs the copied checker with
`python3 -I` and checks messages with `%ae` and `%ce`; the gitleaks download is checked against a
SHA-256 and semgrep is pinned to a version; the `web` job caches `ms-playwright`, avoids
`--with-deps` and retries the install; `.github/dependabot.yml` covers exactly `github-actions`,
`uv` and `docker` at `/` and `npm` at `/web`. That config asks for updates weekly, each release
at least 7 days old; the test does not check the schedule.

## Security scans

`.github/workflows/security.yml` runs on pull requests, pushes to `main` and `v*` tags, and by
hand. Job `secrets` (15 min) downloads a pinned gitleaks release, verifies its SHA-256 and runs
`gitleaks git --redact --exit-code 1` over all reachable history (`fetch-depth: 0`). Job `code`
(20 min, pull requests only) installs a pinned semgrep and runs the `p/default` rules against the
pull request's base commit (`--baseline-commit`), failing on any finding.

## How rule 5 is tested

`tests/test_rule5.py` fails when any line of a `.py`, `.html` or `.js` file under `src/` matches a
fixed, case-insensitive list of agent runtime names, or when a `.py` or `.mjs` file under `tests/`
other than itself contains the word `integrations`. Only `src/` is searched for names. CI runs the
main suite with `integrations/` moved away (see `test` above). The same file loads the runner's
build hook, `clients/python/hatch_build.py`, which bundles the reference adapters from
`integrations/` (or from a source distribution's copy) when present and skips them when absent;
the tests prove it builds both ways. When a chosen adapter is not in the installed package,
`clients/python/slate_connect.py` stops with a setup error asking for a package with the adapter
assets.

## Office test fixtures

`web/e2e/fixtures/` holds `scope.docx`, `long.docx` and `quote.xlsx`. The workbook is generated by
`node web/e2e/fixtures/generate-workbook.mjs` with exceljs, the library the board's preview uses,
from invented content with fixed dates and a generic creator. `web/e2e/documents.spec.ts` uploads
all three as attachments and opens each in the board's viewer. The private-terms check reads their
names and their XML, metadata included, like any other file.
