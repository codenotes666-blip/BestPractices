#!/usr/bin/env python3
"""adopt.py - install the BestPractices kit into a repository (stdlib only).

From any machine, no GitHub login needed (this repo is public):

    curl -fsSL https://raw.githubusercontent.com/codenotes666-blip/BestPractices/main/adopt.py -o adopt.py
    python3 adopt.py /path/to/repo            # Windows: python adopt.py C:\\path\\to\\repo
    rm adopt.py

Options:
    --from <BestPractices checkout>   copy from a local checkout instead of downloading
    --force                           overwrite kit scripts that already exist (templates never)
    --user                            also install the always-on user-level pointer into VS Code
    --dry-run

Installs (overwritten only with --force):
    scripts/wake.py, wake_sync.py, hibernate.py, notes_index.py
    .github/hooks/session-start.json
Merges:
    .gitattributes                    adds `* text=auto eol=lf` (stops cross-OS CRLF noise)
Scaffolds only if missing (fill the TODOs afterwards):
    greg.md, AGENTS.md, SESSION_STATE.md
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

SOURCE_REPO = "codenotes666-blip/BestPractices"
SOURCE_REF = "main"
KIT = {  # target path -> path in this repo
    "scripts/wake.py": "kit/scripts/wake.py",
    "scripts/wake_sync.py": "kit/scripts/wake_sync.py",
    "scripts/hibernate.py": "kit/scripts/hibernate.py",
    "scripts/notes_index.py": "kit/scripts/notes_index.py",
    ".github/hooks/session-start.json": "kit/.github/hooks/session-start.json",
}
TEMPLATES = {
    "greg.md": "kit/templates/greg.md",
    "AGENTS.md": "kit/templates/AGENTS.md",
    "SESSION_STATE.md": "kit/templates/SESSION_STATE.md",
}
USER_POINTER = "kit/user/bootstrap.instructions.md"
EOL_RULE = "* text=auto eol=lf"
EOL_BLOCK = (f"# Text is LF everywhere (BestPractices): stops Windows/macOS CRLF phantom changes.\n"
             f"{EOL_RULE}\n*.bat text eol=crlf\n*.cmd text eol=crlf\n\n")


def vscode_user_prompts_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Code/User/prompts"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Code/User/prompts"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Code/User/prompts"


def loader(source: Path | None):
    def load(rel: str) -> bytes:
        if source:
            return (source / rel).read_bytes()
        url = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_REF}/{rel}"
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
            return r.read()
    return load


def owner_repo(target: Path) -> str:
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=target, capture_output=True, text=True)
    url = r.stdout.strip().rstrip("/")
    if not url:
        return "<owner>/<repo>"
    url = url[:-4] if url.endswith(".git") else url
    return url.split("github.com/")[-1].split(":")[-1]


def merge_gitattributes(text: str) -> str | None:
    """New .gitattributes text with the LF rule first (later rules, e.g. binary, override), or None."""
    if any(l.split("#")[0].split() == EOL_RULE.split() for l in text.splitlines()):
        return None
    return EOL_BLOCK + text


def adopt(target: Path, source: Path | None = None, force: bool = False, dry: bool = False,
          user: bool = False) -> list[str]:
    target = target.resolve()
    load = loader(source)
    log: list[str] = []

    def write(rel: str, data: bytes) -> None:
        log.append(f"{'would write' if dry else 'write'}  {rel}")
        if not dry:
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    for rel, src in KIT.items():
        if (target / rel).exists() and not force:
            log.append(f"keep   {rel} (exists; --force to update)")
        else:
            write(rel, load(src))

    ga = target / ".gitattributes"
    merged = merge_gitattributes(ga.read_text(encoding="utf-8") if ga.exists() else "")
    if merged is None:
        log.append("keep   .gitattributes (already has the LF rule)")
    else:
        write(".gitattributes", merged.encode("utf-8"))

    subs = {"{name}": target.name, "{owner_repo}": owner_repo(target)}
    for rel, src in TEMPLATES.items():
        if (target / rel).exists():
            log.append(f"keep   {rel} (exists; merge the template by hand if useful: {src})")
            continue
        text = load(src).decode("utf-8")
        for k, v in subs.items():
            text = text.replace(k, v)
        write(rel, text.encode("utf-8"))

    if user:
        dest = vscode_user_prompts_dir() / "bootstrap.instructions.md"
        log.append(f"{'would write' if dry else 'write'}  {dest} (user-level pointer)")
        if not dry:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(load(USER_POINTER))
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="install the BestPractices kit into a repo")
    ap.add_argument("target", nargs="?", default=".")
    ap.add_argument("--from", dest="source", help="local BestPractices checkout to copy from")
    ap.add_argument("--force", action="store_true", help="overwrite existing kit scripts")
    ap.add_argument("--user", action="store_true", help="install the user-level VS Code pointer")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    target = Path(a.target)
    if not (target / ".git").exists():
        print(f"WARNING: {target.resolve()} has no .git - is it a repo root?")
    for line in adopt(target, Path(a.source).resolve() if a.source else None, a.force, a.dry_run, a.user):
        print("  " + line)
    print("""
Next (greg_best_practices.md section 4 has the detail):
  1. Fill the TODOs in greg.md (where code/data/state/env/secrets live) and AGENTS.md.
  2. Data in a cloud folder? Code finds it via one config value; see section 5.
  3. Existing CRLF noise: `git add --renormalize .` and review before committing.
  4. python scripts/wake.py   - must run clean.
  5. Show Greg the diff; commit + push only when he approves.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
