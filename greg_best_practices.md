# greg_best_practices.md - how Greg's workspaces are organized

**Audience: any AI agent, on any machine, in any of Greg's workspaces.** Greg
points you here when he wants to set up, move, reorganize or refine a workspace or a
process. Read the section you need and **execute it yourself**: install tools, clone,
build environments. Never hand Greg an install list; pause only for things only he
can do (sign-ins, approving commits, decisions).

This repo is public: never put personal, financial or secret information in it.

Quick map:
| Greg says... | Do |
|---|---|
| "read greg.md" (in a repo) | that repo's `greg.md` (section 3 explains the convention) |
| "set up / move this machine" | the repo's `greg.md`; if it has none, section 4 then its `greg.md` |
| "apply best practices to this repo" | section 4 (adopt), then 5 if code sits in a cloud folder |
| "refine / improve a practice" | section 9 |

## 1. Principles

1. **Greg remembers one thing per repo: `greg.md`.** Everything else is routed from it.
2. **One sync system per kind of thing.** Code moves by GitHub only. Live data moves
   by a cloud folder (OneDrive) only. Never a git checkout inside OneDrive/iCloud/Dropbox.
3. **State lives on disk, not in chat.** Where we left off = `SESSION_STATE.md`;
   owner facts and decisions go to the project's own store (DB, docs), never only chat.
4. **The agent does the work.** Setup, installs, moves - all automated or scripted.
5. **Greg approves commits.** Don't commit until he has seen the result and asks.
6. **Stdlib Python for tooling** (Greg works on Windows and macOS; no bash/PowerShell-only scripts).
7. **Never destroy what you can't prove is stale.** No `git reset --hard`, `git clean`
   or deletes in shared folders; back up before replacing.
8. **Small always-on instructions.** `AGENTS.md` <= ~3k tokens; detail elsewhere (section 8).

## 2. Standard layout

| What | Where | Moves between machines by |
|---|---|---|
| Code | GitHub; local disk checkout: Mac `~/code/<repo>`, Windows `%USERPROFILE%\code\<repo>` | git push/pull (owner-approved) |
| Live data (DBs, documents, statements, generated reports) | OneDrive folder, e.g. `OneDrive/<Name>` | OneDrive, automatically |
| Where we left off | `SESSION_STATE.md`: in the data folder if there is one, else the repo | OneDrive / hibernate commit |
| Per-machine status | `<data>/.machines/<host>.json` (written by wake/hibernate) | OneDrive |
| Python env | `~/.venvs/<repo>` - never inside a cloud folder or the repo | rebuilt per machine |
| Secrets | `.env` in the checkout (git-ignored), from an encrypted vault or copied by hand | per machine |
| Data backup (optional) | data mirror committed to GitHub by a repo command | when owner asks |

OneDrive locations: Mac `~/Library/CloudStorage/OneDrive-Personal/`, Windows `%OneDrive%`.

## 3. The greg.md convention

Every repo has `greg.md` at its root (template: `kit/templates/greg.md`). It is
self-sufficient and has, in order:
1. **Where everything lives** (the section 2 table, filled in for that repo).
2. **Which situation are you in?** -> new machine / move this machine / resume.
3. **New machine**, 4. **Move this machine**, 5. **Resume**, 6. **Before leaving**.

If the repo has a data folder, put an identical copy of `greg.md` there too, so an
agent that opens the data folder finds it.

## 4. Adopt the kit in a repo

```
curl -fsSL https://raw.githubusercontent.com/codenotes666-blip/BestPractices/main/adopt.py -o adopt.py
python3 adopt.py <repo> --user        # Windows: python adopt.py <repo> --user
```
(Delete `adopt.py` after; never commit it into the target repo.)

It installs `scripts/wake.py`, `wake_sync.py`, `hibernate.py`, `notes_index.py`,
the SessionStart hook, adds the LF rule to `.gitattributes`, scaffolds `greg.md`,
`AGENTS.md`, `SESSION_STATE.md` if missing, and (`--user`) the tiny always-on VS Code
pointer to this repo. Existing files are kept; `--force` updates the kit scripts.

Then the agent's part:
1. Fill every TODO in `greg.md` and `AGENTS.md` from what the repo actually does.
   If the repo already has `BOOTSTRAP.md`/`README` setup steps, route to them from `greg.md`.
2. `git add --renormalize .` and check `git diff --cached --stat`: it should be only
   line-ending normalization; mention it to Greg.
3. Optional `.wake.local.py` (repo-specific status lines, < 5 s) and
   `.wake.hooks.json` (template `kit/templates/wake.hooks.json`): python interpreter,
   data folder, shared state file, processes to stop during sync.
4. `python scripts/wake.py` must run clean. Show Greg the diff; commit on approval.

## 5. Move a repo out of a cloud folder (code/data split)

Symptoms that it is needed: hundreds of "modified" files (CRLF), `.git.broken*`
folders, `*-HOSTNAME.*` conflict copies, a machine's git behind while its files are
ahead, "it worked on the other machine".

1. **Inventory.** Which top-level paths are code (tracked source, docs, tests) and
   which are data (DBs, PDFs/statements, generated reports, caches, user uploads)?
   Paths stored inside data (e.g. DB rows) must stay valid: keep them relative to the
   data folder.
