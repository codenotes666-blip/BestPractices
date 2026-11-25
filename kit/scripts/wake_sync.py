#!/usr/bin/env python3
"""wake_sync.py - git sync that is safe for a working tree shared by OneDrive (stdlib only).

Used by wake.py (session start) and hibernate.py (session end). Portable: no
repo-specific names; Windows/macOS/Linux; Python 3.8+.

The situation it handles: the WORKING FILES are synced between machines by a
cloud folder (OneDrive), but each machine's git history only moves when that
machine fetches. So on arrival a machine typically sees:

  * line-ending noise   - Windows wrote CRLF, git here compares LF; the content
                          is identical (same blob hash). Refreshed silently.
  * stale files         - OneDrive delivered an OLDER committed version (or a
                          half-synced state). Content equals some version in
                          this repo's history -> safe to restore from upstream.
  * unknown content     - not in history: probably the other machine's
                          UNCOMMITTED work arriving via OneDrive. Never touched.
  * SQLite databases    - restored from upstream only when upstream contains
                          every local row (or a newer version of it).

sync() moves HEAD to upstream with a MIXED reset (the working tree is not
rewritten), then restores only provably stale files. Anything it overwrites is
first copied to ~/.wake-backups/<repo>/<timestamp>/ (outside the cloud folder).

Optional per-repo hooks in .wake.hooks.json stop/restart processes that hold
files open (e.g. a local web server writing the DB) around the sync:

  {"python": ["~/.venvs/x/bin/python", "~/.venvs/x/Scripts/python.exe"],
   "quiesce": [{"run": ["{python}", "-m", "pkg", "web", "stop"],
                "resume": ["{python}", "-m", "pkg", "web", "start"],
                "resume_if": "stopped"}]}

"{python}" = first existing interpreter in "python", else the running one.
"resume" runs only if "run"'s output matches the "resume_if" regex (if given).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
HOOKS_FILE = ".wake.hooks.json"
BACKUP_HOME = Path.home() / ".wake-backups"
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "objects", ".mypy_cache"}
# "name (1).ext" (OneDrive/browser duplicate) or "name-HOST.ext" / "name-HOST-2.ext" (OneDrive conflict)
_DUP_RE = re.compile(r" \(\d+\)(\.[^./\\]+)?$")


# --------------------------------------------------------------------------- git
class Git:
    def __init__(self, root: Path):
        self.root = Path(root)

    def run(self, *args: str, timeout: float | None = 120, input: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "--no-pager", *args], cwd=self.root, capture_output=True,
                              timeout=timeout, input=input)

    def out(self, *args: str, timeout: float | None = 120) -> str | None:
        try:
            r = self.run(*args, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.decode("utf-8", "replace").rstrip("\n") if r.returncode == 0 else None

    def blob(self, rev: str, path: str) -> bytes | None:
        r = self.run("cat-file", "blob", f"{rev}:{path}")
        return r.stdout if r.returncode == 0 else None

    def zlist(self, *args: str) -> list[str]:
        r = self.run(*args)
        return [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p] if r.returncode == 0 else []


def normalize(b: bytes) -> bytes:
    return b if b"\0" in b[:8000] else b.replace(b"\r\n", b"\n")


def is_db(path: str) -> bool:
    return path.lower().endswith(DB_SUFFIXES)


def status_entries(g: Git) -> list[tuple[str, str]]:
    """[(XY, path)] from `git status --porcelain -z` (rename sources skipped)."""
    raw = g.run("status", "--porcelain=v1", "-z").stdout.decode("utf-8", "replace").split("\0")
    out, i = [], 0
    while i < len(raw):
        e = raw[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        if "R" in xy or "C" in xy:
            i += 1  # skip the rename/copy source
        out.append((xy, path))
    return out


@dataclass
class Tree:
    noise: list[str] = field(default_factory=list)      # identical content, only EOL/stat differ
    real: list[str] = field(default_factory=list)       # tracked, content really differs
    untracked: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.noise) + len(self.real) + len(self.untracked)


def classify(root: Path) -> Tree:
    g, t = Git(root), Tree()
    for xy, path in status_entries(g):
        if xy == "??":
            t.untracked.append(path)
        elif xy[0] == " " and xy[1] == "M" and not is_db(path):
            p = root / path
            idx = g.blob("", path)  # ":path" = index version
            try:
                same = idx is not None and normalize(p.read_bytes()) == normalize(idx)
            except OSError:
                same = False
            (t.noise if same else t.real).append(path)
        else:
            t.real.append(path)
    return t


def refresh_noise(root: Path, noise: list[str]) -> int:
    """Clear EOL-only entries whose hash (with .gitattributes applied) equals the index blob.
    `git add` then stages nothing new - it only refreshes the index stat data."""
    if not noise:
        return 0
    g = Git(root)
    hashed = g.run("hash-object", "--stdin-paths", input="\n".join(noise).encode()).stdout.decode().split()
    idx = {}
    for rec in g.zlist("ls-files", "-s", "-z", "--", *noise):
        meta, _, path = rec.partition("\t")
        idx[path] = meta.split()[1]
    same = [p for p, h in zip(noise, hashed) if idx.get(p) == h]
    for i in range(0, len(same), 100):
        g.run("add", "--", *same[i:i + 100])
    return len(same)


# --------------------------------------------------------------------------- sqlite
def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def db_superseded(local: Path, upstream: bytes) -> tuple[bool, str]:
    """True if `upstream` (db bytes) holds every row of `local`, or a same-key row whose
    updated_at is >= local's. Returns (ok, human detail)."""
    tmp = Path(tempfile.mkdtemp(prefix="wake_db_"))
    try:
        up = tmp / "upstream.db"
        up.write_bytes(upstream)
        L, U = _open_ro(local), _open_ro(up)
        utabs = {r[0] for r in U.execute("select name from sqlite_master where type='table'")}
        problems: list[str] = []
        for (tab,) in L.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
            q = tab.replace('"', '""')
            if tab not in utabs:
                if L.execute(f'select count(*) from "{q}"').fetchone()[0]:
                    problems.append(f"{tab}: table only local")
                continue
            linfo = L.execute(f'pragma table_info("{q}")').fetchall()
            ucols = {r[1] for r in U.execute(f'pragma table_info("{q}")')}
            cols = [r[1] for r in linfo if r[1] in ucols]
            if not cols:
                continue
            sel = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            urows = set(U.execute(f'select {sel} from "{q}"'))
            extra = [r for r in L.execute(f'select {sel} from "{q}"') if r not in urows]
            if not extra:
                continue
            pk = [r[1] for r in sorted(linfo, key=lambda r: r[5]) if r[5] and r[1] in cols]
            ui = {c: i for i, c in enumerate(cols)}
            new = conflict = 0
            if pk:
                ubykey = {tuple(r[ui[c]] for c in pk): r for r in urows}
                for r in extra:
                    u = ubykey.get(tuple(r[ui[c]] for c in pk))
                    if u is None:
                        new += 1
                    elif "updated_at" in ui and (u[ui["updated_at"]] or "") >= (r[ui["updated_at"]] or ""):
                        continue  # upstream holds the same or a newer version of this row
                    else:
                        conflict += 1
            else:
                new = len(extra)
            if new or conflict:
                problems.append(f"{tab}: {new} local-only, {conflict} changed" if conflict else f"{tab}: {new} local-only")
        L.close()
        U.close()
        return (not problems, "; ".join(problems[:6]) + (" ..." if len(problems) > 6 else ""))
    except sqlite3.Error as e:
        return False, f"could not compare ({e})"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def db_in_use(path: Path) -> bool:
    wal = path.with_name(path.name + "-wal")
    return wal.exists() and wal.stat().st_size > 0


