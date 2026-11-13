"""The repository is public (AGENTS.md rule 9): no file may carry a term from a private denylist.

`scripts/check_private_terms.py` holds the mechanism. The list of hashed words is kept outside
the repository (the Git directory, or the file `SLATE_PRIVATE_TERMS_FILE` names): a hashed name
or hostname is recovered by guessing, so the list must never be published. Where a list is
configured, these tests run it over every tracked file; without one that check is skipped.
"""

import importlib.util
import io
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_private_terms.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_private_terms", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _denylist(tmp_path, monkeypatch, *words):
    """Point the checker at a throwaway list holding these words."""
    mod = _load()
    path = tmp_path / "terms.sha256"
    path.write_text("".join(mod._hash(w) + "\n" for w in words), encoding="utf-8")
    monkeypatch.setenv(mod.ENV_FILE, str(path))
    return mod, path


def test_denylist_is_never_tracked():
    """Hashes of short words are guessable, so a committed list would publish the words."""
    mod = _load()
    tracked = [p.relative_to(ROOT).as_posix() for p in mod.tracked_files()]
    assert not [name for name in tracked if name.endswith(".sha256")]


def test_default_denylist_lives_in_the_git_directory(monkeypatch):
    mod = _load()
    monkeypatch.delenv(mod.ENV_FILE, raising=False)
    path = mod.denylist_path()
    assert path is not None
    assert path.name == mod.LIST_NAME
    assert ".git" in path.parts


def test_env_names_the_denylist(tmp_path, monkeypatch):
    mod, path = _denylist(tmp_path, monkeypatch, "privatetestword")
    assert mod.denylist_path() == path
    assert mod.load_hashes() == {mod._hash("privatetestword")}


def test_configured_denylist_is_hashed():
    mod = _load()
    hashes = mod.load_hashes()
    if hashes is None:
        pytest.skip("no private denylist in this checkout")
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in hashes)


def test_scanner_catches_a_denied_word():
    mod = _load()
    word = "probe-word"
    hashes = {mod._hash("probeword")}
    assert mod.scan_text("ok line\nthis has probeword here", hashes) == [(2, "probeword")]
    assert mod.scan_text(f"nothing {word} split", hashes) == []  # hyphen splits the token


def test_no_private_terms_in_tracked_files():
    mod = _load()
    hashes = mod.load_hashes()
    if hashes is None:
        pytest.skip("no private denylist in this checkout")
    hits = [
        f"{p.relative_to(ROOT).as_posix()}:{line}: {w}"
        for p in mod.tracked_files()
        for line, w in mod.scan_file(p, hashes)
    ]
    assert hits == [], "private terms found (AGENTS.md rule 9):\n" + "\n".join(hits)


def test_scans_paths_and_previously_excluded_text(tmp_path):
    mod = _load()
    hashes = {mod._hash("privatetestword")}
    for name in ("LICENSE", "uv.lock", "package-lock.json", "example.pdf"):
        path = tmp_path / name
        path.write_text("privatetestword", encoding="utf-8")
        assert mod.scan_file(path, hashes)
    assert mod.scan_bytes("privatetestword/note.txt", b"ordinary text", hashes)


def test_scans_office_text_and_metadata():
    mod = _load()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", "<t>privatetestword</t>")
        archive.writestr("docProps/core.xml", "<creator>privatetestword</creator>")
    assert len(mod.scan_bytes("sample.docx", buf.getvalue(), {mod._hash("privatetestword")})) == 2


def test_no_denylist_checks_nothing(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setenv(mod.ENV_FILE, str(tmp_path / "absent.sha256"))
    assert mod.load_hashes() is None
    assert mod.main([]) == 0
    assert "nothing to check" in capsys.readouterr().err
    assert mod.main(["--staged"]) == 0
    assert capsys.readouterr().err == ""  # the hooks stay silent for a clone without a list


def test_malformed_denylist_fails_closed(tmp_path, monkeypatch):
    mod = _load()
    path = tmp_path / "bad.sha256"
    monkeypatch.setenv(mod.ENV_FILE, str(path))
    for content in ("", "not-a-hash\n"):
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="denylist"):
            mod.load_hashes()


