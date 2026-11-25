"""adopt.py installs the kit into a repo without clobbering what the repo already has."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adopt  # noqa: E402


def make_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/someone/proj.git"], cwd=path, check=True)
    return path


def test_fresh_repo_gets_kit_templates_and_lf_rule(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / ".gitattributes").write_text("*.db binary\n")

    adopt.adopt(repo, source=ROOT)

    for rel in adopt.KIT:
        assert (repo / rel).read_bytes() == (ROOT / adopt.KIT[rel]).read_bytes()
    ga = (repo / ".gitattributes").read_text()
    assert ga.index("* text=auto eol=lf") < ga.index("*.db binary")
    greg = (repo / "greg.md").read_text()
    assert "someone/proj" in greg and "~/code/proj" in greg and "{name}" not in greg


def test_rerun_keeps_existing_files_and_is_idempotent(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "greg.md").write_text("mine\n")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "wake.py").write_text("old\n")

    adopt.adopt(repo, source=ROOT)
    once = (repo / ".gitattributes").read_text()
    adopt.adopt(repo, source=ROOT)

    assert (repo / "greg.md").read_text() == "mine\n"
    assert (repo / "scripts" / "wake.py").read_text() == "old\n"
    assert (repo / ".gitattributes").read_text() == once

    adopt.adopt(repo, source=ROOT, force=True)
    assert (repo / "scripts" / "wake.py").read_bytes() == (ROOT / "kit/scripts/wake.py").read_bytes()
    assert (repo / "greg.md").read_text() == "mine\n"
