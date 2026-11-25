#!/usr/bin/env python3
"""hibernate.py - save cross-machine working state for ANY git repository (stdlib only).

Stamps SESSION_STATE.md with when/where it was saved, commits it together with
docs/progress (and any extra paths you name), and pushes - so the next
machine's `python scripts/wake.py` shows exactly where you left off. ONE plain
commit; no tags, no reset, no restore points.

If .wake.hooks.json puts SESSION_STATE.md in a shared cloud data folder
("state_file", see wake_sync.py), it is only stamped - the cloud folder carries
it to the other machines, no commit needed - and this machine's status note
(uncommitted / unpushed code) is written for the next machine's wake.py.

Usage:
  python scripts/hibernate.py "finished quiz-5 fixes; next: audit answer keys"
  python scripts/hibernate.py --also cards/foo.json docs/HOWTO.md -- "note"
  python scripts/hibernate.py --no-push "note"

Conventions (portable, no repo-specific names):
  - SESSION_STATE.md lives at the repo root and should stay short (<= ~80 lines):
    what we were doing / how to resume / open TODOs / uncommitted work.
  - A line starting with "**Saved:**" is (re)written automatically; if missing,
    one is inserted after the first heading.
  - Warns if SESSION_STATE.md has no changes other than the stamp (you probably
    forgot to update it) and then skips the commit if nothing else is staged.
"""
from __future__ import annotations

import argparse
import platform
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_sync  # noqa: E402

STATE = wake_sync.state_file(ROOT)
SHARED_STATE = STATE.resolve().parent != ROOT.resolve()


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "--no-pager", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"git {' '.join(args)} failed ({r.returncode})")
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="save SESSION_STATE.md + docs/progress and push")
    ap.add_argument("note", nargs="*", help="commit note (free text)")
    ap.add_argument("--also", nargs="*", default=[], metavar="PATH", help="extra paths to include")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    note = " ".join(args.note).strip() or "hibernate"

    if not STATE.exists():
        print(f"{STATE} not found. Write it first (what/resume/TODO), then hibernate.", file=sys.stderr)
        return 1

    # Did the state file change this session (beyond the stamp we are about to write)?
    if SHARED_STATE:
        changed_before = (datetime.now().timestamp() - STATE.stat().st_mtime) < 12 * 3600
    else:
        changed_before = bool(git("diff", "--numstat", "--", "SESSION_STATE.md").stdout.strip()
                              or git("diff", "--cached", "--numstat", "--", "SESSION_STATE.md").stdout.strip())

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    head = git("rev-parse", "--short", "HEAD").stdout.strip()
    stamp = f"**Saved:** {datetime.now():%Y-%m-%d %H:%M} | {socket.gethostname()} ({platform.system()}) | `{ROOT}` | {branch}@{head}"
    lines = STATE.read_text(encoding="utf-8-sig").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.startswith("**Saved:**")), None)
    if idx is not None:
        lines[idx] = stamp
    else:
        h = next((i for i, l in enumerate(lines) if l.startswith("# ")), 0)
        lines[h + 1:h + 1] = ["", stamp]
    STATE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not changed_before:
        print("WARNING: SESSION_STATE.md had no edits this session besides the stamp - did you update what/resume/TODO?")

    if SHARED_STATE:
        print(f"hibernate: stamped {STATE} (shared folder - reaches the other machines without a commit)")
    paths = [] if SHARED_STATE else ["SESSION_STATE.md"]
    if (ROOT / "docs/progress").is_dir():
        paths.append("docs/progress")
    for p in args.also:
        if (ROOT / p).exists():
            paths.append(p)
        else:
            print(f"WARNING: --also path not found: {p}")

    # A DB in the commit must not be mid-write: stop its writers (.wake.hooks.json) and
    # fold any WAL into the file first. DBs left out are only shared via the cloud folder.
    dbs = [p for p in paths if wake_sync.is_db(p)]
    for xy, p in wake_sync.status_entries(wake_sync.Git(ROOT)):
        if wake_sync.is_db(p) and p not in dbs and xy.strip() == "M":
            print(f"NOTE: {p} has uncommitted changes and is NOT in this commit - GitHub's copy stays older "
                  f"than the cloud-synced one. Add `--also {p}` to snapshot it.")
    resume_cmds: list = []
    if dbs:
        resume_cmds, log = wake_sync.quiesce(ROOT)
        for l in log:
            print(f"hibernate: {l}")
        for p in dbs:
            try:
                con = sqlite3.connect(ROOT / p)
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.close()
            except sqlite3.Error as e:
                print(f"WARNING: WAL checkpoint failed for {p}: {e}")
    try:
        rc = _commit_and_push(paths, changed_before, note, args.no_push) if paths else 0
    finally:
        for l in wake_sync.resume(ROOT, resume_cmds):
            print(f"hibernate: {l}")
    if wake_sync.write_machine_status(ROOT, "hibernate"):
        cs = wake_sync.code_status(ROOT)
        if cs["uncommitted"] or cs["unpushed"]:
            print(f"hibernate: NOT ON GITHUB yet - {len(cs['uncommitted'])} uncommitted file(s), {cs['unpushed']} unpushed "
                  "commit(s). The other machine's wake.py will flag this; commit+push when the owner approves.")
    return rc


def _commit_and_push(paths: list[str], changed_before: bool, note: str, no_push: bool) -> int:
    git("add", "--", *paths)
    staged = [l for l in git("diff", "--cached", "--name-only").stdout.splitlines() if l.strip()]
    if staged == ["SESSION_STATE.md"] and not changed_before:
        # Only the stamp moved: a commit would be pure noise. Put the file back.
        git("reset", "-q", "--", "SESSION_STATE.md")
        git("checkout", "-q", "--", "SESSION_STATE.md")
        staged = []
    if not staged:
        print("hibernate: nothing to commit (state unchanged since last save)")
        return 0

    git("commit", "-q", "-m", f"hibernate: {note}", check=True)
    print(f"hibernate: committed {len(staged)} file(s): {', '.join(staged)}")
    if not no_push:
        r = git("push")
        if r.returncode == 0:
            print("hibernate: pushed")
        else:
            print("WARNING: push failed - run 'git pull --rebase && git push' manually.\n" + r.stderr.strip())

    emit = ROOT / "scripts/monitor_emit.py"
    if emit.exists():  # optional, best effort
        try:
            subprocess.run([sys.executable, str(emit), f"hibernate: {note}", "info", ROOT.name], cwd=ROOT,
                           capture_output=True, timeout=10)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