def test_add_writes_to_the_local_list(tmp_path, monkeypatch):
    mod = _load()
    path = tmp_path / "new.sha256"
    monkeypatch.setenv(mod.ENV_FILE, str(path))
    assert mod.main(["--add", "PrivateTestWord", "privatetestword", "another"]) == 0
    assert mod.load_hashes() == {mod._hash("privatetestword"), mod._hash("another")}
    assert mod.main(["--add", "another"]) == 0
    assert len(path.read_text(encoding="utf-8").split()) == 2


@pytest.mark.parametrize(
    "term", ["my-host", "box.example.net", "Two Words", "alex@example.com", "ключ", ""]
)
def test_add_refuses_a_term_the_scanner_can_never_match(tmp_path, monkeypatch, capsys, term):
    """The scanner matches single words of letters and digits; anything else would be stored
    and then never found, so `--add` refuses it and names the parts to add instead."""
    mod = _load()
    path = tmp_path / "new.sha256"
    monkeypatch.setenv(mod.ENV_FILE, str(path))
    assert mod.main(["--add", "privatetestword", term]) == 2
    assert not path.exists()  # nothing is added when any term is refused
    err = capsys.readouterr().err
    assert "one word" in err
    if term == "my-host":
        assert "my host" in err


def test_accented_words_are_found_with_or_without_their_accents(tmp_path, monkeypatch):
    mod = _load()
    path = tmp_path / "new.sha256"
    monkeypatch.setenv(mod.ENV_FILE, str(path))
    assert mod.main(["--add", "Renée"]) == 0
    hashes = mod.load_hashes()
    assert mod.scan_text("written by renée", hashes) == [(1, "renee")]
    assert mod.scan_text("written by RENEE", hashes) == [(1, "renee")]
    assert mod.scan_text("written by René", hashes) == []
    assert mod.scan_text("prénom: renée-x", hashes) == [(1, "renee")]


def test_ci_logs_name_the_place_not_the_word(tmp_path, monkeypatch, capsys):
    mod, _ = _denylist(tmp_path, monkeypatch, "privatetestword")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    note = tmp_path / "note.txt"
    note.write_text("has privatetestword", encoding="utf-8")
    monkeypatch.setenv("CI", "true")
    assert mod.main([str(note)]) == 1
    out = capsys.readouterr().out
    assert "note.txt:1" in out
    assert "privatetestword" not in out
    monkeypatch.delenv("CI")
    assert mod.main([str(note)]) == 1
    assert "privatetestword" in capsys.readouterr().out


def test_staged_scan_reads_index_not_working_copy(tmp_path, monkeypatch, capsys):
    mod = _load()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "name with spaces.txt"
    path.write_text("privatetestword", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", path.name], check=True)
    path.write_text("clean working copy", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "load_hashes", lambda: {mod._hash("privatetestword")})
    assert mod.main(["--staged"]) == 1
    assert path.name in capsys.readouterr().out


def test_commit_message_check_allows_author_name_but_checks_email(tmp_path, monkeypatch):
    mod = _load()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in (("user.name", "privatetestword"), ("user.email", "dev@example.invalid")):
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    message = tmp_path / "message.txt"
    message.write_text("A product change", encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "load_hashes", lambda: {mod._hash("privatetestword")})
    assert mod.main(["--message", str(message)]) == 0
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "privatetestword@example.invalid"],
        check=True,
    )
    assert mod.main(["--message", str(message)]) == 1
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "config",
            "user.email",
            "123+privatetestword@users.noreply.github.com",
        ],
        check=True,
    )
    assert mod.main(["--message", str(message)]) == 0
