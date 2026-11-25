"""notes_index.py - generate a one-line-per-file README index for a folder of Markdown notes.

Generic (stdlib only). Point it at any folder of ``*.md`` notes and it writes a
``README.md`` there (or ``--out``) listing every note with its title, size, and a
one-line summary, so an agent can triage a large notes folder for ~1-2k tokens
instead of reading all of it.

    python scripts/notes_index.py docs/agent-memory
    python scripts/notes_index.py docs/agent-memory --group-prefix user- --title "Agent memory"
    python scripts/notes_index.py notes --check     # exit 1 if README.md is stale (CI / wake)

Title   = first ``# `` heading (else the filename stem).
Summary = first paragraph line that is not a heading/blockquote/list/table/code
          fence, truncated to ``--summary-chars`` (default 140).
Notes whose filename starts with a ``--group-prefix`` are listed under their own
heading (e.g. ``user-*`` owner preferences vs. repository notes).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
SKIP_RE = re.compile(r"^(\s*$|#|>|[-*+]\s|\d+\.\s|\||```|---|\*\*Saved:\*\*)")


def summarize(path: Path, max_chars: int) -> tuple[str, str]:
    title = path.stem
    summary = ""
    in_fence = False
    saw_h1 = False
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m and not saw_h1 and line.startswith("# "):
            title = m.group(1)
            saw_h1 = True
            continue
        if not summary and not SKIP_RE.match(line):
            summary = re.sub(r"\s+", " ", line).strip()
            # fold a continuation line so very short first lines still say something
            continue
        if summary:
            break
    summary = re.sub(r"[*_`]", "", summary)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "..."
    return title, summary


def build(folder: Path, out_name: str, title: str, prefixes: list[str], max_chars: int) -> str:
    notes = sorted(p for p in folder.glob("*.md") if p.name.lower() != out_name.lower())
    groups: dict[str, list[Path]] = {"": []}
    for pre in prefixes:
        groups[pre] = []
    for p in notes:
        for pre in prefixes:
            if p.name.startswith(pre):
                groups[pre].append(p)
                break
        else:
            groups[""].append(p)

    total_kb = sum(p.stat().st_size for p in notes) / 1024
    lines = [
        f"# {title}",
        "",
        f"{len(notes)} notes, {total_kb:,.0f} KB (~{total_kb * 1024 / 4:,.0f} tokens). "
        "Read THIS index, then open only the notes you need. "
        f"Regenerate with `python scripts/notes_index.py {folder.as_posix()}`.",
        "",
    ]

    def section(heading: str, items: list[Path]) -> None:
        if not items:
            return
        lines.extend([f"## {heading}", "", "| Note | KB | Summary |", "|---|---:|---|"])
        for p in items:
            t, s = summarize(p, max_chars)
            kb = max(1, round(p.stat().st_size / 1024))
            t = t.replace("|", "\\|")
            s = s.replace("|", "\\|")
            lines.append(f"| [{t}](./{p.name}) | {kb} | {s} |")
        lines.append("")

    for pre in prefixes:
        section(f"`{pre}*` notes", groups[pre])
    section("Notes" if not prefixes else "Other notes", groups[""])
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", default="README.md", help="output filename inside folder (default README.md)")
    ap.add_argument("--title", default=None, help="H1 for the index (default: folder name)")
    ap.add_argument("--group-prefix", default="", help="comma-separated filename prefixes to list separately")
    ap.add_argument("--summary-chars", type=int, default=140)
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if the index is missing or stale")
    args = ap.parse_args(argv)

    folder: Path = args.folder
    if not folder.is_dir():
        print(f"not a directory: {folder}", file=sys.stderr)
        return 2
    prefixes = [p for p in (s.strip() for s in args.group_prefix.split(",")) if p]
    text = build(folder, args.out, args.title or folder.name, prefixes, args.summary_chars)
    target = folder / args.out
    if args.check:
        current = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        if current == text:
            print(f"{target} is current")
            return 0
        print(f"{target} is stale or missing - regenerate", file=sys.stderr)
        return 1
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target} ({len(text):,} chars, {text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
