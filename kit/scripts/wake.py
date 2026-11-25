#!/usr/bin/env python3
"""wake.py - one-command agent orientation for ANY git repository (stdlib only).

Prints a bounded (~1-2k token) WAKE block so an agent or human resuming work on
this machine knows where they are and what changed, without a dozen exploratory
commands:

  identity   repo name, branch, remote, checkout path, host, cloud-sync check
  sync       fetch (time-boxed), ahead/behind upstream, then wake_sync.sync(): advance
             HEAD without clobbering cloud-synced work (see scripts/wake_sync.py)
  tree       dirty-file summary (real changes first; line-ending-only noise counted)
  history    last N commit subjects
  env        python in use (.venv preferred), node if a package.json exists
  guidance   always-on instruction files, .github/instructions/*, notes index
  journals   most recently touched docs/progress/*.md
  state      SESSION_STATE.md (full, capped)
  local      output of an optional .wake.local.py (repo-specific extras)

Usage (from anywhere; the script finds its own repo root):
  python scripts/wake.py                normal run
  python scripts/wake.py --no-pull      never modify the working tree
  python scripts/wake.py --commits 15
  python scripts/wake.py --hook         JSON for a Copilot/Claude SessionStart hook
                                        ({hookSpecificOutput:{additionalContext}})
  python scripts/wake.py --fetch-timeout 10

Portable: no repo-specific names; Windows/macOS/Linux; Python 3.8+.
Pair with hibernate.py (saves SESSION_STATE.md + docs/progress and pushes).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import wake_sync  # noqa: E402

CLOUD_MARKERS = ("onedrive", "library/cloudstorage", "dropbox", "google drive", "icloud")


def git(*args: str, timeout: float | None = 30) -> str | None:
    try:
        r = subprocess.run(["git", "--no-pager", *args], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def trunc(s: str, n: int = 100) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def kb_tok(p: Path) -> str:
    n = p.stat().st_size
    return f"{p.relative_to(ROOT).as_posix()} ({n / 1024:.1f} KB ~{n / 4:,.0f} tok)"


def find_python() -> tuple[str | None, str]:
    hp = wake_sync.hook_python(ROOT)
    candidates = ([hp] if hp else []) + [str(ROOT / c) for c in (".venv/Scripts/python.exe", ".venv/bin/python",
                                                                  "venv/Scripts/python.exe", "venv/bin/python")]
    for c in candidates:
        p = Path(c)
        if p.exists():
            try:
                v = subprocess.run([str(p), "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
            except Exception:
                v = "?"
            return str(p), f"{v}  ({p})"
    return None, f"Python {platform.python_version()}  ({sys.executable}; NO repo venv found)"


def build(args: argparse.Namespace) -> list[str]:
    L: list[str] = []
    name = ROOT.name
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    remote = git("remote", "get-url", "origin") or "(none)"
    low = ROOT.as_posix().lower()
    cloud = any(m in low for m in CLOUD_MARKERS)
    try:
        cloud = cloud or ROOT.is_symlink() or (os.name == "nt" and bool(os.stat(ROOT).st_file_attributes & 0x400))  # type: ignore[attr-defined]
    except Exception:
        pass
    L.append(f"===== WAKE: {name}  ({datetime.now():%Y-%m-%d %H:%M})  =====")
    L.append(f"Checkout : {ROOT}")
    L.append(f"Host     : {socket.gethostname()}  os={platform.system()}  user={os.environ.get('USERNAME') or os.environ.get('USER') or '?'}"
             f"  cloud-synced: {'YES (placeholders possible; reads may hydrate slowly)' if cloud else 'no'}")
    L.append(f"Branch   : {branch}   remote: {remote}")

    # ---- sync (wake_sync: safe for a working tree shared by OneDrive - see its docstring)
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not args.no_pull:
        refreshed = wake_sync.refresh_noise(ROOT, wake_sync.classify(ROOT).noise)
    if upstream:
        fetched = git("fetch", "--prune", timeout=args.fetch_timeout) is not None
        counts = git("rev-list", "--left-right", "--count", f"HEAD...{upstream}") or "0 0"
        ahead, behind = (int(x) for x in counts.split()[:2])
        line = f"Sync     : {'fetched' if fetched else f'FETCH SKIPPED/TIMED OUT ({args.fetch_timeout}s) - counts may be stale'}; ahead {ahead} / behind {behind} vs {upstream}"
        sync_details: list[str] = []
        if behind and not args.no_pull:
            if ahead == 0:
                res = wake_sync.sync(ROOT, upstream)
                line += f" -> {res.summary}"
                sync_details = res.details
            else:
                line += " -> DIVERGED; not pulled (rebase/merge manually)"
        L.append(line)
        L.extend(f"           {d}" for d in sync_details)
    else:
        L.append(f"Sync     : no upstream configured for {branch}")

    # ---- tree
    tree = wake_sync.classify(ROOT)
    dirty_raw = [l for l in (git("status", "--porcelain") or "").splitlines() if l.strip()]
    if not dirty_raw:
        L.append("Tree     : clean" + (f" (refreshed {refreshed} line-ending-only file(s))" if not args.no_pull and refreshed else ""))
    else:
        kinds: dict[str, int] = {}
        for l in dirty_raw:
            k = l[:2].strip() or "?"
            kinds[k] = kinds.get(k, 0) + 1
        L.append(f"Tree     : {len(dirty_raw)} entries ({', '.join(f'{k}={v}' for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))})"
                 + (f"; {len(tree.noise)} are line-ending-only (CRLF from Windows via cloud sync; content identical)" if tree.noise else ""))
        real = set(tree.real) | set(tree.untracked)
        shown = [l for l in dirty_raw if l[3:].strip('"') in real] or dirty_raw
        L.extend(f"           {l}" for l in shown[:8])
        if len(shown) > 8:
            L.append(f"           ... {len(shown) - 8} more")
    if cloud:
        new_dups, old_dups = wake_sync.conflict_copies(ROOT)
        if new_dups:
            L.append(f"CloudDup : {len(new_dups)} untracked duplicate/conflict copies - compare with the original, keep one: "
                     + ", ".join(new_dups[:5]) + (" ..." if len(new_dups) > 5 else ""))
        if old_dups:
            L.append(f"CloudDup : {len(old_dups)} committed '(1)'-style duplicates (pre-existing): " + ", ".join(old_dups[:3])
                     + (" ..." if len(old_dups) > 3 else ""))

    # ---- shared data folder + other machines (only when .wake.hooks.json configures them)
    data = wake_sync.data_dir(ROOT)
    if data != ROOT:
        L.append(f"Data     : {data}  (live data + SESSION_STATE.md; synced by the cloud folder, not git)")
    others = wake_sync.other_machines(ROOT)
    if others:
        L.append("Machines :")
        L.extend(f"           {o}" for o in others)

    # ---- history
    L.append("Recent   :")
    for l in (git("log", "-n", str(args.commits), "--format=%h %ad %s", "--date=short") or "").splitlines():
        L.append("           " + trunc(l))

    # ---- env
    _, pylabel = find_python()
    L.append(f"Python   : {pylabel}")
    if (ROOT / "package.json").exists() and shutil.which("node"):
        try:
            L.append("Node     : " + subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout.strip())
        except Exception:
            pass
    if not shutil.which("pwsh") and list(ROOT.glob("*.ps1")):
        L.append("PowerShell: pwsh not installed (optional). Every *.ps1 is a wrapper over a scripts/*.py - see docs/RUN_WITHOUT_POWERSHELL.md for the pwsh-free commands")

    # ---- guidance inventory
    always = [kb_tok(ROOT / f) for f in ("AGENTS.md", ".github/copilot-instructions.md", "CLAUDE.md") if (ROOT / f).exists()]
    if always:
        L.append("Always-on: " + "; ".join(always))
    ins = sorted((ROOT / ".github/instructions").glob("*.instructions.md")) if (ROOT / ".github/instructions").is_dir() else []
    if ins:
        L.append(f"Instructions ({len(ins)}, auto-attach by applyTo): " + ", ".join(p.name[: -len('.instructions.md')] for p in ins))
    for n in ("docs/agent-memory", "docs/notes", "notes"):
        d = ROOT / n
        if d.is_dir():
            cnt = len(list(d.glob("*.md")))
            idx = "index: README.md" if (d / "README.md").exists() else "NO INDEX (run scripts/notes_index.py)"
            L.append(f"Notes    : {n} - {cnt} files, {idx}")
    pj = ROOT / "docs/progress"
    if pj.is_dir():
        recent = sorted(pj.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        L.append("Journals : " + ", ".join(f"{p.name} ({datetime.fromtimestamp(p.stat().st_mtime):%m-%d})" for p in recent))

    # ---- session state
    st = wake_sync.state_file(ROOT)
    if st.exists():
        lines = st.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        where = "" if st.parent == ROOT else f" - shared: {st}"
        L.append(f"----- SESSION_STATE.md ({len(lines)} lines{where}) -----")
        L.extend(lines[: args.state_max_lines])
        if len(lines) > args.state_max_lines:
            L.append(f"... truncated ({len(lines) - args.state_max_lines} more lines) - keep this file short")
    else:
        L.append(f"SESSION_STATE.md: none at {st} (write one before hibernate.py)")

    # ---- repo-specific hook
    local = ROOT / ".wake.local.py"
    if local.exists():
        L.append("----- .wake.local.py -----")
        try:
            r = subprocess.run([sys.executable, str(local)], cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            extra = (r.stdout + r.stderr).splitlines()
            L.extend(extra[:40])
            if len(extra) > 40:
                L.append(f"... truncated ({len(extra) - 40} more lines)")
        except Exception as e:  # noqa: BLE001
            L.append(f"local hook error: {e}")
    L.append("===== END WAKE =====")
    wake_sync.write_machine_status(ROOT, "wake")
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="one-command agent orientation")
    ap.add_argument("--no-pull", action="store_true", help="never modify the working tree")
    ap.add_argument("--commits", type=int, default=8)
    ap.add_argument("--fetch-timeout", type=float, default=25)
    ap.add_argument("--state-max-lines", type=int, default=120)
    ap.add_argument("--hook", action="store_true", help="emit SessionStart hook JSON instead of text")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    text = "\n".join(build(args))
    if args.hook:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    else:
        print(text)
        emit = ROOT / "scripts/monitor_emit.py"
        if emit.exists():  # optional, best effort
            try:
                subprocess.run([sys.executable, str(emit), f"wake: {ROOT.name}", "info", ROOT.name],
                               cwd=ROOT, capture_output=True, timeout=10)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
