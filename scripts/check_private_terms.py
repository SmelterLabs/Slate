"""Fail when a file (or a commit message) contains a term from a private denylist.

This repository is public (AGENTS.md, product rule 9): nothing personal or deployment-specific
belongs in it. A maintainer can keep a list of words that must never be committed (names, hosts,
clients). Every word in a file is lowercased, hashed and looked up in that list.

The list is never part of the repository. Hashing does not hide a short word: a name or a
hostname is recovered from its SHA-256 by guessing, so a committed list publishes the words it
was meant to protect. It lives in `<git dir>/private_terms.sha256` on the maintainer's machine,
or wherever `SLATE_PRIVATE_TERMS_FILE` points (CI writes it there from a repository secret).
Without a list there is nothing to check and every mode exits 0.

Usage:
    python scripts/check_private_terms.py            # every tracked file
    python scripts/check_private_terms.py FILE ...   # just these files
    python scripts/check_private_terms.py --staged    # index bytes, including filenames
    python scripts/check_private_terms.py --message FILE   # message and Git author/committer emails
    python scripts/check_private_terms.py --add WORD ...   # append hashes to the local list

A term is one word of letters and digits, compared without case or accents. `--add` refuses
anything else (exit 2) and names the words to add instead: `my-host` is `my` and `host`.

Exit status 1 lists `path:line: <word>` for every hit (the word is left out of CI logs).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = "SLATE_PRIVATE_TERMS_FILE"
LIST_NAME = "private_terms.sha256"
WORD = re.compile(r"[a-z0-9]+")
OFFICE_SUFFIXES = {".docx", ".xlsx", ".pptx"}
MAX_OFFICE_BYTES = 64 * 1024 * 1024


def _hash(word: str) -> str:
    return hashlib.sha256(word.lower().encode()).hexdigest()


def _fold(text: str) -> str:
    """Lowercase and drop accents, so `Renée` and `renee` are the same word."""
    text = text.lower()
    if text.isascii():
        return text
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def words(text: str) -> list[str]:
    """The words the scanner compares: runs of ASCII letters and digits after `_fold`."""
    return WORD.findall(_fold(text))


def denylist_path() -> Path | None:
    """Where the list is kept: `SLATE_PRIVATE_TERMS_FILE`, else the Git directory shared by every
    worktree of this clone. None outside a Git checkout (a source archive has no list)."""
    configured = os.environ.get(ENV_FILE, "").strip()
    if configured:
        return Path(configured)
    found = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, capture_output=True, text=True
    )
    if found.returncode != 0 or not found.stdout.strip():
        return None
    return (ROOT / found.stdout.strip()).resolve() / LIST_NAME


def load_hashes() -> set[str] | None:
    """The denylist, or None when this checkout has none. A list that exists but is empty or
    malformed is an error: a damaged list must not pass as "nothing to check"."""
    path = denylist_path()
    if path is None or not path.exists():
        return None
    hashes = {line.strip() for line in path.read_text().splitlines() if line.strip()}
    if not hashes or any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        raise ValueError(f"private denylist is empty or malformed: {path}")
    return hashes


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [ROOT / p.decode() for p in out.split(b"\0") if p]


def scan_text(text: str, hashes: set[str]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for word in words(line):
            if _hash(word) in hashes:
                hits.append((lineno, word))
    return hits


def scan_bytes(name: str, data: bytes, hashes: set[str]) -> list[tuple[int, str]]:
    hits = [(0, word) for _, word in scan_text(name, hashes)]
    if Path(name).suffix.lower() in OFFICE_SUFFIXES:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_OFFICE_BYTES:
                raise ValueError(f"Office fixture too large to scan: {name}")
            for info in archive.infolist():
                hits.extend((0, word) for _, word in scan_text(info.filename, hashes))
                if info.filename.endswith((".xml", ".rels")):
                    hits.extend(scan_text(archive.read(info).decode("utf-8"), hashes))
        return hits
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Binary assets still need manual content/metadata review; this catches plain strings.
        text = data.decode("utf-8", errors="replace")
    return hits + scan_text(text, hashes)


def scan_file(path: Path, hashes: set[str]) -> list[tuple[int, str]]:
    name = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    return scan_bytes(name, path.read_bytes(), hashes)


def staged_files() -> list[tuple[str, bytes]]:
    names = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], cwd=ROOT
    )
    return [
        (name.decode(), subprocess.check_output(["git", "show", ":" + name.decode()], cwd=ROOT))
        for name in names.split(b"\0")
        if name
    ]


def _shown(word: str) -> str:
    """A CI log on a public repository is public: say where, not what."""
    return "(term hidden in CI logs; run the check locally)" if os.environ.get("CI") else word


def _add(terms: list[str]) -> int:
    """Append terms to the local list. The scanner only ever compares single words (see
    `words`), so a term with a space, a hyphen, a dot or an `@` could never match: refuse it,
    and name the words to add instead, rather than store a hash that never fires."""
    path = denylist_path()
    if path is None:
        print(f"not a Git checkout: set {ENV_FILE} to the list's path", file=sys.stderr)
        return 1
    folded = [_fold(w) for w in terms]
    refused = [t for t, f in zip(terms, folded, strict=True) if not WORD.fullmatch(f)]
    if refused:
        for term in refused:
            parts = " ".join(words(term))
            hint = f"; add its parts instead: --add {parts}" if parts else ""
            print(
                f"refused {term!r}: a term must be one word of letters and digits{hint}",
                file=sys.stderr,
            )
        print("nothing added", file=sys.stderr)
        return 2
    existing = load_hashes() or set()
    new = list(dict.fromkeys(_hash(w) for w in folded if _hash(w) not in existing))
    with path.open("a") as fh:
        fh.write("".join(h + "\n" for h in new))
    print(f"added {len(new)} hash(es) to {path}")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--add"]:
        return _add(argv[1:])
    hashes = load_hashes()
    if hashes is None:
        if argv[:1] not in (["--staged"], ["--message"]):
            print("no private denylist in this checkout; nothing to check", file=sys.stderr)
        return 0
    if argv[:1] == ["--staged"]:
        failed = False
        for name, data in staged_files():
            for lineno, word in scan_bytes(name, data, hashes):
                failed = True
                print(f"{name}:{lineno}: {_shown(word)}")
        return int(failed)
    if argv[:1] == ["--message"]:
        hits = scan_text(Path(argv[1]).read_text(encoding="utf-8"), hashes)
        for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            identity = subprocess.check_output(["git", "var", kind], cwd=ROOT).decode()
            email = re.search(r"<([^<>]+)>", identity)
            if email and not re.fullmatch(
                r"(?:\d+\+)?[a-z0-9-]+@users\.noreply\.github\.com",
                email.group(1),
                re.IGNORECASE,
            ):
                hits.extend(scan_text(email.group(1), hashes))
        for lineno, word in hits:
            print(f"commit message:{lineno}: {_shown(word)}")
        return 1 if hits else 0
    paths = [Path(p).resolve() for p in argv] if argv else tracked_files()
    failed = False
    for path in paths:
        for lineno, word in scan_file(path, hashes):
            failed = True
            print(f"{path.relative_to(ROOT).as_posix()}:{lineno}: {_shown(word)}")
    if failed:
        print("private terms found; see AGENTS.md rule 9", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
