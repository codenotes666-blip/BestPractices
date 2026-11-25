"""scripts/wake_sync.py: git sync for a working tree shared between machines by OneDrive."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kit" / "scripts"))
import wake_sync  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """(other, here): two clones of one bare remote; `other` plays the machine that pushed."""
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(wake_sync, "BACKUP_HOME", tmp_path / "backups")
    git(tmp_path, "init", "-q", "--bare", "-b", "main", "remote.git")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), "other")
    git(other, "checkout", "-q", "-b", "main")
    (other / ".gitattributes").write_text("* text=auto eol=lf\n*.db binary\n")
    return tmp_path, other


def commit_push(repo, files, msg):
    for name, data in files.items():
        p = repo / name
        if isinstance(data, bytes):
            p.write_bytes(data)
        else:
            p.write_text(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    git(repo, "push", "-q", "origin", "main")


def make_db(path, rows):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("create table s (id integer primary key, name text, tags text, updated_at text)")
    con.executemany("insert into s values (?,?,?,?)", rows)
    con.commit()
    con.close()


def clone_here(tmp_path):
    here = tmp_path / "here"
    git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), "here")
    return here


def test_crlf_copy_is_noise_and_refresh_clears_it(repos):
    tmp_path, other = repos
    commit_push(other, {"a.py": "x = 1\ny = 2\n"}, "v1")
    here = clone_here(tmp_path)
    (here / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")  # what OneDrive delivers from Windows
    t = wake_sync.classify(here)
    assert t.noise == ["a.py"] and not t.real
    assert wake_sync.refresh_noise(here, t.noise) == 1
    assert git(here, "status", "--porcelain") == ""


def test_sync_restores_stale_keeps_unknown_and_handles_db(repos):
    tmp_path, other = repos
    make_db(other / "app.db", [(1, "a", None, "2026-01-01")])
    commit_push(other, {"code.py": "v1\n", "notes.md": "n1\n", "old.txt": "gone soon\n"}, "c1")
    here = clone_here(tmp_path)
    commit_push(other, {"code.py": "v2\n"}, "c2")
    make_db(other / "app.db", [(1, "a", '["tag"]', "2026-01-01"), (2, "b", None, "2026-01-02")])
    (other / "old.txt").unlink()
    commit_push(other, {"code.py": "v3\n", "notes.md": "n2\n", "new.py": "added\n"}, "c3")
    git(here, "fetch", "-q")

    # OneDrive delivered a half-synced tree: code.py at the intermediate commit (CRLF),
    # notes.md with uncommitted edits from the other machine, old DB, new.py not yet.
    (here / "code.py").write_bytes(b"v2\r\n")
    (here / "notes.md").write_text("n2 plus unsaved WIP\n")

    res = wake_sync.sync(here, "origin/main")

    assert git(here, "rev-parse", "HEAD") == git(here, "rev-parse", "origin/main")
    assert (here / "code.py").read_text() == "v3\n"
    assert (here / "new.py").read_text() == "added\n"
    assert not (here / "old.txt").exists()
    assert (here / "notes.md").read_text() == "n2 plus unsaved WIP\n"  # never clobbered
    con = sqlite3.connect(here / "app.db")
    assert con.execute("select count(*) from s").fetchone()[0] == 2  # superseded DB restored
    con.close()
    assert git(here, "status", "--porcelain") == "M notes.md"
    assert "KEPT 1" in res.summary
    backup = next((tmp_path / "backups" / "here").iterdir())
    assert (backup / "code.py").read_bytes() == b"v2\r\n"


def test_sync_keeps_db_with_local_only_rows(repos):
    tmp_path, other = repos
    make_db(other / "app.db", [(1, "a", None, "2026-01-01")])
    commit_push(other, {"x.txt": "1\n"}, "c1")
    here = clone_here(tmp_path)
    make_db(other / "app.db", [(1, "a", None, "2026-01-01"), (2, "b", None, "2026-01-02")])
    commit_push(other, {"x.txt": "2\n"}, "c2")
    git(here, "fetch", "-q")
    make_db(here / "app.db", [(1, "a", None, "2026-01-01"), (3, "local", None, "2026-01-03")])

    res = wake_sync.sync(here, "origin/main")

    con = sqlite3.connect(here / "app.db")
    assert [r[0] for r in con.execute("select id from s")] == [1, 3]
    con.close()
    assert "s: 1 local-only" in "\n".join(res.details)


def test_other_machine_with_unpushed_work_is_flagged(repos, monkeypatch):
    tmp_path, other = repos
    commit_push(other, {"a.py": "1\n"}, "c1")
    here = clone_here(tmp_path)
    shared = tmp_path / "onedrive"
    (here / ".wake.hooks.json").write_text('{"machines_dir": "%s"}' % (shared / ".machines").as_posix())
    monkeypatch.setattr(wake_sync.socket, "gethostname", lambda: "laptop")
    (here / "a.py").write_text("edited on the laptop\n")
    wake_sync.write_machine_status(here, "hibernate")

    monkeypatch.setattr(wake_sync.socket, "gethostname", lambda: "studio")
    lines = wake_sync.other_machines(here)

    assert len(lines) == 1 and lines[0].startswith("laptop ")
    assert "NOT ON GITHUB: 2 uncommitted file(s)" in lines[0] and "a.py" in lines[0]