2. **One config value for the data folder.** Resolution order:
   `$<REPO>_DATA_DIR` -> the checkout itself if it sits inside a cloud folder (old
   layout keeps working during transition) -> the usual OneDrive locations (look for a
   marker file such as the DB) -> the checkout (dev clone). Expose it as a command
   (e.g. `data where`). Route every data path through it - including one-off scripts.
3. **Shared state.** `.wake.hooks.json`: `data_dir_cmd`, `state_file:
   {data}/SESSION_STATE.md`, `machines_dir: {data}/.machines`; `quiesce` hooks for any
   server holding the DB. Git-ignore `.machines/`.
4. **Old-layout warning.** Make `.wake.local.py` print `!! OLD LAYOUT ... do greg.md
   MOVE THIS MACHINE` when the checkout is inside a cloud folder and has `.git`.
5. **Backup (optional).** A command that mirrors the data into the checkout (SQLite
   via the online-backup API) and commits/pushes it on Greg's request.
6. **Tests** for the data-folder resolution and anything new; run the full suite.
7. **First machine:** clone to local disk, point the venv at it (`pip install -e`),
   copy secrets, restart services, prove the live data is used (e.g. `lsof` shows the
   OneDrive DB), `wake.py` clean. Copy the new `greg.md`/`AGENTS.md` into the data folder.
8. Greg approves -> commit + push. Note in `SESSION_STATE.md` which machines moved.
9. **Other machines:** Greg says "read greg.md" there -> MOVE THIS MACHINE.
10. **Cleanup** of code from the cloud folder only when every machine has moved and
    Greg approves. Keep all data files and `greg.md`.

Worked example: private repo `codenotes666-blip/amex`, commit `188d4a5`
(`config.find_data_dir`, `databackup.py`, `greg.md`, `.wake.hooks.json`).

## 6. Daily protocol (every kit repo)

- **Start:** `python scripts/wake.py` (or the SessionStart hook). It fetches, refreshes
  CRLF-only noise, advances to GitHub without clobbering unknown work, lists other
  machines' unpushed code, cloud conflict copies, `SESSION_STATE.md`, repo extras.
- **Leaving:** update `SESSION_STATE.md` (what / resume / TODO, <= 80 lines), then
  `python scripts/hibernate.py "<note>"`. Shared state file -> stamped only; repo
  state file -> committed + pushed.
- **Before editing on machine B** while A has unpushed work (wake says `NOT ON
  GITHUB`): tell Greg; finish/push on A first.

## 7. Cloud-folder pitfalls and remedies

| Pitfall | Remedy |
|---|---|
| Windows CRLF -> ~all files "modified" on Mac | `* text=auto eol=lf` in `.gitattributes`; `wake_sync.classify` treats EOL-only as noise |
| Files arrive before the commit that made them (half-synced tree) | `wake_sync.sync`: mixed reset to upstream, restore only files equal to a committed older version, keep unknown content, back up to `~/.wake-backups/` |
| `.git` corrupted / conflict copies inside `.git` | code out of the cloud folder (section 5) |
| SQLite DB written on two machines | one writer at a time; servers stopped during sync (`quiesce`); DB replaced only when upstream contains every local row |
| `name (1).ext`, `name-HOSTNAME.ext` copies | wake lists them; compare, keep one |
| Cloud-only placeholders make git slow | keep code local; don't run whole-tree scans in the cloud folder |
| venv inside the cloud folder breaks on the other OS | `~/.venvs/<repo>` per machine |

## 8. Instruction tiering (token cost)

- **Tier 0** - `AGENTS.md` (+ `.github/copilot-instructions.md`/`CLAUDE.md` pointing to
  it): identity, 5-6 hard rules, how to run/test, session protocol, routing table.
  <= ~3k tokens; it is paid on every turn.
- **Tier 1** - `.github/instructions/<topic>.instructions.md` with a keyword-rich
  `description: "Use when ..."` and an `applyTo` glob matching real paths.
- **Tier 2** - long notes in a folder with a generated index (`scripts/notes_index.py <dir>`).
- Move text verbatim when re-tiering; one concern per file; never `applyTo: "**"`
  except the tiny user-level pointer.
- Owner facts/decisions that change often belong in the project's DB or docs, not in
  instruction files.

## 9. Refining a practice

1. Edit this repo (clone `~/code/BestPractices`); keep `kit/` generic - no repo,
   host, person or money specifics.
2. Change a kit script -> run `python -m pytest tests -q`; add a test for the new case.
3. Add a line to the changelog below; commit + push with Greg's approval.
4. Roll out: in each repo `python3 adopt.py <repo> --force` (updates kit scripts
   only), run `wake.py`, commit there on approval.

## Changelog

- 2026-11-25 - Created from the FlightTrainer bootstrap kit + the amex OneDrive
  migration: greg.md convention, code/data split, OneDrive-safe `wake_sync.py`
  (CRLF noise, stale-file restore, DB superset check, quiesce hooks, shared
  `SESSION_STATE.md`, per-machine status), public `adopt.py`.