# --------------------------------------------------------------------------- hooks
def _hooks(root: Path) -> dict:
    f = root / HOOKS_FILE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def hook_python(root: Path) -> str | None:
    for c in _hooks(root).get("python", []):
        p = Path(os.path.expanduser(c))
        if p.exists():
            return str(p)
    return None


def _cmd(root: Path, cmd: list[str]) -> list[str]:
    py = hook_python(root) or sys.executable
    return [py if a == "{python}" else os.path.expanduser(a) for a in cmd]


def quiesce(root: Path) -> tuple[list[list[str]], list[str]]:
    """Run the repo's quiesce hooks. Returns (resume commands to run later, log lines)."""
    resume, log = [], []
    for h in _hooks(root).get("quiesce", []):
        try:
            r = subprocess.run(_cmd(root, h["run"]), cwd=root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            text = (r.stdout + r.stderr).strip()
        except Exception as e:  # noqa: BLE001
            log.append(f"quiesce hook failed: {e}")
            continue
        log.append(f"quiesce: {text.splitlines()[0] if text else 'ok'}")
        if h.get("resume") and r.returncode == 0 and re.search(h.get("resume_if", ""), text, re.M):
            resume.append(h["resume"])
    return resume, log


def resume(root: Path, cmds: list[list[str]]) -> list[str]:
    log = []
    for c in cmds:
        try:
            r = subprocess.run(_cmd(root, c), cwd=root, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
            text = (r.stdout + r.stderr).strip()
            log.append(f"resume : {text.splitlines()[0] if text else 'ok'}")
        except Exception as e:  # noqa: BLE001
            log.append(f"resume hook failed: {e}")
    return log


# --------------------------------------------------------------------------- shared data folder
# Optional .wake.hooks.json keys for a repo whose live data/state lives in a cloud folder
# OUTSIDE the checkout (synced instantly, no commit needed):
#   "data_dir_cmd": command printing the data folder ("{data}" in the keys below)
#   "state_file":   e.g. "{data}/SESSION_STATE.md"  (default: <checkout>/SESSION_STATE.md)
#   "machines_dir": e.g. "{data}/.machines"         (per-machine status notes; default: off)
_data_cache: dict[Path, Path] = {}


def data_dir(root: Path) -> Path:
    root = Path(root)
    if root not in _data_cache:
        d = root
        cmd = _hooks(root).get("data_dir_cmd")
        if cmd:
            try:
                r = subprocess.run(_cmd(root, cmd), cwd=root, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30)
                out = r.stdout.strip().splitlines()
                if r.returncode == 0 and out and Path(out[-1]).is_dir():
                    d = Path(out[-1])
            except Exception:  # noqa: BLE001
                pass
        _data_cache[root] = d
    return _data_cache[root]


def _expand(root: Path, template: str) -> Path:
    return Path(os.path.expanduser(template.replace("{data}", str(data_dir(root))).replace("{root}", str(root))))


def state_file(root: Path) -> Path:
    t = _hooks(root).get("state_file")
    return _expand(root, t) if t else Path(root) / "SESSION_STATE.md"


def machines_dir(root: Path) -> Path | None:
    t = _hooks(root).get("machines_dir")
    return _expand(root, t) if t else None


def code_status(root: Path) -> dict:
    """What this checkout holds that the other machines cannot see yet."""
    g = Git(root)
    t = classify(root)
    upstream = g.out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = behind = 0
    if upstream:
        counts = (g.out("rev-list", "--left-right", "--count", f"HEAD...{upstream}") or "0 0").split()
        ahead, behind = int(counts[0]), int(counts[1])
    return {"branch": g.out("rev-parse", "--abbrev-ref", "HEAD"), "head": g.out("rev-parse", "--short", "HEAD"),
            "uncommitted": sorted(set(t.real) | set(t.untracked)), "unpushed": ahead, "behind": behind}


def write_machine_status(root: Path, event: str) -> Path | None:
    d = machines_dir(root)
    if d is None:
        return None
    host = socket.gethostname().split(".")[0]
    rec = {"host": host, "os": sys.platform, "checkout": str(Path(root).resolve()), "event": event,
           "at": datetime.now().astimezone().isoformat(timespec="seconds"), **code_status(root)}
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{host}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    except OSError:
        return None
    return d / f"{host}.json"


def other_machines(root: Path) -> list[str]:
    """One line per OTHER machine from its last status note; flags work not on GitHub."""
    d = machines_dir(root)
    if d is None or not d.is_dir():
        return []
    me = socket.gethostname().split(".")[0]
    lines = []
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("host") == me:
            continue
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(r["at"])
            ago = f"{age.days}d" if age.days else f"{age.seconds // 3600}h{age.seconds % 3600 // 60:02d}m"
        except (KeyError, ValueError):
            ago = "?"
        unc, unp = r.get("uncommitted") or [], r.get("unpushed") or 0
        flag = ""
        if unc or unp:
            flag = (f"  <-- NOT ON GITHUB: {len(unc)} uncommitted file(s)"
                    + (f" ({', '.join(unc[:4])}{' ...' if len(unc) > 4 else ''})" if unc else "")
                    + (f", {unp} unpushed commit(s)" if unp else "")
                    + " - finish/commit there (or ask the owner) before editing the same files here")
        lines.append(f"{r.get('host')} ({r.get('os')}) {r.get('event')} {ago} ago, {r.get('branch')}@{r.get('head')}"
                     f", checkout {r.get('checkout')}{flag}")
    return lines


# --------------------------------------------------------------------------- conflict copies
def conflict_copies(root: Path) -> tuple[list[str], list[str]]:
    """(untracked, tracked) files named like OneDrive duplicates/conflict copies."""
    hosts = {h for h in {socket.gethostname().split(".")[0], *_hooks(root).get("hosts", [])} if h}
    host_re = re.compile(r"-(" + "|".join(map(re.escape, hosts)) + r"|DESKTOP-[A-Z0-9]+|LAPTOP-[A-Z0-9]+)(-\d+)?(\.[^.]+)?$",
                         re.I) if hosts else re.compile(r"-(DESKTOP|LAPTOP)-[A-Z0-9]+(-\d+)?(\.[^.]+)?$")
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for n in filenames + [d for d in dirnames if _DUP_RE.search(d) or host_re.search(d)]:
            if _DUP_RE.search(n) or host_re.search(n):
                hits.append(Path(dirpath, n).relative_to(root).as_posix())
    if not hits:
        return [], []
    tracked = set(Git(root).zlist("ls-files", "-z", "--", *hits))
    return [h for h in hits if h not in tracked], [h for h in hits if h in tracked]


# --------------------------------------------------------------------------- sync
@dataclass
class SyncResult:
    summary: str
    details: list[str] = field(default_factory=list)


def _is_stale(g: Git, root: Path, path: str, old: str, new: str) -> bool:
    try:
        wt = normalize((root / path).read_bytes())
    except OSError:
        return False
    revs = (g.out("rev-list", f"{old}..{new}", "--", path) or "").split() + [old]
    for rev in revs:
        b = g.blob(rev, path)
        if b is not None and normalize(b) == wt:
            return True
    return False


def _backup(root: Path, paths: list[str], stamp: str) -> Path | None:
    if not paths:
        return None
    dest = BACKUP_HOME / root.name / stamp
    for p in paths:
        src = root / p
        if src.exists():
            (dest / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / p)
    return dest


def sync(root: Path, upstream: str) -> SyncResult:
    """Fast-forward HEAD to `upstream` without clobbering unknown work. Caller guarantees
    HEAD is an ancestor of upstream (ahead == 0)."""
    root = Path(root)
    g = Git(root)
    old, new = g.out("rev-parse", "HEAD"), g.out("rev-parse", upstream)
    if not old or not new:
        return SyncResult("sync FAILED (cannot resolve HEAD/upstream)")
    if g.out("merge-base", "--is-ancestor", old, new) is None:
        return SyncResult("DIVERGED; not pulled (rebase/merge manually)")
    resume_cmds, log = quiesce(root)
    details: list[str] = list(log)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        if g.run("reset", "-q", "--mixed", new).returncode != 0:
            return SyncResult("sync FAILED (git reset --mixed)", details)
        noise = set(classify(root).noise)
        refresh_noise(root, sorted(noise))
        entries = dict((p, xy) for xy, p in status_entries(g))
        added = set(g.zlist("diff", "--name-only", "-z", "--diff-filter=A", old, new))
        removed = set(g.zlist("diff", "--name-only", "-z", "--diff-filter=D", old, new))
        restore, delete, kept = [], [], []
        for path, xy in entries.items():
            if xy == " M":
                if is_db(path):
                    if db_in_use(root / path):
                        kept.append(f"{path} (open: -wal present)")
                        continue
                    up = g.blob(new, path)
                    ok, why = db_superseded(root / path, up) if up is not None else (False, "missing upstream")
                    (restore.append(path) if ok else kept.append(f"{path} ({why})"))
                elif path in noise:
                    continue
                elif _is_stale(g, root, path, old, new):
                    restore.append(path)
                else:
                    kept.append(path)
            elif xy == " D" and path in added:
                restore.append(path)  # added upstream; OneDrive has not delivered it yet
            elif xy == "??" and path in removed and _is_stale(g, root, path, old, new):
                delete.append(path)   # deleted upstream; we still hold an old committed copy
        bdir = _backup(root, restore + delete, stamp)
        for i in range(0, len(restore), 100):
            g.run("checkout", "--", *restore[i:i + 100])
        for p in delete:
            try:
                (root / p).unlink()
            except OSError:
                kept.append(f"{p} (could not delete)")
        refresh_noise(root, classify(root).noise)
        short = lambda r: r[:7]  # noqa: E731
        summary = f"advanced {short(old)}..{short(new)}"
        if restore or delete:
            summary += f"; restored {len(restore)} stale, removed {len(delete)} stale file(s)"
            details.append(f"stale files from an older commit, now replaced: {', '.join((restore + delete)[:6])}"
                           + (" ..." if len(restore + delete) > 6 else ""))
            details.append(f"backup of what was replaced: {bdir}")
        if kept:
            summary += f"; KEPT {len(kept)} file(s) whose content is not in git history"
            details.append("kept (likely the other machine's uncommitted work via OneDrive, or local edits) - review:")
            details.extend(f"  {k}" for k in kept[:10])
            if len(kept) > 10:
                details.append(f"  ... {len(kept) - 10} more")
        return SyncResult(summary, details)
    finally:
        details.extend(resume(root, resume_cmds))
